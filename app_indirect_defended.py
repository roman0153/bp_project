"""
Obranená verzia HR a faktúrovej aplikácie (Aplikácia B, C).

Implementuje vrstvy 5.2.2, 5.2.4 a 5.2.5 z kapitoly 5.

Použitie:
    from app_indirect_defended import query_hr_defended, query_invoice_defended

    response, info = query_hr_defended(
        pdf_path="documents/generated_v2/s_hr_white_experience.pdf",
        model="gpt-4o-mini",
        layers={"safe_extract": True, "domain_validate": True, "judge": True,
                "harden": True},
    )
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

from app_indirect import (
    HR_SYSTEM_PROMPT,
    INVOICE_SYSTEM_PROMPT,
    _call_model,
    extract_text_from_pdf,
)
from config import MODELS

from defenses import (
    safe_extract_pdf,
    validate_hr_output,
    validate_invoice_output,
    LLMJudge,
)
from defenses.prompt_hardener import (
    get_hardened_hr_prompt,
    get_hardened_invoice_prompt,
)


@dataclass
class DocDefenseInfo:
    """Diagnostické info pre dokumentové obrany."""
    blocked_by: str | None = None
    # PDF extraction stats
    blocks_total: int = 0
    blocks_kept: int = 0
    blocks_filtered_color: int = 0
    blocks_filtered_size: int = 0
    blocks_filtered_outside: int = 0
    metadata_dropped: bool = False
    # Domain validation
    validation_valid: bool = True
    validation_warnings: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    requires_human: bool = False
    # Judge
    judge_label: str | None = None
    judge_reason: str | None = None
    # Layers
    layers_active: list[str] = field(default_factory=list)


_judge = None


def _get_judge() -> LLMJudge:
    global _judge
    if _judge is None:
        _judge = LLMJudge(judge_model="claude-haiku")
    return _judge


def _extract_doc_text(pdf_path: str, layers: dict) -> tuple[str, DocDefenseInfo]:
    """Vyextrahuje text z PDF — buď naivne alebo cez safe extraction."""
    info = DocDefenseInfo()
    info.layers_active = [k for k, v in layers.items() if v]

    if layers.get("safe_extract"):
        report = safe_extract_pdf(pdf_path)
        info.blocks_total = report.blocks_total
        info.blocks_kept = report.blocks_kept
        info.blocks_filtered_color = report.blocks_filtered_color
        info.blocks_filtered_size = report.blocks_filtered_size
        info.blocks_filtered_outside = report.blocks_filtered_outside
        info.metadata_dropped = report.metadata_dropped
        return report.text, info
    else:
        # Naivná extrakcia (rovnaká ako v app_indirect.py)
        return extract_text_from_pdf(pdf_path), info


def query_hr_defended(
    pdf_path: str,
    model: str,
    layers: dict | None = None,
) -> tuple[str, DocDefenseInfo]:
    """
    HR screening so zapnutými obrannými vrstvami.

    Args:
        pdf_path: cesta k PDF životopisu.
        model: identifikátor modelu.
        layers: dict booleans:
            - "safe_extract":  bezpečná extrakcia textu z PDF (5.2.2)
            - "harden":        zosilnený systémový prompt (5.1.2 + 5.2.x)
            - "domain_validate": doménová validácia výstupu (5.2.4)
            - "judge":         LLM sudca obsahu (5.2.5)
            - "hitl":          human-in-the-loop eskalácia (5.2.6) —
                                blokuje keď domain_validator vráti requires_human=True
    """
    if layers is None:
        layers = {}

    # === Vrstva 1: PDF sanitizácia ===
    doc_text, info = _extract_doc_text(pdf_path, layers)

    # === Vrstva 2: Zosilnený systémový prompt ===
    system_prompt = HR_SYSTEM_PROMPT
    if layers.get("harden"):
        system_prompt = get_hardened_hr_prompt(HR_SYSTEM_PROMPT)

    user_message = f"Analyzuj nasledujúci životopis a vyhodnoť kandidáta:\n\n{doc_text}"
    response = _call_model(system_prompt, user_message, model)

    # === Vrstva 3: doménová validácia ===
    if layers.get("domain_validate"):
        v = validate_hr_output(response)
        info.validation_valid = v.is_valid
        info.validation_warnings = list(v.warnings)
        info.validation_errors = list(v.errors)
        info.requires_human = v.requires_human
        if not v.is_valid:
            info.blocked_by = "domain_validator"

    # === Vrstva 4: LLM judge (beží vždy, keď je zapnutý — nezávisle od domain_validate) ===
    if layers.get("judge"):
        verdict = _get_judge().judge_hr(response)
        info.judge_label = verdict.label
        info.judge_reason = verdict.reason
        if not verdict.is_safe and info.blocked_by is None:
            info.blocked_by = "llm_judge"

    # === Vrstva 5: HITL (human-in-the-loop) ===
    # Ak je HITL zapnuté a domain_validator vyžaduje manuálnu kontrolu,
    # útok sa eskaluje na človeka (z pohľadu automatu = zablokované).
    if layers.get("hitl") and info.requires_human and info.blocked_by is None:
        info.blocked_by = "hitl"

    return response, info


def query_invoice_defended(
    pdf_path: str,
    model: str,
    layers: dict | None = None,
) -> tuple[str, DocDefenseInfo]:
    """Faktúrové spracovanie so zapnutými obrannými vrstvami."""
    if layers is None:
        layers = {}

    # === Vrstva 1: PDF sanitizácia ===
    doc_text, info = _extract_doc_text(pdf_path, layers)

    # === Vrstva 2: Zosilnený systémový prompt ===
    system_prompt = INVOICE_SYSTEM_PROMPT
    if layers.get("harden"):
        system_prompt = get_hardened_invoice_prompt(INVOICE_SYSTEM_PROMPT)

    user_message = f"Spracuj nasledujúcu faktúru a extrahuj údaje:\n\n{doc_text}"
    response = _call_model(system_prompt, user_message, model)

    # === Vrstva 3: doménová validácia ===
    if layers.get("domain_validate"):
        v = validate_invoice_output(response)
        info.validation_valid = v.is_valid
        info.validation_warnings = list(v.warnings)
        info.validation_errors = list(v.errors)
        info.requires_human = v.requires_human
        if not v.is_valid:
            info.blocked_by = "domain_validator"

    # === Vrstva 4: LLM judge (beží vždy, keď je zapnutý — nezávisle od domain_validate) ===
    if layers.get("judge"):
        verdict = _get_judge().judge_invoice(response)
        info.judge_label = verdict.label
        info.judge_reason = verdict.reason
        if not verdict.is_safe and info.blocked_by is None:
            info.blocked_by = "llm_judge"

    # === Vrstva 5: HITL (human-in-the-loop) ===
    if layers.get("hitl") and info.requires_human and info.blocked_by is None:
        info.blocked_by = "hitl"

    return response, info
