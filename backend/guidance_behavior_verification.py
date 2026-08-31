"""Backend-only latency and behavior verification for live guidance.

The fixture command is a deterministic OpenSpec 9.5 regression gate::

    python -m backend.guidance_behavior_verification --mode fixture

It exercises the real capacity-one processor, guidance transport, and state
machine with synthetic frame identifiers.  No image bytes, credentials, or
provider response bodies are included in its JSON report.

Live measurement runs only when explicitly selected and ``OPENAI_API_KEY`` is
available::

    python -m backend.guidance_behavior_verification --mode live

Without credentials, live mode returns a finite ``skipped`` report.  With
credentials, the default 20-sample command is a required gate: one prewarmed
Realtime session, provider errors 0, and observed-to-publish p95 below 1,000ms.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, TextIO

from .guidance_state_machine import GuidanceStateMachine
from .guidance_transport import GuidanceTransportAdapter
from .live_agent import AgentRuntime, entrypoint
from .providers.runtime import LiveVisionGuidanceProvider
from .providers.vision_guidance import (
    EncodedImage,
    GuidanceInput,
    GuidanceShot,
    validate_vision_decision,
)
from .providers.vision_guidance_realtime import (
    DEFAULT_REALTIME_GUIDANCE_MODEL,
    OpenAIRealtimeVisionGuidanceAnalyzer,
)


FIXTURE_REQUIRED_P95_MS = 1_000.0
LIVE_TARGET_P95_MS = 1_000.0
LIVE_UPPER_P95_MS = 1_000.0
DEFAULT_LIVE_SAMPLES = 20
DEFAULT_LIVE_CALL_DEADLINE_SECONDS = 0.95


MetricClock = Callable[[], float]
Inference = Callable[["VerificationFrame"], Awaitable[object]]


@dataclass
class ManualMetricClock:
    """A deterministic millisecond clock used only by the fixture scenario."""

    now_ms: float = 10_000.0

    def __call__(self) -> float:
        return self.now_ms

    def advance(self, milliseconds: float) -> None:
        if milliseconds < 0 or not math.isfinite(milliseconds):
            raise ValueError("clock advance must be a finite non-negative number")
        self.now_ms += milliseconds


@dataclass(frozen=True, slots=True)
class VerificationFrame:
    """One non-sensitive fixture/live input tracked by an integer identifier."""

    frame_id: int
    shot: GuidanceShot
    fixture_code: str | None = None
    fixture_provider_latency_ms: float = 0.0
    guidance_input: GuidanceInput | None = None


@dataclass(slots=True)
class FrameTiming:
    frame_id: int
    shot: str
    observed_at: int
    observed_metric_ms: float
    provider_started_ms: float | None = None
    provider_completed_ms: float | None = None
    backend_published_ms: float | None = None
    decision_code: str | None = None
    provider_failed: bool = False

    def to_payload(self) -> dict[str, object]:
        return {
            "frameId": self.frame_id,
            "shot": self.shot,
            "observedAt": self.observed_at,
            "providerStartedAtMs": _rounded(self.provider_started_ms),
            "providerCompletedAtMs": _rounded(self.provider_completed_ms),
            "backendPublishedAtMs": _rounded(self.backend_published_ms),
            "queueWaitMs": _difference(
                self.provider_started_ms,
                self.observed_metric_ms,
            ),
            "providerLatencyMs": _difference(
                self.provider_completed_ms,
                self.provider_started_ms,
            ),
            "observedToProviderCompleteMs": _difference(
                self.provider_completed_ms,
                self.observed_metric_ms,
            ),
            "publishLatencyMs": _difference(
                self.backend_published_ms,
                self.provider_completed_ms,
            ),
            "observedToPublishMs": _difference(
                self.backend_published_ms,
                self.observed_metric_ms,
            ),
            "decisionCode": self.decision_code,
            "displayEventPublished": self.backend_published_ms is not None,
            "providerFailed": self.provider_failed,
        }


@dataclass
class _Publisher:
    timings_by_observed_at: dict[int, FrameTiming]
    metric_clock: MetricClock
    fixture_clock: ManualMetricClock | None = None
    state_publish_latency_ms: float = 5.0
    display_publish_latency_ms: float = 15.0
    calls: list[tuple[dict[str, object], bool]] = field(default_factory=list)

    async def publish_data(self, payload: bytes, *, reliable: bool = True) -> None:
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise TypeError("guidance packet must be a JSON object")

        is_display_event = "type" not in decoded
        if self.fixture_clock is not None:
            self.fixture_clock.advance(
                self.display_publish_latency_ms
                if is_display_event
                else self.state_publish_latency_ms
            )
        self.calls.append((decoded, reliable))

        if is_display_event:
            observed_at = decoded.get("observedAt")
            if not isinstance(observed_at, int) or isinstance(observed_at, bool):
                raise TypeError("guidance packet observedAt must be an integer")
            timing = self.timings_by_observed_at.get(observed_at)
            if timing is None:
                raise RuntimeError("guidance packet has no matching observed frame")
            timing.backend_published_ms = self.metric_clock()


@dataclass
class _Room:
    local_participant: _Publisher
    name: str
    remote_participants: list[object] = field(default_factory=list)
    handlers: dict[str, Any] = field(default_factory=dict)

    def on(self, event: str, callback: Any) -> None:
        self.handlers[event] = callback


class _Context:
    def __init__(self, room: _Room) -> None:
        self.room = room
        self.shutdown_callbacks: list[Any] = []

    async def connect(self, **_kwargs: Any) -> None:
        return None

    def add_shutdown_callback(self, callback: Any) -> None:
        self.shutdown_callbacks.append(callback)


@dataclass
class _InferenceMetrics:
    metric_clock: MetricClock
    delegate: Inference
    timings: dict[int, FrameTiming]
    active: int = 0
    max_active: int = 0
    provider_order: list[int] = field(default_factory=list)

    async def __call__(self, frame: VerificationFrame) -> object:
        timing = self.timings[frame.frame_id]
        timing.provider_started_ms = self.metric_clock()
        self.provider_order.append(frame.frame_id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            result = await self.delegate(frame)
            decision = validate_vision_decision(result)
            timing.decision_code = decision.code.value
            return decision
        except BaseException:
            timing.provider_failed = True
            raise
        finally:
            timing.provider_completed_ms = self.metric_clock()
            self.active -= 1


@dataclass
class _RunResult:
    runtime: AgentRuntime
    publisher: _Publisher
    inference: _InferenceMetrics
    timings: dict[int, FrameTiming]
    submitted_count: int


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def _difference(later: float | None, earlier: float | None) -> float | None:
    if later is None or earlier is None:
        return None
    return _rounded(max(0.0, later - earlier))


def _nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return _rounded(ordered[index])


def _distribution(values: Sequence[float]) -> dict[str, object]:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return {
        "count": len(finite),
        "minMs": _rounded(min(finite)) if finite else None,
        "p50Ms": _nearest_rank(finite, 0.50),
        "p95Ms": _nearest_rank(finite, 0.95),
        "maxMs": _rounded(max(finite)) if finite else None,
    }


def _metric_values(
    timings: Sequence[FrameTiming],
    field_name: str,
) -> list[float]:
    payloads = [timing.to_payload() for timing in timings]
    return [
        float(value)
        for payload in payloads
        if isinstance((value := payload[field_name]), (int, float))
        and not isinstance(value, bool)
    ]


def _targets() -> dict[str, object]:
    return {
        "comparison": "p95 strictly less than threshold",
        "fixtureRequiredP95Ms": FIXTURE_REQUIRED_P95_MS,
        "liveTargetP95Ms": LIVE_TARGET_P95_MS,
        "liveUpperP95Ms": LIVE_UPPER_P95_MS,
    }


def _empty_report(mode: str, status: str, reason_code: str) -> dict[str, object]:
    empty = _distribution([])
    return {
        "schemaVersion": 1,
        "mode": mode,
        "status": status,
        "reasonCode": reason_code,
        "targets": _targets(),
        "counts": {
            "framesSubmitted": 0,
            "framesProcessed": 0,
            "framesDropped": 0,
            "providerCalls": 0,
            "providerErrors": 0,
            "guidanceDisplayEvents": 0,
            "duplicateDisplayEventsSuppressed": 0,
        },
        "concurrency": {"maxInFlight": 0, "maxPendingDepth": 0},
        "latencyMs": {
            "queueWait": empty,
            "provider": empty,
            "observedToProviderComplete": empty,
            "publish": empty,
            "observedToPublish": empty,
        },
        "behavior": {
            "capacityOnePass": False,
            "singleInferencePass": False,
            "sameDecisionNonResendPass": False,
        },
        "evaluation": {
            "fixtureRequiredPass": None,
            "liveTargetPass": None,
            "liveUpperPass": None,
        },
        "frames": [],
    }


def _build_report(
    *,
    mode: str,
    run: _RunResult,
    status_override: str | None = None,
    reason_code: str | None = None,
) -> dict[str, object]:
    processor = run.runtime.subscriber.processor
    processed = [
        run.timings[frame_id]
        for frame_id in run.inference.provider_order
        if run.timings[frame_id].provider_completed_ms is not None
    ]
    display_events = [
        payload
        for payload, _reliable in run.publisher.calls
        if "type" not in payload
    ]

    previous_by_shot: dict[str, str] = {}
    duplicate_suppressed = 0
    dedupe_valid = True
    for timing in processed:
        if timing.provider_failed or timing.decision_code is None:
            continue
        previous = previous_by_shot.get(timing.shot)
        should_publish = previous != timing.decision_code
        did_publish = timing.backend_published_ms is not None
        if previous == timing.decision_code and not did_publish:
            duplicate_suppressed += 1
        if should_publish != did_publish:
            dedupe_valid = False
        previous_by_shot[timing.shot] = timing.decision_code

    latency = {
        "queueWait": _distribution(_metric_values(processed, "queueWaitMs")),
        "provider": _distribution(_metric_values(processed, "providerLatencyMs")),
        "observedToProviderComplete": _distribution(
            _metric_values(processed, "observedToProviderCompleteMs")
        ),
        "publish": _distribution(_metric_values(processed, "publishLatencyMs")),
        "observedToPublish": _distribution(
            _metric_values(processed, "observedToPublishMs")
        ),
    }
    end_to_end_p95 = latency["observedToPublish"]["p95Ms"]
    has_end_to_end = isinstance(end_to_end_p95, (int, float))
    fixture_latency_pass = bool(
        has_end_to_end and float(end_to_end_p95) < FIXTURE_REQUIRED_P95_MS
    )
    live_target_pass = bool(
        has_end_to_end and float(end_to_end_p95) < LIVE_TARGET_P95_MS
    )
    live_upper_pass = bool(
        has_end_to_end and float(end_to_end_p95) < LIVE_UPPER_P95_MS
    )

    capacity_pass = processor.max_pending <= 1
    single_inference_pass = (
        processor.max_in_flight <= 1 and run.inference.max_active <= 1
    )
    provider_errors = sum(timing.provider_failed for timing in processed)
    fixture_behavior_pass = (
        capacity_pass
        and single_inference_pass
        and dedupe_valid
        and provider_errors == 0
        and processor.error_count == 0
    )
    if status_override is not None:
        status = status_override
    elif mode == "fixture":
        status = "passed" if fixture_latency_pass and fixture_behavior_pass else "failed"
    else:
        status = "completed" if provider_errors == 0 else "completed_with_errors"

    return {
        "schemaVersion": 1,
        "mode": mode,
        "status": status,
        "reasonCode": reason_code,
        "targets": _targets(),
        "counts": {
            "framesSubmitted": run.submitted_count,
            "framesProcessed": len(processed),
            "framesDropped": processor.dropped_count,
            "providerCalls": len(run.inference.provider_order),
            "providerErrors": provider_errors,
            "guidanceDisplayEvents": len(display_events),
            "duplicateDisplayEventsSuppressed": duplicate_suppressed,
        },
        "concurrency": {
            "maxInFlight": max(processor.max_in_flight, run.inference.max_active),
            "maxPendingDepth": processor.max_pending,
        },
        "latencyMs": latency,
        "behavior": {
            "capacityOnePass": capacity_pass,
            "singleInferencePass": single_inference_pass,
            "sameDecisionNonResendPass": dedupe_valid,
        },
        "evaluation": {
            "fixtureRequiredPass": fixture_latency_pass if mode == "fixture" else None,
            "liveTargetPass": live_target_pass if mode == "live" else None,
            "liveUpperPass": live_upper_pass if mode == "live" else None,
        },
        "frames": [timing.to_payload() for timing in processed],
    }


async def _start_runtime(
    *,
    session_id: str,
    inference: _InferenceMetrics,
    publisher: _Publisher,
    observation_clock: Callable[[], int],
) -> AgentRuntime:
    room = _Room(publisher, name=session_id)

    def transport_factory(
        _room: _Room,
        _current_shot: Callable[[], str],
    ) -> GuidanceTransportAdapter:
        return GuidanceTransportAdapter(
            inference,
            publisher,
            state_machine=GuidanceStateMachine(
                session_id,
                clock=observation_clock,
                # This verifier measures one-frame provider/publish latency.
                # Production READY hardening is covered separately by the
                # default state-machine and transport contract tests.
                ready_confirmation_count=1,
            ),
        )

    return await entrypoint(
        _Context(room),
        inference=inference,
        transport_factory=transport_factory,
        observation_clock=observation_clock,
    )


async def run_fixture_verification(
    *,
    provider_latencies_ms: Sequence[float] = (120, 180, 220, 160, 200, 140),
) -> dict[str, object]:
    """Run the deterministic fixture gate and return its finite JSON payload."""

    if len(provider_latencies_ms) != 6 or any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
        for value in provider_latencies_ms
    ):
        raise ValueError("fixture verification requires six finite non-negative latencies")

    clock = ManualMetricClock()
    first_provider_started = asyncio.Event()
    release_first_provider = asyncio.Event()
    frames = [
        VerificationFrame(1, GuidanceShot.FRONT, "READY", provider_latencies_ms[0]),
        VerificationFrame(2, GuidanceShot.FRONT, "READY", provider_latencies_ms[1]),
        VerificationFrame(3, GuidanceShot.FRONT, "READY", provider_latencies_ms[2]),
        VerificationFrame(
            4,
            GuidanceShot.FRONT,
            "HOLD_STEADY",
            provider_latencies_ms[3],
        ),
        VerificationFrame(
            5,
            GuidanceShot.FRONT,
            "HOLD_STEADY",
            provider_latencies_ms[4],
        ),
        VerificationFrame(6, GuidanceShot.FRONT, "READY", provider_latencies_ms[5]),
    ]
    timings: dict[int, FrameTiming] = {}
    timings_by_observed_at: dict[int, FrameTiming] = {}
    current_observed_at = [int(clock())]

    async def fixture_provider(frame: VerificationFrame) -> object:
        if frame.frame_id == 1:
            first_provider_started.set()
            await release_first_provider.wait()
        clock.advance(frame.fixture_provider_latency_ms)
        return {"code": frame.fixture_code, "confidence": 1.0}

    measured_inference = _InferenceMetrics(clock, fixture_provider, timings)
    publisher = _Publisher(
        timings_by_observed_at,
        clock,
        fixture_clock=clock,
    )
    runtime = await _start_runtime(
        session_id="guidance-fixture-verification",
        inference=measured_inference,
        publisher=publisher,
        observation_clock=lambda: current_observed_at[0],
    )
    processor = runtime.subscriber.processor
    submitted_count = 0

    def submit(frame: VerificationFrame) -> None:
        nonlocal submitted_count
        observed_at = int(clock())
        current_observed_at[0] = observed_at
        timing = FrameTiming(
            frame_id=frame.frame_id,
            shot=frame.shot.value,
            observed_at=observed_at,
            observed_metric_ms=clock(),
        )
        timings[frame.frame_id] = timing
        timings_by_observed_at[observed_at] = timing
        if not processor.submit_nowait(frame):
            raise RuntimeError("fixture processor rejected an open-session frame")
        submitted_count += 1

    try:
        # Force one pending replacement while the first inference is in flight.
        submit(frames[0])
        await asyncio.wait_for(first_provider_started.wait(), timeout=1)
        clock.advance(5)
        submit(frames[1])
        clock.advance(5)
        submit(frames[2])
        release_first_provider.set()
        await asyncio.wait_for(processor.wait_idle(), timeout=1)

        # The remaining frames exercise code changes and identical-code dedupe.
        for frame in frames[3:]:
            clock.advance(25)
            submit(frame)
            await asyncio.wait_for(processor.wait_idle(), timeout=1)
    finally:
        await runtime.close()

    return _build_report(
        mode="fixture",
        run=_RunResult(
            runtime=runtime,
            publisher=publisher,
            inference=measured_inference,
            timings=timings,
            submitted_count=submitted_count,
        ),
    )


def _synthetic_guidance_image() -> EncodedImage:
    """Create one anonymous image without reading user or repository content."""

    from PIL import Image, ImageDraw

    output = BytesIO()
    image = Image.new("RGB", (96, 96), (232, 232, 232))
    draw = ImageDraw.Draw(image)
    draw.rectangle((24, 18, 72, 82), fill=(96, 112, 144))
    image.save(output, format="PNG")
    return EncodedImage(output.getvalue(), "image/png", width=96, height=96)


def _positive_float(value: object, *, default: float) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return default
    return converted if math.isfinite(converted) and converted > 0 else default


def _epoch_metric_clock() -> MetricClock:
    """Return epoch milliseconds advanced by a monotonic elapsed-time source."""

    epoch_origin_ms = time.time_ns() / 1_000_000
    monotonic_origin_ms = time.perf_counter_ns() / 1_000_000

    def clock() -> float:
        monotonic_now_ms = time.perf_counter_ns() / 1_000_000
        return epoch_origin_ms + (monotonic_now_ms - monotonic_origin_ms)

    return clock


async def run_live_verification(
    *,
    environ: Mapping[str, str] | None = None,
    samples: int = DEFAULT_LIVE_SAMPLES,
    live_analyzer: Callable[[GuidanceInput], Awaitable[object]] | None = None,
) -> dict[str, object]:
    """Measure the optional live analyzer without exposing credentials/images."""

    if (
        isinstance(samples, bool)
        or not isinstance(samples, int)
        or not 1 <= samples <= 100
    ):
        raise ValueError("live sample count must be an integer between 1 and 100")
    env = os.environ if environ is None else environ
    api_key = env.get("OPENAI_API_KEY", "").strip()
    if not api_key and live_analyzer is None:
        return _empty_report("live", "skipped", "LIVE_CREDENTIALS_UNAVAILABLE")

    owned_provider: LiveVisionGuidanceProvider | None = None
    realtime_analyzer: OpenAIRealtimeVisionGuidanceAnalyzer | None = None
    cold_start_ms: float | None = None
    analyzer = live_analyzer
    if analyzer is None:
        try:
            from openai import AsyncOpenAI

            model = (
                env.get("VISION_GUIDANCE_MODEL", "").strip()
                or DEFAULT_REALTIME_GUIDANCE_MODEL
            )
            reasoning_effort = env.get(
                "VISION_GUIDANCE_REASONING_EFFORT", ""
            ).strip() or None
            if reasoning_effort is None and model.startswith("gpt-realtime-2"):
                reasoning_effort = "minimal"
            realtime_analyzer = OpenAIRealtimeVisionGuidanceAnalyzer(
                AsyncOpenAI(api_key=api_key),
                model,
                response_timeout_seconds=_positive_float(
                    env.get("VISION_GUIDANCE_RESPONSE_TIMEOUT_SECONDS"),
                    default=0.90,
                ),
                connect_timeout_seconds=_positive_float(
                    env.get("VISION_GUIDANCE_CONNECT_TIMEOUT_SECONDS"),
                    default=8.0,
                ),
                prewarm_timeout_seconds=_positive_float(
                    env.get("VISION_GUIDANCE_PREWARM_TIMEOUT_SECONDS"),
                    default=8.0,
                ),
                image_max_edge=int(env.get("VISION_GUIDANCE_IMAGE_MAX_EDGE", "256")),
                jpeg_quality=int(env.get("VISION_GUIDANCE_JPEG_QUALITY", "55")),
                max_output_tokens=int(
                    env.get(
                        "VISION_GUIDANCE_MAX_OUTPUT_TOKENS",
                        "64" if model.startswith("gpt-realtime-2") else "32",
                    )
                ),
                reasoning_effort=reasoning_effort,
            )
            owned_provider = LiveVisionGuidanceProvider(realtime_analyzer)
            cold_started = time.perf_counter()
            await owned_provider.prewarm()
            cold_start_ms = (time.perf_counter() - cold_started) * 1_000
            analyzer = owned_provider.analyze
        except Exception:
            if owned_provider is not None:
                await owned_provider.aclose()
            return _empty_report("live", "completed_with_errors", "LIVE_PROVIDER_UNAVAILABLE")

    # Keep the externally visible observedAt and the provider/publish markers
    # on one epoch-millisecond timeline, while deriving elapsed time from a
    # monotonic clock so wall-clock corrections cannot corrupt the samples.
    metric_clock = _epoch_metric_clock()
    current_observed_at = [int(metric_clock())]
    deadline_seconds = _positive_float(
        env.get("GUIDANCE_LIVE_VERIFICATION_DEADLINE_SECONDS"),
        default=DEFAULT_LIVE_CALL_DEADLINE_SECONDS,
    )
    image = _synthetic_guidance_image()
    shots = tuple(GuidanceShot)
    frames = [
        VerificationFrame(
            frame_id=index + 1,
            shot=shots[index % len(shots)],
            guidance_input=GuidanceInput(
                frame=image,
                requested_shot=shots[index % len(shots)],
            ),
        )
        for index in range(samples)
    ]
    timings: dict[int, FrameTiming] = {}
    timings_by_observed_at: dict[int, FrameTiming] = {}

    async def provider(frame: VerificationFrame) -> object:
        if frame.guidance_input is None or analyzer is None:
            raise RuntimeError("live verification frame is not configured")
        return await asyncio.wait_for(
            analyzer(frame.guidance_input),
            timeout=deadline_seconds,
        )

    measured_inference = _InferenceMetrics(metric_clock, provider, timings)
    publisher = _Publisher(timings_by_observed_at, metric_clock)
    runtime = await _start_runtime(
        session_id="guidance-live-verification",
        inference=measured_inference,
        publisher=publisher,
        observation_clock=lambda: current_observed_at[0],
    )
    processor = runtime.subscriber.processor
    submitted_count = 0
    try:
        for frame in frames:
            # A shot change resets display dedupe, yielding an end-to-end publish
            # latency sample for every optional live provider call.
            await runtime.set_shot(frame.shot)
            current_observed_at[0] = int(metric_clock())
            timing = FrameTiming(
                frame_id=frame.frame_id,
                shot=frame.shot.value,
                observed_at=current_observed_at[0],
                observed_metric_ms=metric_clock(),
            )
            timings[frame.frame_id] = timing
            timings_by_observed_at[current_observed_at[0]] = timing
            if not processor.submit_nowait(frame):
                raise RuntimeError("live processor rejected an open-session frame")
            submitted_count += 1
            await processor.wait_idle()
    finally:
        await runtime.close()
        if owned_provider is not None:
            await owned_provider.aclose()

    report = _build_report(
        mode="live",
        run=_RunResult(
            runtime=runtime,
            publisher=publisher,
            inference=measured_inference,
            timings=timings,
            submitted_count=submitted_count,
        ),
    )
    report["realtimeSession"] = {
        "model": None if realtime_analyzer is None else realtime_analyzer.model,
        "coldStartMs": _rounded(cold_start_ms),
        "connectCount": (
            None if realtime_analyzer is None else realtime_analyzer.connect_count
        ),
        "requestCount": (
            None if realtime_analyzer is None else realtime_analyzer.request_count
        ),
        "prewarmed": None if realtime_analyzer is None else True,
    }
    if samples >= 20:
        provider_errors = report["counts"]["providerErrors"]
        publish_p95 = report["latencyMs"]["publish"]["p95Ms"]
        required_pass = (
            provider_errors == 0
            and report["evaluation"]["liveTargetPass"] is True
            and isinstance(publish_p95, (int, float))
            and float(publish_p95) < 50.0
            and report["counts"]["framesProcessed"] == samples
        )
        report["status"] = "passed" if required_pass else "failed"
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.guidance_behavior_verification"
    )
    parser.add_argument(
        "--mode",
        choices=("fixture", "live"),
        default=None,
        help="fixture is the required deterministic gate; live is optional",
    )
    parser.add_argument(
        "--live-samples",
        type=int,
        default=None,
        help="number of live provider calls (1-100; default 20)",
    )
    parser.add_argument("--pretty", action="store_true", help="indent JSON output")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    live_analyzer: Callable[[GuidanceInput], Awaitable[object]] | None = None,
) -> int:
    env = os.environ if environ is None else environ
    args = build_parser().parse_args(argv)
    mode = args.mode or env.get("GUIDANCE_VERIFICATION_MODE", "fixture").strip()
    if mode not in {"fixture", "live"}:
        raise SystemExit("GUIDANCE_VERIFICATION_MODE must be fixture or live")

    if args.live_samples is not None:
        samples = args.live_samples
    else:
        try:
            samples = int(env.get("GUIDANCE_LIVE_VERIFICATION_SAMPLES", DEFAULT_LIVE_SAMPLES))
        except ValueError:
            samples = DEFAULT_LIVE_SAMPLES

    report = (
        asyncio.run(run_fixture_verification())
        if mode == "fixture"
        else asyncio.run(
            run_live_verification(
                environ=env,
                samples=samples,
                live_analyzer=live_analyzer,
            )
        )
    )
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if args.pretty else None,
            allow_nan=False,
        ),
        file=stdout or sys.stdout,
    )
    if mode == "fixture":
        return 1 if report["status"] != "passed" else 0
    if report["status"] == "skipped":
        return 0
    return 1 if samples >= 20 and report["status"] != "passed" else 0


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())


__all__ = [
    "DEFAULT_LIVE_CALL_DEADLINE_SECONDS",
    "DEFAULT_LIVE_SAMPLES",
    "FIXTURE_REQUIRED_P95_MS",
    "LIVE_TARGET_P95_MS",
    "LIVE_UPPER_P95_MS",
    "build_parser",
    "main",
    "run_fixture_verification",
    "run_live_verification",
]
