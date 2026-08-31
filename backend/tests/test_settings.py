"""Runtime settings contracts for server-only integration boundaries."""

from __future__ import annotations

import pytest

from backend.settings import BackendSettings, SettingsError


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
