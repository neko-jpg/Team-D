"""Regression tests for the shared Python backend entrypoints."""

from __future__ import annotations

import pytest

from backend.config import BackendSettings, ConfigurationError


def test_fixture_mode_is_the_safe_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROVIDER_MODE", raising=False)
    monkeypatch.delenv("API_PORT", raising=False)

    settings = BackendSettings.from_env()

    assert settings.provider_mode == "fixture"
    assert settings.api_port == 3001


def test_live_mode_is_explicit_and_invalid_mode_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_MODE", "live")
    assert BackendSettings.from_env().provider_mode == "live"

    monkeypatch.setenv("PROVIDER_MODE", "automatic-fixture-fallback")
    with pytest.raises(ConfigurationError, match="PROVIDER_MODE"):
        BackendSettings.from_env()


def test_live_mode_never_normalizes_to_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared settings preserve an explicit live selection for both roots."""

    monkeypatch.setenv("PROVIDER_MODE", "live")
    server_settings = BackendSettings.from_env()
    agent_settings = BackendSettings.from_env()

    assert server_settings.provider_mode == agent_settings.provider_mode == "live"


def test_agent_import_uses_the_same_provider_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROVIDER_MODE", "fixture")
    import backend.agent
    from backend.providers.vision_guidance import VisionGuidanceProvider

    assert BackendSettings.from_env().provider_mode == "fixture"
    assert VisionGuidanceProvider.__module__ == "backend.providers.vision_guidance"
    assert backend.agent.__name__ == "backend.agent"
