"""Contract tests for the SDK-independent guidance data transport."""

from __future__ import annotations

import asyncio
import json
import math
import unittest

from backend.guidance_state_machine import GuidanceEvent, GuidanceStateMachine
from backend.guidance_transport import (
    GuidanceTransportAdapter,
    GuidanceTransportError,
    encode_guidance_event,
)


class AsyncPublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, object], bool]] = []

    async def publish_data(self, payload: bytes, *, reliable: bool) -> None:
        self.calls.append((json.loads(payload), reliable))


class SyncPublisher(AsyncPublisher):
    def publish_data(self, payload: bytes, *, reliable: bool) -> None:  # type: ignore[override]
        self.calls.append((json.loads(payload), reliable))


class FailingPublisher(AsyncPublisher):
    async def publish_data(self, payload: bytes, *, reliable: bool) -> None:
        del payload, reliable
        raise RuntimeError("data channel unavailable")


class FailOncePublisher(AsyncPublisher):
    def __init__(self) -> None:
        super().__init__()
        self.failures_remaining = 1

    async def publish_data(self, payload: bytes, *, reliable: bool) -> None:
        if reliable and self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("reliable data channel unavailable")
        self.calls.append((json.loads(payload), reliable))


class DropFirstLossyPublisher(AsyncPublisher):
    def __init__(self) -> None:
        super().__init__()
        self.dropped = False

    async def publish_data(self, payload: bytes, *, reliable: bool) -> None:
        if not reliable and not self.dropped:
            self.dropped = True
            return
        self.calls.append((json.loads(payload), reliable))


class GuidanceTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_validated_inference_uses_fixed_copy_and_transport_classes(self) -> None:
        publisher = AsyncPublisher()

        async def infer(_frame: object) -> object:
            return {"code": "READY", "confidence": 0.8}

        adapter = GuidanceTransportAdapter(
            infer,
            publisher,
            state_machine=GuidanceStateMachine(
                "transport", clock=lambda: 1_000, ready_confirmation_count=1
            ),
        )
        event = await adapter.process_frame(b"frame", shot="front")
        assert event is not None
        self.assertEqual(event.message, "撮影できます。")
        self.assertEqual([reliable for _, reliable in publisher.calls], [True, False])
        self.assertEqual(publisher.calls[0][0]["type"], "shot_changed")
        self.assertEqual(publisher.calls[1][0]["expiresAt"], 3_000)

        snapshot = await adapter.snapshot()
        self.assertEqual(snapshot.sequence, 3)
        self.assertTrue(publisher.calls[-1][1])
        self.assertEqual(publisher.calls[-1][0]["type"], "resync")

    async def test_invalid_provider_copy_is_rejected_without_publication(self) -> None:
        publisher = SyncPublisher()

        async def infer(_frame: object) -> object:
            return {"code": "READY", "confidence": 1.0, "message": "untrusted"}

        adapter = GuidanceTransportAdapter(infer, publisher, session_id="transport")
        with self.assertRaises(ValueError):
            await adapter.process_frame(b"frame", shot="front")
        # The reliable shot transition is valid; no untrusted advice event is sent.
        self.assertEqual(len(publisher.calls), 1)
        self.assertTrue(publisher.calls[0][1])

    async def test_default_transport_confirms_ready_twice_and_deduplicates_afterward(self) -> None:
        publisher = AsyncPublisher()

        async def infer(_frame: object) -> object:
            return {"code": "READY", "confidence": 1.0}

        adapter = GuidanceTransportAdapter(infer, publisher, session_id="transport")

        self.assertIsNone(await adapter.process_frame(b"first", shot="front"))
        confirmed = await adapter.process_frame(b"second", shot="front")
        self.assertIsNotNone(confirmed)
        self.assertIsNone(await adapter.process_frame(b"third", shot="front"))

        assert confirmed is not None
        self.assertEqual(confirmed.code.value, "READY")
        self.assertEqual(
            [payload.get("type", "guidance") for payload, _ in publisher.calls],
            ["shot_changed", "heartbeat", "guidance", "heartbeat"],
        )

    async def test_heartbeat_recovers_dropped_guidance_and_renews_ttl_without_redraw(self) -> None:
        publisher = DropFirstLossyPublisher()

        async def infer(_frame: object) -> object:
            return {"code": "CENTER_GARMENT", "confidence": 1.0}

        adapter = GuidanceTransportAdapter(
            infer,
            publisher,
            state_machine=GuidanceStateMachine(
                "transport", clock=lambda: 1_000, ready_confirmation_count=1
            ),
        )
        first = await adapter.process_frame(b"first", shot="front", observed_at=1_000)
        duplicate = await adapter.process_frame(
            b"second", shot="front", observed_at=2_500
        )

        assert first is not None
        assert duplicate is None
        assert publisher.calls[-1] == (
            {
                "type": "heartbeat",
                "sessionId": "transport",
                "sequence": 3,
                "shot": "front",
                "code": "CENTER_GARMENT",
                "message": "衣類をガイドの中央に合わせてください。",
                "observedAt": 2_500,
                "expiresAt": 4_500,
                "displayChanged": False,
            },
            False,
        )

    async def test_disconnect_drops_in_flight_result_then_reconnect_snapshot_precedes_advice(
        self,
    ) -> None:
        publisher = AsyncPublisher()
        started = asyncio.Event()
        release = asyncio.Event()

        async def infer(_frame: object) -> object:
            started.set()
            await release.wait()
            return {"code": "READY", "confidence": 1.0}

        adapter = GuidanceTransportAdapter(
            infer,
            publisher,
            state_machine=GuidanceStateMachine(
                "transport", clock=lambda: 1_000, ready_confirmation_count=1
            ),
        )
        in_flight = asyncio.create_task(adapter.process_frame(b"old", shot="front"))
        await asyncio.wait_for(started.wait(), timeout=1)
        self.assertTrue(await adapter.mark_disconnected())
        self.assertFalse(adapter.connected)
        release.set()
        self.assertIsNone(await in_flight)
        self.assertEqual([call[0]["type"] for call in publisher.calls], ["shot_changed"])

        snapshot = await adapter.on_reconnected()
        self.assertTrue(adapter.connected)
        self.assertEqual(snapshot.sequence, 2)

        # A new frame is allowed only after the reliable snapshot succeeds.
        event = await adapter.process_frame(b"new", shot="front")
        assert event is not None
        self.assertEqual(
            [(payload.get("type", "guidance"), reliable) for payload, reliable in publisher.calls],
            [("shot_changed", True), ("resync", True), ("guidance", False)],
        )
        self.assertEqual([payload["sequence"] for payload, _ in publisher.calls], [1, 2, 3])

    async def test_shot_change_fences_an_in_flight_result_without_waiting_for_provider(
        self,
    ) -> None:
        publisher = SyncPublisher()
        started = asyncio.Event()
        release = asyncio.Event()

        async def infer(_frame: object) -> object:
            started.set()
            await release.wait()
            return {"code": "READY", "confidence": 1.0}

        adapter = GuidanceTransportAdapter(infer, publisher, session_id="transport")
        in_flight = asyncio.create_task(adapter.process_frame(b"front", shot="front"))
        await asyncio.wait_for(started.wait(), timeout=1)
        changed = await asyncio.wait_for(adapter.set_shot("back"), timeout=1)
        self.assertIsNotNone(changed)
        release.set()
        self.assertIsNone(await in_flight)

        self.assertIsNone(await adapter.process_frame(b"back-1", shot="back"))
        event = await adapter.process_frame(b"back-2", shot="back")
        assert event is not None
        self.assertEqual(
            [payload["sequence"] for payload, _ in publisher.calls], [1, 2, 3, 4]
        )
        self.assertEqual(publisher.calls[-1][0]["shot"], "back")

    async def test_close_fences_inference_and_rejects_new_operations(self) -> None:
        publisher = AsyncPublisher()
        started = asyncio.Event()
        release = asyncio.Event()

        async def infer(_frame: object) -> object:
            started.set()
            await release.wait()
            return {"code": "READY", "confidence": 1.0}

        adapter = GuidanceTransportAdapter(infer, publisher, session_id="transport")
        in_flight = asyncio.create_task(adapter.process_frame(b"frame", shot="front"))
        await asyncio.wait_for(started.wait(), timeout=1)
        self.assertTrue(await asyncio.wait_for(adapter.close(), timeout=1))
        release.set()
        self.assertIsNone(await in_flight)
        self.assertTrue(adapter.closed)

        for operation in (
            adapter.set_shot("back"),
            adapter.resync(),
            adapter.process_frame(b"next", shot="front"),
        ):
            with self.subTest(operation=operation), self.assertRaises(GuidanceTransportError):
                await operation

    async def test_reconnect_snapshot_failure_keeps_transport_disconnected(self) -> None:
        publisher = AsyncPublisher()

        async def infer(_frame: object) -> object:
            return {"code": "READY", "confidence": 1.0}

        adapter = GuidanceTransportAdapter(infer, publisher, session_id="transport")
        await adapter.mark_disconnected()
        with self.assertRaises(RuntimeError):
            await adapter.on_reconnected(publisher=FailingPublisher())
        self.assertFalse(adapter.connected)
        with self.assertRaises(GuidanceTransportError):
            await adapter.process_frame(b"next", shot="front")

    async def test_set_shot_failure_fences_transport_until_reconnect_resync(self) -> None:
        publisher = FailOncePublisher()

        async def infer(_frame: object) -> object:
            return {"code": "READY", "confidence": 1.0}

        adapter = GuidanceTransportAdapter(
            infer,
            publisher,
            state_machine=GuidanceStateMachine(
                "transport", clock=lambda: 1_000, ready_confirmation_count=1
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "reliable"):
            await adapter.set_shot("front")
        self.assertEqual(adapter.current_shot, "front")
        self.assertFalse(adapter.connected)
        self.assertEqual(adapter.sequence, 1)

        with self.assertRaises(GuidanceTransportError):
            await adapter.process_frame(b"blocked", shot="front")
        self.assertEqual(publisher.calls, [])

        snapshot = await adapter.on_reconnected()
        self.assertTrue(adapter.connected)
        self.assertEqual((snapshot.sequence, snapshot.shot), (2, "front"))
        event = await adapter.process_frame(b"new", shot="front")
        assert event is not None
        self.assertEqual(
            [(payload.get("type", "guidance"), reliable, payload["sequence"])
             for payload, reliable in publisher.calls],
            [("resync", True, 2), ("guidance", False, 3)],
        )

    async def test_auto_shot_transition_failure_never_runs_inference_or_sends_advice(
        self,
    ) -> None:
        publisher = FailOncePublisher()
        inference_calls = 0

        async def infer(_frame: object) -> object:
            nonlocal inference_calls
            inference_calls += 1
            return {"code": "READY", "confidence": 1.0}

        adapter = GuidanceTransportAdapter(
            infer,
            publisher,
            state_machine=GuidanceStateMachine(
                "transport", clock=lambda: 1_000, ready_confirmation_count=1
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "reliable"):
            await adapter.process_frame(b"front", shot="front")
        self.assertEqual(inference_calls, 0)
        self.assertFalse(adapter.connected)
        self.assertEqual(publisher.calls, [])

        snapshot = await adapter.on_reconnected()
        self.assertEqual((snapshot.sequence, snapshot.shot), (2, "front"))
        event = await adapter.process_frame(b"front", shot="front")
        assert event is not None
        self.assertEqual(
            [(payload.get("type", "guidance"), reliable, payload["sequence"])
             for payload, reliable in publisher.calls],
            [("resync", True, 2), ("guidance", False, 3)],
        )

    def test_encoder_rejects_custom_copy_and_non_finite_values(self) -> None:
        with self.assertRaises(GuidanceTransportError):
            encode_guidance_event(
                GuidanceEvent(
                    session_id="transport",
                    sequence=1,
                    shot="front",  # type: ignore[arg-type]
                    code="READY",  # type: ignore[arg-type]
                    message="provider text",
                    confidence=1.0,
                    observed_at=1,
                    expires_at=2,
                )
            )
        with self.assertRaises(ValueError):
            GuidanceEvent(
                session_id="transport",
                sequence=1,
                shot="front",  # type: ignore[arg-type]
                code="READY",  # type: ignore[arg-type]
                message="撮影できます。",
                confidence=math.nan,
                observed_at=1,
                expires_at=2,
            )


if __name__ == "__main__":
    unittest.main()
