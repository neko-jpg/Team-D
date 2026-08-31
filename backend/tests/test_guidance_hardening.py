from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from functools import wraps
from types import SimpleNamespace
from typing import Any

import pytest

from backend import agent
from backend.guidance_state_machine import GuidanceStateMachine
from backend.guidance_transport import GuidanceTransportAdapter
from backend.live_agent import (
    SHOT_COMMAND_TOPIC,
    ShotCommandError,
    decode_shot_command,
    entrypoint,
)
from backend.providers.runtime import FixtureVisionGuidanceProvider


def async_test(function: Any) -> Any:
    @wraps(function)
    def run() -> None:
        asyncio.run(function())

    return run


@dataclass
class Publisher:
    calls: list[tuple[dict[str, object], bool]] = field(default_factory=list)
    fail_next_reliable: bool = False

    async def publish_data(self, payload: bytes, *, reliable: bool = True) -> None:
        decoded = json.loads(payload)
        if reliable and self.fail_next_reliable:
            self.fail_next_reliable = False
            raise RuntimeError("reliable snapshot unavailable")
        self.calls.append((decoded, reliable))


@dataclass
class Room:
    local_participant: Publisher
    name: str = "capture-session"
    remote_participants: list[object] = field(default_factory=list)
    handlers: dict[str, Any] = field(default_factory=dict)

    def on(self, event: str, callback: Any) -> None:
        self.handlers[event] = callback


class Context:
    def __init__(self, room: Room) -> None:
        self.room = room
        self.shutdown_callbacks: list[Any] = []

    async def connect(self, **_kwargs: Any) -> None:
        return None

    def add_shutdown_callback(self, callback: Any) -> None:
        self.shutdown_callbacks.append(callback)


def command(shot: str, revision: int, *, session_id: str = "capture-session") -> object:
    return SimpleNamespace(
        topic=SHOT_COMMAND_TOPIC,
        kind="reliable",
        data=json.dumps(
            {
                "type": "set_shot",
                "sessionId": session_id,
                "shot": shot,
                "clientRevision": revision,
            }
        ).encode(),
    )


async def settle_runtime_tasks(runtime: Any) -> None:
    while runtime._lifecycle_tasks:
        await asyncio.gather(*tuple(runtime._lifecycle_tasks), return_exceptions=True)
        await asyncio.sleep(0)


@async_test
async def test_reliable_shot_command_is_finite_monotonic_and_idempotent() -> None:
    publisher = Publisher()
    room = Room(publisher)
    runtime = await entrypoint(
        Context(room),
        inference=lambda _frame: None,
        transport_factory=agent.build_transport_factory(
            FixtureVisionGuidanceProvider(),
            process_epoch="epoch-command",
        ),
    )
    try:
        assert "data_received" in room.handlers
        assert publisher.calls[0][0] == {
            "type": "shot_changed",
            "sessionId": "capture-session",
            "sequence": 1,
            "shot": "front",
            "code": None,
            "observedAt": publisher.calls[0][0]["observedAt"],
            "processEpoch": "epoch-command",
        }

        room.handlers["data_received"](command("back", 1))
        await settle_runtime_tasks(runtime)
        assert runtime.current_shot == "back"
        assert runtime.last_client_revision == 1
        assert publisher.calls[-1][0]["shot"] == "back"
        assert publisher.calls[-1][0]["processEpoch"] == "epoch-command"
        call_count = len(publisher.calls)

        room.handlers["data_received"](command("back", 1))
        await settle_runtime_tasks(runtime)
        assert len(publisher.calls) == call_count

        room.handlers["data_received"](command("tag", 1))
        await settle_runtime_tasks(runtime)
        assert isinstance(runtime.last_command_error, ShotCommandError)
        assert runtime.current_shot == "back"

        room.handlers["data_received"](command("tag", 2))
        await settle_runtime_tasks(runtime)
        assert runtime.current_shot == "tag"
        assert runtime.last_client_revision == 2
        assert runtime.last_command_error is None
    finally:
        await runtime.close()


def test_shot_command_rejects_unknown_fields_and_invalid_values() -> None:
    valid = {
        "type": "set_shot",
        "sessionId": "capture-session",
        "shot": "front",
        "clientRevision": 1,
    }
    assert decode_shot_command(json.dumps(valid).encode()).shot == "front"
    for invalid in (
        {**valid, "unknown": True},
        {**valid, "shot": "edit"},
        {**valid, "clientRevision": 0},
        {**valid, "clientRevision": True},
    ):
        with pytest.raises((ShotCommandError, ValueError)):
            decode_shot_command(json.dumps(invalid).encode())


@async_test
async def test_pending_old_shot_frame_is_not_relabelled_after_command() -> None:
    publisher = Publisher()
    room = Room(publisher)
    started = asyncio.Event()
    release = asyncio.Event()
    seen: list[tuple[bytes, str]] = []

    def transport_factory(
        bound_room: Room, current_shot: Any
    ) -> GuidanceTransportAdapter:
        async def infer(frame: bytes) -> dict[str, object]:
            seen.append((frame, current_shot()))
            if frame == b"old-in-flight":
                started.set()
                await release.wait()
            return {"code": "READY", "confidence": 1.0}

        return GuidanceTransportAdapter(
            infer,
            bound_room.local_participant,
            session_id=bound_room.name,
            process_epoch="epoch-frame",
            provider_deadline_seconds=1,
        )

    runtime = await entrypoint(
        Context(room),
        inference=lambda _frame: None,
        transport_factory=transport_factory,
    )
    processor = runtime.subscriber.processor
    try:
        assert processor.submit_nowait(b"old-in-flight")
        await asyncio.wait_for(started.wait(), timeout=1)
        assert processor.submit_nowait(b"old-pending")
        old_generation = processor.observation_generation

        assert await runtime.handle_data_packet(command("back", 1))
        assert processor.observation_generation == old_generation + 1
        assert processor.pending_count == 0
        release.set()
        await processor.wait_idle()
        assert seen == [(b"old-in-flight", "front")]

        assert processor.submit_nowait(b"new-back")
        await processor.wait_idle()
        assert seen[-1] == (b"new-back", "back")
        assert publisher.calls[-1][0]["shot"] == "back"
    finally:
        release.set()
        await runtime.close()


@async_test
async def test_process_epoch_and_heartbeat_are_separate_from_display_dedupe() -> None:
    publisher = Publisher()

    async def infer(_frame: object) -> dict[str, object]:
        return {"code": "READY", "confidence": 1.0}

    adapter = GuidanceTransportAdapter(
        infer,
        publisher,
        state_machine=GuidanceStateMachine(
            "same-room",
            clock=lambda: 1_000,
            process_epoch="epoch-one",
            ready_confirmation_count=1,
        ),
    )
    first = await adapter.process_frame(b"one", shot="front")
    duplicate = await adapter.process_frame(
        b"two",
        shot="front",
        observed_at=2_000,
    )

    assert first is not None
    assert duplicate is None
    assert [payload.get("type", "guidance") for payload, _ in publisher.calls] == [
        "shot_changed",
        "guidance",
        "heartbeat",
    ]
    assert publisher.calls[-1] == (
        {
            "type": "heartbeat",
            "sessionId": "same-room",
            "sequence": 3,
            "shot": "front",
            "code": "READY",
            "message": "撮影できます。",
            "observedAt": 2_000,
            "expiresAt": 4_000,
            "displayChanged": False,
            "processEpoch": "epoch-one",
        },
        False,
    )
    assert all(payload["processEpoch"] == "epoch-one" for payload, _ in publisher.calls)

    replacement = GuidanceTransportAdapter(
        infer,
        Publisher(),
        state_machine=GuidanceStateMachine(
            "same-room",
            clock=lambda: 1_000,
            process_epoch="epoch-two",
            ready_confirmation_count=1,
        ),
    )
    restarted = await replacement.process_frame(b"new", shot="front")
    assert restarted is not None
    assert restarted.sequence == 2
    assert restarted.process_epoch == "epoch-two"


@async_test
async def test_session_owned_provider_is_prewarmed_once_and_closed_after_runtime() -> None:
    sessions: list[object] = []

    class SessionProvider:
        def __init__(self) -> None:
            self.prewarm_calls = 0
            self.close_calls = 0

        async def prewarm(self) -> None:
            self.prewarm_calls += 1

        async def analyze(self, _input: object) -> dict[str, object]:
            assert self.prewarm_calls == 1
            return {"code": "READY", "confidence": 1.0}

        async def aclose(self) -> None:
            self.close_calls += 1

    class ProviderFactory:
        async def analyze(self, _input: object) -> dict[str, object]:
            raise AssertionError("the worker-level provider must not serve Room frames")

        def new_session(self) -> SessionProvider:
            provider = SessionProvider()
            sessions.append(provider)
            return provider

    publisher = Publisher()
    runtime = await entrypoint(
        Context(Room(publisher)),
        inference=lambda _frame: None,
        transport_factory=agent.build_transport_factory(
            ProviderFactory(),  # type: ignore[arg-type]
            process_epoch="epoch-realtime-lifecycle",
        ),
    )
    session = sessions[0]
    assert session.prewarm_calls == 1
    assert session.close_calls == 0

    assert runtime.subscriber.processor.submit_nowait(b"frame")
    await runtime.subscriber.processor.wait_idle()
    await runtime.close()
    await runtime.close()

    assert session.close_calls == 1


@async_test
async def test_deadline_and_provider_error_publish_unavailable_without_worker_error() -> None:
    publisher = Publisher()
    attempts = 0

    async def infer(_frame: object) -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await asyncio.Event().wait()
        if attempts == 2:
            raise RuntimeError("private provider failure")
        return {"code": "READY", "confidence": 1.0}

    adapter = GuidanceTransportAdapter(
        infer,
        publisher,
        session_id="deadline-session",
        process_epoch="epoch-deadline",
        provider_deadline_seconds=0.01,
    )
    first = await asyncio.wait_for(
        adapter.process_frame(b"timeout", shot="front"), timeout=0.2
    )
    second = await adapter.process_frame(b"error", shot="front")
    unconfirmed = await adapter.process_frame(b"ok-1", shot="front")
    recovered = await adapter.process_frame(b"ok-2", shot="front")

    assert first is not None and first.code.value == "AGENT_UNAVAILABLE"
    assert second is None
    assert unconfirmed is None
    assert recovered is not None and recovered.code.value == "READY"
    assert [payload.get("type", "guidance") for payload, _ in publisher.calls] == [
        "shot_changed",
        "guidance",
        "heartbeat",
        "heartbeat",
        "guidance",
    ]
    assert [
        payload["code"]
        for payload, _ in publisher.calls
        if payload.get("type", "guidance") == "guidance"
    ] == [
        "AGENT_UNAVAILABLE",
        "READY",
    ]


@async_test
async def test_reconnect_snapshot_failure_is_retrievable_and_retryable() -> None:
    publisher = Publisher()
    room = Room(publisher)
    runtime = await entrypoint(
        Context(room),
        inference=lambda _frame: None,
        transport_factory=agent.build_transport_factory(
            FixtureVisionGuidanceProvider(),
            process_epoch="epoch-reconnect",
        ),
    )
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unhandled: list[dict[str, Any]] = []
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        room.handlers["reconnecting"]()
        await settle_runtime_tasks(runtime)
        publisher.fail_next_reliable = True
        room.handlers["reconnected"]()
        await settle_runtime_tasks(runtime)
        assert isinstance(runtime.last_lifecycle_error, RuntimeError)
        assert runtime.guidance_transport is not None
        assert not runtime.guidance_transport.connected

        snapshot = await runtime.retry_reconnect_snapshot()
        assert snapshot.to_payload()["processEpoch"] == "epoch-reconnect"
        assert runtime.last_lifecycle_error is None
        assert runtime.guidance_transport.connected
        await asyncio.sleep(0)
        assert unhandled == []
    finally:
        loop.set_exception_handler(previous_handler)
        await runtime.close()
