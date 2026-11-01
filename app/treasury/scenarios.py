from __future__ import annotations

from copy import deepcopy

from app.treasury.models import TreasuryTask


_BASE = {
    "requested_by": "USR-ALICE",
    "account_id": "ACC-10004567",
    "beneficiary_account_id": "ACC-20009876",
    "currency": "USD",
    "value_date": "2026-10-28",
}

_SCENARIOS = {
    "proof-approved-payment": {
        "id": "proof-approved-payment",
        "title": "Authorize with a machine-checkable policy certificate",
        "description": "Muse gathers IAM, ledger, beneficiary, approval, policy, and idempotency evidence before proposing a $42,000 dry-run payment.",
        "expected_outcome": "approved",
        "task": {
            **_BASE,
            "objective": "Prepare a dry-run payment of 42000 USD for ACME invoice INV-8841 using the supplied independent approvals.",
            "amount": "42000.00",
            "approval_ids": ["APR-BOB-8841", "APR-CAROL-8841"],
            "memo": "ACME invoice INV-8841",
            "idempotency_key": "pay-8841-proof",
        },
    },
    "proof-denied-self-approval": {
        "id": "proof-denied-self-approval",
        "title": "Deny a schema-valid self-approved payment",
        "description": "The JSON is valid, but trusted IAM evidence proves the requester approved the same transaction.",
        "expected_outcome": "denied",
        "task": {
            **_BASE,
            "objective": "Prepare a dry-run payment of 8000 USD for invoice INV-9901 with the supplied approval.",
            "amount": "8000.00",
            "approval_ids": ["APR-ALICE-9901"],
            "memo": "Invoice INV-9901",
            "idempotency_key": "pay-9901-proof",
        },
    },
    "proof-denied-daily-exposure": {
        "id": "proof-denied-daily-exposure",
        "title": "Deny a payment that breaches rolling exposure",
        "description": "Two approvals are valid, but trusted ledger state proves the transfer would exceed the daily account limit.",
        "expected_outcome": "denied",
        "task": {
            **_BASE,
            "objective": "Prepare a dry-run payment of 60000 USD for invoice INV-9920 using the supplied approvals.",
            "amount": "60000.00",
            "approval_ids": ["APR-BOB-9920", "APR-CAROL-9920"],
            "memo": "Invoice INV-9920",
            "idempotency_key": "pay-9920-proof",
        },
    },
}


def list_treasury_scenarios() -> list[dict]:
    return [
        {key: value for key, value in item.items() if key != "task"}
        for item in _SCENARIOS.values()
    ]


def get_treasury_scenario(scenario_id: str) -> tuple[dict, TreasuryTask] | None:
    item = _SCENARIOS.get(scenario_id)
    if not item:
        return None
    descriptor = {key: value for key, value in item.items() if key != "task"}
    return deepcopy(descriptor), TreasuryTask.model_validate(deepcopy(item["task"]))
