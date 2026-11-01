from app.config import Settings


def test_model_api_key_is_not_masked_by_empty_meta_alias():
    settings = Settings(MODEL_API_KEY="model-key", META_API_KEY="")
    assert settings.upstream_api_key == "model-key"
    assert settings.has_upstream_key is True


def test_meta_api_key_remains_supported():
    settings = Settings(MODEL_API_KEY="", META_API_KEY="meta-key")
    assert settings.upstream_api_key == "meta-key"


def test_open_weight_endpoint_key_takes_priority():
    settings = Settings(
        OPEN_WEIGHT_API_KEY="modal-proxy-token",
        MODEL_API_KEY="hosted-meta-key",
    )
    assert settings.upstream_api_key == "modal-proxy-token"
