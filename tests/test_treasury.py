from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.clients.meta import ModelTurn, ToolCall
from app.config import get_settings
from app.gateway.proxy import provide_model_client
from app.main import create_app
from app.treasury.agent import run_treasury_agent
from app.treasury.scenarios import get_treasury_scenario
from app.treasury.store import TrustedTreasuryStore
from app.treasury.verifier import verify_treasury_policy


class ScriptedTreasuryClient:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.calls = []

    def complete(self, messages, *, json_schema=None, schema_name="payload", tools=None):
        self.calls.append({"messages": messages, "tools": tools})
        return self.turns.pop(0)


def _tool_turn(task) -> ModelTurn:
    requested = [
        ("lookup_actor", {"actor_id": task.requested_by}),
        ("get_account_state", {"account_id": task.account_id}),
        ("get_beneficiary_profile", {"account_id": task.beneficiary_account_id}),
        ("check_idempotency", {"idempotency_key": task.idempotency_key}),
        ("get_treasury_policy", {}),
        *(("get_approval", {"approval_id": item}) for item in task.approval_ids),
    ]
    return ModelTurn(
        content="",
        model="muse-spark-open",
        prompt_tokens=100,
        completion_tokens=40,
        finish_reason="tool_calls",
        tool_calls=[
            ToolCall(id=f"call-{index}", name=name, arguments=json.dumps(arguments))
            for index, (name, arguments) in enumerate(requested)
        ],
    )


def _proposal_turn(task) -> ModelTurn:
    proposal = {
        **task.model_dump(mode="json", exclude={"objective"}),
        "execution_mode": "DRY_RUN",
    }
    return ModelTurn(
        content=json.dumps(proposal),
        model="muse-spark-open",
        prompt_tokens=400,
        completion_tokens=120,
    )


def test_proof_agent_gathers_tools_and_approves_valid_payment():
    _, task = get_treasury_scenario("proof-approved-payment")
    client = ScriptedTreasuryClient([_tool_turn(task), _proposal_turn(task)])
    result = run_treasury_agent(task, client=client)
    assert result.status == "approved"
    assert result.certificate.status == "SAT"
    assert result.model_calls == 2
    assert result.tool_calls == 7
    assert len(result.certificate.certificate_sha256) == 64


def test_policy_denies_schema_valid_self_approval():
    _, task = get_treasury_scenario("proof-denied-self-approval")
    proposal = _proposal_turn(task)
    from app.treasury.models import TreasuryProposal

    validated = TreasuryProposal.model_validate(json.loads(proposal.content))
    certificate = verify_treasury_policy(validated, task, TrustedTreasuryStore())
    assert certificate.status == "UNSAT"
    failed = {item.policy for item in certificate.checks if not item.passed}
    assert "separation_of_duties" in failed


def test_policy_denies_rolling_daily_exposure():
    _, task = get_treasury_scenario("proof-denied-daily-exposure")
    from app.treasury.models import TreasuryProposal

    validated = TreasuryProposal.model_validate(json.loads(_proposal_turn(task).content))
    certificate = verify_treasury_policy(validated, task, TrustedTreasuryStore())
    assert certificate.status == "UNSAT"
    failed = {item.policy for item in certificate.checks if not item.passed}
    assert failed == {"daily_exposure"}


def test_public_treasury_scenario_runs_full_agent(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    get_settings.cache_clear()
    _, task = get_treasury_scenario("proof-approved-payment")
    client = ScriptedTreasuryClient([_tool_turn(task), _proposal_turn(task)])
    application = create_app()
    application.dependency_overrides[provide_model_client] = lambda: client
    try:
        with TestClient(application) as test_client:
            catalog = test_client.get("/v1/treasury/scenarios")
            response = test_client.post(
                "/v1/treasury/run", json={"scenario_id": "proof-approved-payment"}
            )
        assert catalog.status_code == 200
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "approved"
        assert body["certificate"]["status"] == "SAT"
    finally:
        get_settings.cache_clear()
