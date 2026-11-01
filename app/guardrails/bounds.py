from __future__ import annotations

from decimal import Decimal

from app.exceptions import GuardrailViolation
from app.guardrails.schemas import FinancialTransactionPayload, WorkflowExecutionPayload


class BusinessBounds:
    def __init__(self, financial_max_amount: Decimal) -> None:
        self.financial_max_amount = financial_max_amount

    def enforce(self, payload: object) -> None:
        if isinstance(payload, FinancialTransactionPayload):
            self._financial(payload)
        elif isinstance(payload, WorkflowExecutionPayload):
            self._workflow(payload)

    def _financial(self, payload: FinancialTransactionPayload) -> None:
        if payload.amount > self.financial_max_amount:
            raise GuardrailViolation(
                "Transaction exceeds configured financial ceiling",
                details={
                    "amount": str(payload.amount),
                    "ceiling": str(self.financial_max_amount),
                    "currency": payload.currency.value,
                },
            )
        if payload.memo.lower() in {"test", "asdf", "lorem ipsum"}:
            raise GuardrailViolation("memo is not a production-grade description")

    def _workflow(self, payload: WorkflowExecutionPayload) -> None:
        blocked_keys = {"password", "secret", "token", "private_key"}
        offending = [key for key in payload.parameters if key.lower() in blocked_keys]
        if offending:
            raise GuardrailViolation(
                "workflow parameters contain blocked secret keys",
                details={"keys": offending},
            )
