from __future__ import annotations

from dataclasses import dataclass, field
from app.config import get_settings
from app.exceptions import GuardrailViolation
from app.guardrails.bounds import BusinessBounds
from app.guardrails.injection import InjectionScanner
from app.guardrails.pii import PIIDetector, PIIMatch


@dataclass
class FirewallReport:
    original_chars: int
    sanitized_chars: int
    redactions: list[str] = field(default_factory=list)
    injection_hits: int = 0


class InputFirewall:
    """Layer 1: block injections, strip secrets, bound prompt size."""

    def __init__(self) -> None:
        self.injection = InjectionScanner()
        self.pii = PIIDetector()
        settings = get_settings()
        self.max_chars = settings.max_prompt_chars

    def process(self, prompt: str) -> tuple[str, FirewallReport]:
        if prompt is None or not str(prompt).strip():
            raise GuardrailViolation("Prompt is empty")
        text = str(prompt)
        if len(text) > self.max_chars:
            raise GuardrailViolation(
                "Prompt exceeds maximum size",
                details={"max_chars": self.max_chars, "actual": len(text)},
            )
        self.injection.enforce(text)
        redacted, matches = self.pii.redact(text)
        sanitized = " ".join(redacted.split())
        report = FirewallReport(
            original_chars=len(text),
            sanitized_chars=len(sanitized),
            redactions=[match.kind.value for match in matches],
        )
        return sanitized, report


class OutputFirewall:
    """Post-model scan: raw model text must not leak secrets or card data."""

    def __init__(self) -> None:
        self.pii = PIIDetector()
        self.bounds = BusinessBounds(get_settings().financial_max_amount)

    def scan_text(self, text: str) -> list[PIIMatch]:
        # Emails in memos can be legitimate; block high-risk material only.
        return [
            match
            for match in self.pii.find(text)
            if match.kind.value in {"ssn", "credit_card", "api_token"}
        ]

    def enforce_text(self, text: str) -> None:
        hits = self.scan_text(text)
        if hits:
            raise GuardrailViolation(
                "Model output contained regulated or secret material",
                details={"kinds": [hit.kind.value for hit in hits]},
            )

    def enforce_payload(self, payload: object) -> None:
        self.bounds.enforce(payload)


def sanitize_input(prompt: str) -> str:
    """Sprint-compatible helper used by tests and the correction agent."""
    cleaned, _report = InputFirewall().process(prompt)
    return cleaned
