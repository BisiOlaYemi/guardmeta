from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class PIIKind(str, Enum):
    SSN = "ssn"
    CREDIT_CARD = "credit_card"
    EMAIL = "email"
    PHONE = "phone"
    API_TOKEN = "api_token"


@dataclass(frozen=True)
class PIIMatch:
    kind: PIIKind
    start: int
    end: int
    redacted: str


_SSN = re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")
_CC = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_EMAIL = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b")
_TOKEN = re.compile(
    r"\b(?:sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})\b"
)


def _luhn_ok(digits: str) -> bool:
    total = 0
    reverse = digits[::-1]
    for index, ch in enumerate(reverse):
        n = ord(ch) - 48
        if index % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


class PIIDetector:
    """Deterministic detectors for common regulated identifiers."""

    def find(self, text: str) -> list[PIIMatch]:
        matches: list[PIIMatch] = []
        matches.extend(self._find_ssn(text))
        matches.extend(self._find_cards(text))
        matches.extend(self._find_generic(text, _EMAIL, PIIKind.EMAIL, "[REDACTED_EMAIL]"))
        matches.extend(self._find_generic(text, _PHONE, PIIKind.PHONE, "[REDACTED_PHONE]"))
        matches.extend(self._find_generic(text, _TOKEN, PIIKind.API_TOKEN, "[REDACTED_TOKEN]"))
        matches.sort(key=lambda item: item.start)
        return matches

    def redact(self, text: str) -> tuple[str, list[PIIMatch]]:
        matches = self.find(text)
        if not matches:
            return text, []
        pieces: list[str] = []
        cursor = 0
        for match in matches:
            if match.start < cursor:
                continue
            pieces.append(text[cursor : match.start])
            pieces.append(match.redacted)
            cursor = match.end
        pieces.append(text[cursor:])
        return "".join(pieces), matches

    def _find_ssn(self, text: str) -> list[PIIMatch]:
        return [
            PIIMatch(PIIKind.SSN, m.start(), m.end(), "[REDACTED_SSN]")
            for m in _SSN.finditer(text)
        ]

    def _find_cards(self, text: str) -> list[PIIMatch]:
        found: list[PIIMatch] = []
        for match in _CC.finditer(text):
            digits = re.sub(r"\D", "", match.group(0))
            if 13 <= len(digits) <= 19 and _luhn_ok(digits):
                found.append(
                    PIIMatch(PIIKind.CREDIT_CARD, match.start(), match.end(), "[REDACTED_CARD]")
                )
        return found

    def _find_generic(
        self, text: str, pattern: re.Pattern[str], kind: PIIKind, token: str
    ) -> list[PIIMatch]:
        return [PIIMatch(kind, m.start(), m.end(), token) for m in pattern.finditer(text)]
