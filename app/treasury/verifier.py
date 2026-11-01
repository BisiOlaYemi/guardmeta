from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from z3 import And, BoolRef, BoolVal, IntVal, RealVal, Solver, sat

from app.treasury.models import PolicyCertificate, PolicyCheck, TreasuryProposal, TreasuryTask
from app.treasury.store import POLICY_VERSION, TrustedTreasuryStore


def verify_treasury_policy(
    proposal: TreasuryProposal,
    task: TreasuryTask,
    store: TrustedTreasuryStore,
) -> PolicyCertificate:
    actor = store.lookup_actor(proposal.requested_by)
    account = store.get_account_state(proposal.account_id)
    beneficiary = store.get_beneficiary_profile(proposal.beneficiary_account_id)
    approvals = [store.get_approval(item) for item in proposal.approval_ids]
    idempotency = store.check_idempotency(proposal.idempotency_key)
    policy = store.get_policy()

    amount = Decimal(proposal.amount)
    high_value = amount > Decimal(policy["high_value_threshold"])
    required_approvals = (
        policy["high_value_required_approvals"]
        if high_value
        else policy["standard_required_approvals"]
    )
    valid_approval_actors = {
        item.get("actor_id")
        for item in approvals
        if _approval_is_bound(item, proposal, store)
    }

    checks: list[PolicyCheck] = []

    def add(policy_name: str, passed: bool, explanation: str, refs: list[str]) -> None:
        checks.append(
            PolicyCheck(
                policy=policy_name,
                passed=_solver_truth(passed),
                explanation=explanation,
                evidence_refs=refs,
            )
        )

    add(
        "intent_immutability",
        _matches_task(proposal, task),
        "The proposed transaction must exactly match the trusted task envelope.",
        ["trusted-task"],
    )
    add(
        "requester_authorized",
        bool(actor.get("active")) and "treasury_requester" in actor.get("roles", []),
        "Requester must be active and hold the treasury_requester role.",
        [proposal.requested_by],
    )
    add(
        "approval_threshold",
        IntVal(len(valid_approval_actors)) >= IntVal(required_approvals),
        f"Transaction requires {required_approvals} independently bound approval(s).",
        proposal.approval_ids,
    )
    add(
        "separation_of_duties",
        proposal.requested_by not in valid_approval_actors,
        "Requester cannot approve the same transaction.",
        [proposal.requested_by, *proposal.approval_ids],
    )
    add(
        "available_balance",
        BoolVal(account.get("found", True))
        & (RealVal(str(amount)) <= RealVal(account.get("available_balance", "0"))),
        "Available ledger balance must cover the transfer.",
        [proposal.account_id],
    )
    add(
        "daily_exposure",
        BoolVal(account.get("found", True))
        & (
            RealVal(account.get("daily_outflow", "0")) + RealVal(str(amount))
            <= RealVal(account.get("daily_limit", "0"))
        ),
        "Existing daily outflow plus this amount must remain within the daily limit.",
        [proposal.account_id],
    )
    add(
        "currency_allowlist",
        proposal.currency.value in account.get("allowed_currencies", []),
        "Transaction currency must be enabled for the source account.",
        [proposal.account_id],
    )
    add(
        "beneficiary_trust",
        bool(beneficiary.get("verified")) and bool(beneficiary.get("sanctions_clear")),
        "Beneficiary must be verified and sanctions-clear.",
        [proposal.beneficiary_account_id],
    )
    add(
        "beneficiary_tenure",
        BoolVal(not high_value)
        | (
            IntVal(beneficiary.get("relationship_age_days", 0))
            >= IntVal(policy["high_value_beneficiary_tenure_days"])
        ),
        "High-value beneficiaries must satisfy the minimum relationship tenure.",
        [proposal.beneficiary_account_id],
    )
    add(
        "idempotency",
        not idempotency["already_used"],
        "Idempotency key must not have been used by a prior execution.",
        [proposal.idempotency_key],
    )

    combined = Solver()
    combined.add(And(*[BoolVal(item.passed) for item in checks]))
    status = "SAT" if combined.check() == sat else "UNSAT"
    counterexamples = [
        f"{item.policy}: {item.explanation}" for item in checks if not item.passed
    ]
    certificate_payload = {
        "policy_version": POLICY_VERSION,
        "status": status,
        "proposal": proposal.model_dump(mode="json"),
        "checks": [item.model_dump(mode="json") for item in checks],
    }
    digest = hashlib.sha256(
        json.dumps(certificate_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return PolicyCertificate(
        status=status,
        policy_version=POLICY_VERSION,
        certificate_sha256=digest,
        checks=checks,
        counterexamples=counterexamples,
    )


def _solver_truth(value: bool | BoolRef) -> bool:
    """Evaluate concrete and symbolic policy constraints through Z3."""

    solver = Solver()
    solver.add(BoolVal(value) if isinstance(value, bool) else value)
    return solver.check() == sat


def _approval_is_bound(
    approval: dict,
    proposal: TreasuryProposal,
    store: TrustedTreasuryStore,
) -> bool:
    actor = store.lookup_actor(str(approval.get("actor_id", "")))
    return all(
        [
            approval.get("status") == "APPROVED",
            approval.get("account_id") == proposal.account_id,
            approval.get("beneficiary_account_id") == proposal.beneficiary_account_id,
            approval.get("currency") == proposal.currency.value,
            approval.get("idempotency_key") == proposal.idempotency_key,
            Decimal(approval.get("max_amount", "0")) >= proposal.amount,
            bool(actor.get("active")),
            bool(
                {"treasury_approver", "senior_treasury_approver"}
                & set(actor.get("roles", []))
            ),
        ]
    )


def _matches_task(proposal: TreasuryProposal, task: TreasuryTask) -> bool:
    return all(
        [
            proposal.requested_by == task.requested_by,
            proposal.account_id == task.account_id,
            proposal.beneficiary_account_id == task.beneficiary_account_id,
            proposal.amount == task.amount,
            proposal.currency == task.currency,
            set(proposal.approval_ids) == set(task.approval_ids),
            proposal.memo == task.memo,
            proposal.value_date == task.value_date,
            proposal.idempotency_key == task.idempotency_key,
            proposal.execution_mode == "DRY_RUN",
        ]
    )
