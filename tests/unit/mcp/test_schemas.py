"""AC-005: every tool input/output model rejects an unknown field."""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from magicite.mcp import schemas


def _all_models() -> list[type]:
    return [
        obj
        for _name, obj in vars(schemas).items()
        if inspect.isclass(obj) and issubclass(obj, schemas.MagiciteModel)
    ]


def test_unknown_field_rejected() -> None:
    """spec §3.1: extra="forbid" on every input AND output model, no exceptions."""
    models = _all_models()
    assert len(models) >= 16 * 2, "expected at least an input+output model per tool"

    for model in models:
        assert model.model_config.get("extra") == "forbid", model.__name__


def test_route_input_rejects_unknown_top_level_field() -> None:
    with pytest.raises(ValidationError):
        schemas.RouteInput.model_validate({"query": "x", "bogus_field": 1})


def test_route_input_rejects_unknown_nested_field() -> None:
    with pytest.raises(ValidationError):
        schemas.RouteInput.model_validate(
            {"query": "x", "context": {"project_tag": "steam-gaming", "nope": True}}
        )


def test_route_input_accepts_well_formed_payload() -> None:
    parsed = schemas.RouteInput.model_validate(
        {"query": "x", "context": {"project_tag": "steam-gaming"}, "k": 3}
    )
    assert parsed.k == 3


def test_register_input_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        schemas.RegisterInput.model_validate({"path": ".", "extra_thing": True})
