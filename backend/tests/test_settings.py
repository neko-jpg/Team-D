"""Runtime settings contracts for server-only integration boundaries."""

from __future__ import annotations

import pytest

from backend.providers.hybrid_vision_guidance import HybridVisionGuidanceAnalyzer
from backend.providers.runtime import LiveVisionGuidanceProvider, create_vision_guidance_provider
from backend.providers.vision_guidance_realtime import OpenAIRealtimeVisionGuidanceAnalyzer
from backend.settings import BackendSettings, ProviderMode, SettingsError


def test_rembg_port_defaults_to_the_documented_loopback_endpoint() -> None:
    settings = BackendSettings.from_env({})

    assert settings.rembg_port == 7000
    assert settings.rembg_remove_url == "http://127.0.0.1:7000/api/remove"


def test_rembg_port_can_move_when_the_default_port_is_occupied() -> None:
    settings = BackendSettings.from_env({"REMBG_PORT": "7001"})

    assert settings.rembg_port == 7001
    assert settings.rembg_remove_url == "http://127.0.0.1:7001/api/remove"


@pytest.mark.parametrize("value", ["", "0", "65536", "not-a-port"])
def test_invalid_rembg_port_is_rejected(value: str) -> None:
    with pytest.raises(SettingsError, match="REMBG_PORT"):
        BackendSettings.from_env({"REMBG_PORT": value})


def test_live_runtime_builds_hybrid_geometry_and_semantic_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    settings = BackendSettings(provider_mode=ProviderMode.LIVE, rembg_port=7111)

    provider = create_vision_guidance_provider(settings)

    assert isinstance(provider, LiveVisionGuidanceProvider)
    analyzer = provider._analyzer
    assert isinstance(analyzer, HybridVisionGuidanceAnalyzer)
    assert analyzer.geometry.remove_url == "http://127.0.0.1:7111/api/remove"  # type: ignore[attr-defined]
    assert isinstance(analyzer.semantic, OpenAIRealtimeVisionGuidanceAnalyzer)
    assert analyzer.semantic._semantic_only_geometry is True  # type: ignore[attr-defined]
