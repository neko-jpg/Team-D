"""Concurrency, precedence, and lifecycle tests for hybrid live guidance."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from io import BytesIO
import time

import pytest
from PIL import Image

from backend.providers.hybrid_vision_guidance import HybridVisionGuidanceAnalyzer
from backend.providers.vision_guidance import (
    EncodedImage,
    GuidanceCode,
    GuidanceContractError,
    GuidanceInput,
    GuidanceShot,
    VisionDecision,
)


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 32), "white").save(output, "PNG")
    return output.getvalue()


def _input(shot: GuidanceShot = GuidanceShot.FRONT) -> GuidanceInput:
    return GuidanceInput(EncodedImage(_png(), "image/png", 32, 32), shot)


class Geometry:
    def __init__(
        self,
        result: VisionDecision | None = None,
        *,
        error: Exception | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.gate = gate
        self.calls = 0
        self.prewarm_calls = 0
        self.close_calls = 0

    async def analyze_geometry(
        self, _input_value: GuidanceInput
    ) -> VisionDecision | None:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.result

    async def prewarm(self) -> None:
        self.prewarm_calls += 1

    async def aclose(self) -> None:
        self.close_calls += 1

    def new_session(self) -> "Geometry":
        return Geometry(self.result, error=self.error)


class Semantic:
    def __init__(
        self,
        result: VisionDecision,
        *,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.result = result
        self.gate = gate
        self.calls = 0
        self.started = asyncio.Event()
        self.cancelled = False
        self.prewarm_calls = 0
        self.close_calls = 0

    async def __call__(self, _input_value: GuidanceInput) -> VisionDecision:
        self.calls += 1
        self.started.set()
        try:
            if self.gate is not None:
                await self.gate.wait()
            return self.result
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def prewarm(self) -> None:
        self.prewarm_calls += 1

    async def aclose(self) -> None:
        self.close_calls += 1

    def new_session(self) -> "Semantic":
        return Semantic(self.result)


class CancellationResistantSemantic(Semantic):
    """Model a client whose response task and socket close both stall."""

    def __init__(self) -> None:
        super().__init__(VisionDecision(GuidanceCode.READY, 1.0))
        self.cancel_seen = asyncio.Event()
        self.close_started = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()

    async def __call__(self, _input_value: GuidanceInput) -> VisionDecision:
        self.calls += 1
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            self.cancel_seen.set()
            # Deliberately resist cancellation until an external release.
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    continue
        finally:
            self.finished.set()
        raise asyncio.CancelledError

    async def aclose(self) -> None:
        self.close_calls += 1
        self.close_started.set()
        await self.release.wait()


class CancellationResistantGeometry(Geometry):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()

    async def analyze_geometry(
        self, _input_value: GuidanceInput
    ) -> VisionDecision | None:
        self.calls += 1
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    continue
        finally:
            self.finished.set()
        raise asyncio.CancelledError


class FailingPrewarmGeometry(Geometry):
    def __init__(self, peer_started: asyncio.Event) -> None:
        super().__init__()
        self.peer_started = peer_started

    async def prewarm(self) -> None:
        self.prewarm_calls += 1
        await self.peer_started.wait()
        raise RuntimeError("geometry prewarm failed")


class ResistantPrewarmSemantic(Semantic):
    def __init__(self) -> None:
        super().__init__(VisionDecision(GuidanceCode.READY, 1.0))
        self.prewarm_started = asyncio.Event()
        self.prewarm_cancelled = asyncio.Event()
        self.release = asyncio.Event()

    async def prewarm(self) -> None:
        self.prewarm_calls += 1
        self.prewarm_started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.prewarm_cancelled.set()
            await self.release.wait()

    async def aclose(self) -> None:
        self.close_calls += 1
        self.release.set()


class FailingCloseGeometry(Geometry):
    async def aclose(self) -> None:
        self.close_calls += 1
        raise RuntimeError("geometry close failed")


class FailingCloseSemantic(Semantic):
    async def aclose(self) -> None:
        self.close_calls += 1
        raise ValueError("semantic close failed")


class FailingRecoveryCloseSemantic(CancellationResistantSemantic):
    async def aclose(self) -> None:
        self.close_calls += 1
        raise RuntimeError("private recovery close detail")


class ResistantCloseGeometry(Geometry):
    def __init__(self) -> None:
        super().__init__()
        self.close_started = asyncio.Event()
        self.close_finished = asyncio.Event()
        self.release = asyncio.Event()

    async def aclose(self) -> None:
        self.close_calls += 1
        self.close_started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    continue
        finally:
            self.close_finished.set()


class GatedCloseGeometry(Geometry):
    def __init__(self) -> None:
        super().__init__()
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()

    async def aclose(self) -> None:
        self.close_calls += 1
        self.close_started.set()
        await self.release_close.wait()


class NoFactoryGeometry(Geometry):
    new_session = None  # type: ignore[assignment]


class NoFactorySemantic(Semantic):
    new_session = None  # type: ignore[assignment]


class NoFactoryCancellationResistantSemantic(CancellationResistantSemantic):
    new_session = None  # type: ignore[assignment]


def test_front_geometry_and_semantic_start_together_and_correction_wins() -> None:
    async def run() -> None:
        geometry_gate = asyncio.Event()
        semantic_gate = asyncio.Event()
        geometry = Geometry(
            VisionDecision(GuidanceCode.MOVE_FARTHER, 1.0), gate=geometry_gate
        )
        semantic = Semantic(
            VisionDecision(GuidanceCode.READY, 1.0), gate=semantic_gate
        )
        analyzer = HybridVisionGuidanceAnalyzer(geometry, semantic)

        pending = asyncio.create_task(analyzer(_input()))
        await asyncio.wait_for(semantic.started.wait(), 0.2)
        assert geometry.calls == semantic.calls == 1
        geometry_gate.set()
        decision = await asyncio.wait_for(pending, 0.2)

        assert decision.code is GuidanceCode.MOVE_FARTHER
        assert semantic.cancelled

    asyncio.run(run())


def test_geometry_pass_selects_semantic_result() -> None:
    geometry = Geometry(None)
    semantic = Semantic(VisionDecision(GuidanceCode.WRONG_SIDE, 0.9))
    decision = asyncio.run(HybridVisionGuidanceAnalyzer(geometry, semantic)(_input()))

    assert decision == VisionDecision(GuidanceCode.WRONG_SIDE, 0.9)
    assert geometry.calls == semantic.calls == 1
    assert not semantic.cancelled


def test_geometry_failure_is_fail_closed_and_cancels_semantic() -> None:
    async def run() -> None:
        semantic = Semantic(
            VisionDecision(GuidanceCode.READY, 1.0), gate=asyncio.Event()
        )
        analyzer = HybridVisionGuidanceAnalyzer(
            Geometry(error=RuntimeError("mask unavailable")), semantic
        )

        with pytest.raises(RuntimeError, match="mask unavailable"):
            await analyzer(_input())
        assert semantic.cancelled

    asyncio.run(run())


@pytest.mark.parametrize(
    ("geometry", "expected_error"),
    [
        (Geometry(VisionDecision(GuidanceCode.MOVE_FARTHER, 1.0)), None),
        (Geometry(error=RuntimeError("mask unavailable")), RuntimeError),
    ],
)
def test_cancellation_resistant_semantic_cannot_break_one_second_deadline(
    geometry: Geometry,
    expected_error: type[Exception] | None,
) -> None:
    async def run() -> None:
        semantic = CancellationResistantSemantic()
        analyzer = HybridVisionGuidanceAnalyzer(geometry, semantic)
        started = time.perf_counter()
        try:
            if expected_error is None:
                decision = await asyncio.wait_for(analyzer(_input()), 0.8)
                assert decision.code is GuidanceCode.MOVE_FARTHER
            else:
                with pytest.raises(expected_error, match="mask unavailable"):
                    await asyncio.wait_for(analyzer(_input()), 0.8)
            elapsed = time.perf_counter() - started

            assert elapsed < 0.8
            assert semantic.cancel_seen.is_set()
            assert semantic.close_started.is_set()
            with pytest.raises(RuntimeError, match="semantic guidance is unavailable"):
                await analyzer(_input())
        finally:
            semantic.release.set()
            await asyncio.wait_for(semantic.finished.wait(), 0.2)

    asyncio.run(run())


def test_semantic_cannot_reintroduce_front_geometry_after_pass() -> None:
    analyzer = HybridVisionGuidanceAnalyzer(
        Geometry(None),
        Semantic(VisionDecision(GuidanceCode.CENTER_GARMENT, 1.0)),
    )
    with pytest.raises(
        GuidanceContractError, match="not valid semantic model guidance"
    ):
        asyncio.run(analyzer(_input()))


@pytest.mark.parametrize("shot", [GuidanceShot.TAG, GuidanceShot.MEASUREMENT])
def test_non_geometry_shots_bypass_local_mask(shot: GuidanceShot) -> None:
    geometry = Geometry(error=AssertionError("must not run"))
    semantic = Semantic(VisionDecision(GuidanceCode.READY, 1.0))
    decision = asyncio.run(
        HybridVisionGuidanceAnalyzer(geometry, semantic)(_input(shot))
    )

    assert decision.code is GuidanceCode.READY
    assert geometry.calls == 0
    assert semantic.calls == 1


def test_prewarm_close_and_new_session_cover_both_components_once() -> None:
    geometry = Geometry(None)
    semantic = Semantic(VisionDecision(GuidanceCode.READY, 1.0))
    analyzer = HybridVisionGuidanceAnalyzer(geometry, semantic)

    asyncio.run(analyzer.prewarm())
    isolated = analyzer.new_session()
    asyncio.run(analyzer.aclose())
    asyncio.run(analyzer.aclose())

    assert geometry.prewarm_calls == semantic.prewarm_calls == 1
    assert geometry.close_calls == semantic.close_calls == 1
    assert isolated is not analyzer
    assert isolated.geometry is not geometry
    assert isolated.semantic is not semantic


def test_prewarm_failure_finitely_drains_peer_and_closes_both_components() -> None:
    async def run() -> None:
        semantic = ResistantPrewarmSemantic()
        geometry = FailingPrewarmGeometry(semantic.prewarm_started)
        analyzer = HybridVisionGuidanceAnalyzer(geometry, semantic)

        with pytest.raises(RuntimeError, match="geometry prewarm failed"):
            await asyncio.wait_for(analyzer.prewarm(), 0.8)

        assert semantic.prewarm_cancelled.is_set()
        assert geometry.close_calls == semantic.close_calls == 1
        with pytest.raises(RuntimeError, match="session is closed"):
            await analyzer(_input())
        # Cleanup was already attempted for both components; retry is a no-op.
        await analyzer.aclose()
        assert geometry.close_calls == semantic.close_calls == 1

    asyncio.run(run())


def test_close_attempts_both_components_before_propagating_one_failure() -> None:
    async def run() -> None:
        geometry = FailingCloseGeometry()
        semantic = Semantic(VisionDecision(GuidanceCode.READY, 1.0))
        analyzer = HybridVisionGuidanceAnalyzer(geometry, semantic)

        with pytest.raises(RuntimeError, match="geometry close failed"):
            await analyzer.aclose()

        assert geometry.close_calls == semantic.close_calls == 1
        # Later callers join the same completed close and see the same failure.
        with pytest.raises(RuntimeError, match="geometry close failed"):
            await analyzer.aclose()
        assert geometry.close_calls == semantic.close_calls == 1

    asyncio.run(run())


def test_close_deadline_is_finite_even_when_one_component_resists_cancel() -> None:
    async def run() -> None:
        geometry = ResistantCloseGeometry()
        semantic = Semantic(VisionDecision(GuidanceCode.READY, 1.0))
        analyzer = HybridVisionGuidanceAnalyzer(geometry, semantic)
        started = time.perf_counter()
        try:
            with pytest.raises(TimeoutError, match="close exceeded its deadline"):
                await asyncio.wait_for(analyzer.aclose(), 0.8)

            assert time.perf_counter() - started < 0.8
            assert geometry.close_started.is_set()
            assert semantic.close_calls == 1
        finally:
            geometry.release.set()
            await asyncio.wait_for(geometry.close_finished.wait(), 0.2)

    asyncio.run(run())


def test_tag_parent_timeout_stays_below_one_second_and_closes_session() -> None:
    async def run() -> None:
        semantic = CancellationResistantSemantic()
        analyzer = HybridVisionGuidanceAnalyzer(Geometry(None), semantic)
        started = time.perf_counter()
        try:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(analyzer(_input(GuidanceShot.TAG)), 0.95)
            elapsed = time.perf_counter() - started

            assert elapsed < 1.0
            assert semantic.cancel_seen.is_set()
            with pytest.raises(RuntimeError, match="session is closed"):
                await analyzer(_input(GuidanceShot.TAG))
        finally:
            semantic.release.set()
            await asyncio.wait_for(semantic.finished.wait(), 0.2)
            await analyzer.aclose()

    asyncio.run(run())


def test_geometry_phase_parent_cancel_detaches_children_and_forbids_reuse() -> None:
    async def run() -> None:
        geometry = CancellationResistantGeometry()
        semantic = CancellationResistantSemantic()
        analyzer = HybridVisionGuidanceAnalyzer(geometry, semantic)
        request = asyncio.create_task(analyzer(_input()))
        await asyncio.wait_for(geometry.started.wait(), 0.2)
        await asyncio.wait_for(semantic.started.wait(), 0.2)

        started = time.perf_counter()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert time.perf_counter() - started < 0.05
        with pytest.raises(RuntimeError, match="session is closed"):
            await analyzer(_input())

        geometry.release.set()
        semantic.release.set()
        await asyncio.wait_for(geometry.finished.wait(), 0.2)
        await asyncio.wait_for(semantic.finished.wait(), 0.2)
        await analyzer.aclose()

    asyncio.run(run())


def test_concurrent_and_cancelled_close_callers_share_one_close_task() -> None:
    async def run() -> None:
        geometry = GatedCloseGeometry()
        semantic = Semantic(VisionDecision(GuidanceCode.READY, 1.0))
        analyzer = HybridVisionGuidanceAnalyzer(geometry, semantic)

        cancelled_caller = asyncio.create_task(analyzer.aclose())
        await asyncio.wait_for(geometry.close_started.wait(), 0.2)
        waiting_caller = asyncio.create_task(analyzer.aclose())
        await asyncio.sleep(0)
        assert not waiting_caller.done()
        assert geometry.close_calls == semantic.close_calls == 1

        cancelled_caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_caller
        assert not waiting_caller.done()

        geometry.release_close.set()
        await asyncio.wait_for(waiting_caller, 0.2)
        await analyzer.aclose()
        assert geometry.close_calls == semantic.close_calls == 1

    asyncio.run(run())


def test_multiple_close_failures_are_preserved_for_every_caller() -> None:
    async def run() -> None:
        geometry = FailingCloseGeometry()
        semantic = FailingCloseSemantic(
            VisionDecision(GuidanceCode.READY, 1.0)
        )
        analyzer = HybridVisionGuidanceAnalyzer(geometry, semantic)

        for _ in range(2):
            with pytest.raises(ExceptionGroup) as captured:
                await analyzer.aclose()
            assert len(captured.value.exceptions) == 2
            assert {type(error) for error in captured.value.exceptions} == {
                RuntimeError,
                ValueError,
            }
        assert geometry.close_calls == semantic.close_calls == 1

    asyncio.run(run())


def test_recovery_close_failure_is_logged_without_private_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        semantic = FailingRecoveryCloseSemantic()
        analyzer = HybridVisionGuidanceAnalyzer(
            Geometry(VisionDecision(GuidanceCode.MOVE_FARTHER, 1.0)),
            semantic,
        )
        try:
            decision = await analyzer(_input())
            assert decision.code is GuidanceCode.MOVE_FARTHER
            assert semantic.close_calls == 1
        finally:
            semantic.release.set()
            await asyncio.wait_for(semantic.finished.wait(), 0.2)

    with caplog.at_level("ERROR"):
        asyncio.run(run())

    assert "hybrid semantic recovery close failed: RuntimeError" in caplog.text
    assert "private recovery close detail" not in caplog.text


def test_closed_or_unavailable_session_requires_fresh_component_factories() -> None:
    async def run() -> None:
        closed = HybridVisionGuidanceAnalyzer(
            NoFactoryGeometry(None),
            NoFactorySemantic(VisionDecision(GuidanceCode.READY, 1.0)),
        )
        await closed.aclose()
        with pytest.raises(RuntimeError, match="requires fresh component factories"):
            closed.new_session()

        semantic = NoFactoryCancellationResistantSemantic()
        unavailable = HybridVisionGuidanceAnalyzer(
            NoFactoryGeometry(
                VisionDecision(GuidanceCode.MOVE_FARTHER, 1.0)
            ),
            semantic,
        )
        try:
            decision = await unavailable(_input())
            assert decision.code is GuidanceCode.MOVE_FARTHER
            with pytest.raises(
                RuntimeError, match="requires fresh component factories"
            ):
                unavailable.new_session()
        finally:
            semantic.release.set()
            await asyncio.wait_for(semantic.finished.wait(), 0.2)

    asyncio.run(run())


@dataclass
class InvalidGeometry:
    async def analyze_geometry(self, _input_value: GuidanceInput) -> object:
        return {"code": "READY", "confidence": 1.0}


def test_geometry_component_cannot_manufacture_ready() -> None:
    analyzer = HybridVisionGuidanceAnalyzer(
        InvalidGeometry(),  # type: ignore[arg-type]
        Semantic(VisionDecision(GuidanceCode.WRONG_SIDE, 1.0)),
    )
    with pytest.raises(GuidanceContractError, match="only a geometry correction"):
        asyncio.run(analyzer(_input()))
