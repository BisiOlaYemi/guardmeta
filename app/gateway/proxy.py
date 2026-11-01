from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.agents.correction import AttemptTrace, run_correction_loop
from app.clients.meta import ChatCompletionsClient, MetaModelClient
from app.config import get_settings
from app.demo import get_demo_scenario, list_demo_scenarios
from app.eligibility import evaluate_eligibility
from app.exceptions import GuardMetaError, GuardrailViolation
from app.guardrails.registry import get_registry
from app.guardrails.validator import OutputFirewall, sanitize_input

logger = logging.getLogger("guardmeta.gateway")
router = APIRouter()


def provide_model_client() -> ChatCompletionsClient:
    return MetaModelClient()


class GuardedExecuteRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=32_000)
    schema_name: str = Field(..., examples=["financial_transaction"])
    max_retries: int | None = Field(default=None, ge=1, le=8)
    candidate: str | dict[str, Any] | None = Field(
        default=None,
        description="Optional upstream candidate to validate before asking the model to repair it.",
    )


class ChatMessage(BaseModel):
    role: Literal["developer", "system", "user", "assistant"]
    content: str


class ChatCompletionsRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage] = Field(..., min_length=1)
    schema_name: str | None = None
    max_retries: int | None = Field(default=None, ge=1, le=8)


class DemoRunRequest(BaseModel):
    scenario_id: str = Field(..., min_length=1, max_length=80)


def _trace_payload(traces: list[AttemptTrace]) -> list[dict[str, Any]]:
    return [
        {
            "attempt": item.attempt,
            "source": item.source,
            "latency_ms": round(item.latency_ms, 1),
            "error": item.error,
            "raw_preview": item.raw_preview,
            "prompt_tokens": item.prompt_tokens,
            "completion_tokens": item.completion_tokens,
            "decision": "approved" if item.error is None else "rejected",
        }
        for item in traces
    ]


@router.get("/v1/schemas")
def list_schemas() -> dict[str, Any]:
    registry = get_registry()
    return {"schemas": [item.model_dump() for item in registry.list()]}


@router.get("/v1/demo/scenarios")
def demo_scenarios() -> dict[str, Any]:
    settings = get_settings()
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Public demo is disabled")
    return {"scenarios": list_demo_scenarios()}


@router.post("/v1/demo/run")
def run_demo_scenario(
    body: DemoRunRequest,
    request: Request,
    model_client: ChatCompletionsClient = Depends(provide_model_client),
) -> dict[str, Any]:
    """Run one immutable, rate-limited scenario without accepting an arbitrary public prompt."""

    settings = get_settings()
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Public demo is disabled")
    scenario = get_demo_scenario(body.scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Unknown demo scenario")

    try:
        result = run_correction_loop(
            scenario.prompt,
            get_registry().get(scenario.schema_name),
            client=model_client,
            initial_candidate=(
                _json_dumps(scenario.candidate) if scenario.candidate is not None else None
            ),
        )
    except GuardrailViolation as exc:
        if scenario.expected_outcome != "blocked":
            raise
        evidence = evaluate_eligibility(settings)
        return {
            "status": "blocked",
            "request_id": getattr(request.state, "request_id", None),
            "scenario": scenario.public_descriptor(),
            "decision": {
                "outcome": "blocked",
                "reason": exc.message,
                "model_calls": 0,
                "human_review_required": False,
            },
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "runtime": evidence.to_dict(),
            "trace": [
                {
                    "stage": "input_firewall",
                    "decision": "blocked",
                    "error": exc.message,
                }
            ],
        }

    payload = _result_payload(result, request)
    payload["scenario"] = scenario.public_descriptor()
    payload["decision"]["outcome"] = "repaired" if result.attempts > 1 else "approved"
    return payload


@router.post("/v1/guarded/execute")
def guarded_execute(
    body: GuardedExecuteRequest,
    request: Request,
    model_client: ChatCompletionsClient = Depends(provide_model_client),
) -> dict[str, Any]:
    request_id = getattr(request.state, "request_id", None)
    registry = get_registry()
    schema = registry.get(body.schema_name)
    try:
        result = run_correction_loop(
            body.prompt,
            schema,
            max_retries=body.max_retries,
            client=model_client,
            initial_candidate=(
                _json_dumps(body.candidate)
                if isinstance(body.candidate, dict)
                else body.candidate
            ),
        )
    except GuardMetaError:
        raise
    logger.info(
        "guarded_execute_success",
        extra={"request_id": request_id, "schema": schema.name, "attempt": result.attempts},
    )
    return _result_payload(result, request)


def _result_payload(result, request: Request) -> dict[str, Any]:
    settings = get_settings()
    report = result.input_report
    evidence = evaluate_eligibility(settings)
    model_calls = sum(1 for item in result.trace if item.source == "model")
    return {
        "status": result.status,
        "request_id": getattr(request.state, "request_id", None),
        "schema": result.schema_name,
        "model": result.model,
        "attempts": result.attempts,
        "latency_ms": round(result.latency_ms, 1),
        "data": result.data,
        "usage": result.usage,
        "guardrails": {
            "input": {
                "status": "passed",
                "redactions": report.redactions,
                "original_chars": report.original_chars,
                "sanitized_chars": report.sanitized_chars,
            },
            "output": {"status": "passed"},
        },
        "decision": {
            "outcome": "approved",
            "correction_performed": result.attempts > 1,
            "model_calls": model_calls,
            "human_review_required": False,
        },
        "runtime": evidence.to_dict(),
        "trace": _trace_payload(result.trace),
    }


@router.post("/v1/chat/completions")
def intercept_chat_completions(
    body: ChatCompletionsRequest,
    request: Request,
    x_guardmeta_schema: str | None = Header(default=None, alias="X-GuardMeta-Schema"),
    model_client: ChatCompletionsClient = Depends(provide_model_client),
) -> dict[str, Any]:
    """OpenAI-compatible interception proxy.

    Without a schema header, prompts are sanitized then forwarded to Meta Model API.
    With a schema, the autonomous correction loop runs and a deterministic payload is returned.
    """
    joined = "\n".join(message.content for message in body.messages if message.role == "user")
    schema_name = body.schema_name or x_guardmeta_schema
    if schema_name:
        registry = get_registry()
        schema = registry.get(schema_name)
        result = run_correction_loop(
            joined, schema, max_retries=body.max_retries, client=model_client
        )
        return {
            "id": getattr(request.state, "request_id", "chatcmpl-guardmeta"),
            "object": "chat.completion",
            "model": result.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": _json_dumps(result.data),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": result.usage,
            "guardmeta": {
                "status": result.status,
                "schema": result.schema_name,
                "attempts": result.attempts,
                "trace": _trace_payload(result.trace),
            },
        }

    sanitized_messages = _sanitize_messages(body.messages)
    turn = model_client.complete(sanitized_messages, json_schema=None)
    OutputFirewall().enforce_text(turn.content)
    return {
        "id": getattr(request.state, "request_id", "chatcmpl-guardmeta"),
        "object": "chat.completion",
        "model": turn.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": turn.content},
                "finish_reason": turn.finish_reason or "stop",
            }
        ],
        "usage": {
            "prompt_tokens": turn.prompt_tokens,
            "completion_tokens": turn.completion_tokens,
            "total_tokens": turn.prompt_tokens + turn.completion_tokens,
        },
    }


def _json_dumps(data: dict[str, Any]) -> str:
    import json

    return json.dumps(data)


def _sanitize_messages(messages: list[ChatMessage]) -> list[dict[str, str]]:
    """Treat every inbound role as untrusted and enforce an aggregate size bound."""

    settings = get_settings()
    total_chars = sum(len(message.content) for message in messages)
    if total_chars > settings.max_prompt_chars:
        raise GuardrailViolation(
            "Conversation exceeds maximum size",
            details={"max_chars": settings.max_prompt_chars, "actual": total_chars},
        )
    return [
        {"role": message.role, "content": sanitize_input(message.content)}
        for message in messages
    ]
