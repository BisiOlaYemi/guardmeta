import json

import pytest

from app.agents.correction import execute_with_self_correction, run_correction_loop
from app.exceptions import SchemaExhaustedError
from app.guardrails.registry import get_registry
from app.guardrails.schemas import FinancialTransactionPayload
from tests.conftest import VALID_FINANCIAL, ScriptedModelClient


def test_self_correction_repairs_invalid_enum():
    broken = json.dumps({**VALID_FINANCIAL, "transaction_type": "WIRE"})
    client = ScriptedModelClient([broken, json.dumps(VALID_FINANCIAL)])
    result = execute_with_self_correction(
        "Move 1250.50 USD between ACC-10004567 and ACC-20009876",
        FinancialTransactionPayload,
        max_retries=3,
        schema_name="financial_transaction",
        client=client,
    )
    assert result["status"] == "success"
    assert result["attempts"] == 2
    assert result["data"]["transaction_type"] == "DEBIT"
    assert len(client.calls) == 2
    assert "failed deterministic validation" in client.calls[1][-1]["content"]


def test_self_correction_exhausts_retries():
    broken = json.dumps({**VALID_FINANCIAL, "amount": "-12.00"})
    client = ScriptedModelClient([broken, broken, broken])
    with pytest.raises(SchemaExhaustedError):
        run_correction_loop(
            "debit twelve dollars",
            get_registry().get("financial_transaction"),
            max_retries=3,
            client=client,
        )


def test_json_embedded_in_markdown_is_accepted():
    fenced = "```json\n" + json.dumps(VALID_FINANCIAL) + "\n```"
    client = ScriptedModelClient([fenced])
    result = execute_with_self_correction(
        "vendor payout",
        FinancialTransactionPayload,
        client=client,
    )
    assert result["attempts"] == 1
    assert result["data"]["currency"] == "USD"


def test_malformed_candidate_is_repaired_by_live_model_turn():
    broken = json.dumps({**VALID_FINANCIAL, "transaction_type": "WIRE"})
    client = ScriptedModelClient([json.dumps(VALID_FINANCIAL)])
    result = run_correction_loop(
        "Move 1250.50 USD between the supplied accounts",
        get_registry().get("financial_transaction"),
        max_retries=3,
        client=client,
        initial_candidate=broken,
    )
    assert result.attempts == 2
    assert [item.source for item in result.trace] == ["candidate", "model"]
    assert result.trace[0].error is not None
    assert result.trace[1].error is None
    assert len(client.calls) == 1
