from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.guardrails.schemas import CurrencyCode


class TreasuryTask(BaseModel):
    """Trusted task envelope. Muse may interpret it but cannot change these facts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    objective: str = Field(..., min_length=10, max_length=1000)
    requested_by: str = Field(..., pattern=r"^USR-[A-Z0-9]+$")
    account_id: str = Field(..., pattern=r"^ACC-[0-9]+$")
    beneficiary_account_id: str = Field(..., pattern=r"^ACC-[0-9]+$")
    amount: Decimal = Field(..., gt=0, decimal_places=2)
    currency: CurrencyCode
    approval_ids: list[str] = Field(default_factory=list, max_length=4)
    memo: str = Field(..., min_length=3, max_length=280)
    value_date: date
    idempotency_key: str = Field(..., pattern=r"^[A-Za-z0-9_-]{8,64}$")


class TreasuryProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    requested_by: str
    account_id: str
    beneficiary_account_id: str
    amount: Decimal = Field(..., gt=0, decimal_places=2)
    currency: CurrencyCode
    approval_ids: list[str] = Field(default_factory=list, max_length=4)
    memo: str = Field(..., min_length=3, max_length=280)
    value_date: date
    idempotency_key: str
    execution_mode: Literal["DRY_RUN"] = "DRY_RUN"

    @field_validator("approval_ids")
    @classmethod
    def distinct_approvals(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("approval_ids must be distinct")
        return value


class PolicyCheck(BaseModel):
    policy: str
    passed: bool
    explanation: str
    evidence_refs: list[str] = Field(default_factory=list)


class PolicyCertificate(BaseModel):
    status: Literal["SAT", "UNSAT"]
    policy_version: str
    certificate_sha256: str
    checks: list[PolicyCheck]
    counterexamples: list[str]


class TreasuryAgentResult(BaseModel):
    status: Literal["approved", "denied"]
    proposal: TreasuryProposal
    certificate: PolicyCertificate
    model: str
    model_calls: int
    tool_calls: int
    usage: dict[str, int]
    trace: list[dict]
