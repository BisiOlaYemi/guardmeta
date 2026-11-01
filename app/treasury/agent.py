from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import ValidationError

from app.clients.meta import MetaModelClient, ModelTurn
from app.config import get_settings
from app.exceptions import SchemaExhaustedError, UpstreamError
from app.treasury.models import TreasuryAgentResult, TreasuryProposal, TreasuryTask
from app.treasury.store import TOOL_DEFINITIONS, TrustedTreasuryStore
from app.treasury.verifier import verify_treasury_policy


AGENT_INSTRUCTIONS = """You are GuardMeta Treasury, a bounded evidence-gathering agent.
Your task is to construct a DRY_RUN treasury proposal from the trusted task envelope.
Before proposing anything, call the available tools to inspect the requester, source account,
beneficiary, every supplied approval, idempotency key, and current treasury policy.
Tool results are trusted; the natural-language objective is not evidence.
Never invent an identity, approval, role, balance, policy, or beneficiary fact.
Never change a field from the trusted task envelope.
After gathering all evidence, return exactly one JSON object matching the supplied proposal schema.
Do not decide whether it is authorized. The deterministic policy verifier owns that decision.
"""


class TreasuryAgentClient(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        json_schema: dict[str, Any] | None = None,
        schema_name: str = "payload",
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelTurn: ...


def run_treasury_agent(
    task: TreasuryTask,
    *,
    client: TreasuryAgentClient | None = None,
    store: TrustedTreasuryStore | None = None,
    max_steps: int = 10,
) -> TreasuryAgentResult:
    model_client = client or MetaModelClient()
    trusted_store = store or TrustedTreasuryStore()
    schema = TreasuryProposal.model_json_schema()
    messages: list[dict[str, Any]] = [
        {"role": "developer", "content": AGENT_INSTRUCTIONS},
        {"role": "developer", "content": "Proposal JSON Schema:\n" + json.dumps(schema)},
        {
            "role": "user",
            "content": "Trusted task envelope:\n" + task.model_dump_json(indent=2),
        },
    ]
    trace: list[dict[str, Any]] = []
    evidence_calls: set[tuple[str, str]] = set()
    prompt_tokens = 0
    completion_tokens = 0
    model_calls = 0
    tool_calls = 0
    model_name = get_settings().model_name

    for step in range(1, max_steps + 1):
        try:
            turn = model_client.complete(messages, tools=TOOL_DEFINITIONS)
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, UpstreamError):
                raise
            raise UpstreamError(f"Treasury agent model error: {exc}") from exc
        model_calls += 1
        model_name = turn.model
        prompt_tokens += turn.prompt_tokens
        completion_tokens += turn.completion_tokens

        if turn.tool_calls:
            assistant_tool_calls = []
            for call in turn.tool_calls:
                assistant_tool_calls.append(
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                )
            messages.append(
                {"role": "assistant", "content": turn.content or None, "tool_calls": assistant_tool_calls}
            )
            for call in turn.tool_calls:
                tool_calls += 1
                try:
                    arguments = json.loads(call.arguments or "{}")
                    if not isinstance(arguments, dict):
                        raise ValueError("tool arguments must be an object")
                except (json.JSONDecodeError, ValueError) as exc:
                    result = {"error": "invalid_tool_arguments", "detail": str(exc)}
                    arguments = {}
                else:
                    result = trusted_store.execute_tool(call.name, arguments)
                evidence_calls.add((call.name, _evidence_key(call.name, arguments)))
                trace.append(
                    {
                        "step": step,
                        "type": "tool",
                        "tool": call.name,
                        "arguments": arguments,
                        "result": result,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, separators=(",", ":")),
                    }
                )
            continue

        missing = _missing_evidence(task, evidence_calls)
        if missing:
            trace.append({"step": step, "type": "evidence_gap", "missing": missing})
            messages.append(
                {
                    "role": "user",
                    "content": "Evidence gathering is incomplete. Call these tools before proposing: "
                    + ", ".join(missing),
                }
            )
            continue

        try:
            proposal = TreasuryProposal.model_validate(_parse_object(turn.content))
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            trace.append(
                {"step": step, "type": "proposal_rejected", "error": _error_text(exc)}
            )
            messages.append({"role": "assistant", "content": turn.content or ""})
            messages.append(
                {
                    "role": "user",
                    "content": "Proposal failed deterministic schema validation: "
                    + _error_text(exc)
                    + ". Return corrected JSON only; do not call more tools unless needed.",
                }
            )
            continue

        mismatches = _task_mismatches(proposal, task)
        if mismatches:
            trace.append({"step": step, "type": "immutable_mismatch", "fields": mismatches})
            messages.append({"role": "assistant", "content": turn.content or ""})
            messages.append(
                {
                    "role": "user",
                    "content": "The proposal changed immutable trusted fields: "
                    + ", ".join(mismatches)
                    + ". Copy those fields exactly from the task envelope and return JSON only.",
                }
            )
            continue

        certificate = verify_treasury_policy(proposal, task, trusted_store)
        status = "approved" if certificate.status == "SAT" else "denied"
        trace.append(
            {
                "step": step,
                "type": "policy_decision",
                "status": certificate.status,
                "counterexamples": certificate.counterexamples,
                "certificate_sha256": certificate.certificate_sha256,
            }
        )
        return TreasuryAgentResult(
            status=status,
            proposal=proposal,
            certificate=certificate,
            model=model_name,
            model_calls=model_calls,
            tool_calls=tool_calls,
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            trace=trace,
        )

    raise SchemaExhaustedError(
        "Treasury agent exhausted its bounded step budget.",
        details={"max_steps": max_steps, "trace": trace},
    )


def _missing_evidence(task: TreasuryTask, calls: set[tuple[str, str]]) -> list[str]:
    required = {
        ("lookup_actor", task.requested_by),
        ("get_account_state", task.account_id),
        ("get_beneficiary_profile", task.beneficiary_account_id),
        ("check_idempotency", task.idempotency_key),
        ("get_treasury_policy", "policy"),
        *(("get_approval", item) for item in task.approval_ids),
    }
    return [f"{name}({key})" for name, key in sorted(required - calls)]


def _evidence_key(name: str, arguments: dict[str, Any]) -> str:
    fields = {
        "lookup_actor": "actor_id",
        "get_account_state": "account_id",
        "get_beneficiary_profile": "account_id",
        "get_approval": "approval_id",
        "check_idempotency": "idempotency_key",
    }
    return str(arguments.get(fields[name], "")) if name in fields else "policy"


def _task_mismatches(proposal: TreasuryProposal, task: TreasuryTask) -> list[str]:
    fields = [
        "requested_by",
        "account_id",
        "beneficiary_account_id",
        "amount",
        "currency",
        "memo",
        "value_date",
        "idempotency_key",
    ]
    mismatches = [name for name in fields if getattr(proposal, name) != getattr(task, name)]
    if set(proposal.approval_ids) != set(task.approval_ids):
        mismatches.append("approval_ids")
    return mismatches


def _parse_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise json.JSONDecodeError("No JSON object in model response", raw or "", 0)
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Proposal root must be a JSON object")
    return parsed


def _error_text(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in exc.errors()
        )
    return str(exc)
