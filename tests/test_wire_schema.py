"""Offline tests for the shared Gemini wire-schema helper.

The helper turns a Pydantic model into the JSON schema Gemini accepts. It is
shared by the Interviewer and the Scripter, so its rules are pinned here on a
small probe model, independent of either feature.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from telegram_documentaries.wire_schema import flatten_wire_schema


class _Inner(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    label: str


class _Probe(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    inner: _Inner
    maybe: str | None = None
    count: int = 3


def _walk(node: object) -> list[tuple[str, object]]:
    """Yield every (key, value) pair in a nested JSON-schema structure."""
    pairs: list[tuple[str, object]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            pairs.append((key, value))
            pairs.extend(_walk(value))
    elif isinstance(node, list):
        for item in node:
            pairs.extend(_walk(item))
    return pairs


def _schema() -> dict[str, Any]:
    return flatten_wire_schema(_Probe)


def test_the_flattened_schema_is_an_object_with_its_top_level_properties() -> None:
    schema = _schema()

    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"inner", "maybe", "count"}


def test_every_top_level_property_is_marked_required() -> None:
    assert set(_schema()["required"]) == {"inner", "maybe", "count"}


def test_references_are_inlined_and_no_definitions_remain() -> None:
    keys = {key for key, _ in _walk(_schema())}

    assert "$ref" not in keys
    assert "$defs" not in keys
    label = _schema()["properties"]["inner"]["properties"]["label"]
    assert label["type"] == "string"


def test_additional_properties_are_dropped_for_the_wire() -> None:
    keys = {key for key, _ in _walk(_schema())}

    assert "additionalProperties" not in keys


def test_an_optional_field_becomes_nullable_without_a_null_type() -> None:
    maybe = _schema()["properties"]["maybe"]

    assert maybe["nullable"] is True
    assert maybe["type"] == "string"
    assert "anyOf" not in maybe


def test_no_null_type_or_default_reaches_the_wire() -> None:
    pairs = _walk(_schema())
    types = [value for key, value in pairs if key == "type"]
    keys = {key for key, _ in pairs}

    assert "null" not in types
    assert "default" not in keys


def test_a_model_without_optional_fields_still_flattens() -> None:
    class _Flat(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)

        script: str

    schema = flatten_wire_schema(_Flat)

    assert schema["type"] == "object"
    assert schema["properties"]["script"]["type"] == "string"
    assert schema["required"] == ["script"]
