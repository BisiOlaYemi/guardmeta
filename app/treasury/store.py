from __future__ import annotations

from copy import deepcopy
from typing import Any


POLICY_VERSION = "treasury-v1.0"

_ACTORS = {
    "USR-ALICE": {
        "actor_id": "USR-ALICE",
        "active": True,
        "roles": ["treasury_requester", "treasury_approver"],
    },
    "USR-BOB": {"actor_id": "USR-BOB", "active": True, "roles": ["treasury_approver"]},
    "USR-CAROL": {"actor_id": "USR-CAROL", "active": True, "roles": ["senior_treasury_approver"]},
}

_ACCOUNTS = {
    "ACC-10004567": {
        "account_id": "ACC-10004567",
        "available_balance": "500000.00",
        "daily_outflow": "20000.00",
        "daily_limit": "75000.00",
        "allowed_currencies": ["USD", "EUR"],
    }
}

_BENEFICIARIES = {
    "ACC-20009876": {
        "account_id": "ACC-20009876",
        "verified": True,
        "relationship_age_days": 120,
        "sanctions_clear": True,
    }
}


def _approval(approval_id: str, actor_id: str, key: str, max_amount: str) -> dict[str, Any]:
    return {
        "approval_id": approval_id,
        "actor_id": actor_id,
        "status": "APPROVED",
        "account_id": "ACC-10004567",
        "beneficiary_account_id": "ACC-20009876",
        "currency": "USD",
        "idempotency_key": key,
        "max_amount": max_amount,
    }


_APPROVALS = {
    "APR-BOB-8841": _approval("APR-BOB-8841", "USR-BOB", "pay-8841-proof", "50000.00"),
    "APR-CAROL-8841": _approval("APR-CAROL-8841", "USR-CAROL", "pay-8841-proof", "50000.00"),
    "APR-ALICE-9901": _approval("APR-ALICE-9901", "USR-ALICE", "pay-9901-proof", "10000.00"),
    "APR-BOB-9920": _approval("APR-BOB-9920", "USR-BOB", "pay-9920-proof", "70000.00"),
    "APR-CAROL-9920": _approval("APR-CAROL-9920", "USR-CAROL", "pay-9920-proof", "70000.00"),
}

_USED_IDEMPOTENCY_KEYS = {"pay-already-executed"}

_POLICY = {
    "policy_version": POLICY_VERSION,
    "high_value_threshold": "25000.00",
    "high_value_required_approvals": 2,
    "standard_required_approvals": 1,
    "high_value_beneficiary_tenure_days": 30,
    "rules": [
        "requester must be active and hold treasury_requester",
        "approvers must be active treasury approvers and independent from requester",
        "approvals must be bound to the exact transaction intent",
        "high-value transfers require two independent approvals",
        "daily exposure and available balance must cover the transfer",
        "beneficiary must be verified, sanctions-clear, and sufficiently established",
        "idempotency key must be unused",
    ],
}


class TrustedTreasuryStore:
    """Deterministic demo data standing in for IAM, ledger, and approval services."""

    def lookup_actor(self, actor_id: str) -> dict[str, Any]:
        return deepcopy(_ACTORS.get(actor_id) or {"actor_id": actor_id, "found": False})

    def get_account_state(self, account_id: str) -> dict[str, Any]:
        return deepcopy(_ACCOUNTS.get(account_id) or {"account_id": account_id, "found": False})

    def get_beneficiary_profile(self, account_id: str) -> dict[str, Any]:
        return deepcopy(_BENEFICIARIES.get(account_id) or {"account_id": account_id, "found": False})

    def get_approval(self, approval_id: str) -> dict[str, Any]:
        return deepcopy(_APPROVALS.get(approval_id) or {"approval_id": approval_id, "found": False})

    def check_idempotency(self, idempotency_key: str) -> dict[str, Any]:
        return {"idempotency_key": idempotency_key, "already_used": idempotency_key in _USED_IDEMPOTENCY_KEYS}

    def get_policy(self) -> dict[str, Any]:
        return deepcopy(_POLICY)

    def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        dispatch = {
            "lookup_actor": lambda: self.lookup_actor(str(arguments.get("actor_id", ""))),
            "get_account_state": lambda: self.get_account_state(str(arguments.get("account_id", ""))),
            "get_beneficiary_profile": lambda: self.get_beneficiary_profile(str(arguments.get("account_id", ""))),
            "get_approval": lambda: self.get_approval(str(arguments.get("approval_id", ""))),
            "check_idempotency": lambda: self.check_idempotency(str(arguments.get("idempotency_key", ""))),
            "get_treasury_policy": self.get_policy,
        }
        if name not in dispatch:
            return {"error": "unknown_tool", "tool": name}
        return dispatch[name]()


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_actor",
            "description": "Read trusted IAM status and roles for an actor.",
            "parameters": {"type": "object", "properties": {"actor_id": {"type": "string"}}, "required": ["actor_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_account_state",
            "description": "Read trusted ledger balance, daily exposure, limit, and allowed currencies.",
            "parameters": {"type": "object", "properties": {"account_id": {"type": "string"}}, "required": ["account_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_beneficiary_profile",
            "description": "Read trusted beneficiary verification, sanctions, and relationship age.",
            "parameters": {"type": "object", "properties": {"account_id": {"type": "string"}}, "required": ["account_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_approval",
            "description": "Read one trusted approval and its exact transaction binding.",
            "parameters": {"type": "object", "properties": {"approval_id": {"type": "string"}}, "required": ["approval_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_idempotency",
            "description": "Check whether an execution idempotency key has already been used.",
            "parameters": {"type": "object", "properties": {"idempotency_key": {"type": "string"}}, "required": ["idempotency_key"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_treasury_policy",
            "description": "Read the current versioned treasury authorization policy.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
