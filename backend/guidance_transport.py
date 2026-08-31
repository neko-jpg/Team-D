"""SDK-independent delivery of finite live-guidance events.

``GuidanceStateMachine`` owns the public guidance state.  This adapter is the
small boundary between an Agent's :class:`ProviderInference` callback and a
LiveKit-compatible data publisher.  In particular, it never accepts provider
copy: every guidance message comes from the state machine's fixed mapping.

The publisher is deliberately structural rather than a LiveKit SDK type.  A
``LocalParticipant`` can be passed directly in production, while tests can use
an in-memory object with ``publish_data``.  Both synchronous and asynchronous
publisher implementations are supported.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from math import isfinite
from typing import Protocol, TypeAlias, runtime_checkable

from backend.guidance_state_machine import (
    GUIDANCE_MESSAGES,
    GuidanceEvent,
    GuidanceHeartbeat,
    GuidanceStateEvent,
    GuidanceStateMachine,
)
from backend.providers.runtime import ProviderInference
from backend.providers.vision_guidance import (
    GuidanceCode,
    GuidanceShot,
    validate_guidance_shot,
    validate_vision_decision_for_shot,
)


class GuidanceTransportError(RuntimeError):
    """Raised when an event cannot be safely put on the data channel."""


@runtime_checkable
class DataPublisher(Protocol):
    """The minimal common surface of a LiveKit data-channel publisher."""

    def publish_data(self, payload: bytes, *, reliable: bool) -> object:
        """Publish bytes, returning either a value or an awaitable value."""


Event: TypeAlias = GuidanceEvent | GuidanceStateEvent | GuidanceHeartbeat

_GUIDANCE_FIELDS = frozenset(
    {
        "sessionId",
        "sequence",
        "shot",
        "code",
        "message",
        "confidence",
        "observedAt",
        "expiresAt",
    }
)
_STATE_FIELDS = frozenset({"type", "sessionId", "sequence", "shot", "code", "observedAt"})
_HEARTBEAT_FIELDS = frozenset(
    {
        "type",
        "sessionId",
        "sequence",
        "shot",
        "code",
        "message",
        "observedAt",
        "expiresAt",
        "displayChanged",
    }
)
_PROCESS_EPOCH_FIELD = frozenset({"processEpoch"})


def _finite_json(value: object) -> bool:
    """Return whether ``value`` is made solely of finite JSON primitives."""

    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return True
    if isinstance(value, float):
        return isfinite(value)
    if isinstance(value, list):
        return all(_finite_json(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _finite_json(item) for key, item in value.items())
    return False


def encode_guidance_event(event: Event) -> bytes:
    """Encode a closed, finite wire payload for one state-machine event.

    This defensive validation makes the transport boundary closed even if an
    integration constructs a valid-looking dataclass itself.  In particular,
    a custom ``message`` cannot bypass the state machine's fixed Japanese
    copy.
    """

    if isinstance(event, GuidanceEvent):
        if event.message != GUIDANCE_MESSAGES[event.code]:
            raise GuidanceTransportError("guidance message must be state-machine generated")
        payload = event.to_payload()
        expected_fields = _GUIDANCE_FIELDS
    elif isinstance(event, GuidanceStateEvent):
        payload = event.to_payload()
        expected_fields = _STATE_FIELDS
    elif isinstance(event, GuidanceHeartbeat):
        payload = event.to_payload()
        expected_fields = _HEARTBEAT_FIELDS
    else:
        raise GuidanceTransportError(
            "event must be a GuidanceEvent, GuidanceStateEvent, or GuidanceHeartbeat"
        )

    if event.process_epoch is not None:
        expected_fields |= _PROCESS_EPOCH_FIELD

    if set(payload) != expected_fields or not _finite_json(payload):
        raise GuidanceTransportError("event payload must have a finite closed shape")
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:  # defensive if the model changes
        raise GuidanceTransportError("event payload is not JSON serializable") from error


class GuidanceTransportAdapter:
    """Validate Agent inference, update session state, and publish its events.

    ``mark_disconnected`` fences all work which was started on the old Room.
    ``on_reconnected`` sends a reliable snapshot and only enables inference
    after that publication succeeds.  The inference call itself is always made
    outside the state lock, so a disconnect, shot change, or close can invalidate
    an in-flight model result immediately.
    """

    def __init__(
        self,
        inference: ProviderInference,
        publisher: DataPublisher,
        *,
        session_id: str | None = None,
        state_machine: GuidanceStateMachine | None = None,
        process_epoch: str | None = None,
        provider_deadline_seconds: float | None = None,
    ) -> None:
        if not callable(inference):
            raise TypeError("inference must be callable")
        publish = getattr(publisher, "publish_data", None)
        if not callable(publish):
            raise TypeError("publisher must provide publish_data")
        if state_machine is None:
            if session_id is None:
                raise TypeError("session_id is required when state_machine is not supplied")
            state_machine = GuidanceStateMachine(
                session_id,
                ready_confirmation_count=2,
                process_epoch=process_epoch,
            )
        elif session_id is not None and session_id != state_machine.session_id:
            raise GuidanceTransportError("session_id does not match state_machine")
        elif process_epoch is not None and process_epoch != state_machine.process_epoch:
            raise GuidanceTransportError("process_epoch does not match state_machine")

        if provider_deadline_seconds is not None:
            if (
                isinstance(provider_deadline_seconds, bool)
                or not isinstance(provider_deadline_seconds, (int, float))
                or not isfinite(float(provider_deadline_seconds))
                or float(provider_deadline_seconds) <= 0
            ):
                raise GuidanceTransportError(
                    "provider_deadline_seconds must be a finite positive number"
                )

        self._inference = inference
        self._publisher = publisher
        self._state_machine = state_machine
        self._provider_deadline_seconds = (
            None
            if provider_deadline_seconds is None
            else float(provider_deadline_seconds)
        )
        self._shot: GuidanceShot | None = None
        self._connected = True
        self._closed = False
        self._connection_generation = 0
        self._shot_generation = 0
        self._frame_generation = 0
        self._provider_closed = False
        # State allocation and publication are one serial critical section.
        # Inference deliberately happens outside this lock.
        self._lock = asyncio.Lock()

    @property
    def session_id(self) -> str:
        return self._state_machine.session_id

    @property
    def current_shot(self) -> GuidanceShot | None:
        return self._shot

    @property
    def sequence(self) -> int:
        return self._state_machine.sequence

    @property
    def process_epoch(self) -> str | None:
        return self._state_machine.process_epoch

    @property
    def connected(self) -> bool:
        return self._connected and not self._closed

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def connection_generation(self) -> int:
        """Monotonic lifecycle generation, useful to a reconnect owner."""

        return self._connection_generation

    def _require_active(self) -> None:
        if self._closed:
            raise GuidanceTransportError("guidance session is closed")
        if not self._connected:
            raise GuidanceTransportError(
                "guidance transport is disconnected; call on_reconnected first"
            )

    @staticmethod
    def _validate_publisher(publisher: object) -> DataPublisher:
        if not callable(getattr(publisher, "publish_data", None)):
            raise TypeError("publisher must provide publish_data")
        return publisher  # type: ignore[return-value]

    async def _publish(self, event: Event) -> Event:
        reliable = isinstance(event, GuidanceStateEvent)
        try:
            payload = encode_guidance_event(event)
            result = self._publisher.publish_data(payload, reliable=reliable)
            if inspect.isawaitable(result):
                await result
        except BaseException:
            if reliable:
                # A failed reliable packet leaves the peer's state unknown.  The
                # state machine has already allocated its sequence, so retrying
                # that event is unsafe; fence this Room and require a successful
                # reliable resync before any later lossy advice can be emitted.
                self._connected = False
                self._connection_generation += 1
                self._frame_generation += 1
            raise
        return event

    async def set_shot(
        self,
        shot: object,
        *,
        observed_at: object | None = None,
    ) -> GuidanceStateEvent | None:
        """Publish a reliable shot transition when the selected shot changes."""

        shot_value = validate_guidance_shot(shot)
        async with self._lock:
            self._require_active()
            if shot_value == self._shot:
                return None
            event = self._state_machine.set_shot(shot_value, observed_at=observed_at)
            self._shot = shot_value
            self._shot_generation += 1
            return await self._publish(event)  # type: ignore[return-value]

    async def resync(
        self, *, observed_at: object | None = None
    ) -> GuidanceStateEvent:
        """Publish the current state as a reliable snapshot/resynchronization."""

        async with self._lock:
            self._require_active()
            return await self._publish(
                self._state_machine.resync(observed_at=observed_at)
            )  # type: ignore[return-value]

    # A snapshot is the public vocabulary used by reconnect owners; it maps to
    # the finite ``resync`` state event today.
    snapshot = resync

    async def heartbeat(
        self, *, observed_at: object | None = None
    ) -> GuidanceHeartbeat:
        """Publish liveness without changing or redrawing display guidance."""

        async with self._lock:
            self._require_active()
            return await self._publish(
                self._state_machine.heartbeat(observed_at=observed_at)
            )  # type: ignore[return-value]

    async def mark_disconnected(self) -> bool:
        """Fence the current connection and suppress all future publication.

        A result already awaiting provider inference observes the changed
        generation when it returns and is dropped without state allocation or
        data-channel publication.  Repeated disconnect notifications are
        harmless and do not create additional generations.
        """

        async with self._lock:
            if self._closed or not self._connected:
                return False
            self._connected = False
            self._connection_generation += 1
            self._frame_generation += 1
            return True

    async def on_reconnected(
        self,
        *,
        publisher: DataPublisher | None = None,
        observed_at: object | None = None,
    ) -> GuidanceStateEvent:
        """Publish a reliable snapshot, then permit guidance on the new Room.

        If snapshot delivery fails, the adapter remains disconnected and the
        exception is propagated.  There is intentionally no fixture fallback.
        ``publisher`` permits a new Room participant to be injected without
        coupling this module to a LiveKit SDK class.
        """

        async with self._lock:
            if self._closed:
                raise GuidanceTransportError("guidance session is closed")
            if self._connected:
                raise GuidanceTransportError("guidance transport is already connected")
            if publisher is not None:
                self._publisher = self._validate_publisher(publisher)
            # A reconnect is a distinct lifecycle even after a preceding
            # disconnect has fenced old work.  Do not set connected until the
            # reliable packet has been accepted by the publisher.
            self._connection_generation += 1
            event = self._state_machine.resync(observed_at=observed_at)
            await self._publish(event)
            self._connected = True
            return event

    async def close(self) -> bool:
        """End this non-persistent session and fence any in-flight inference."""

        async with self._lock:
            if self._closed:
                return False
            self._closed = True
            self._connected = False
            self._connection_generation += 1
            self._frame_generation += 1
            return True

    async def prewarm_provider(self) -> None:
        """Prepare a session-owned provider before subscribing to camera frames."""

        prewarm = getattr(self._inference, "prewarm", None)
        if callable(prewarm):
            result = prewarm()
            if inspect.isawaitable(result):
                await result

    async def close_provider(self) -> None:
        """Release the provider only after camera inference has been drained."""

        if self._provider_closed:
            return
        close = getattr(self._inference, "aclose", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result
        self._provider_closed = True

    async def process_frame(
        self,
        frame: object,
        *,
        shot: object,
        observed_at: object | None = None,
    ) -> GuidanceEvent | None:
        """Run inference for ``frame`` and publish a lossy finite advice event.

        The inference result is validated here even when it originates from a
        provider wrapper that normally validates it.  Validation failure and
        publisher failure propagate; neither path falls back to fixture output.
        """

        shot_value = validate_guidance_shot(shot)
        # First take a short state snapshot.  Do not hold the lock while the
        # provider awaits: controls must be able to fence this result.
        async with self._lock:
            self._require_active()
            if shot_value != self._shot:
                state_event = self._state_machine.set_shot(
                    shot_value, observed_at=observed_at
                )
                self._shot = shot_value
                self._shot_generation += 1
                await self._publish(state_event)
            self._frame_generation += 1
            connection_generation = self._connection_generation
            shot_generation = self._shot_generation
            frame_generation = self._frame_generation

        try:
            if self._provider_deadline_seconds is None:
                raw_result = await self._inference(frame)
            else:
                raw_result = await asyncio.wait_for(
                    self._inference(frame),
                    timeout=self._provider_deadline_seconds,
                )
            decision = validate_vision_decision_for_shot(raw_result, shot_value)
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._provider_deadline_seconds is None:
                # Preserve the original SDK-independent adapter behavior for
                # existing callers. Production enables a finite deadline and
                # therefore the normalized failure path below.
                raise
            decision = validate_vision_decision_for_shot(
                {"code": GuidanceCode.AGENT_UNAVAILABLE, "confidence": 0.0},
                shot_value,
            )
            # An unavailable state starts when the failure becomes known, not
            # when the now-stale camera frame was originally observed.
            observed_at = None

        # Re-enter the serial publication boundary only after inference.  A
        # later frame is also a newer observation, so an older completion is
        # discarded instead of being published out of observation order.
        async with self._lock:
            if (
                self._closed
                or not self._connected
                or connection_generation != self._connection_generation
                or shot_generation != self._shot_generation
                or frame_generation != self._frame_generation
                or shot_value != self._shot
            ):
                return None
            event = self._state_machine.emit(
                shot_value,
                decision,
                observed_at=observed_at,
            )
            if event is None:
                await self._publish(
                    self._state_machine.heartbeat(observed_at=observed_at)
                )
                return None
            return await self._publish(event)  # type: ignore[return-value]

    # Clear integration-oriented spelling for use as a LatestFrameProcessor
    # result sink when the caller already knows the selected shot.
    handle_frame = process_frame


# Short names make the adapter easy to discover from a small Agent entrypoint.
GuidanceTransport = GuidanceTransportAdapter
LiveKitGuidanceTransport = GuidanceTransportAdapter


__all__ = [
    "DataPublisher",
    "Event",
    "GuidanceTransport",
    "GuidanceTransportAdapter",
    "GuidanceTransportError",
    "LiveKitGuidanceTransport",
    "encode_guidance_event",
]
