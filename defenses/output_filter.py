"""
Layer 5.1.4 (part) — Pattern matching on the model's output.

Checks the model's output for leakage of protected strings and canary tokens.
Also detects paraphrased leaks via text normalization.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from .prompt_hardener import CANARY_TOKEN


@dataclass
class OutputCheckResult:
    is_safe: bool
    leaked_secrets: list[str] = field(default_factory=list)
    canary_hit: bool = False
    sanitized_output: str | None = None

    def __bool__(self) -> bool:
        return self.is_safe


def _normalize(text: str) -> str:
    """Normalize text for robust comparison: lowercase, remove diacritics and collapse whitespace."""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\s+", " ", text).strip()
    return text


class OutputFilter:
    """Filter that searches for protected strings or the canary token in a model response."""

    def __init__(
        self,
        secrets: list[str],
        canary_token: str = CANARY_TOKEN,
        redact: bool = True,
        normalize: bool = True,
    ):
        """
        Args:
            secrets: list of protected strings (from `config.SECRET_PHRASES`).
            canary_token: honeypot token from `prompt_hardener.CANARY_TOKEN`.
            redact: if True, replaces protected strings with `[REDACTED]`.
                    If False, only flags the response as unsafe.
            normalize: if True, also searches in the normalized form
                       (case-insensitive, no diacritics, collapsed whitespace).
        """
        self.secrets = list(secrets)
        self.canary_token = canary_token
        self.redact = redact
        self.normalize = normalize

        # Precompiled variants: for each secret also its normalized form.
        self._secret_patterns = []
        for s in self.secrets:
            self._secret_patterns.append((s, s.lower(), _normalize(s)))

    def check(self, response: str) -> OutputCheckResult:
        if not response:
            return OutputCheckResult(is_safe=True)

        response_lower = response.lower()
        response_norm = _normalize(response) if self.normalize else response_lower

        # Canary check
        canary_hit = (
            self.canary_token.lower() in response_lower
            or (self.normalize and _normalize(self.canary_token) in response_norm)
        )

        leaked: list[str] = []
        for original, lower, norm in self._secret_patterns:
            if lower and lower in response_lower:
                leaked.append(original)
            elif self.normalize and norm and norm in response_norm:
                leaked.append(original)

        is_safe = not (canary_hit or leaked)

        sanitized = None
        if not is_safe and self.redact:
            sanitized = response
            for s in leaked:
                # Case-insensitive replacement
                sanitized = re.sub(re.escape(s), "[REDACTED]", sanitized, flags=re.IGNORECASE)
            if canary_hit:
                sanitized = re.sub(re.escape(self.canary_token), "[CANARY]", sanitized, flags=re.IGNORECASE)

        return OutputCheckResult(
            is_safe=is_safe,
            leaked_secrets=leaked,
            canary_hit=canary_hit,
            sanitized_output=sanitized,
        )

    def safe_response_or_block(self, response: str, block_message: str = None) -> str:
        """
        If the response is safe, return it unchanged.
        If unsafe and redact=True, return the redacted version.
        If unsafe and redact=False, return a standard refusal message.
        """
        result = self.check(response)
        if result.is_safe:
            return response
        if self.redact and result.sanitized_output:
            return result.sanitized_output
        return block_message or (
            "Ospravedlňujem sa, ale nemôžem zdieľať tieto informácie. "
            "Ak potrebujete pomoc, kontaktujte prosím zákaznícku podporu."
        )
