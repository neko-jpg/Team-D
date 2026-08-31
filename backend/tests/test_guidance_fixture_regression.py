"""One-command regression for the fixture guidance transport lifecycle.

Run with::

    .venv/bin/python -m pytest -q backend/tests/test_guidance_fixture_regression.py

The single scenario deliberately mixes normal inference, a provider timeout,
an out-of-date in-flight result, a shot change, and Room reconnection.  Keeping
these transitions in one test catches state rollback bugs which isolated
transport contract tests cannot expose.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import dataclass, field

from backend.guidance_state_machine import GuidanceStateMachine
from backend.guidance_transport import (
    GuidanceTransportAdapter,
    GuidanceTransportError,
)
from backend.providers.runtime import create_provider_inference
from backend.providers.vision_guidance import (
    GuidanceCode,
    GuidanceInput,
    GuidanceShot,
    VisionDecision,
)


@dataclass
class FixturePublisher:
    calls: list[tuple[dict[str, object], bool]] = field(default_factory=list)

    async def publish_data(self, payload: bytes, *, reliable: bool) -> None:
        self.calls.append((json.loads(payload), reliable))


class SequencedFixtureProvider:
    """Deterministic provider which exposes one delayed stale result."""

    def __init__(self) -> None:
        self.calls: list[tuple[bytes, GuidanceShot]] = []
        self.stale_started = asyncio.Event()
        self.release_stale = asyncio.Event()

    async def analyze(self, input: GuidanceInput) -> VisionDecision:
        frame = input.frame.data
        self.calls.append((frame, input.requested_shot))
        if frame == b"provider-timeout":
            raise TimeoutError("fixture provider timeout")
        if frame == b"stale-front":
            self.stale_started.set()
            await self.release_stale.wait()
            return VisionDecision(GuidanceCode.MOVE_CLOSER, 0.7)
        return VisionDecision(GuidanceCode.READY, 1.0)


class GuidanceFixtureRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_sequence_expiry_shot_and_provider_calls_never_roll_back(self) -> None:
        before_reconnect = FixturePublisher()
        after_reconnect = FixturePublisher()
        provider = SequencedFixtureProvider()
        selected_shot = GuidanceShot.FRONT
        state_machine = GuidanceStateMachine(
            "fixture-regression",
            clock=lambda: 0,
            guidance_ttl_ms=2_000,
            ready_confirmation_count=1,
        )
        adapter = GuidanceTransportAdapter(
            create_provider_inference(
                provider,
                requested_shot=lambda: selected_shot,
            ),
            before_reconnect,
            state_machine=state_machine,
        )

        # 1. Normal guidance establishes the initial sequence and expiry.
        normal = await adapter.process_frame(
            b"normal-front",
            shot="front",
            observed_at=1_000,
        )
        self.assertIsNotNone(normal)
        assert normal is not None
        self.assertEqual((normal.sequence, normal.expires_at), (2, 3_000))
        self.assertEqual(adapter.current_shot, GuidanceShot.FRONT)

        # 2. A timeout is a failed provider call, not a state transition.
        with self.assertRaisesRegex(TimeoutError, "fixture provider timeout"):
            await adapter.process_frame(
                b"provider-timeout",
                shot="front",
                observed_at=2_000,
            )
        self.assertEqual(adapter.sequence, 2)
        self.assertEqual(adapter.current_shot, GuidanceShot.FRONT)

        # 3-4. Start an older observation, change shot while it is in flight,
        # then let it finish.  Publishing it would regress expiry to 2,500 and
        # put a front event after the authoritative back transition.
        stale = asyncio.create_task(
            adapter.process_frame(
                b"stale-front",
                shot="front",
                observed_at=500,
            )
        )
        await asyncio.wait_for(provider.stale_started.wait(), timeout=1)
        changed = await adapter.set_shot("back", observed_at=3_000)
        selected_shot = GuidanceShot.BACK
        self.assertEqual((changed.sequence, changed.shot), (3, GuidanceShot.BACK))
        provider.release_stale.set()
        self.assertIsNone(await stale)
        self.assertEqual(adapter.sequence, 3)
        self.assertEqual(adapter.current_shot, GuidanceShot.BACK)

        # 5-6. No disconnected frame may reach the provider.  Reconnection
        # first sends a reliable snapshot and only then re-enables guidance.
        self.assertTrue(await adapter.mark_disconnected())
        with self.assertRaises(GuidanceTransportError):
            await adapter.process_frame(
                b"disconnected",
                shot="back",
                observed_at=3_500,
            )
        self.assertEqual(adapter.sequence, 3)

        snapshot = await adapter.on_reconnected(
            publisher=after_reconnect,
            observed_at=4_000,
        )
        self.assertEqual(
            (snapshot.kind, snapshot.sequence, snapshot.shot),
            ("resync", 4, GuidanceShot.BACK),
        )
        fresh = await adapter.process_frame(
            b"fresh-back",
            shot="back",
            observed_at=5_000,
        )
        self.assertIsNotNone(fresh)
        assert fresh is not None

        published = [*before_reconnect.calls, *after_reconnect.calls]
        payloads = [payload for payload, _reliable in published]
        self.assertEqual(
            [payload["sequence"] for payload, _reliable in before_reconnect.calls],
            [1, 2, 3],
        )
        self.assertEqual(
            [payload["sequence"] for payload, _reliable in after_reconnect.calls],
            [4, 5],
        )
        self.assertEqual(
            [payload["sequence"] for payload in payloads],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(
            [
                (payload.get("type", "guidance"), reliable)
                for payload, reliable in published
            ],
            [
                ("shot_changed", True),
                ("guidance", False),
                ("shot_changed", True),
                ("resync", True),
                ("guidance", False),
            ],
        )
        guidance_payloads = [payload for payload in payloads if "expiresAt" in payload]
        self.assertEqual(
            [payload["expiresAt"] for payload in guidance_payloads],
            [3_000, 7_000],
        )
        self.assertEqual(
            [payload["shot"] for payload in payloads[2:]],
            ["back", "back", "back"],
        )
        self.assertEqual(adapter.current_shot, GuidanceShot.BACK)
        self.assertEqual(adapter.sequence, 5)
        self.assertEqual(
            provider.calls,
            [
                (b"normal-front", GuidanceShot.FRONT),
                (b"provider-timeout", GuidanceShot.FRONT),
                (b"stale-front", GuidanceShot.FRONT),
                (b"fresh-back", GuidanceShot.BACK),
            ],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
