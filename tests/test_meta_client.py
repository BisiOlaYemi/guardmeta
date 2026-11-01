from types import SimpleNamespace

from app.clients.meta import MetaModelClient
from app.config import Settings


def _response(content: str = "ok"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
        model="local-model",
        model_dump=lambda **_: {"model": "local-model"},
    )


class RecordingCompletions:
    def __init__(self, failures: int = 0):
        self.failures = failures
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= self.failures:
            raise TypeError("unsupported request field")
        return _response()


def _client(completions: RecordingCompletions) -> MetaModelClient:
    client = MetaModelClient(
        Settings(
            META_BASE_URL="http://localhost:9000/v1",
            MODEL_NAME="local-model",
            REASONING_EFFORT="low",
        )
    )
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client


def test_plain_completion_does_not_force_json_mode():
    completions = RecordingCompletions()
    result = _client(completions).complete([{"role": "user", "content": "hello"}])
    assert result.content == "ok"
    assert "response_format" not in completions.calls[0]


def test_structured_completion_falls_back_to_json_and_legacy_tokens():
    completions = RecordingCompletions(failures=2)
    result = _client(completions).complete(
        [{"role": "user", "content": "return JSON"}],
        json_schema={"type": "object", "properties": {}},
    )
    assert result.content == "ok"
    assert completions.calls[0]["response_format"]["type"] == "json_schema"
    assert completions.calls[1]["response_format"] == {"type": "json_object"}
    assert "reasoning_effort" not in completions.calls[1]
    assert "max_tokens" in completions.calls[2]
    assert "max_completion_tokens" not in completions.calls[2]
