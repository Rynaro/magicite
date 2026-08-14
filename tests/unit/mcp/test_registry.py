"""AC-004: every registered tool exposes a non-null risk_class/side_effect/idempotent triple."""

from __future__ import annotations


def test_metadata_complete() -> None:
    from magicite.mcp import app as _app  # noqa: F401  (populates TOOL_REGISTRY)
    from magicite.mcp.registry import manifest

    rows = manifest()
    assert len(rows) == 16
    for row in rows:
        assert row["risk_class"], row
        assert row["risk_class"] in {"R0", "R1", "R2", "R3"}, row
        assert row["side_effect"], row
        assert row["idempotent"] is not None, row
        assert isinstance(row["idempotent"], bool), row
        # signal_tier is legitimately None for non-learning tools (spec §3.2); every
        # other field on the row is required to be present and non-null.
        assert "signal_tier" in row


def test_registration_is_idempotent_per_name() -> None:
    """Re-registering the same tool name is a programming error, not a silent overwrite."""
    import pytest

    from magicite.mcp import bind_retrieval  # noqa: F401  (ensures `route` is already registered)
    from magicite.mcp.registry import magicite_tool
    from magicite.mcp.schemas import RouteInput, RouteOutput

    with pytest.raises(ValueError):

        @magicite_tool(
            risk="R0",
            side_effect="none",
            idempotent=True,
            input_model=RouteInput,
            output_model=RouteOutput,
        )
        def route(ctx, params):  # shadows the real `route` name on purpose
            raise NotImplementedError
