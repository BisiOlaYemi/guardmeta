from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from app.clients.meta import ChatCompletionsClient, MetaModelClient, ModelTurn
from app.config import get_settings
from app.exceptions import SchemaExhaustedError, UpstreamError
from app.guardrails.jsonschema import to_meta_json_schema
from app.guardrails.registry import RegisteredSchema
from app.guardrails.validator import FirewallReport, InputFirewall, OutputFirewall

logger = logging.getLogger("guardmeta.correction")

_FENCE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


@dataclass
class AttemptTrace:
    attempt: int
    source: str
    latency_ms: float
    error: str | None
    raw_preview: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class CorrectionResult:
    status: str
    attempts: int
    data: dict[str, Any]
    schema_name: str
    model: str
    latency_ms: float
    usage: dict[str, int]
    input_report: FirewallReport
    trace: list[AttemptTrace] = field(default_factory=list)


DEVELOPER_INSTRUCTIONS = """You are GuardMeta's payload compiler for high-risk enterprise workflows.
Return a single JSON object that satisfies the provided schema exactly.
Do not wrap JSON in markdown. Do not add commentary, keys, or nulls that the schema forbids.
Honor every constraint: enums, patterns, numeric bounds, distinct identifiers, and confirmation rules.
Never invent authorization, confirmation, account, clinical, or execution data. If a required value is absent,
return your best schema-shaped attempt and allow deterministic validation to reject it.
Never emit secrets, PAN/SSN, or raw patient names.
"""


def execute_with_self_correction(
    prompt: str,
    schema_model: type[BaseModel],
    max_retries: int | None = None,
    *,
    schema_name: str = "payload",
    client: ChatCompletionsClient | None = None,
    output_firewall: OutputFirewall | None = None,
) -> dict[str, Any]:
    """Sprint-compatible entry: validate model JSON against a Pydantic model with retries."""
    registered = RegisteredSchema(
        name=schema_name,
        title=schema_name,
        description="",
        risk="high",
        model=schema_model,
    )
    result = run_correction_loop(
        prompt,
        registered,
        max_retries=max_retries,
        client=client,
        output_firewall=output_firewall,
    )
    return {
        "status": result.status,
        "attempts": result.attempts,
        "data": result.data,
    }


def run_correction_loop(
    prompt: str,
    schema: RegisteredSchema,
    *,
    max_retries: int | None = None,
    client: ChatCompletionsClient | None = None,
    output_firewall: OutputFirewall | None = None,
    initial_candidate: str | None = None,
) -> CorrectionResult:
    settings = get_settings()
    attempts_allowed = max_retries or settings.max_correction_attempts
    model_client = client or MetaModelClient(settings)
    firewall = output_firewall or OutputFirewall()
    json_schema = schema.json_schema()
    sanitized, input_report = InputFirewall().process(prompt)

    messages: list[dict[str, str]] = [
        {"role": "developer", "content": DEVELOPER_INSTRUCTIONS},
        {
            "role": "developer",
            "content": "JSON Schema:\n" + json.dumps(json_schema, indent=2),
        },
        {"role": "user", "content": sanitized},
    ]

    traces: list[AttemptTrace] = []
    last_error: str | None = None
    prompt_tokens = 0
    completion_tokens = 0
    started = time.perf_counter()
    model_name = settings.model_name

    for attempt in range(1, attempts_allowed + 1):
        turn_started = time.perf_counter()
        source = "candidate" if attempt == 1 and initial_candidate is not None else "model"
        if source == "candidate":
            turn = ModelTurn(content=initial_candidate or "", model="client-supplied")
        else:
            turn = _call_model(model_client, messages, json_schema, schema.name)
        model_name = turn.model
        prompt_tokens += turn.prompt_tokens
        completion_tokens += turn.completion_tokens
        preview = _preview(turn.content)
        try:
            firewall.enforce_text(turn.content)
            parsed = _parse_json(turn.content)
            validated = schema.model.model_validate(parsed)
            firewall.enforce_payload(validated)
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            last_error = _compact_error(exc)
            traces.append(
                AttemptTrace(
                    attempt=attempt,
                    source=source,
                    latency_ms=(time.perf_counter() - turn_started) * 1000,
                    error=last_error,
                    raw_preview=preview,
                    prompt_tokens=turn.prompt_tokens,
                    completion_tokens=turn.completion_tokens,
                )
            )
            logger.info(
                "schema_validation_failed",
                extra={"attempt": attempt, "schema": schema.name, "event": "retry"},
            )
            messages.append({"role": "assistant", "content": turn.content or ""})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Previous attempt failed deterministic validation:\n"
                        f"{last_error}\n"
                        "Emit a repaired JSON object that satisfies the schema and the original task. JSON only."
                    ),
                }
            )
            continue
        except Exception:
            raise

        traces.append(
            AttemptTrace(
                attempt=attempt,
                source=source,
                latency_ms=(time.perf_counter() - turn_started) * 1000,
                error=None,
                raw_preview=preview,
                prompt_tokens=turn.prompt_tokens,
                completion_tokens=turn.completion_tokens,
            )
        )
        return CorrectionResult(
            status="success",
            attempts=attempt,
            data=_dump(validated),
            schema_name=schema.name,
            model=model_name,
            latency_ms=(time.perf_counter() - started) * 1000,
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            input_report=input_report,
            trace=traces,
        )

    raise SchemaExhaustedError(
        f"Max retries ({attempts_allowed}) reached. Failed validation.",
        details={
            "last_error": last_error,
            "attempts": attempts_allowed,
            "schema": schema.name,
            "trace": [trace.__dict__ for trace in traces],
        },
    )


def _call_model(
    client: ChatCompletionsClient,
    messages: list[dict[str, str]],
    json_schema: dict[str, Any],
    schema_name: str,
) -> ModelTurn:
    try:
        return client.complete(messages, json_schema=json_schema, schema_name=schema_name)
    except UpstreamError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise UpstreamError(f"Model communication error: {exc}") from exc


def _parse_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise json.JSONDecodeError("Empty model response", raw or "", 0)
    candidates = [text]
    fenced = _FENCE.search(text)
    if fenced:
        candidates.insert(0, fenced.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start : end + 1])
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(parsed, dict):
            return parsed
        last_error = ValueError("JSON root must be an object")
    if isinstance(last_error, json.JSONDecodeError):
        raise last_error
    raise ValueError(str(last_error) if last_error else "Unable to parse JSON object")


def _compact_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors():
            loc = ".".join(str(item) for item in err.get("loc", ())) or "root"
            parts.append(f"{loc}: {err.get('msg')}")
        return "; ".join(parts)
    return str(exc)


def _preview(raw: str, limit: int = 280) -> str:
    compact = " ".join((raw or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _dump(model: BaseModel) -> dict[str, Any]:
    return json.loads(model.model_dump_json())
