"""Offline tests for the shared FastAPI/Agent backend scaffold (task 3.3)."""

from __future__ import annotations

import asyncio
import io
import json
import logging
from pathlib import Path
import sys
from dataclasses import dataclass
from typing import Any

import pytest

from backend import agent
from backend.app import create_app
from backend.cli import build_parser, main as cli_main
from backend.livekit_token import get_livekit_config
from backend.providers.runtime import (
    FixtureVisionGuidanceProvider,
    LiveVisionGuidanceProvider,
    ProviderUnavailableError,
    create_provider_inference,
    create_vision_guidance_provider,
    guidance_input_from_frame,
)
from backend.providers.vision_guidance import (
    GuidanceCode,
    GuidanceInput,
    VisionDecision,
    VisionGuidanceProvider,
)
from backend.settings import BackendSettings, ProviderMode, SettingsError


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def configured_settings(mode: ProviderMode = ProviderMode.FIXTURE) -> BackendSettings:
    return BackendSettings(
        provider_mode=mode,
        livekit_url="wss://room.example.invalid",
        livekit_api_key="test-api-key",
        livekit_api_secret="test-api-secret",
    )


async def asgi_get(app: Any, path: str) -> tuple[int, dict[str, str], bytes]:
    messages: list[dict[str, Any]] = []
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 0),
            "server": ("testserver", 80),
        },
        receive,
        send,
    )
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in start.get("headers", [])
    }
    return int(start["status"]), headers, body


def test_settings_accept_only_fixture_or_live_and_do_not_fallback() -> None:
    fixture = BackendSettings.from_env({"PROVIDER_MODE": "fixture"})
    live = BackendSettings.from_env({"PROVIDER_MODE": "live"})

    assert fixture.provider_mode is ProviderMode.FIXTURE
    assert live.provider_mode is ProviderMode.LIVE
    for invalid in ("LIVE", " live", "auto", ""):
        with pytest.raises(SettingsError, match="fixture or live"):
            BackendSettings.from_env({"PROVIDER_MODE": invalid})


def test_shared_settings_hide_secrets_and_validate_agent_url() -> None:
    settings = BackendSettings.from_env(
        {
            "PROVIDER_MODE": "live",
            "LIVEKIT_URL": "http://unsafe.example.invalid",
            "LIVEKIT_API_KEY": "visible-key-if-buggy",
            "LIVEKIT_API_SECRET": "visible-secret-if-buggy",
        }
    )
    rendered = repr(settings)
    assert "visible-key-if-buggy" not in rendered
    assert "visible-secret-if-buggy" not in rendered
    assert "http://unsafe.example.invalid" not in rendered
    with pytest.raises(SettingsError, match="https or wss"):
        settings.require_livekit()


def test_fastapi_uses_shared_settings_and_preserves_health_contract() -> None:
    settings = BackendSettings(
        provider_mode=ProviderMode.LIVE,
        livekit_url="wss://configured.example.invalid",
        livekit_api_key="shared-key",
        livekit_api_secret="shared-secret",
        livekit_token_ttl_seconds=75,
        livekit_token_max_ttl_seconds=120,
    )
    app = create_app(settings)

    assert app.state.settings is settings
    token_config = app.dependency_overrides[get_livekit_config]()
    assert token_config.api_key == "shared-key"
    assert token_config.api_secret == "shared-secret"
    assert token_config.url == "wss://configured.example.invalid"
    assert token_config.token_ttl_seconds == 75
    assert token_config.max_token_ttl_seconds == 120

    status, headers, body = asyncio.run(asgi_get(app, "/api/health"))
    assert status == 200
    assert headers["cache-control"] == "no-store"
    assert headers["content-type"].startswith("application/json")
    assert body == b'{"status":"ok"}'
    assert json.loads(body) == {"status": "ok"}


@dataclass
class FakeLiveKitFrame:
    data: memoryview
    width: int
    height: int


def test_frame_adapter_and_fixture_provider_conform_to_contract() -> None:
    frame = FakeLiveKitFrame(memoryview(b"raw-frame"), 320, 240)
    input_value = guidance_input_from_frame(frame, requested_shot="back")
    provider = create_vision_guidance_provider(configured_settings())

    assert isinstance(provider, FixtureVisionGuidanceProvider)
    assert isinstance(provider, VisionGuidanceProvider)
    assert isinstance(input_value, GuidanceInput)
    assert input_value.requestedShot == "back"
    assert input_value.frame.data == b"raw-frame"
    assert input_value.frame.width == 320
    assert input_value.frame.height == 240

    decision = asyncio.run(create_provider_inference(provider)(b"fixture-frame"))
    assert decision == VisionDecision(GuidanceCode.READY, 1.0)


def test_live_provider_failure_is_explicit_and_never_uses_fixture() -> None:
    settings = configured_settings(ProviderMode.LIVE)
    provider = create_vision_guidance_provider(settings)

    assert isinstance(provider, LiveVisionGuidanceProvider)
    assert isinstance(provider, VisionGuidanceProvider)
    with pytest.raises(ProviderUnavailableError, match="not configured"):
        asyncio.run(create_provider_inference(provider)(b"live-frame"))


def test_live_provider_validates_analyzer_result() -> None:
    seen: list[GuidanceInput] = []

    async def analyzer(input_value: GuidanceInput) -> dict[str, object]:
        seen.append(input_value)
        return {"code": "CENTER_GARMENT", "confidence": 0.8}

    provider = create_vision_guidance_provider(
        configured_settings(ProviderMode.LIVE),
        live_analyzer=analyzer,
    )
    decision = asyncio.run(create_provider_inference(provider)(b"live-frame"))

    assert decision == VisionDecision(GuidanceCode.CENTER_GARMENT, 0.8)
    assert len(seen) == 1


def test_agent_wires_provider_inference_and_logs_no_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = configured_settings(ProviderMode.FIXTURE)
    server = object()
    captured: dict[str, Any] = {}

    def server_factory(**kwargs: Any) -> object:
        captured.update(kwargs)
        return server

    def runner(received_server: object) -> None:
        captured["server"] = received_server

    with caplog.at_level(logging.INFO, logger="backend.agent"):
        agent.run_agent_worker(
            settings,
            runner=runner,
            server_factory=server_factory,
        )

    assert captured["server"] is server
    inference = captured["inference"]
    assert asyncio.run(inference(b"frame")).code is GuidanceCode.READY
    startup_log = "\n".join(caplog.messages)
    assert "agent_worker_starting" in startup_log
    assert "provider_mode=fixture" in startup_log
    assert "FixtureVisionGuidanceProvider" in startup_log
    assert settings.livekit_api_key not in startup_log
    assert settings.livekit_api_secret not in startup_log
    assert settings.livekit_url not in startup_log


def test_agent_server_receives_live_inference_without_fixture_fallback() -> None:
    captured: dict[str, Any] = {}

    def server_factory(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    provider = agent.check_agent(
        configured_settings(ProviderMode.LIVE),
        server_factory=server_factory,
    )

    assert isinstance(provider, LiveVisionGuidanceProvider)
    with pytest.raises(ProviderUnavailableError, match="not configured"):
        asyncio.run(captured["inference"](b"frame"))


def test_root_cli_checks_api_and_agent_without_network() -> None:
    api_output = io.StringIO()
    agent_output = io.StringIO()
    seen: dict[str, Any] = {}

    def app_factory(settings: BackendSettings) -> object:
        seen["api_settings"] = settings
        return object()

    def server_factory(**kwargs: Any) -> object:
        seen["agent_inference"] = kwargs["inference"]
        return object()

    assert (
        cli_main(
            ["api", "--provider-mode", "fixture", "--check"],
            env={},
            app_factory=app_factory,
            stdout=api_output,
        )
        == 0
    )
    assert (
        cli_main(
            ["agent", "--provider-mode", "live", "--check"],
            env={},
            agent_server_factory=server_factory,
            stdout=agent_output,
        )
        == 0
    )

    assert seen["api_settings"].provider_mode is ProviderMode.FIXTURE
    assert api_output.getvalue().strip() == "api check ok provider_mode=fixture"
    assert "agent check ok provider_mode=live" in agent_output.getvalue()
    with pytest.raises(ProviderUnavailableError):
        asyncio.run(seen["agent_inference"](b"frame"))


def test_root_cli_passes_dev_command_to_injected_agent_runner() -> None:
    seen: dict[str, Any] = {}
    original_argv = tuple(sys.argv)

    def server_factory(**kwargs: Any) -> object:
        seen["inference"] = kwargs["inference"]
        return object()

    def runner(_server: object) -> None:
        seen["argv"] = tuple(sys.argv)

    result = cli_main(
        ["agent", "--provider-mode", "fixture"],
        env={
            "LIVEKIT_URL": "wss://room.example.invalid",
            "LIVEKIT_API_KEY": "test-key",
            "LIVEKIT_API_SECRET": "test-secret",
        },
        agent_runner=runner,
        agent_server_factory=server_factory,
    )

    assert result == 0
    assert seen["argv"][1:] == ("dev",)
    assert tuple(sys.argv) == original_argv
    assert asyncio.run(seen["inference"](b"frame")).code is GuidanceCode.READY


def test_root_package_scripts_follow_the_backend_cli_contract() -> None:
    package = json.loads((REPOSITORY_ROOT / "package.json").read_text())
    scripts = package["scripts"]

    assert scripts["dev:api:fixture"].endswith(
        "python -m backend api --provider-mode fixture"
    )
    assert scripts["dev:api:live"].endswith(
        "python -m backend api --provider-mode live"
    )
    assert scripts["dev:agent:fixture"].endswith(
        "python -m backend agent --provider-mode fixture --worker-command dev"
    )
    assert scripts["dev:agent:live"].endswith(
        "python -m backend agent --provider-mode live --worker-command dev"
    )
    assert scripts["start:agent"].endswith(
        "python -m backend agent --provider-mode live --worker-command start"
    )
    assert scripts["check:backend:live"].endswith(
        "python -m backend api --provider-mode live --check"
    )
    assert scripts["check:agent:live"].endswith(
        "python -m backend agent --provider-mode live --check"
    )

    parser = build_parser()
    assert parser.parse_args(
        ["agent", "--provider-mode", "fixture", "--worker-command", "dev"]
    ).worker_command == "dev"
    assert parser.parse_args(
        ["agent", "--provider-mode", "live", "--worker-command", "start"]
    ).worker_command == "start"
