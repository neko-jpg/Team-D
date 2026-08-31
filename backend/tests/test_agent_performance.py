"""Deterministic performance contracts for the live guidance Agent path."""

from __future__ import annotations

import asyncio
import json
import math
import unittest
from dataclasses import dataclass, field
from typing import Any

from backend.guidance_state_machine import GuidanceStateMachine
from backend.guidance_transport import GuidanceTransportAdapter
from backend.live_agent import entrypoint


@dataclass
class ManualClock:
    now_ms: int

    def __call__(self) -> int:
        return self.now_ms

    def advance(self, milliseconds: int) -> None:
        self.now_ms += milliseconds

    def set(self, milliseconds: int) -> None:
        if milliseconds < self.now_ms:
            raise ValueError("manual clock cannot move backwards")
        self.now_ms = milliseconds


@dataclass(frozen=True)
class Frame:
    number: int
    latency_ms: int
    code: str


@dataclass
class Publisher:
    clock: ManualClock
    calls: list[tuple[dict[str, object], bool, int]] = field(default_factory=list)

    def publish_data(self, payload: bytes, *, reliable: bool = True) -> None:
        self.calls.append((json.loads(payload), reliable, self.clock()))


@dataclass
class Room:
    local_participant: Publisher
    name: str = "performance-session"
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


def _nearest_rank_p95(values: list[int]) -> int:
    if not values:
        raise ValueError("at least one latency sample is required")
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * 0.95) - 1]


def _guidance_latencies(publisher: Publisher) -> list[int]:
    return [
        generated_at - int(payload["observedAt"])
        for payload, _reliable, generated_at in publisher.calls
        if "type" not in payload
    ]


class AgentPerformanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_guidance_p95_concurrency_queue_and_unhandled_errors(self) -> None:
        """Measure every 5.7 acceptance value without wall-clock timing noise."""

        clock = ManualClock(10_000)
        publisher = Publisher(clock)
        active = 0
        max_active = 0
        observed_at: list[int] = []
        expected_latencies = [100 + index * 75 for index in range(20)]

        async def provider(frame: Frame) -> dict[str, object]:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            try:
                clock.advance(frame.latency_ms)
                return {"code": frame.code, "confidence": 1.0}
            finally:
                active -= 1

        def transport_factory(room: Room, _current_shot: Any) -> GuidanceTransportAdapter:
            return GuidanceTransportAdapter(
                provider,
                room.local_participant,
                state_machine=GuidanceStateMachine(
                    "performance-session",
                    clock=clock,
                    ready_confirmation_count=1,
                ),
            )

        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        unhandled: list[dict[str, Any]] = []
        loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
        runtime = None
        try:
            runtime = await entrypoint(
                Context(Room(publisher)),
                inference=provider,
                transport_factory=transport_factory,
                observation_clock=clock,
            )
            processor = runtime.subscriber.processor

            for index, latency_ms in enumerate(expected_latencies):
                clock.advance(10)
                observed_at.append(clock())
                code = "READY" if index % 2 == 0 else "HOLD_STEADY"
                self.assertTrue(
                    processor.submit_nowait(Frame(index, latency_ms, code))
                )
                await processor.wait_idle()

            measured_latencies = _guidance_latencies(publisher)
            self.assertEqual(measured_latencies, expected_latencies)
            self.assertEqual(
                [
                    int(payload["observedAt"])
                    for payload, _reliable, _generated_at in publisher.calls
                    if "type" not in payload
                ],
                observed_at,
            )
            self.assertLessEqual(_nearest_rank_p95(measured_latencies), 2_000)
            self.assertEqual(processor.max_concurrency, 1)
            self.assertEqual(processor.max_in_flight, 1)
            self.assertEqual(max_active, 1)
            self.assertEqual(processor.queue.maxsize, 1)
            self.assertLessEqual(processor.max_pending, 1)
            self.assertEqual(processor.error_count, 0)
        finally:
            if runtime is not None:
                await runtime.close()
            await asyncio.sleep(0)
            loop.set_exception_handler(previous_handler)

        self.assertEqual(unhandled, [])

    async def test_latency_miss_coalesces_burst_to_only_the_latest_frame(self) -> None:
        """A slow provider must not trade a missed target for a larger queue."""

        clock = ManualClock(50_000)
        publisher = Publisher(clock)
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        processed: list[int] = []
        active = 0
        max_active = 0

        async def provider(frame: Frame) -> dict[str, object]:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            processed.append(frame.number)
            try:
                if frame.number == 1:
                    first_started.set()
                    await release_first.wait()
                    clock.set(53_000)
                else:
                    clock.advance(frame.latency_ms)
                return {"code": frame.code, "confidence": 1.0}
            finally:
                active -= 1

        def transport_factory(room: Room, _current_shot: Any) -> GuidanceTransportAdapter:
            return GuidanceTransportAdapter(
                provider,
                room.local_participant,
                state_machine=GuidanceStateMachine(
                    "performance-session",
                    clock=clock,
                    ready_confirmation_count=1,
                ),
            )

        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        unhandled: list[dict[str, Any]] = []
        loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
        runtime = None
        try:
            runtime = await entrypoint(
                Context(Room(publisher)),
                inference=provider,
                transport_factory=transport_factory,
                observation_clock=clock,
            )
            processor = runtime.subscriber.processor
            self.assertTrue(processor.submit_nowait(Frame(1, 3_000, "READY")))
            await asyncio.wait_for(first_started.wait(), timeout=1)

            max_queue_size = 0
            for number in range(2, 101):
                clock.advance(1)
                self.assertTrue(
                    processor.submit_nowait(
                        Frame(number, 100, "HOLD_STEADY")
                    )
                )
                max_queue_size = max(max_queue_size, processor.queue_size)
                self.assertLessEqual(processor.queue_size, 1)

            self.assertEqual(processor.latest_frame.number, 100)
            release_first.set()
            await processor.wait_idle()

            self.assertEqual(processed, [1, 100])
            self.assertEqual(processor.processed_count, 2)
            self.assertEqual(processor.dropped_count, 98)
            self.assertEqual(processor.max_in_flight, 1)
            self.assertEqual(max_active, 1)
            self.assertEqual(processor.queue.maxsize, 1)
            self.assertEqual(processor.max_pending, 1)
            self.assertEqual(max_queue_size, 1)
            guidance_payloads = [
                payload
                for payload, _reliable, _generated_at in publisher.calls
                if "type" not in payload
            ]
            self.assertEqual(
                [payload["observedAt"] for payload in guidance_payloads],
                [50_000, 50_099],
            )
            measured_latencies = _guidance_latencies(publisher)
            self.assertEqual(measured_latencies, [3_000, 3_001])
            self.assertTrue(all(latency > 2_000 for latency in measured_latencies))
            self.assertEqual(processor.error_count, 0)
        finally:
            if runtime is not None:
                await runtime.close()
            await asyncio.sleep(0)
            loop.set_exception_handler(previous_handler)

        self.assertEqual(unhandled, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
