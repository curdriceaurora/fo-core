"""Tests for BaseModel.generate_structured agnostic default (#433)."""

from __future__ import annotations

from typing import Any

import pytest

from models.base import BaseModel, ModelConfig, ModelType
from models.vision_schema import StructuredParseError


class _FakeModel(BaseModel):
    """Minimal BaseModel whose generate() returns a canned string."""

    def __init__(self, response: str) -> None:
        super().__init__(ModelConfig(name="fake", model_type=ModelType.VISION))
        self._response = response
        self.last_prompt = ""

    def initialize(self) -> None:  # pragma: no cover - not exercised
        self._initialized = True

    def generate(self, prompt: str, **kwargs: Any) -> str:
        self.last_prompt = prompt
        if self._response == "__raise__":
            raise RuntimeError("backend down")
        return self._response

    def cleanup(self) -> None:  # pragma: no cover - not exercised
        pass


@pytest.mark.ci
def test_generate_structured_parses_fields() -> None:
    model = _FakeModel('{"description": "a dog", "folder_name": "animals"}')
    out = model.generate_structured(["description", "folder_name"])
    assert out == {"description": "a dog", "folder_name": "animals"}


@pytest.mark.ci
def test_generate_structured_strict_flag_changes_prompt() -> None:
    model = _FakeModel('{"description": "x"}')
    model.generate_structured(["description"], strict_json_only=True)
    assert model.last_prompt.lower().startswith("return only one valid json object")


@pytest.mark.ci
def test_generate_structured_bad_json_raises_structured_parse_error() -> None:
    model = _FakeModel("not json at all")
    with pytest.raises(StructuredParseError):
        model.generate_structured(["description"])


@pytest.mark.ci
def test_generate_structured_backend_error_propagates_unwrapped() -> None:
    model = _FakeModel("__raise__")
    with pytest.raises(RuntimeError):  # NOT StructuredParseError
        model.generate_structured(["description"])
