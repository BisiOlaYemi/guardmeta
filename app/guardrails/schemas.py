from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TransactionType(str, Enum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"


class CurrencyCode(str, Enum):
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    CAD = "CAD"
    NGN = "NGN"


class FinancialTransactionPayload(StrictModel):
    """High-risk funds movement. Amounts are decimal strings to avoid float drift."""

    account_id: str = Field(..., min_length=8, max_length=34, pattern=r"^[A-Z0-9\-]+$")
    counterparty_account_id: str = Field(..., min_length=8, max_length=34, pattern=r"^[A-Z0-9\-]+$")
    transaction_type: TransactionType
    amount: Decimal = Field(..., gt=0, max_digits=14, decimal_places=2)
    currency: CurrencyCode
    authorized_by: str = Field(..., min_length=2, max_length=120)
    memo: str = Field(..., min_length=3, max_length=280)
    value_date: date
    idempotency_key: str = Field(..., min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")

    @field_validator("amount")
    @classmethod
    def two_decimal_places(cls, value: Decimal) -> Decimal:
        quantized = value.quantize(Decimal("0.01"))
        if quantized != value:
            raise ValueError("amount must have at most two decimal places")
        return quantized

    @model_validator(mode="after")
    def distinct_accounts(self) -> "FinancialTransactionPayload":
        if self.account_id == self.counterparty_account_id:
            raise ValueError("account_id and counterparty_account_id must differ")
        return self


class ClinicalSeverity(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class MedicalRecordPayload(StrictModel):
    patient_ref: str = Field(..., min_length=6, max_length=40, pattern=r"^PT-[A-Z0-9]+$")
    encounter_id: str = Field(..., min_length=6, max_length=40, pattern=r"^ENC-[A-Z0-9]+$")
    icd10_codes: list[str] = Field(..., min_length=1, max_length=12)
    summary: str = Field(..., min_length=12, max_length=2000)
    severity: ClinicalSeverity
    clinician_id: str = Field(..., min_length=4, max_length=40, pattern=r"^DR-[A-Z0-9]+$")
    recorded_at: datetime
    follow_up_required: bool

    @field_validator("icd10_codes")
    @classmethod
    def valid_icd10(cls, codes: list[str]) -> list[str]:
        cleaned: list[str] = []
        for code in codes:
            normalized = code.strip().upper()
            if not _ICD10.match(normalized):
                raise ValueError(f"invalid ICD-10 code: {code}")
            cleaned.append(normalized)
        return cleaned


class WorkflowRisk(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class WorkflowExecutionPayload(StrictModel):
    """Allowlisted enterprise action. Destructive steps require explicit confirmation."""

    workflow_id: str = Field(..., min_length=4, max_length=64, pattern=r"^[a-z0-9][a-z0-9_\-]+$")
    action: Literal[
        "create_ticket",
        "notify_slack",
        "update_crm",
        "queue_payout",
        "revoke_session",
        "lock_account",
    ]
    target_id: str = Field(..., min_length=1, max_length=128)
    parameters: dict[str, str] = Field(default_factory=dict, max_length=20)
    requested_by: str = Field(..., min_length=2, max_length=120)
    risk: WorkflowRisk
    dry_run: bool
    confirmation_token: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def high_risk_requires_confirmation(self) -> "WorkflowExecutionPayload":
        destructive = self.action in {"queue_payout", "revoke_session", "lock_account"}
        if destructive and self.risk != WorkflowRisk.HIGH:
            raise ValueError("destructive actions must be classified HIGH risk")
        if destructive and not self.dry_run and not self.confirmation_token:
            raise ValueError("live destructive actions require confirmation_token")
        if self.confirmation_token and len(self.confirmation_token) < 16:
            raise ValueError("confirmation_token must be at least 16 characters")
        return self


class SchemaDescriptor(BaseModel):
    name: str
    title: str
    description: str
    risk: Literal["high", "critical"]


_ICD10 = re.compile(r"^[A-TV-Z][0-9][0-9A-Z](?:\.[0-9A-Z]{1,4})?$")
