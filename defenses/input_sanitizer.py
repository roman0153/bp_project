"""
Layer 5.1.1 — Input sanitization for direct prompt injection attacks.

Detects known attack patterns in user input using regular expressions.
Patterns cover the main attack categories from chapter 3
(echo/verbatim, role-play, format change, completion bait, fake SYSTEM
message, incremental probing).

Usage:
    from defenses import InputSanitizer
    sanitizer = InputSanitizer()
    result = sanitizer.check(user_message)
    if not result.is_safe:
        # block or escalate
        ...
"""

import re
from dataclasses import dataclass, field


@dataclass
class SanitizationResult:
    is_safe: bool
    matched_patterns: list[str] = field(default_factory=list)
    category: str | None = None
    matched_text: str | None = None

    def __bool__(self) -> bool:
        return self.is_safe


# Patterns grouped by attack categories from chapter 3 (Table 4).
# They cover specific attacks 1.1 - 1.8 from the catalog in the appendix.
ATTACK_PATTERNS: dict[str, list[str]] = {
    "echo_verbatim": [
        # 1.2a-d: echo / verbatim
        r"\brepeat\b.{0,40}\b(above|verbatim|everything|instructions|message)\b",
        r"\bzopakuj\b.{0,40}\b(všetk|inštrukcie|vyššie|dostal)\b",
        r"\bprint\b.{0,40}\b(your\s+)?(initial|system|original)\s+(instructions|prompt|message)\b",
        r"\bskopíruj\b.{0,40}\b(systémovú|inštrukcie|celý\s+text)\b",
        r"\bvypíš\b.{0,40}\b(všetk[oy]|inštrukcie|systémov)\b",
        r"\bword[\s-]*for[\s-]*word\b",
        r"\bdoslova\b",
    ],
    "naive_request": [
        # 1.1a-e: naive direct asking
        r"\baký\s+je\s+(tvoj|tvoja)\s+(systémov[ýy]|interný)\s+(prompt|inštrukcie|pravidlá)\b",
        r"\bwhat\s+(is|are)\s+your\s+(system\s+)?(prompt|instructions|rules)\b",
        r"\btell\s+me\s+(your|the\s+)?(secret|verification|internal)\b",
        r"\bpovedz\s+mi\s+(tajný|interný|všetky)\s+",
        r"\baké\s+(inštrukcie|pravidlá)\s+si\s+dostal\b",
    ],
    "role_play": [
        # 1.3a-f: role-play / persona
        r"\bDAN\b(\s|\(|$)",
        r"\bDo\s+Anything\s+Now\b",
        r"\bdebug\s+(mode|režim)\b",
        r"\bsystem(ový)?\s+admin",
        r"\bbezpečnostný\s+audítor\b",
        r"\bignore\s+(all\s+|previous\s+|prior\s+)+(instructions|prompts|rules)\b",
        r"\bignoruj\s+(všetky\s+|predchádzajúce\s+|všetk[oé]\s+|moje\s+)+(inštrukcie|pravidlá|systémové)\b",
        r"\bpretend\s+(you\s+(are|to be))\b",
        r"\bpredstav\s+si,?\s+že\s+si\b",
        r"\bod\s+teraz\s+si\b",
        r"\bI\s+am\s+the\s+(developer|creator)\b",
        r"\bsom\s+(developer|tvorca|vývojár)\s+ktorý\s+(ťa|teba)\s+(vytvoril|vyvinul)\b",
    ],
    "format_change": [
        # 1.4a-f: output format change
        r"\bbase64\b",
        r"\bencode\b.{0,40}\b(prompt|instructions|system|rules|secrets)\b",
        r"\b(prepíš|rewrite)\b.{0,40}\b(ako|as|do)\b.{0,40}\b(python|json|xml|yaml|komentár)\b",
        r"\bJSON\b.{0,60}\b(system|rules|secrets|prompt|inštrukcie|pravidlá)\b",
        r"\btranslate\b.{0,40}\b(your\s+)?(system|instructions|prompt)\b",
        r"\bprelož\b.{0,40}\b(inštrukcie|svoje|systémov)\b",
        r"\bnapíš\s+báseň\b.{0,80}\b(prompt|inštrukcie|systémov)",
        r"\bakrostich\b",
        r"\bbullet[\s-]*point\b.{0,40}\b(prompt|rules|instructions)\b",
        r"\bzhrn(\s|úť|i)\b.{0,40}\b(systémový\s+prompt|všetky\s+pravidlá)\b",
    ],
    "completion_bait": [
        # 1.6a-d: completion bait
        r"tvoj\s+(systémový\s+)?prompt\s+začína",
        r"your\s+(system\s+)?prompt\s+(starts|begins|contains)",
        r"INTERNÉ\s+PRAVIDLÁ\b\s*:",
        r"\bdokonči\b.{0,200}(zľavov[ýé]|tajn[ýé]|interné|verifikačný|kód)",
        r"TAJNÝ\s+(VERIFIKAČNÝ\s+)?KÓD",
        r"\bzľavový\s+kód\s+_+\s+poskytuje\s+_+",
        r"\bnávratová\s+politika.{0,40}\bumožňuje\s+výnimky\s+pre\s*\.\.\.",
    ],
    "fake_system": [
        # 1.8a-f: creative attacks / fake SYSTEM message
        r"\[SYSTEM\]",
        r"<!--\s*SYSTEM",
        r"#\s*SYSTEM\s+OVERRIDE",
        r"###\s*SYSTEM\s*###",
        r">\s*cat\s+/etc/",
        r"SEC[-_]\d{4}[-_]\d{4}",
        r"\bSYSTEM\s+MESSAGE\b\s*:",
        r"\bnový\s+režim\s*[-:]",
        r"\bbezpečnostný\s+test\s+SEC",
        r"system_prompt\.(txt|md|json)",
    ],
    "incremental_probing": [
        # 1.5a-e: incremental probing - these are the hardest to catch
        r"\bobsahuje\s+tvoj\s+prompt\s+(slovo|reťazec|kód)\b",
        r"\btajný\s+kód\s+(začína|končí|obsahuje)\s+(na|s|reťazcom)\b",
        r"\bmarž[aiu]\b.{0,40}\b(je|okolo|asi|približne|cca)\s+\d{1,3}\s*%",
        r"\blen\s+(áno\s+alebo\s+nie|yes\s+or\s+no)\b",
        r"\b(spomína|obsahuje)\s+tvoj\s+prompt\s+",
        r"\b(začína|končí)\s+na\s+ALPHA",
    ],
    "fragment_replay": [
        # Attacks containing strings from the actual system prompt
        # (replay attacks that masquerade as "confirmation")
        r"PRERODINU2026",
        r"ALPHA2353",
        r"Turbo[-\s]?5000",
        r"Fei\s+Technologies",
    ],
}


class InputSanitizer:
    """Detector of known prompt injection patterns in user input."""

    def __init__(self, patterns: dict | None = None, case_insensitive: bool = True):
        if patterns is None:
            patterns = ATTACK_PATTERNS
        flags = re.IGNORECASE | re.DOTALL if case_insensitive else re.DOTALL
        self.compiled: dict[str, list[re.Pattern]] = {}
        for category, pats in patterns.items():
            self.compiled[category] = [re.compile(p, flags) for p in pats]

    def check(self, message: str) -> SanitizationResult:
        """Return SanitizationResult with is_safe flag and details of the first matched pattern."""
        if not message:
            return SanitizationResult(is_safe=True)

        for category, regexes in self.compiled.items():
            for regex in regexes:
                m = regex.search(message)
                if m:
                    return SanitizationResult(
                        is_safe=False,
                        matched_patterns=[regex.pattern],
                        category=category,
                        matched_text=m.group(0)[:120],
                    )
        return SanitizationResult(is_safe=True)

    def check_all(self, message: str) -> list[SanitizationResult]:
        """Return list of all matched patterns (for diagnostics, not blocking)."""
        hits = []
        for category, regexes in self.compiled.items():
            for regex in regexes:
                m = regex.search(message)
                if m:
                    hits.append(SanitizationResult(
                        is_safe=False,
                        matched_patterns=[regex.pattern],
                        category=category,
                        matched_text=m.group(0)[:120],
                    ))
        return hits
