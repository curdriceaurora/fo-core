"""Unit tests for the structured vision schema/prompt/parser (#433)."""

from __future__ import annotations

import pytest

from models.vision_schema import (
    StructuredParseError,
    build_vision_json_prompt,
    build_vision_json_schema,
    parse_structured_json,
)


@pytest.mark.ci
def test_schema_requests_only_given_fields() -> None:
    schema = build_vision_json_schema(["description", "folder_name"])
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"description", "folder_name"}
    assert schema["required"] == ["description", "folder_name"]
    assert schema["additionalProperties"] is False


@pytest.mark.ci
def test_prompt_lists_requested_fields_and_strict_prefix() -> None:
    plain = build_vision_json_prompt(["description", "filename"])
    assert '"description"' in plain and '"filename"' in plain
    assert '"folder_name"' not in plain
    strict = build_vision_json_prompt(["description"], strict=True)
    assert strict.lower().startswith("return only one valid json object")


@pytest.mark.ci
def test_prompt_includes_text_priority_only_when_naming_requested() -> None:
    with_naming = build_vision_json_prompt(["folder_name"])
    assert "prioritize that text" in with_naming
    without_naming = build_vision_json_prompt(["description"])
    assert "prioritize that text" not in without_naming


@pytest.mark.ci
def test_parse_plain_object() -> None:
    raw = '{"description": "a cat", "folder_name": "animals"}'
    assert parse_structured_json(raw, ["description", "folder_name"]) == {
        "description": "a cat",
        "folder_name": "animals",
    }


@pytest.mark.ci
def test_parse_strips_code_fences_and_prose() -> None:
    raw = 'Sure!\n```json\n{"description": "x"}\n```\n'
    assert parse_structured_json(raw, ["description"]) == {"description": "x"}


@pytest.mark.ci
def test_parse_tolerates_extra_keys() -> None:
    raw = '{"description": "x", "folder_name": "y", "confidence": 0.9}'
    assert parse_structured_json(raw, ["description"]) == {"description": "x"}


@pytest.mark.ci
def test_parse_missing_requested_key_raises() -> None:
    raw = '{"description": "x"}'
    with pytest.raises(StructuredParseError):
        parse_structured_json(raw, ["description", "folder_name"])


@pytest.mark.ci
def test_parse_handles_braces_inside_string_values() -> None:
    raw = '{"extracted_text": "func() { return {1}; }", "folder_name": "code"}'
    out = parse_structured_json(raw, ["extracted_text", "folder_name"])
    assert out["extracted_text"] == "func() { return {1}; }"
    assert out["folder_name"] == "code"


@pytest.mark.ci
def test_parse_no_json_raises() -> None:
    with pytest.raises(StructuredParseError):
        parse_structured_json("the model refused to answer", ["description"])


@pytest.mark.ci
def test_parse_coerces_json_null_to_empty_string() -> None:
    raw = '{"description": "a cat", "folder_name": null}'
    out = parse_structured_json(raw, ["description", "folder_name"])
    assert out == {"description": "a cat", "folder_name": ""}
