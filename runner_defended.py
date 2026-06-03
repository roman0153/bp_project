"""
Runner for direct prompt injection attacks with defense layers active.

Executes the same attacks as runner.py, but passes each attack through
the defense layers selected by the --layer flag. Produces CSV output
in a format compatible with the existing analysis.

Usage:
    # Baseline (no defenses — sanity check that behavior matches runner.py)
    python runner_defended.py --layer 0 --models gpt-4o-mini --runs 10

    # Input sanitization only (Layer 1)
    python runner_defended.py --layer 1 --models gpt-4o-mini --runs 10

    # Sanitization + prompt hardening (Layers 1+2)
    python runner_defended.py --layer 2 --models gpt-4o-mini --runs 10

    # Full layered defense (Layers 1+2+3, without LLM judge due to cost)
    python runner_defended.py --layer 3 --models gpt-4o-mini --runs 10

    # Full defense including LLM judge (Layers 1+2+3+4)
    python runner_defended.py --layer 4 --models gpt-4o-mini --runs 5

    # Replicate Table 20 from the thesis — runs baseline + all layers
    python runner_defended.py --layer all --runs 10
"""

import argparse, csv, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

from app_defended import query_model_defended, DefenseInfo
from attacks.direct_attacks import ALL_SINGLE_TURN, ALL_MULTI_TURN
from config import MODELS
from evaluate import evaluate_response


# Mapping of layer number to dict of active defenses.
# Layers are cumulative: a higher level includes all preceding ones.
LAYER_CONFIGS = {
    0: {},  # Baseline — no defenses
    1: {"sanitize": True},
    2: {"sanitize": True, "harden": True},
    3: {"sanitize": True, "harden": True, "output_filter": True},
    4: {"sanitize": True, "harden": True, "output_filter": True, "judge": True},
}


def run_single_turn(attack: dict, model: str, layers: dict) -> dict:
    try:
        response, info = query_model_defended(
            message=attack["prompt"], model=model, layers=layers,
        )
        evaluation = evaluate_response(response)
        return {
            "response": response, "error": None,
            "blocked_by": info.blocked_by,
            "defense_meta": json.dumps({
                "sanitizer_category": info.sanitizer_category,
                "sanitizer_match": info.sanitizer_match,
                "leaked_secrets": info.leaked_secrets,
                "canary_hit": info.canary_hit,
                "judge_label": info.judge_label,
                "judge_reason": info.judge_reason,
            }, ensure_ascii=False),
            **evaluation,
        }
    except Exception as e:
        return {
            "response": None, "error": str(e),
            "blocked_by": None, "defense_meta": "{}",
            "secrets_extracted": [], "secrets_count": 0, "secrets_total": 0,
            "secrets_ratio": 0.0,
            "classification": "error", "success": False,
        }


def run_multi_turn(attack: dict, model: str, layers: dict) -> dict:
    """Multi-turn útoky: každú správu prevedieme cez obranné vrstvy."""
    messages = attack["prompt"]
    history = []
    last_response = ""
    blocked_at_turn = None
    block_info_combined = []

    try:
        for idx, msg in enumerate(messages):
            response, info = query_model_defended(
                message=msg["content"], model=model, history=history, layers=layers,
            )
            history.append({"role": "user", "content": msg["content"]})
            history.append({"role": "assistant", "content": response})
            last_response = response
            if info.blocked_by:
                block_info_combined.append(f"turn{idx}:{info.blocked_by}")
                if blocked_at_turn is None:
                    blocked_at_turn = idx

        all_responses = " ".join(h["content"] for h in history if h["role"] == "assistant")
        evaluation = evaluate_response(all_responses)
        return {
            "response": last_response,
            "full_conversation": json.dumps(history, ensure_ascii=False),
            "error": None,
            "blocked_by": ";".join(block_info_combined) if block_info_combined else None,
            "defense_meta": json.dumps({"blocked_at_turn": blocked_at_turn}, ensure_ascii=False),
            **evaluation,
        }
    except Exception as e:
        return {
            "response": None,
            "full_conversation": json.dumps(history, ensure_ascii=False),
            "error": str(e),
            "blocked_by": None, "defense_meta": "{}",
            "secrets_extracted": [], "secrets_count": 0, "secrets_total": 0,
            "secrets_ratio": 0.0,
            "classification": "error", "success": False,
        }


def execute_one(model, attack, attack_type, run, layers, delay):
    if attack_type == "single":
        result = run_single_turn(attack, model, layers)
    else:
        result = run_multi_turn(attack, model, layers)
    time.sleep(delay)
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
        "blocked_by": result["blocked_by"],
        "defense_meta": result["defense_meta"],
        "secrets_count": result["secrets_count"],
        "secrets_total": result["secrets_total"],
        "secrets_ratio": result["secrets_ratio"],
        "secrets_extracted": json.dumps(result["secrets_extracted"], ensure_ascii=False),
        "classification": result["classification"],
        "success": result["success"],
    }


def run_one_layer(layer_num: int, models: list[str], attacks: list,
                  runs: int, output_dir: Path, delay: float, workers: int):
    layers = LAYER_CONFIGS[layer_num]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"defended_layer{layer_num}_{timestamp}.csv"

    total = len(attacks) * len(models) * runs
    layer_desc = "baseline" if layer_num == 0 else " + ".join(layers.keys())
    print(f"\n{'='*70}")
    print(f"  Layer {layer_num} ({layer_desc}) | {total} volaní")
    print(f"  Output: {output_file}")
    print(f"{'='*70}\n")

    fields = [
        "timestamp", "model", "attack_id", "technique", "description",
        "attack_type", "run_number", "response", "error",
        "blocked_by", "defense_meta",
        "secrets_count", "secrets_total", "secrets_ratio", "secrets_extracted",
        "classification", "success",
    ]

    write_lock = __import__("threading").Lock()
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        with tqdm(total=total, desc=f"Layer {layer_num}", unit="call") as pbar:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {}
                for model in models:
                    for attack, attack_type in attacks:
                        for run in range(1, runs + 1):
                            fut = executor.submit(
                                execute_one, model, attack, attack_type, run, layers, delay,
                            )
                            futures[fut] = (model, attack["id"])
                for fut in as_completed(futures):
                    model, aid = futures[fut]
                    try:
                        row = fut.result()
                        with write_lock:
                            writer.writerow(row)
                            f.flush()
                    except Exception as e:
                        print(f"\n  ⚠ Chyba {model}/{aid}: {e}")
                    pbar.update(1)
                    pbar.set_postfix(model=model, attack=aid)

    return output_file


def main():
    parser = argparse.ArgumentParser(description="Defended Direct Attack Runner")
    parser.add_argument("--layer", default="0",
                        help="Number of defense layer (0-4) or 'all' for all layers")
    parser.add_argument("--models", nargs="+", default=None,
                        help=f"Models. Available: {', '.join(MODELS.keys())}")
    parser.add_argument("--runs", type=int, default=10,
                        help="Repetitions per attack (default: 10)")
    parser.add_argument("--attack-id", type=str, default=None,
                        help="Run only specific attack")
    parser.add_argument("--technique", type=str, default=None,
                        help="Filter by technique name")
    parser.add_argument("--output-dir", default="results")
    parser.add_argument("--delay", type=float, default=0.1)
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()

    models = args.models or list(MODELS.keys())
    for m in models:
        if m not in MODELS:
            print(f"Unknown model: {m}")
            sys.exit(1)

    # Collect attacks
    single = list(ALL_SINGLE_TURN)
    multi = list(ALL_MULTI_TURN)
    if args.attack_id:
        single = [a for a in single if a["id"] == args.attack_id]
        multi = [a for a in multi if a["id"] == args.attack_id]
    if args.technique:
        single = [a for a in single if args.technique.lower() in a["technique"].lower()]
        multi = [a for a in multi if args.technique.lower() in a["technique"].lower()]
    attacks = [(a, "single") for a in single] + [(a, "multi") for a in multi]

    if not attacks:
        print("Žiadne útoky.")
        sys.exit(0)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)

    # Layers to run
    if args.layer == "all":
        layers_to_run = [0, 1, 2, 3, 4]
    else:
        layers_to_run = [int(args.layer)]
    for l in layers_to_run:
        if l not in LAYER_CONFIGS:
            print(f"Neznáma vrstva: {l}. Dostupné: {list(LAYER_CONFIGS.keys())}")
            sys.exit(1)

    print(f"Layers: {layers_to_run}")
    print(f"Models: {models}")
    print(f"Attacks: {len(attacks)} ({len(single)} single-turn, {len(multi)} multi-turn)")
    print(f"Runs: {args.runs}")

    output_files = []
    for layer in layers_to_run:
        f = run_one_layer(layer, models, attacks, args.runs, output_dir,
                          args.delay, args.workers)
        output_files.append(f)

    print(f"\n{'='*70}")
    print("  Hotovo. Výsledky:")
    for f in output_files:
        print(f"    {f}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
