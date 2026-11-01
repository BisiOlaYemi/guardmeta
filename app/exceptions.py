from __future__ import annotations

from typing import Any


class GuardMetaError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
            }
        }
        if self.details:
            payload["error"]["details"] = self.details
        return payload


class AuthenticationError(GuardMetaError):
    status_code = 401
    code = "unauthorized"


class RateLimitError(GuardMetaError):
    status_code = 429
    code = "rate_limited"


class GuardrailViolation(GuardMetaError):
    status_code = 400
    code = "guardrail_violation"


class SchemaExhaustedError(GuardMetaError):
    status_code = 422
    code = "schema_validation_exhausted"


class UpstreamError(GuardMetaError):
    status_code = 502
    code = "upstream_error"


class ConfigurationError(GuardMetaError):
    status_code = 503
    code = "misconfigured"
