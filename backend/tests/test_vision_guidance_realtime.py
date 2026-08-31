"""Contract tests for the persistent OpenAI Realtime guidance adapter."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from functools import wraps
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from backend.providers.vision_guidance import (
    EncodedImage,
    GuidanceCode,
    GuidanceContractError,
    GuidanceInput,
    GuidanceShot,
    VisionDecision,
)
from backend.providers.vision_guidance_realtime import (
    OpenAIRealtimeVisionGuidanceAnalyzer,
    RealtimeGuidanceTimeoutError,
)


def png(width: int = 800, height: int = 600) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "white").save(output, "PNG")
    return output.getvalue()


def async_test(function: Any) -> Any:
    @wraps(function)
    def run() -> None:
        asyncio.run(function())

    return run


@dataclass
class FakeConnection:
    codes: list[str]
    stall: bool = False
    events: asyncio.Queue[object] = field(default_factory=asyncio.Queue)
    requests: list[dict[str, object]] = field(default_factory=list)
    closed: bool = False

    def __post_init__(self) -> None:
        self.response = FakeResponse(self)
        self.events.put_nowait(SimpleNamespace(type="session.created"))

    async def recv(self) -> object:
        return await self.events.get()

    async def close(self) -> None:
        self.closed = True


class FakeResponse:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.last_metadata: object | None = None
        self.cancel_calls = 0

    async def create(self, *, response: dict[str, object]) -> None:
        self.connection.requests.append(response)
        self.last_metadata = response["metadata"]
        if self.connection.stall:
            return
        code = self.connection.codes.pop(0) if self.connection.codes else "READY"
        metadata = response["metadata"]
        await self.connection.events.put(
            SimpleNamespace(type="response.output_text.done", text=code)
        )
        await self.connection.events.put(
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(
                    metadata=metadata,
                    status="completed",
                    output=[],
                ),
            )
        )

    async def cancel(self) -> None:
        self.cancel_calls += 1
        await self.connection.events.put(
            SimpleNamespace(
                type="response.done",
                response=SimpleNamespace(
                    metadata=self.last_metadata,
                    status="cancelled",
                    output=[],
                ),
            )
        )


class FakeManager:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def enter(self) -> FakeConnection:
        return self.connection


class FakeRealtime:
    def __init__(self, client: "FakeClient") -> None:
        self.client = client

    def connect(self, **kwargs: object) -> FakeManager:
        self.client.connect_calls.append(kwargs)
        connection = FakeConnection(list(self.client.codes), stall=self.client.stall)
        self.client.connections.append(connection)
        return FakeManager(connection)


class FakeClient:
    default_codes = ["READY", "CENTER_GARMENT", "CENTER_GARMENT"]

    def __init__(self, codes: list[str] | None = None, *, stall: bool = False) -> None:
        self.codes = list(self.default_codes if codes is None else codes)
        self.stall = stall
        self.connect_calls: list[dict[str, object]] = []
        self.connections: list[FakeConnection] = []
        self.close_calls = 0
        self.realtime = FakeRealtime(self)

    async def close(self) -> None:
        self.close_calls += 1


def guidance(shot: GuidanceShot = GuidanceShot.FRONT) -> GuidanceInput:
    return GuidanceInput(
        frame=EncodedImage(png(), "image/png", 800, 600),
        requested_shot=shot,
    )


@async_test
async def test_reuses_one_socket_and_keeps_each_frame_out_of_conversation() -> None:
    client = FakeClient()
    analyzer = OpenAIRealtimeVisionGuidanceAnalyzer(client, "test-realtime")

    await analyzer.prewarm()
    first = await analyzer(guidance())
    second = await analyzer(guidance())

    assert first == VisionDecision(GuidanceCode.CENTER_GARMENT, 1.0)
    assert second == VisionDecision(GuidanceCode.CENTER_GARMENT, 1.0)
    assert analyzer.connect_count == 1
    assert analyzer.request_count == 3  # one image-path prewarm plus two camera frames
    assert len(client.connect_calls) == 1
    assert client.connect_calls[0] == {
        "model": "test-realtime",
        "max_retries": 0,
        "max_queue_size": 262_144,
    }

    requests = client.connections[0].requests
    assert all(request["conversation"] == "none" for request in requests)
    assert all(request["output_modalities"] == ["text"] for request in requests)
    assert all(request["max_output_tokens"] == 32 for request in requests)
    assert all(request["tool_choice"] == {"type": "function", "name": "guidance"} for request in requests)
    assert all(set(request["metadata"]) == {"request_id"} for request in requests)

    camera_image = requests[-1]["input"][0]["content"][0]  # type: ignore[index]
    header, encoded = camera_image["image_url"].split(",", 1)  # type: ignore[index,union-attr]
    assert header == "data:image/jpeg;base64"
    with Image.open(BytesIO(base64.b64decode(encoded))) as prepared:
        assert max(prepared.size) == 256
        guide_pixel = prepared.convert("RGB").getpixel(
            (round(prepared.width * 0.15), round(prepared.height * 0.15))
        )
        assert guide_pixel[1] > 180
        assert guide_pixel[2] > 180
        assert guide_pixel[1] - guide_pixel[0] > 50
        assert guide_pixel[2] - guide_pixel[0] > 50

    await analyzer.aclose()
    await analyzer.aclose()
    assert client.connections[0].closed
    assert client.close_calls == 1


@async_test
async def test_invalid_or_wrong_shot_code_is_rejected_after_response_is_drained() -> None:
    client = FakeClient(["NOT_A_CODE"])
    analyzer = OpenAIRealtimeVisionGuidanceAnalyzer(
        client,
        "test-realtime",
        prewarm=False,
    )

    with pytest.raises(GuidanceContractError, match="code must be one of"):
        await analyzer(guidance())

    # The complete response was drained, so runtime validation can reject it
    # without abandoning an otherwise healthy persistent socket.
    assert analyzer.connected
    assert client.connections[0].events.empty()
    await analyzer.aclose()


@async_test
async def test_single_image_model_cannot_emit_backend_or_temporal_states() -> None:
    for forbidden in ("HOLD_STEADY", "AGENT_UNAVAILABLE"):
        client = FakeClient([forbidden])
        analyzer = OpenAIRealtimeVisionGuidanceAnalyzer(
            client,
            "test-realtime",
            prewarm=False,
        )

        with pytest.raises(GuidanceContractError, match="not valid model guidance"):
            await analyzer(guidance())

        await analyzer.aclose()


def test_request_uses_positive_ready_criteria_and_model_only_allowlist() -> None:
    analyzer = OpenAIRealtimeVisionGuidanceAnalyzer(
        FakeClient(),
        "test-realtime",
        prewarm=False,
    )
    input_value = GuidanceInput(
        frame=EncodedImage(png(), "image/png", 800, 600),
        requested_shot=GuidanceShot.FRONT,
        previous_code=GuidanceCode.HOLD_STEADY,
    )

    request = analyzer.request_for(input_value, request_id="request-1")
    instructions = request["instructions"]
    enum = request["tools"][0]["parameters"]["properties"]["code"]["enum"]  # type: ignore[index]

    assert "READY only when" in instructions
    assert "Never choose READY when unsure" in instructions
    assert "MOVE_CLOSER" in instructions
    assert "MOVE_FARTHER" in instructions
    assert "prev=" not in instructions
    assert "HOLD_STEADY" not in enum
    assert "AGENT_UNAVAILABLE" not in enum


def test_measurement_request_matches_overlay_and_model_only_rules() -> None:
    analyzer = OpenAIRealtimeVisionGuidanceAnalyzer(
        FakeClient(),
        "test-realtime",
        prewarm=False,
    )
    input_value = GuidanceInput(
        frame=EncodedImage(png(), "image/png", 800, 600),
        requested_shot=GuidanceShot.MEASUREMENT,
    )

    request = analyzer.request_for(input_value, request_id="measurement-1")
    instructions = request["instructions"]
    enum = request["tools"][0]["parameters"]["properties"]["code"]["enum"]  # type: ignore[index]
    camera_image = request["input"][0]["content"][0]  # type: ignore[index]
    _header, encoded = camera_image["image_url"].split(",", 1)  # type: ignore[index,union-attr]

    assert "cyan rectangle and cross" in instructions
    assert "WRONG_SIDE" not in enum
    with Image.open(BytesIO(base64.b64decode(encoded))) as prepared:
        guide_pixel = prepared.convert("RGB").getpixel(
            (round(prepared.width * 0.15), round(prepared.height * 0.15))
        )
        assert guide_pixel[1] > 180
        assert guide_pixel[2] > 180


@async_test
async def test_response_timeout_cancels_without_reintroducing_cold_connect() -> None:
    client = FakeClient(stall=True)
    analyzer = OpenAIRealtimeVisionGuidanceAnalyzer(
        client,
        "test-realtime",
        prewarm=False,
        response_timeout_seconds=0.01,
    )

    with pytest.raises(RealtimeGuidanceTimeoutError, match="exceeded"):
        await analyzer(guidance())

    assert client.connections[0].response.cancel_calls == 1
    assert not client.connections[0].closed
    assert analyzer.connected
    await analyzer.aclose()


@async_test
async def test_new_session_has_an_independent_unopened_client() -> None:
    original = OpenAIRealtimeVisionGuidanceAnalyzer(
        FakeClient(), "test-realtime", prewarm=False
    )
    isolated = original.new_session()

    assert isolated is not original
    assert not original.connected
    assert not isolated.connected
    await isolated(guidance())
    assert isolated.connect_count == 1
    assert original.connect_count == 0
    await isolated.aclose()
    await original.aclose()
