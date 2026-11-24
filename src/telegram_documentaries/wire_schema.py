"""Shared JSON-schema flattening for Gemini response schemas.

Pydantic emits ``$defs`` references, ``additionalProperties``, and
``{"type": "null"}`` branches. The Gemini response schema accepts none of them.
This module rewrites a Pydantic model into the Gemini-compatible form. It is
used by every model seam that asks for structured JSON (the Interviewer and the
Scripter), so the rules live in one place. The strict local check always runs
again on the reply, so the wire schema only has to be accepted by the provider,
not trusted.

Nothing here imports the ADK, the genai SDK, or touches the network.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def _flatten_schema(node: Any, defs: dict[str, Any]) -> Any:
    """Inline ``$ref`` and convert nullable unions into Gemini-compatible form."""
    if isinstance(node, list):
        return [_flatten_schema(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        return _flatten_schema(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
    branches = node.get("anyOf")
    if isinstance(branches, list) and {"type": "null"} in branches:
        non_null = [branch for branch in branches if branch != {"type": "null"}]
        if len(non_null) == 1:
            merged = {key: value for key, value in node.items() if key != "anyOf"}
            merged.update(_flatten_schema(non_null[0], defs))
            merged["nullable"] = True
            return _drop_wire_noise(merged)
    return _flatten_schema_object(node, defs)


def _drop_wire_noise(node: dict[str, Any]) -> dict[str, Any]:
    """Remove keys Gemini does not accept, such as defaults and the null type."""
    return {key: value for key, value in node.items() if key != "default"}


def _flatten_schema_object(
    node: dict[str, Any], defs: dict[str, Any]
) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in _drop_wire_noise(node).items():
        if key == "additionalProperties":
            continue
        flattened[key] = _flatten_schema(value, defs)
    return flattened


def flatten_wire_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return ``model``'s JSON schema in the form Gemini accepts as a response schema.

    Every top-level property is marked required (optional values are nullable), so
    the model always emits the full shape.
    """
    raw = model.model_json_schema()
    defs: dict[str, Any] = raw.pop("$defs", {})
    schema: dict[str, Any] = _flatten_schema(raw, defs)
    schema["required"] = list(schema["properties"])
    return schema
