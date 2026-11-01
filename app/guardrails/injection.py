from __future__ import annotations

import re
from dataclasses import dataclass

from app.exceptions import GuardrailViolation


@dataclass(frozen=True)
class InjectionHit:
    pattern: str
    category: str


_COMPILED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p, re.IGNORECASE), category)
    for p, category in [
        (r"ignore (all )?(previous|prior|above) instructions", "instruction_override"),
        (r"system override", "instruction_override"),
        (r"you are now (dan|unrestricted|jailbroken)", "jailbreak"),
        (r"developer mode (enabled|on)", "jailbreak"),
        (r"do not follow (your|the) (safety|system) (rules|prompt)", "jailbreak"),
        (r"reveal (your )?(system|hidden) prompt", "exfiltration"),
        (r"print (the )?(secret|api keys?|credentials)", "exfiltration"),
        (r"drop\s+(table|database)", "destructive_sql"),
        (r"union\s+select\s+.+\s+from", "destructive_sql"),
        (r";\s*rm\s+-rf\s+/", "command_injection"),
        (r"\$\(.*\)", "command_injection"),
        (r"<script\b", "xss"),
        (r"javascript:", "xss"),
        (r"\bBEGIN PRIVATE KEY\b", "secret_material"),
        (r"\bAKIA[0-9A-Z]{16}\b", "secret_material"),
    ]
]


class InjectionScanner:
    def scan(self, text: str) -> list[InjectionHit]:
        hits: list[InjectionHit] = []
        for pattern, category in _COMPILED:
            match = pattern.search(text)
            if match:
                hits.append(InjectionHit(pattern=match.group(0), category=category))
        return hits

    def enforce(self, text: str) -> None:
        hits = self.scan(text)
        if hits:
            first = hits[0]
            raise GuardrailViolation(
                f"Malicious prompt pattern detected ({first.category})",
                details={
                    "category": first.category,
                    "pattern": first.pattern,
                    "hit_count": len(hits),
                },
            )
