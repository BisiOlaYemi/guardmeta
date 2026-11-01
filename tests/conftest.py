from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("GUARDMETA_API_KEY", "")
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "1000")

from app.clients.meta import ModelTurn  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.gateway.proxy import provide_model_client  # noqa: E402
from app.main import create_app  # noqa: E402

get_settings.cache_clear()


VALID_FINANCIAL = {
    "account_id": "ACC-10004567",
    "counterparty_account_id": "ACC-20009876",
    "transaction_type": "DEBIT",
    "amount": "1250.50",
    "currency": "USD",
    "authorized_by": "treasury.ops",
    "memo": "vendor payout for invoice INV-8841",
    "value_date": "2026-10-27",
    "idempotency_key": "pay-8841-a1",
}


class ScriptedModelClient:
    def __init__(self, payloads: list[str]) -> None:
        self.payloads = list(payloads)
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages, *, json_schema=None, schema_name="payload"):
        self.calls.append(messages)
        if not self.payloads:
            raise RuntimeError("ScriptedModelClient has no remaining payloads")
        content = self.payloads.pop(0)
        return ModelTurn(content=content, model="muse-spark-1.3", prompt_tokens=12, completion_tokens=40)


@pytest.fixture
def scripted_success():
    return ScriptedModelClient([json.dumps(VALID_FINANCIAL)])


@pytest.fixture
def scripted_repair():
    broken = json.dumps({**VALID_FINANCIAL, "transaction_type": "WIRE"})
    return ScriptedModelClient([broken, json.dumps(VALID_FINANCIAL)])


@pytest.fixture
def client(scripted_success):
    application = create_app()
    application.dependency_overrides[provide_model_client] = lambda: scripted_success
    with TestClient(application) as test_client:
        yield test_client
    application.dependency_overrides.clear()
