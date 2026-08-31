"""Integration contracts for the Agent guidance transport wiring."""

from __future__ import annotations

import asyncio
import json
import unittest
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


class GuidanceAgentIntegrationTests(unittest.IsolatedAsyncioTestCase):
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
        self.assertTrue(runtime.subscriber.processor.submit_nowait(b"back-frame"))
        await runtime.subscriber.processor.wait_idle()

        self.assertEqual(seen_shots, ["back"])
        self.assertEqual(
            [(payload.get("type", "guidance"), payload["shot"], reliable) for payload, reliable in publisher.calls],
            [("shot_changed", "back", True), ("guidance", "back", False)],
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
        runtime = await entrypoint(
            Context(Room(publisher)),
            inference=create_provider_inference(provider),
            transport_factory=agent.build_transport_factory(provider),
        )

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
            "sequence": 2,
            "shot": "back",
            "code": None,
            "observedAt": publisher.calls[-1][0]["observedAt"],
        })

        self.assertTrue(runtime.subscriber.processor.submit_nowait(b"back-frame"))
        await runtime.subscriber.processor.wait_idle()
        self.assertEqual(publisher.calls[-1][0]["shot"], "back")
        await runtime.close()
        await runtime.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
