import pytest

from app.clients.meta import MetaModelClient
from app.config import Settings
from app.eligibility import evaluate_eligibility
from app.exceptions import ConfigurationError


def test_hosted_meta_api_is_not_misrepresented_as_open_weight():
    settings = Settings(
        META_BASE_URL="https://api.meta.ai/v1",
        MODEL_NAME="muse-spark-1.3",
        MODEL_PROVIDER="auto",
    )
    evidence = evaluate_eligibility(settings)
    assert evidence.eligible is False
    assert evidence.provider == "meta_hosted"
    assert evidence.hosted_meta_api is True


def test_modal_muse_spark_open_is_eligible():
    settings = Settings(
        META_BASE_URL="https://example.modal.run/v1",
        MODEL_NAME="meta-models/Muse-Spark-Open-816B-A42B",
        MODEL_PROVIDER="modal",
        REQUIRE_OPEN_WEIGHT_MODEL=True,
    )
    evidence = evaluate_eligibility(settings)
    assert evidence.eligible is True
    assert evidence.open_weight is True
    assert evidence.model_family == "Muse Spark Open"


def test_enforcement_fails_closed_before_hosted_model_call():
    settings = Settings(
        MODEL_API_KEY="unused",
        META_BASE_URL="https://api.meta.ai/v1",
        MODEL_NAME="muse-spark-1.3",
        REQUIRE_OPEN_WEIGHT_MODEL=True,
    )
    client = MetaModelClient(settings)
    with pytest.raises(ConfigurationError) as raised:
        client.complete([{"role": "user", "content": "hello"}])
    assert raised.value.details["eligible"] is False
