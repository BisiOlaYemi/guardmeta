from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def to_meta_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Convert a Pydantic model into a Meta structured-output JSON Schema.

    Meta constrained decoding rejects recursive schemas and, in strict mode,
    requires additionalProperties=false plus a complete required list.
    """

    schema = model.model_json_schema(mode="serialization")
    _normalize(schema)
    for definition in (schema.get("$defs") or {}).values():
        _normalize(definition)
    schema.pop("title", None)
    return schema


def _normalize(node: Any) -> None:
    if not isinstance(node, dict):
        return
    if "properties" in node:
        properties = node.get("properties") or {}
        node["type"] = "object"
        node["additionalProperties"] = False
        node["required"] = list(properties.keys())
        for child in properties.values():
            _normalize(child)
    elif node.get("type") == "object" and isinstance(node.get("additionalProperties"), dict):
        # Preserve typed mapping values (for example dict[str, str]). Some strict
        # providers reject open maps and will use the client's JSON-mode fallback;
        # rewriting the map to false would silently change the payload contract.
        _normalize(node["additionalProperties"])
    if "items" in node:
        _normalize(node["items"])
    for combinator in ("anyOf", "oneOf", "allOf"):
        for child in node.get(combinator, []):
            _normalize(child)
    for definition in (node.get("$defs") or {}).values():
        _normalize(definition)
