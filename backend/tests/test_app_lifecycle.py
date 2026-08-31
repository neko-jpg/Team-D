"""Application-owned live clients are closed with the FastAPI lifespan."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from backend.settings import BackendSettings


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.responses = object()
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


def test_live_responses_client_is_closed_before_the_event_loop_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_module = importlib.import_module("backend.app")
    client_owner = _FakeOpenAIClient()
    monkeypatch.setattr(
        app_module,
        "_create_live_openai_client",
        lambda _settings: client_owner,
    )
    app = app_module.create_app(BackendSettings(provider_mode="live"))

    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200

    assert client_owner.close_calls == 1
