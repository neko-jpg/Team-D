"""Offline contract tests for OpenSpec task 3.10."""

from __future__ import annotations

import math
import unittest

from backend.guidance_state_machine import (
    GuidanceStateMachine,
    GuidanceValidationError,
    TransportKind,
)
from backend.providers.vision_guidance import (
    GUIDANCE_CODES,
    EncodedImage,
    GuidanceCode,
    GuidanceInput,
    VisionDecision,
    VisionGuidanceProvider,
    validate_guidance_code,
    validate_vision_decision,
)


class FakeClock:
    def __init__(self, now_ms: int = 1_000_000) -> None:
        self.now_ms = now_ms

    def __call__(self) -> int:
        return self.now_ms


class RecordingProvider:
    def __init__(self, result: object) -> None:
        self.result = result
        self.inputs: list[GuidanceInput] = []

    async def analyze(self, input: GuidanceInput) -> object:
        self.inputs.append(input)
        return self.result


class ProviderContractTests(unittest.TestCase):
    def test_guidance_code_is_finite_and_unknown_values_are_rejected(self) -> None:
        self.assertEqual(
            set(GUIDANCE_CODES),
            {
                "MOVE_CLOSER",
                "MOVE_FARTHER",
                "CENTER_GARMENT",
                "SHOW_FULL_GARMENT",
                "WRONG_SIDE",
                "MOVE_TO_TAG",
                "PLACE_MARKER",
                "MARKER_NOT_VISIBLE",
                "FLATTEN_GARMENT",
                "CAMERA_OVERHEAD",
                "HOLD_STEADY",
                "READY",
                "AGENT_UNAVAILABLE",
            },
        )
        self.assertIs(validate_guidance_code("READY"), GuidanceCode.READY)
        for invalid in ("", "ready", "FREE_TEXT", None, 1):
            with self.subTest(invalid=invalid), self.assertRaises(GuidanceValidationError):
                validate_guidance_code(invalid)

    def test_provider_output_is_only_code_and_finite_confidence(self) -> None:
        decision = validate_vision_decision({"code": "READY", "confidence": 0.8})
        self.assertIs(decision.code, GuidanceCode.READY)
        self.assertEqual(decision.confidence, 0.8)

        invalid = (
            {"code": "READY", "confidence": math.nan},
            {"code": "READY", "confidence": 1.1},
            {"code": "UNKNOWN", "confidence": 0.8},
            {"code": "READY", "confidence": 0.8, "message": "provider copy"},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(GuidanceValidationError):
                validate_vision_decision(value)

    def test_input_is_one_downscaled_frame_and_current_shot(self) -> None:
        value = GuidanceInput.from_mapping(
            {
                "frame": {
                    "data": b"jpeg",
                    "mimeType": "image/jpeg",
                    "width": 320,
                    "height": 240,
                },
                "requestedShot": "front",
                "previousCode": "HOLD_STEADY",
            }
        )
        self.assertEqual(value.frame, EncodedImage(b"jpeg", width=320, height=240))
        self.assertEqual(value.requestedShot, "front")
        self.assertEqual(value.previousCode, "HOLD_STEADY")

        with self.assertRaises(GuidanceValidationError):
            GuidanceInput.from_mapping(
                {"frame": b"jpeg", "requestedShot": "front", "queue": [b"other"]}
            )


class GuidanceStateMachineTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_contract_is_async_and_validated_on_both_sides(self) -> None:
        provider = RecordingProvider({"code": "CENTER_GARMENT", "confidence": 0.75})
        self.assertIsInstance(provider, VisionGuidanceProvider)
        input_value = GuidanceInput(
            frame=EncodedImage(b"small-frame", width=320, height=240),
            requested_shot="front",  # type: ignore[arg-type]
            previous_code="HOLD_STEADY",  # type: ignore[arg-type]
        )
        machine = GuidanceStateMachine("session-provider", clock=lambda: 5_000)

        event = await machine.analyze(provider, input_value)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(provider.inputs, [input_value])
        self.assertIs(event.code, GuidanceCode.CENTER_GARMENT)
        self.assertNotEqual(event.message, "")

        invalid_provider = RecordingProvider(
            {"code": "CENTER_GARMENT", "confidence": 0.75, "message": "injected"}
        )
        with self.assertRaises(GuidanceValidationError):
            await machine.analyze(invalid_provider, input_value)

    def test_sequence_is_strictly_monotonic_and_session_scoped(self) -> None:
        clock = FakeClock()
        session_a = GuidanceStateMachine("session-a", clock=clock)
        session_b = GuidanceStateMachine("session-b", clock=clock)

        a1 = session_a.emit("front", VisionDecision("READY", 0.9))  # type: ignore[arg-type]
        b1 = session_b.emit("front", VisionDecision("READY", 0.9))  # type: ignore[arg-type]
        self.assertIsNotNone(a1)
        self.assertIsNotNone(b1)
        assert a1 is not None and b1 is not None
        self.assertEqual((a1.session_id, a1.sequence), ("session-a", 1))
        self.assertEqual((b1.session_id, b1.sequence), ("session-b", 1))

        clock.now_ms += 100
        reliable = session_a.resync()
        clock.now_ms += 100
        a2 = session_a.emit("front", VisionDecision("HOLD_STEADY", 0.8))  # type: ignore[arg-type]
        assert a2 is not None
        self.assertEqual([a1.sequence, reliable.sequence, a2.sequence], [1, 2, 3])

    def test_observed_expiry_and_wire_shape_use_epoch_milliseconds(self) -> None:
        machine = GuidanceStateMachine(
            "session-time", clock=lambda: 10_000, guidance_ttl_ms=1_500
        )
        event = machine.emit("tag", {"code": "MOVE_TO_TAG", "confidence": 0.6})
        assert event is not None
        self.assertEqual(event.observedAt, 10_000)
        self.assertEqual(event.expiresAt, 11_500)
        self.assertFalse(event.is_expired(11_499))
        self.assertTrue(event.is_expired(11_500))
        self.assertEqual(
            set(event.to_payload()),
            {
                "sessionId",
                "sequence",
                "shot",
                "code",
                "message",
                "confidence",
                "observedAt",
                "expiresAt",
            },
        )

    def test_same_shot_and_code_are_deduplicated_until_state_changes(self) -> None:
        clock = FakeClock(20_000)
        machine = GuidanceStateMachine("session-dedupe", clock=clock, guidance_ttl_ms=500)
        first = machine.emit("front", {"code": "READY", "confidence": 0.9})
        assert first is not None

        clock.now_ms = 30_000
        self.assertIsNone(
            machine.emit("front", {"code": "READY", "confidence": 0.1})
        )
        self.assertEqual(machine.sequence, first.sequence)

        changed_code = machine.emit(
            "front", {"code": "CENTER_GARMENT", "confidence": 0.8}
        )
        changed_shot = machine.emit(
            "back", {"code": "CENTER_GARMENT", "confidence": 0.8}
        )
        assert changed_code is not None and changed_shot is not None
        self.assertEqual([first.sequence, changed_code.sequence, changed_shot.sequence], [1, 2, 3])

    def test_guidance_is_lossy_and_shot_state_and_resync_are_reliable(self) -> None:
        machine = GuidanceStateMachine("session-transport", clock=lambda: 40_000)
        guidance = machine.emit("front", {"code": "READY", "confidence": 1.0})
        assert guidance is not None
        self.assertIs(guidance.transport, TransportKind.LOSSY)

        shot_state = machine.set_shot("back")
        snapshot = machine.resync()
        self.assertIs(shot_state.transport, TransportKind.RELIABLE)
        self.assertIs(snapshot.transport, TransportKind.RELIABLE)
        self.assertEqual(shot_state.kind, "shot_changed")
        self.assertEqual(snapshot.kind, "resync")
        self.assertEqual([guidance.sequence, shot_state.sequence, snapshot.sequence], [1, 2, 3])
        self.assertEqual(snapshot.to_payload()["shot"], "back")

    def test_invalid_state_timestamp_does_not_advance_sequence_or_change_shot(self) -> None:
        machine = GuidanceStateMachine("session-transaction", clock=lambda: 50_000)
        first = machine.set_shot("front")

        with self.assertRaises(GuidanceValidationError):
            machine.set_shot("back", observed_at=math.nan)

        snapshot = machine.resync()
        self.assertEqual([first.sequence, snapshot.sequence], [1, 2])
        self.assertEqual(snapshot.to_payload()["shot"], "front")


if __name__ == "__main__":
    unittest.main()
