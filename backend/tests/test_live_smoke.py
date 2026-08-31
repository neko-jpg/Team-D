"""Offline orchestration contract for the backend-only live smoke command."""

from __future__ import annotations

import asyncio

import pytest

from backend.live_smoke import (
    LiveSmokeError,
    _ApiSmokeResult,
    _GuidanceSmokeResult,
    run_live_smoke,
)
from backend.settings import BackendSettings


def test_live_smoke_runs_each_boundary_once_and_in_order() -> None:
    order: list[str] = []
    settings = BackendSettings(provider_mode="live")

    async def guidance(received: BackendSettings) -> _GuidanceSmokeResult:
        assert received is settings
        order.append("guidance")
        return _GuidanceSmokeResult(True, ("SHOW_FULL_GARMENT", "READY"))

    async def api(received: BackendSettings) -> _ApiSmokeResult:
        assert received is settings
        order.append("api")
        return _ApiSmokeResult("front", 4, "mask-digest")

    async def background(received: BackendSettings) -> str:
        assert received is settings
        order.append("background")
        return "background-digest"

    result = asyncio.run(
        run_live_smoke(
            settings,
            guidance_check=guidance,
            api_check=api,
            background_check=background,
        )
    )

    assert order == ["guidance", "api", "background"]
    assert result.to_payload() == {
        "cameraSubscribed": True,
        "guidanceCodes": ["SHOW_FULL_GARMENT", "READY"],
        "shotType": "front",
        "measurementEndpointCount": 4,
        "maskSha256": "mask-digest",
        "backgroundSha256": "background-digest",
    }


def test_live_smoke_refuses_fixture_mode_before_any_boundary_runs() -> None:
    calls = 0

    async def should_not_run(_settings: BackendSettings) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("unreachable")

    with pytest.raises(LiveSmokeError, match="PROVIDER_MODE=live"):
        asyncio.run(
            run_live_smoke(
                BackendSettings(provider_mode="fixture"),
                guidance_check=should_not_run,  # type: ignore[arg-type]
                api_check=should_not_run,  # type: ignore[arg-type]
                background_check=should_not_run,  # type: ignore[arg-type]
            )
        )
    assert calls == 0
