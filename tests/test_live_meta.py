"""Optional live call against Meta Model API. Skipped unless MODEL_API_KEY is present."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live_client():
    get_settings.cache_clear()
    if not get_settings().has_upstream_key:
        pytest.skip("MODEL_API_KEY not configured")
    with TestClient(create_app()) as client:
        yield client


def test_live_financial_payload(live_client):
    response = live_client.post(
        "/v1/guarded/execute",
        json={
            "schema_name": "financial_transaction",
            "prompt": (
                "Create a DEBIT of 1250.50 USD from ACC-10004567 to ACC-20009876. "
                "Authorized by treasury.ops. Memo: vendor payout for invoice INV-8841. "
                "Value date 2026-10-27. Idempotency key pay-8841-live."
            ),
        },
        timeout=120,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "success"
    assert body["data"]["currency"] == "USD"
    assert float(body["data"]["amount"]) == 1250.50
