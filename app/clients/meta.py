from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from app.config import Settings, get_settings
from app.eligibility import evaluate_eligibility
from app.exceptions import ConfigurationError, UpstreamError


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class ModelTurn:
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class ChatCompletionsClient(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        json_schema: dict[str, Any] | None = None,
        schema_name: str = "payload",
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelTurn: ...


class MetaModelClient:
    """Live OpenAI-compatible client pointed at Meta Model API or a self-hosted Open-weight runtime."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        timeout = httpx.Timeout(self.settings.request_timeout_seconds, connect=10.0)
        api_key = self.settings.upstream_api_key or "not-required"
        self._client = OpenAI(
            api_key=api_key,
            base_url=self.settings.meta_base_url,
            timeout=timeout,
            max_retries=2,
        )
        self.model = self.settings.model_name

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        json_schema: dict[str, Any] | None = None,
        schema_name: str = "payload",
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelTurn:
        evidence = evaluate_eligibility(self.settings)
        if self.settings.require_open_weight_model and not evidence.eligible:
            raise ConfigurationError(
                "Open-weight eligibility enforcement rejected the configured upstream.",
                details=evidence.to_dict(),
            )
        if not self.settings.has_upstream_key and self.settings.meta_base_url.startswith(
            "https://api.meta.ai"
        ):
            raise ConfigurationError(
                "MODEL_API_KEY / META_API_KEY is not set. Hosted Meta Model API requires a bearer token."
            )
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": 2048,
        }
        if self.settings.reasoning_effort:
            kwargs["reasoning_effort"] = self.settings.reasoning_effort
        if json_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": json_schema,
                    "strict": True,
                },
            }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        # Plain chat uses the provider's default text format. JSON mode is only
        # appropriate when a guarded/schema response was requested.
        response = self._create_with_compatibility_fallback(kwargs, structured=bool(json_schema))

        try:
            choice = response.choices[0]
            message = choice.message
            content = message.content or ""
            usage = response.usage
        except (AttributeError, IndexError, TypeError) as exc:
            raise UpstreamError("Upstream model API returned an invalid chat completion") from exc
        parsed_tool_calls = []
        for tool_call in getattr(message, "tool_calls", None) or []:
            function = getattr(tool_call, "function", None)
            parsed_tool_calls.append(
                ToolCall(
                    id=getattr(tool_call, "id", "") or "",
                    name=getattr(function, "name", "") or "",
                    arguments=getattr(function, "arguments", "{}") or "{}",
                )
            )
        return ModelTurn(
            content=content,
            model=response.model or self.model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            finish_reason=choice.finish_reason,
            tool_calls=parsed_tool_calls,
            raw=response.model_dump(exclude_none=True),
        )

    def _create_with_compatibility_fallback(
        self, kwargs: dict[str, Any], *, structured: bool
    ) -> Any:
        """Try modern, JSON-mode, then legacy OpenAI-compatible request shapes."""

        variants = [dict(kwargs)]

        compatible = dict(kwargs)
        compatible.pop("reasoning_effort", None)
        if structured:
            compatible["response_format"] = {"type": "json_object"}
        if compatible != variants[-1]:
            variants.append(compatible)

        legacy = dict(compatible)
        legacy["max_tokens"] = legacy.pop("max_completion_tokens")
        if legacy != variants[-1]:
            variants.append(legacy)

        last_error: Exception | None = None
        for index, candidate in enumerate(variants):
            try:
                return self._client.chat.completions.create(**candidate)
            except TypeError as exc:
                last_error = exc
            except APIStatusError as exc:
                last_error = exc
                if exc.status_code not in {400, 422} or index == len(variants) - 1:
                    raise _wrap_upstream(exc) from exc
            except (APITimeoutError, APIConnectionError) as exc:
                raise _wrap_upstream(exc) from exc
            except Exception as exc:  # noqa: BLE001 — last-mile transport failures
                raise UpstreamError(f"Model communication error: {exc}") from exc

        if isinstance(last_error, (APIStatusError, APITimeoutError, APIConnectionError)):
            raise _wrap_upstream(last_error) from last_error
        raise UpstreamError(f"Model client rejected the request shape: {last_error}") from last_error


def _wrap_upstream(exc: Exception) -> UpstreamError:
    status = getattr(exc, "status_code", None)
    body = ""
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            body = response.text[:500]
        except Exception:  # noqa: BLE001
            body = ""
    return UpstreamError(
        f"Upstream model API failed: {exc}",
        details={"status_code": status, "body": body},
    )
