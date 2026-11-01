from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Type

from pydantic import BaseModel

from app.exceptions import GuardrailViolation
from app.guardrails.jsonschema import to_meta_json_schema
from app.guardrails.schemas import (
    FinancialTransactionPayload,
    MedicalRecordPayload,
    SchemaDescriptor,
    WorkflowExecutionPayload,
)


@dataclass(frozen=True)
class RegisteredSchema:
    name: str
    title: str
    description: str
    risk: str
    model: Type[BaseModel]

    def json_schema(self) -> dict:
        return to_meta_json_schema(self.model)

    def descriptor(self) -> SchemaDescriptor:
        return SchemaDescriptor(
            name=self.name,
            title=self.title,
            description=self.description,
            risk=self.risk,  # type: ignore[arg-type]
        )


class SchemaRegistry:
    def __init__(self) -> None:
        self._items: dict[str, RegisteredSchema] = {}
        self.register(
            RegisteredSchema(
                name="financial_transaction",
                title="Financial Transaction",
                description="Ledger-safe funds movement payload with decimal amounts and idempotency.",
                risk="critical",
                model=FinancialTransactionPayload,
            )
        )
        self.register(
            RegisteredSchema(
                name="medical_record",
                title="Medical Encounter Summary",
                description="Structured clinical summary keyed by opaque patient references, not raw PHI.",
                risk="critical",
                model=MedicalRecordPayload,
            )
        )
        self.register(
            RegisteredSchema(
                name="workflow_execution",
                title="Enterprise Workflow Execution",
                description="Allowlisted downstream action with confirmation on destructive steps.",
                risk="high",
                model=WorkflowExecutionPayload,
            )
        )

    def register(self, item: RegisteredSchema) -> None:
        self._items[item.name] = item

    def get(self, name: str) -> RegisteredSchema:
        try:
            return self._items[name]
        except KeyError as exc:
            raise GuardrailViolation(
                f"Unknown schema '{name}'",
                details={"available": sorted(self._items)},
            ) from exc

    def list(self) -> list[SchemaDescriptor]:
        return [item.descriptor() for item in self._items.values()]


@lru_cache(maxsize=1)
def get_registry() -> SchemaRegistry:
    return SchemaRegistry()
