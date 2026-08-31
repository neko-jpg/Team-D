"""Integration contracts for the Agent guidance transport wiring."""

from __future__ import annotations

import asyncio
import gc
import json
import unittest
import weakref
from dataclasses import dataclass, field
from typing import Any

from backend import agent
from backend.live_agent import entrypoint
from backend.providers.runtime import create_provider_inference
from backend.providers.vision_guidance import GuidanceCode
from backend.settings import BackendSettings, ProviderMode


@dataclass
class Publisher:
    calls: list[tuple[dict[str, object], bool]] = field(default_factory=list)

    def publish_data(self, payload: bytes, *, reliable: bool = True) -> None:
        self.calls.append((json.loads(payload), reliable))


class FailingOncePublisher(Publisher):
    def __init__(self) -> None:
        super().__init__()
        self._fail_reliable = True

    def publish_data(self, payload: bytes, *, reliable: bool = True) -> None:
        super().publish_data(payload, reliable=reliable)
        if reliable and self._fail_reliable:
            self._fail_reliable = False
            raise RuntimeError("reliable packet unavailable")


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


class Payload:
    """Weak-referenceable stand-in for a decoded camera or provider payload."""

    def __init__(self, name: str) -> None:
        self.name = name


class PayloadError(RuntimeError):
    """Weak-referenceable provider failure used to verify traceback release."""


class GuidanceAgentIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_close_is_retryable_and_still_drains_inference(
        self,
    ) -> None:
        inference_started = asyncio.Event()
        first_worker_cancel_observed = asyncio.Event()

        async def analyzer(_frame: Payload) -> None:
            inference_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                first_worker_cancel_observed.set()
                # Suppress the worker's first cancellation.  A retried close
                # must cancel and await this still-running inference again.
                await asyncio.Future()

        runtime = await entrypoint(
            Context(Room(Publisher())),
            inference=analyzer,
        )
        processor = runtime.subscriber.processor
        in_flight_frame = Payload("in-flight")
        pending_frame = Payload("pending")
        in_flight_ref = weakref.ref(in_flight_frame)
        pending_ref = weakref.ref(pending_frame)

        self.assertTrue(processor.submit_nowait(in_flight_frame))
        await asyncio.wait_for(inference_started.wait(), timeout=1)
        self.assertTrue(processor.submit_nowait(pending_frame))
        del in_flight_frame, pending_frame

        first_close = asyncio.create_task(runtime.close())
        await asyncio.wait_for(first_worker_cancel_observed.wait(), timeout=1)
        first_close.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first_close
        del first_close

        self.assertFalse(runtime.closed)
        self.assertEqual(processor.pending_count, 0)
        self.assertEqual(processor.in_flight, 1)
        self.assertIsNotNone(processor.worker_task)

        await asyncio.wait_for(runtime.close(), timeout=1)
        self.assertTrue(runtime.closed)
        self.assertEqual(processor.pending_count, 0)
        self.assertEqual(processor.in_flight, 0)
        self.assertIsNone(processor.worker_task)

        await asyncio.sleep(0)
        gc.collect()
        self.assertIsNone(in_flight_ref())
        self.assertIsNone(pending_ref())

    async def test_close_awaits_in_flight_inference_and_releases_pending_frames(
        self,
    ) -> None:
        inference_started = asyncio.Event()
        cancellation_observed = asyncio.Event()
        allow_cancelled_inference_to_finish = asyncio.Event()
        late_result_refs: list[weakref.ReferenceType[Payload]] = []
        result_callback_count = 0
        error_callback_count = 0

        async def analyzer(_frame: Payload) -> Payload:
            inference_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                # Model a provider client that performs async cleanup and then
                # suppresses cancellation by returning a late result.
                cancellation_observed.set()
                await allow_cancelled_inference_to_finish.wait()
                late_result = Payload("late-result")
                late_result_refs.append(weakref.ref(late_result))
                return late_result

        def on_result(_result: Payload, _frame: Payload) -> None:
            nonlocal result_callback_count
            result_callback_count += 1

        def on_error(_error: BaseException, _frame: Payload) -> None:
            nonlocal error_callback_count
            error_callback_count += 1

        runtime = await entrypoint(
            Context(Room(Publisher())),
            inference=analyzer,
            on_result=on_result,
            on_error=on_error,
        )
        processor = runtime.subscriber.processor
        in_flight_frame = Payload("in-flight")
        pending_frame = Payload("pending")
        in_flight_ref = weakref.ref(in_flight_frame)
        pending_ref = weakref.ref(pending_frame)

        self.assertTrue(processor.submit_nowait(in_flight_frame))
        await asyncio.wait_for(inference_started.wait(), timeout=1)
        self.assertTrue(processor.submit_nowait(pending_frame))
        self.assertEqual(processor.in_flight, 1)
        self.assertEqual(processor.pending_count, 1)
        self.assertIsNotNone(processor.worker_task)
        del in_flight_frame, pending_frame

        close_task = asyncio.create_task(runtime.close())
        await asyncio.wait_for(cancellation_observed.wait(), timeout=1)
        await asyncio.sleep(0)
        self.assertFalse(close_task.done())

        allow_cancelled_inference_to_finish.set()
        await asyncio.wait_for(close_task, timeout=1)
        del close_task

        self.assertTrue(runtime.closed)
        self.assertTrue(processor.closed)
        self.assertEqual(processor.pending_count, 0)
        self.assertEqual(processor.in_flight, 0)
        self.assertIsNone(processor.worker_task)
        self.assertIsNone(processor.last_processed_frame)
        self.assertIsNone(processor.last_result)
        self.assertIsNone(processor.last_error)
        self.assertEqual(result_callback_count, 0)
        self.assertEqual(error_callback_count, 0)

        # Both spellings and concurrent repeated calls converge without doing
        # more lifecycle work or resurrecting any payload reference.
        await asyncio.gather(runtime.close(), runtime.aclose(), runtime.close())
        await asyncio.sleep(0)
        gc.collect()
        self.assertIsNone(in_flight_ref())
        self.assertIsNone(pending_ref())
        self.assertEqual(len(late_result_refs), 1)
        self.assertIsNone(late_result_refs[0]())

    async def test_close_drops_late_error_callback_after_cancel(self) -> None:
        inference_started = asyncio.Event()
        cancellation_observed = asyncio.Event()
        allow_cancelled_inference_to_finish = asyncio.Event()
        late_error_refs: list[weakref.ReferenceType[PayloadError]] = []
        error_callback_count = 0

        async def analyzer(_frame: Payload) -> None:
            inference_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancellation_observed.set()
                await allow_cancelled_inference_to_finish.wait()
                late_error = PayloadError("late-error")
                late_error_refs.append(weakref.ref(late_error))
                raise late_error

        def on_error(_error: BaseException, _frame: Payload) -> None:
            nonlocal error_callback_count
            error_callback_count += 1

        runtime = await entrypoint(
            Context(Room(Publisher())),
            inference=analyzer,
            on_error=on_error,
        )
        processor = runtime.subscriber.processor
        frame = Payload("in-flight-error")
        frame_ref = weakref.ref(frame)
        self.assertTrue(processor.submit_nowait(frame))
        await asyncio.wait_for(inference_started.wait(), timeout=1)
        del frame

        close_task = asyncio.create_task(runtime.aclose())
        await asyncio.wait_for(cancellation_observed.wait(), timeout=1)
        self.assertFalse(close_task.done())
        allow_cancelled_inference_to_finish.set()
        await asyncio.wait_for(close_task, timeout=1)
        del close_task

        self.assertEqual(error_callback_count, 0)
        self.assertEqual(processor.error_count, 0)
        self.assertIsNone(processor.last_error)
        self.assertIsNone(processor.worker_task)
        await asyncio.sleep(0)
        gc.collect()
        self.assertIsNone(frame_ref())
        self.assertEqual(len(late_error_refs), 1)
        self.assertIsNone(late_error_refs[0]())

    async def test_close_clears_completed_frame_result_and_error_diagnostics(
        self,
    ) -> None:
        created_result_refs: list[weakref.ReferenceType[Payload]] = []
        created_error_refs: list[weakref.ReferenceType[PayloadError]] = []

        async def analyzer(frame: Payload) -> Payload:
            if frame.name == "success":
                result = Payload("completed-result")
                created_result_refs.append(weakref.ref(result))
                return result
            error = PayloadError("completed-error")
            created_error_refs.append(weakref.ref(error))
            raise error

        runtime = await entrypoint(
            Context(Room(Publisher())),
            inference=analyzer,
        )
        processor = runtime.subscriber.processor
        success_frame = Payload("success")
        error_frame = Payload("error")
        success_frame_ref = weakref.ref(success_frame)
        error_frame_ref = weakref.ref(error_frame)

        self.assertTrue(processor.submit_nowait(success_frame))
        await processor.wait_idle()
        self.assertIs(processor.last_processed_frame, success_frame)
        self.assertIs(processor.last_result, created_result_refs[0]())

        self.assertTrue(processor.submit_nowait(error_frame))
        await processor.wait_idle()
        self.assertIs(processor.last_error, created_error_refs[0]())
        del success_frame, error_frame

        await runtime.aclose()
        self.assertIsNone(processor.last_processed_frame)
        self.assertIsNone(processor.last_result)
        self.assertIsNone(processor.last_error)
        self.assertEqual(processor.pending_count, 0)
        self.assertEqual(processor.in_flight, 0)
        self.assertIsNone(processor.worker_task)

        await asyncio.sleep(0)
        gc.collect()
        self.assertIsNone(success_frame_ref())
        self.assertIsNone(error_frame_ref())
        self.assertIsNone(created_result_refs[0]())
        self.assertIsNone(created_error_refs[0]())

    async def test_server_wires_shot_aware_provider_and_transport_lifecycle(self) -> None:
        seen_shots: list[str] = []
        captured: dict[str, Any] = {}

        async def analyzer(input_value: Any) -> dict[str, object]:
            seen_shots.append(input_value.requestedShot)
            return {"code": "READY", "confidence": 1.0}

        settings = BackendSettings(
            provider_mode=ProviderMode.LIVE,
            livekit_url="wss://room.example.invalid",
            livekit_api_key="key",
            livekit_api_secret="secret",
        )

        def server_factory(**kwargs: Any) -> object:
            captured.update(kwargs)
            return object()

        agent.run_agent_worker(
            settings,
            runner=lambda _server: None,
            live_analyzer=analyzer,
            server_factory=server_factory,
        )
        self.assertIn("transport_factory", captured)

        publisher = Publisher()
        room = Room(publisher)
        runtime = await entrypoint(
            Context(room),
            inference=captured["inference"],
            transport_factory=captured["transport_factory"],
        )
        self.assertIsNotNone(runtime.guidance_transport)
        self.assertEqual(runtime.guidance_transport.session_id, "capture-session")

        await runtime.set_shot("back")
        self.assertTrue(runtime.subscriber.processor.submit_nowait(b"back-frame-1"))
        await runtime.subscriber.processor.wait_idle()
        self.assertTrue(runtime.subscriber.processor.submit_nowait(b"back-frame-2"))
        await runtime.subscriber.processor.wait_idle()

        self.assertEqual(seen_shots, ["back", "back"])
        self.assertEqual(
            [(payload.get("type", "guidance"), payload["shot"], reliable) for payload, reliable in publisher.calls],
            [
                ("shot_changed", "front", True),
                ("shot_changed", "back", True),
                ("heartbeat", "back", False),
                ("guidance", "back", False),
            ],
        )
        self.assertEqual(
            len({payload["processEpoch"] for payload, _ in publisher.calls}),
            1,
        )
        self.assertEqual(publisher.calls[-1][0]["code"], GuidanceCode.READY.value)

        room.handlers["reconnecting"]()
        await asyncio.sleep(0)
        self.assertFalse(runtime.guidance_transport.connected)
        room.handlers["reconnected"]()
        await asyncio.sleep(0)
        self.assertTrue(runtime.guidance_transport.connected)
        self.assertEqual(publisher.calls[-1][0]["type"], "resync")
        self.assertTrue(publisher.calls[-1][1])

        sent_before_close = len(publisher.calls)
        await runtime.close()
        await runtime.close()
        self.assertFalse(runtime.subscriber.processor.submit_nowait(b"closed-frame"))
        await asyncio.sleep(0)
        self.assertEqual(len(publisher.calls), sent_before_close)

    async def test_failed_shot_publish_keeps_runtime_aligned_for_reconnect(self) -> None:
        settings = BackendSettings(
            provider_mode=ProviderMode.FIXTURE,
            livekit_url="wss://room.example.invalid",
            livekit_api_key="key",
            livekit_api_secret="secret",
        )
        provider = agent.build_runtime_provider(settings)
        publisher = FailingOncePublisher()
        publisher._fail_reliable = False
        runtime = await entrypoint(
            Context(Room(publisher)),
            inference=create_provider_inference(provider),
            transport_factory=agent.build_transport_factory(provider),
        )
        publisher._fail_reliable = True

        with self.assertRaisesRegex(RuntimeError, "reliable packet unavailable"):
            await runtime.set_shot("back")

        self.assertEqual(runtime.current_shot, "back")
        self.assertEqual(runtime.guidance_transport.current_shot.value, "back")
        self.assertFalse(runtime.guidance_transport.connected)

        await runtime.on_reconnected()
        self.assertTrue(runtime.guidance_transport.connected)
        self.assertEqual(publisher.calls[-1][0], {
            "type": "resync",
            "sessionId": "capture-session",
            "sequence": 3,
            "shot": "back",
            "code": None,
            "observedAt": publisher.calls[-1][0]["observedAt"],
            "processEpoch": runtime.guidance_transport.process_epoch,
        })

        self.assertTrue(runtime.subscriber.processor.submit_nowait(b"back-frame"))
        await runtime.subscriber.processor.wait_idle()
        self.assertEqual(publisher.calls[-1][0]["shot"], "back")
        await runtime.close()
        await runtime.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
