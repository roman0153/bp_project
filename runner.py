"""
Automated attack runner.
Executes all attacks against selected models, collects responses, evaluates metrics,
and saves results to CSV for analysis.

Usage:
    # Run all attacks on all models (30 repetitions each)
    python runner.py

    # Run on specific model(s)
    python runner.py --models gpt-4o-mini claude-sonnet

    # Quick test run (1 repetition)
    python runner.py --runs 1

    # Run only specific technique category
    python runner.py --technique "Naivné opýtanie"

    # Run single attack by ID
    python runner.py --attack-id 1.3a
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

from app import query_model
from attacks.direct_attacks import ALL_SINGLE_TURN, ALL_MULTI_TURN
from config import MODELS, N_RUNS
from evaluate import evaluate_response


def run_single_turn_attack(attack: dict, model: str) -> dict:
    """Execute a single-turn attack and evaluate the response."""
    try:
        response = query_model(message=attack["prompt"], model=model)
        evaluation = evaluate_response(response)
        return {
            "response": response,
            "error": None,
            **evaluation,
        }
    except Exception as e:
        return {
            "response": None,
            "error": str(e),
            "secrets_extracted": [],
            "secrets_count": 0,
            "secrets_total": 0,
            "secrets_ratio": 0.0,
            "classification": "error",
            "success": False,
        }


def run_multi_turn_attack(attack: dict, model: str) -> dict:
    """Execute a multi-turn attack, sending messages sequentially."""
    messages = attack["prompt"]
    history = []
    last_response = ""

    try:
        for msg in messages:
            response = query_model(
                message=msg["content"],
                model=model,
                history=history,
            )
            # Add both user message and assistant response to history
            history.append({"role": "user", "content": msg["content"]})
            history.append({"role": "assistant", "content": response})
            last_response = response

        # Evaluate the final response (most likely to contain leaked info)
        # Also evaluate all responses combined
        all_responses = " ".join(
            h["content"] for h in history if h["role"] == "assistant"
        )
        evaluation = evaluate_response(all_responses)

        return {
            "response": last_response,
            "full_conversation": json.dumps(history, ensure_ascii=False),
            "error": None,
            **evaluation,
        }
    except Exception as e:
        return {
            "response": None,
            "full_conversation": json.dumps(history, ensure_ascii=False),
            "error": str(e),
            "secrets_extracted": [],
            "secrets_count": 0,
            "secrets_total": 0,
            "secrets_ratio": 0.0,
            "classification": "error",
            "success": False,
        }


def check_model_available(model: str) -> bool:
    """Quick check if a model is accessible."""
    try:
        response = query_model("Ahoj", model)
        return True
    except Exception as e:
        print(f"  ⚠ Model {model} nedostupný: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Prompt Injection Attack Runner")
    parser.add_argument(
        "--models", nargs="+", default=None,
        help=f"Models to test. Available: {', '.join(MODELS.keys())}",
    )
    parser.add_argument(
        "--runs", type=int, default=N_RUNS,
        help=f"Number of repetitions per attack (default: {N_RUNS})",
    )
    parser.add_argument(
        "--technique", type=str, default=None,
        help="Filter attacks by technique name (e.g., 'Role-play')",
    )
    parser.add_argument(
        "--attack-id", type=str, default=None,
        help="Run only a specific attack by ID (e.g., '1.3a')",
    )
    parser.add_argument(
        "--output-dir", type=str, default="results",
        help="Output directory for CSV results",
    )
    parser.add_argument(
        "--set", type=str, default="direct", choices=["direct"],
        help="Attack set to run (default: direct)",
    )
    parser.add_argument(
        "--delay", type=float, default=0.1,
        help="Delay between API calls in seconds (default: 0.1)",
    )
    parser.add_argument(
        "--workers", type=int, default=5,
        help="Number of parallel worker threads (default: 5)",
    )
    args = parser.parse_args()

    # Select models
    models = args.models if args.models else list(MODELS.keys())
    for m in models:
        if m not in MODELS:
            print(f"Neznámy model: {m}. Dostupné: {', '.join(MODELS.keys())}")
            sys.exit(1)

    # Filter attacks by set
    single_turn = list(ALL_SINGLE_TURN)
    multi_turn = list(ALL_MULTI_TURN)

    if args.attack_id:
        single_turn = [a for a in single_turn if a["id"] == args.attack_id]
        multi_turn = [a for a in multi_turn if a["id"] == args.attack_id]
        if not single_turn and not multi_turn:
            print(f"Útok s ID '{args.attack_id}' nebol nájdený.")
            sys.exit(1)

    if args.technique:
        single_turn = [a for a in single_turn if args.technique.lower() in a["technique"].lower()]
        multi_turn = [a for a in multi_turn if args.technique.lower() in a["technique"].lower()]

    all_attacks = (
        [(a, "single") for a in single_turn]
        + [(a, "multi") for a in multi_turn]
    )

    if not all_attacks:
        print("Žiadne útoky na spustenie.")
        sys.exit(0)

    # Setup output
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"results_{timestamp}.csv"

    # Summary
    total_calls = len(all_attacks) * len(models) * args.runs
    print(f"\n{'='*60}")
    print(f"  PROMPT INJECTION LAB — Attack Runner")
    print(f"{'='*60}")
    print(f"  Útoky:     {len(all_attacks)} ({len(single_turn)} single-turn, {len(multi_turn)} multi-turn)")
    print(f"  Modely:     {', '.join(models)}")
    print(f"  Opakovania: {args.runs}")
    print(f"  Celkovo:    {total_calls} API volaní")
    print(f"  Výstup:     {output_file}")
    print(f"{'='*60}\n")

    # Check model availability
    print("Kontrola dostupnosti modelov...")
    available_models = []
    for model in models:
        if check_model_available(model):
            print(f"  ✓ {MODELS[model]['display_name']} — OK")
            available_models.append(model)
        else:
            print(f"  ✗ {MODELS[model]['display_name']} — preskočený")

    if not available_models:
        print("\nŽiadny model nie je dostupný. Skontrolujte API kľúče v .env súbore.")
        sys.exit(1)

    models = available_models
    print()

    # CSV setup
    fieldnames = [
        "timestamp", "model", "attack_id", "technique", "description",
        "attack_type", "run_number",
        "response", "error",
        "secrets_count", "secrets_total", "secrets_ratio",
        "secrets_extracted",
        "classification", "success",
    ]

    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        # Main loop — parallel execution across runs
        total = len(all_attacks) * len(models) * args.runs
        write_lock = __import__('threading').Lock()

        def execute_one(model, attack, attack_type, run):
            if attack_type == "single":
                result = run_single_turn_attack(attack, model)
            else:
                result = run_multi_turn_attack(attack, model)
            time.sleep(args.delay)
            return {
                "timestamp": datetime.now().isoformat(),
                "model": model,
                "attack_id": attack["id"],
                "technique": attack["technique"],
                "description": attack["description"],
                "attack_type": attack_type,
                "run_number": run,
                "response": (result["response"] or "")[:2000],
                "error": result["error"],
                "secrets_count": result["secrets_count"],
                "secrets_total": result["secrets_total"],
                "secrets_ratio": result["secrets_ratio"],
                "secrets_extracted": json.dumps(result["secrets_extracted"], ensure_ascii=False),
                "classification": result["classification"],
                "success": result["success"],
            }

        from concurrent.futures import ThreadPoolExecutor, as_completed

        with tqdm(total=total, desc="Spúšťam útoky", unit="volanie") as pbar:
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = {}
                for model in models:
                    for attack, attack_type in all_attacks:
                        for run in range(1, args.runs + 1):
                            future = executor.submit(
                                execute_one, model, attack, attack_type, run
                            )
                            futures[future] = (model, attack["id"])

                for future in as_completed(futures):
                    model, attack_id = futures[future]
                    try:
                        row = future.result()
                        with write_lock:
                            writer.writerow(row)
                            f.flush()
                    except Exception as e:
                        print(f"\n  ⚠ Chyba: {model}/{attack_id}: {e}")

                    pbar.update(1)
                    pbar.set_postfix(model=model, attack=attack_id)

    # Print summary
    print(f"\n{'='*60}")
    print(f"  VÝSLEDKY ULOŽENÉ: {output_file}")
    print(f"{'='*60}\n")
    print("Pre analýzu spustite:")
    print(f"  Výsledky sú uložené v CSV formáte v adresári results/\n")


if __name__ == "__main__":
    main()
