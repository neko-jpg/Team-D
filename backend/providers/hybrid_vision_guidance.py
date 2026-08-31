"""Compose deterministic garment geometry with Realtime semantic guidance.

For front/back frames both operations start concurrently.  A measured
geometry correction wins and cancels the no-longer-needed semantic response;
PASS waits for the semantic result.  Geometry failure is fail-closed because
falling back to a semantic READY would reintroduce the unsafe ambiguity this
provider exists to remove.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol, TypeAlias, runtime_checkable

from .geometry_guidance import GeometryGuidanceProvider
from .vision_guidance import (
    GEOMETRY_GUIDANCE_CODES,
    GuidanceContractError,
    GuidanceInput,
    GuidanceShot,
    VisionDecision,
    validate_guidance_input,
    validate_model_vision_decision_for_shot,
    validate_semantic_model_vision_decision_for_shot,
    validate_vision_decision_for_shot,
)


AnalyzerResult: TypeAlias = VisionDecision | Mapping[str, object]
SemanticAnalyzer: TypeAlias = Callable[
    [GuidanceInput], AnalyzerResult | Awaitable[AnalyzerResult]
]


# These budgets are deliberately well below the transport's 950 ms provider
# deadline.  ``Task.cancel`` is cooperative, so waiting for a Realtime SDK
# coroutine without a separate bound would otherwise defeat that deadline.
_CANCEL_DRAIN_GRACE_SECONDS = 0.10
_CANCEL_RECOVERY_CLOSE_SECONDS = 0.15
_LIFECYCLE_CLOSE_SECONDS = 0.25
# Parent cancellation normally arrives at the transport's 950 ms deadline.
# Keep this path below 50 ms; full close continues in a shared task.
_PARENT_CANCEL_DRAIN_GRACE_SECONDS = 0.01

_LOGGER = logging.getLogger(__name__)


@runtime_checkable
class GeometryAnalyzer(Protocol):
    async def analyze_geometry(
        self, input_value: GuidanceInput
    ) -> VisionDecision | None:
        """Return a framing correction or None when geometry passes."""


async def _await_result(value: object) -> object:
    return await value if inspect.isawaitable(value) else value


async def _call_semantic(
    analyzer: SemanticAnalyzer, input_value: GuidanceInput
) -> AnalyzerResult:
    return await _await_result(analyzer(input_value))  # type: ignore[return-value]


async def _call_optional(component: object, method_name: str) -> None:
    method = getattr(component, method_name, None)
    if callable(method):
        await _await_result(method())


async def _call_close(component: object) -> None:
    method = getattr(component, "aclose", None)
    if not callable(method):
        method = getattr(component, "close", None)
    if callable(method):
        await _await_result(method())


def _consume_future_result(future: asyncio.Future[object]) -> None:
    """Retrieve and safely observe detached task failures."""

    try:
        error = future.exception()
    except asyncio.CancelledError:
        return
    except BaseException as error:
        _LOGGER.error(
            "detached hybrid task could not be observed: %s",
            type(error).__name__,
        )
        return
    if error is not None:
        _LOGGER.error(
            "detached hybrid task failed: %s",
            type(error).__name__,
        )


def _log_close_errors(context: str, errors: tuple[BaseException, ...]) -> None:
    """Log every cleanup failure without copying provider messages to logs."""

    for error in errors:
        _LOGGER.error("%s: %s", context, type(error).__name__)


def _raise_close_errors(errors: tuple[BaseException, ...]) -> None:
    if not errors:
        return
    if len(errors) == 1:
        raise errors[0]
    message = "multiple hybrid component closes failed"
    if all(isinstance(error, Exception) for error in errors):
        raise ExceptionGroup(message, list(errors))  # type: ignore[arg-type]
    raise BaseExceptionGroup(message, list(errors))


async def _cancel_and_drain(
    tasks: tuple[asyncio.Task[object], ...],
    *,
    timeout_seconds: float = _CANCEL_DRAIN_GRACE_SECONDS,
) -> bool:
    """Request cancellation and wait only for a small, finite grace period."""

    if not tasks:
        return True
    for task in tasks:
        if not task.done():
            task.cancel()
    try:
        done, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
            task.add_done_callback(_consume_future_result)
        raise
    for task in done:
        _consume_future_result(task)
    for task in pending:
        task.add_done_callback(_consume_future_result)
    return not pending


async def _close_components_bounded(
    components: tuple[object, ...],
    *,
    timeout_seconds: float,
) -> tuple[BaseException, ...]:
    """Attempt every component close and report failures without hanging."""

    tasks = tuple(
        asyncio.create_task(_call_close(component), name="hybrid-component-close")
        for component in components
    )
    if not tasks:
        return ()
    try:
        done, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
            task.add_done_callback(_consume_future_result)
        raise
    errors: list[BaseException] = []
    for task in tasks:
        if task in pending:
            errors.append(TimeoutError("hybrid component close exceeded its deadline"))
            task.cancel()
            task.add_done_callback(_consume_future_result)
            continue
        try:
            task.result()
        except BaseException as error:
            errors.append(error)
    return tuple(errors)


class HybridVisionGuidanceAnalyzer:
    """Session-owned analyzer preserving the public finite decision contract."""

    def __init__(
        self,
        geometry: GeometryAnalyzer,
        semantic: SemanticAnalyzer,
    ) -> None:
        if not isinstance(geometry, GeometryAnalyzer):
            raise TypeError("geometry must provide analyze_geometry")
        if not callable(semantic):
            raise TypeError("semantic must be callable")
        self._geometry = geometry
        self._semantic = semantic
        self._closed = False
        self._semantic_unavailable = False
        self._close_task: asyncio.Task[tuple[BaseException, ...]] | None = None

    @property
    def geometry(self) -> GeometryAnalyzer:
        return self._geometry

    @property
    def semantic(self) -> SemanticAnalyzer:
        return self._semantic

    async def __call__(self, input_value: GuidanceInput) -> VisionDecision:
        if self._closed:
            raise RuntimeError("hybrid guidance session is closed")
        if self._semantic_unavailable:
            raise RuntimeError("hybrid semantic guidance is unavailable")
        validated = validate_guidance_input(input_value)

        if validated.requested_shot not in {GuidanceShot.FRONT, GuidanceShot.BACK}:
            semantic_task = asyncio.create_task(
                _call_semantic(self._semantic, validated),
                name="garment-semantic-guidance",
            )
            try:
                semantic_result = await asyncio.shield(semantic_task)
            except asyncio.CancelledError:
                await self._handle_parent_cancel((semantic_task,))
                raise
            return validate_model_vision_decision_for_shot(
                semantic_result, validated.requested_shot
            )

        geometry_task = asyncio.create_task(
            self._geometry.analyze_geometry(validated),
            name="garment-geometry-guidance",
        )
        semantic_task = asyncio.create_task(
            _call_semantic(self._semantic, validated),
            name="garment-semantic-guidance",
        )
        try:
            # Shield gives this coordinator control of cancellation.  Without
            # it, a child that ignores CancelledError can hold the caller past
            # the transport deadline before this cleanup code gets to run.
            geometry_result = await asyncio.shield(geometry_task)
        except asyncio.CancelledError:
            await self._handle_parent_cancel((geometry_task, semantic_task))
            raise
        except Exception:
            await self._stop_semantic_task(semantic_task)
            raise

        if geometry_result is not None:
            try:
                decision = validate_vision_decision_for_shot(
                    geometry_result, validated.requested_shot
                )
                if decision.code not in GEOMETRY_GUIDANCE_CODES:
                    raise GuidanceContractError(
                        "local geometry may return only a geometry correction or PASS"
                    )
            except Exception:
                await self._stop_semantic_task(semantic_task)
                raise
            # Geometry correction is complete and safe without a semantic
            # answer.  Drain cancellation so the Realtime adapter can send
            # response.cancel and keep its warm socket consistent.
            await self._stop_semantic_task(semantic_task)
            return decision

        try:
            semantic_result = await asyncio.shield(semantic_task)
        except asyncio.CancelledError:
            await self._handle_parent_cancel((semantic_task,))
            raise
        return validate_semantic_model_vision_decision_for_shot(
            semantic_result, validated.requested_shot
        )

    async def _stop_semantic_task(self, task: asyncio.Task[object]) -> None:
        if await _cancel_and_drain((task,)):
            return

        # A response that cannot acknowledge cancellation may have left its
        # socket protocol state ambiguous.  Do not reuse it: make one bounded
        # public close attempt, then fail closed on later frames in this session.
        self._semantic_unavailable = True
        errors = await _close_components_bounded(
            (self._semantic,),
            timeout_seconds=_CANCEL_RECOVERY_CLOSE_SECONDS,
        )
        _log_close_errors("hybrid semantic recovery close failed", errors)
        await _cancel_and_drain((task,))

    def _ensure_close_task(self) -> asyncio.Task[tuple[BaseException, ...]]:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(
                _close_components_bounded(
                    (self._geometry, self._semantic),
                    timeout_seconds=_LIFECYCLE_CLOSE_SECONDS,
                ),
                name="hybrid-session-close",
            )
            self._close_task.add_done_callback(self._observe_close_task)
        return self._close_task

    @staticmethod
    def _observe_close_task(
        task: asyncio.Task[tuple[BaseException, ...]],
    ) -> None:
        try:
            errors = task.result()
        except asyncio.CancelledError:
            _LOGGER.error("hybrid shared close task was cancelled")
        except BaseException as error:
            _LOGGER.error(
                "hybrid shared close task failed: %s",
                type(error).__name__,
            )
        else:
            _log_close_errors("hybrid component close failed", errors)

    async def _handle_parent_cancel(
        self,
        tasks: tuple[asyncio.Task[object], ...],
    ) -> None:
        """Make the session unusable and return within the transport margin."""

        self._semantic_unavailable = True
        self._closed = True
        await _cancel_and_drain(
            tasks,
            timeout_seconds=_PARENT_CANCEL_DRAIN_GRACE_SECONDS,
        )
        # Do not add the 250 ms lifecycle budget to a 950 ms transport timeout.
        # The shared task is strongly referenced and every failure is observed;
        # an explicit later ``aclose`` joins this same task.
        self._ensure_close_task()

    async def prewarm(self) -> None:
        if self._closed:
            raise RuntimeError("hybrid guidance session is closed")
        if self._semantic_unavailable:
            raise RuntimeError("hybrid semantic guidance is unavailable")
        tasks = (
            asyncio.create_task(
                _call_optional(self._geometry, "prewarm"),
                name="hybrid-geometry-prewarm",
            ),
            asyncio.create_task(
                _call_optional(self._semantic, "prewarm"),
                name="hybrid-semantic-prewarm",
            ),
        )
        group = asyncio.gather(*tasks)
        try:
            await asyncio.shield(group)
        except asyncio.CancelledError:
            await _cancel_and_drain(
                tasks,
                timeout_seconds=_PARENT_CANCEL_DRAIN_GRACE_SECONDS,
            )
            self._closed = True
            self._ensure_close_task()
            if not group.done():
                group.add_done_callback(_consume_future_result)
            raise
        except BaseException:
            await _cancel_and_drain(tasks)
            # A partially warmed session is not safe to reuse.  Cleanup is
            # finite, attempts both components, and preserves the root error.
            self._closed = True
            errors = await asyncio.shield(self._ensure_close_task())
            _log_close_errors("hybrid prewarm cleanup failed", errors)
            if not group.done():
                group.add_done_callback(_consume_future_result)
            raise

    async def aclose(self) -> None:
        """Join one shared close; caller cancellation never cancels that close."""

        errors = await asyncio.shield(self._ensure_close_task())
        _raise_close_errors(errors)

    close = aclose

    def new_session(self) -> "HybridVisionGuidanceAnalyzer":
        geometry_factory = getattr(self._geometry, "new_session", None)
        semantic_factory = getattr(self._semantic, "new_session", None)
        requires_fresh_components = self._closed or self._semantic_unavailable
        if requires_fresh_components and not (
            callable(geometry_factory) and callable(semantic_factory)
        ):
            raise RuntimeError(
                "closed or unavailable hybrid guidance requires fresh component "
                "factories"
            )
        geometry = geometry_factory() if callable(geometry_factory) else self._geometry
        semantic = semantic_factory() if callable(semantic_factory) else self._semantic
        if requires_fresh_components and (
            geometry is self._geometry or semantic is self._semantic
        ):
            raise RuntimeError(
                "closed or unavailable hybrid guidance cannot reuse components"
            )
        return HybridVisionGuidanceAnalyzer(geometry, semantic)


def create_hybrid_vision_guidance_analyzer(
    geometry: GeometryGuidanceProvider,
    semantic: SemanticAnalyzer,
) -> HybridVisionGuidanceAnalyzer:
    """Named factory used by runtime wiring and tests."""

    return HybridVisionGuidanceAnalyzer(geometry, semantic)


__all__ = [
    "AnalyzerResult",
    "GeometryAnalyzer",
    "HybridVisionGuidanceAnalyzer",
    "SemanticAnalyzer",
    "create_hybrid_vision_guidance_analyzer",
]
