"""
Defense mechanisms against prompt injection attacks.

This package contains implementations of the defense layers described in
chapter 5 of the thesis. Layers can be used independently or composed
into a multi-layer defense (defense-in-depth).

Defenses against direct attacks (chapter 5.1):
    - InputSanitizer        — input sanitization (5.1.1)
    - harden_system_prompt  — system prompt hardening (5.1.2)
    - OutputFilter          — pattern matching on output (5.1.4)
    - LLMJudge              — LLM-as-judge on output (5.1.4)

Defenses against indirect (data-based) attacks (chapter 5.2):
    - safe_extract_pdf      — document-level sanitization (5.2.2)
    - validate_hr_output    — domain validation for HR (5.2.4)
    - validate_invoice_output — domain validation for invoices (5.2.4)
    - LLMJudge              — content judge for indirect attacks (5.2.5)
"""

from .input_sanitizer import InputSanitizer, SanitizationResult, ATTACK_PATTERNS
from .prompt_hardener import (
    harden_system_prompt,
    HARDENED_HR_SYSTEM_PROMPT,
    HARDENED_INVOICE_SYSTEM_PROMPT,
    CANARY_TOKEN,
)
from .output_filter import OutputFilter, OutputCheckResult
from .llm_judge import LLMJudge
from .pdf_safe_extract import safe_extract_pdf, ExtractionReport
from .domain_validator import (
    validate_hr_output,
    validate_invoice_output,
    HR_VALIDATION_THRESHOLD,
    SUPPLIER_IBAN_DB,
)

__all__ = [
    "InputSanitizer", "SanitizationResult", "ATTACK_PATTERNS",
    "harden_system_prompt", "HARDENED_HR_SYSTEM_PROMPT",
    "HARDENED_INVOICE_SYSTEM_PROMPT", "CANARY_TOKEN",
    "OutputFilter", "OutputCheckResult",
    "LLMJudge",
    "safe_extract_pdf", "ExtractionReport",
    "validate_hr_output", "validate_invoice_output",
    "HR_VALIDATION_THRESHOLD", "SUPPLIER_IBAN_DB",
]
