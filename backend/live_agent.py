"""LiveKit camera-track ingestion for the garment capture agent.

The agent deliberately keeps the transport boundary small.  LiveKit owns the
room and WebRTC transport, while this module owns two pieces of backpressure:

* only publications whose media kind is video and whose source is the camera
  are subscribed; and
* a frame slot with capacity one feeds a processor with at most one inference
  in flight.  A new frame replaces a pending frame instead of joining an
  unbounded queue.

The LiveKit packages are optional at import time.  This is useful for the
offline fixture tests and also keeps the frame policy independently testable.
When the packages are installed, :func:`entrypoint` is a normal LiveKit
Agents room entrypoint.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections.abc import AsyncIterable, Awaitable, Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, Literal, Optional, TypeVar

from .settings import BackendSettings


FrameT = TypeVar("FrameT")
ResultT = TypeVar("ResultT")

Shot = Literal["front", "back", "tag", "measurement"]


class _QueueSize(int):
    """An integer that also supports ``qsize()`` queue-style callers."""

    def __call__(self) -> int:
        return int(self)


class FrameSlotClosed(RuntimeError):
    """Optional strict error type for callers that reject closed submissions."""


class LatestFrameSlot(Generic[FrameT]):
    """A thread-safe, capacity-one slot that always retains the newest frame.

    This is intentionally not an ``asyncio.Queue``.  ``VideoStream`` callbacks
    can arrive while inference is awaiting an external provider, and replacing
    the value synchronously makes the boundedness invariant explicit.  The
    processor consumes the slot from the same event loop, while the small
    ``RLock`` also makes diagnostics and test producers safe from another
    thread.
    """

    maxsize = 1

    def __init__(self) -> None:
        self._frame: Optional[FrameT] = None
        self._has_frame = False
        self._closed = False
        self._replaced_count = 0
        self._accepted_count = 0
        self._lock = threading.RLock()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def qsize(self) -> _QueueSize:
        """Return the number of frames waiting (always zero or one)."""

        with self._lock:
            return _QueueSize(1 if self._has_frame else 0)

    @property
    def size(self) -> int:
        return self.qsize

    @property
    def replaced_count(self) -> int:
        with self._lock:
            return self._replaced_count

    @property
    def accepted_count(self) -> int:
        with self._lock:
            return self._accepted_count

    def put(self, frame: FrameT) -> bool:
        """Store ``frame`` and return whether it replaced a pending frame.

        The method never grows beyond one pending value.  A closed slot rejects
        the frame and returns ``False``; callers that need to distinguish this
        case can inspect :attr:`closed`.
        """

        with self._lock:
            if self._closed:
                return False
            replaced = self._has_frame
            if replaced:
                self._replaced_count += 1
            self._frame = frame
            self._has_frame = True
            self._accepted_count += 1
            return replaced

    # Names used by callers that want the coalescing behavior to be explicit.
    put_latest = put
    offer = put

    def take(self) -> Optional[FrameT]:
        """Remove and return the newest pending frame, if any."""

        with self._lock:
            if not self._has_frame:
                return None
            frame = self._frame
            self._frame = None
            self._has_frame = False
            return frame

    get_latest = take
    pop = take

    def peek(self) -> Optional[FrameT]:
        with self._lock:
            return self._frame if self._has_frame else None

    def clear(self) -> Optional[FrameT]:
        return self.take()

    def close(self) -> Optional[FrameT]:
        """Close the slot and release the pending frame reference."""

        with self._lock:
            self._closed = True
            frame = self._frame if self._has_frame else None
            self._frame = None
            self._has_frame = False
            return frame

    def __len__(self) -> int:
        return self.qsize

    def empty(self) -> bool:
        return self.qsize == 0

    def full(self) -> bool:
        return self.qsize == self.maxsize

    put_nowait = put
    get_nowait = take


Inference = Callable[[FrameT], ResultT | Awaitable[ResultT]]
ResultSink = Callable[[ResultT, FrameT], Any]
ErrorSink = Callable[[BaseException, FrameT], Any]


class LatestFrameProcessor(Generic[FrameT, ResultT]):
    """Process camera frames with one in-flight inference and latest-frame coalescing.

    ``submit`` returns as soon as the frame has been put into the slot.  It does
    not wait for inference, so a fast WebRTC stream cannot back up behind a
    slow provider.  While a provider call is in flight, each submitted frame
    replaces the previous pending value; once the call completes, the worker
    processes only the newest value.
    """

    max_concurrency = 1

    def __init__(
        self,
        inference: Optional[Inference[FrameT, ResultT]] = None,
        *,
        infer: Optional[Inference[FrameT, ResultT]] = None,
        on_result: Optional[ResultSink[ResultT, FrameT]] = None,
        on_error: Optional[ErrorSink] = None,
        slot: Optional[LatestFrameSlot[FrameT]] = None,
    ) -> None:
        selected_inference = inference if inference is not None else infer
        if selected_inference is None:
            raise TypeError("LatestFrameProcessor requires an inference callback")
        self._inference = selected_inference
        self._on_result = on_result
        self._on_error = on_error
        self.slot = slot if slot is not None else LatestFrameSlot()
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._closed = False
        self._in_flight = 0
        self._max_in_flight = 0
        self._processed_count = 0
        self._error_count = 0
        self._max_pending = 0
        self._last_frame: Optional[FrameT] = None
        self._last_result: Optional[ResultT] = None
        self._last_error: Optional[BaseException] = None

    @property
    def queue(self) -> LatestFrameSlot[FrameT]:
        """Compatibility view used by metrics/tests (``queue.maxsize == 1``)."""

        return self.slot

    @property
    def latest_frame(self) -> Optional[FrameT]:
        return self.slot.peek()

    @property
    def pending_count(self) -> int:
        return self.slot.qsize

    @property
    def queue_size(self) -> int:
        return self.slot.qsize

    @property
    def in_flight(self) -> int:
        return self._in_flight

    @property
    def inference_in_flight(self) -> bool:
        return self._in_flight == 1

    @property
    def max_in_flight(self) -> int:
        return self._max_in_flight

    @property
    def max_pending(self) -> int:
        # A slot has capacity one by construction; expose the invariant as a
        # metric rather than relying on an implementation detail in callers.
        return self._max_pending

    @property
    def max_queue_size(self) -> int:
        return self._max_pending

    @property
    def processed_count(self) -> int:
        return self._processed_count

    @property
    def dropped_count(self) -> int:
        return self.slot.replaced_count

    @property
    def last_processed_frame(self) -> Optional[FrameT]:
        return self._last_frame

    @property
    def last_result(self) -> Optional[ResultT]:
        return self._last_result

    @property
    def last_error(self) -> Optional[BaseException]:
        return self._last_error

    @property
    def error_count(self) -> int:
        return self._error_count

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def worker_task(self) -> Optional[asyncio.Task[None]]:
        return self._worker_task

    async def submit(self, frame: FrameT) -> bool:
        """Submit a frame without waiting for inference to finish."""

        return self.submit_nowait(frame)

    async def enqueue(self, frame: FrameT) -> bool:
        return await self.submit(frame)

    async def process(self, frame: FrameT) -> bool:
        return await self.submit(frame)

    async def submit_frame(self, frame: FrameT) -> bool:
        return await self.submit(frame)

    def submit_nowait(self, frame: FrameT) -> bool:
        """Synchronous counterpart for a LiveKit frame callback."""

        if self._closed:
            return False
        self.slot.put(frame)
        self._max_pending = max(self._max_pending, self.slot.qsize)
        self._ensure_worker()
        return True

    push = submit_nowait
    offer = submit_nowait

    def _ensure_worker(self) -> None:
        if self._closed:
            return
        task = self._worker_task
        if task is not None and not task.done():
            return
        loop = asyncio.get_running_loop()
        self._worker_task = loop.create_task(self._drain(), name="latest-frame-processor")

    async def _drain(self) -> None:
        try:
            while not self._closed:
                frame = self.slot.take()
                if frame is None:
                    # ``submit_nowait`` cannot interleave with this synchronous
                    # section on the same event loop.  Clearing the task while
                    # holding this path avoids a race for the next submission.
                    self._worker_task = None
                    return

                self._in_flight = 1
                self._max_in_flight = max(self._max_in_flight, self._in_flight)
                self._last_error = None
                try:
                    result = self._inference(frame)
                    if inspect.isawaitable(result):
                        result = await result
                    self._last_result = result
                    self._last_frame = frame
                    self._processed_count += 1
                    if self._on_result is not None:
                        sink_result = self._on_result(result, frame)
                        if inspect.isawaitable(sink_result):
                            await sink_result
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._last_error = error
                    self._error_count += 1
                    if self._on_error is not None:
                        sink_error = self._on_error(error, frame)
                        if inspect.isawaitable(sink_error):
                            await sink_error
                finally:
                    self._in_flight = 0
        finally:
            # Cancellation can happen while an inference is awaiting.  A
            # future submit should be able to create a fresh worker.
            if self._worker_task is asyncio.current_task():
                self._worker_task = None

    async def wait_idle(self) -> None:
        """Wait until all currently accepted frames have been handled."""

        while True:
            task = self._worker_task
            if task is None:
                return
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # The processor's worker may itself have been cancelled by
                # ``stop``; there is no remaining work to await in that case.
                return

    async def flush(self) -> None:
        await self.wait_idle()

    def stop(self, *, cancel: bool = True) -> None:
        """Stop accepting frames and optionally cancel in-flight inference."""

        self._closed = True
        self.slot.close()
        task = self._worker_task
        if cancel and task is not None and not task.done():
            task.cancel()

    close = stop

    async def aclose(self, *, cancel: bool = True) -> None:
        self.stop(cancel=cancel)
        await self.wait_idle()


def _enum_strings(value: Any) -> set[str]:
    """Return normalized spellings for strings and enum-like values."""

    if value is None:
        return set()

    values: list[Any] = [value]
    if isinstance(value, Enum):
        values.extend((value.name, value.value))
    else:
        for attribute in ("name", "value"):
            candidate = getattr(value, attribute, None)
            if candidate is not None:
                values.append(candidate)

    result: set[str] = set()
    for candidate in values:
        text = (
            candidate.strip().lower()
            if isinstance(candidate, str)
            else str(candidate).strip().lower()
        )
        result.add(text)
        result.add(text.replace("_", ""))
        result.add(text.replace("-", ""))
    return result


def is_video_kind(value: Any) -> bool:
    """Recognize LiveKit's ``KIND_VIDEO`` without importing LiveKit eagerly."""

    # ``livekit.rtc.TrackKind`` is a protobuf EnumTypeWrapper in the current
    # Python SDK.  Its values are plain integers (rather than ``Enum``
    # instances), so the textual fallback below cannot identify them.  Keep
    # the comparison lazy so importing this module still works offline.
    try:
        from livekit import rtc  # type: ignore[import-not-found]

        expected = getattr(getattr(rtc, "TrackKind", None), "KIND_VIDEO", None)
        if expected is not None and value == expected:
            return True
    except (ImportError, AttributeError, TypeError, ValueError):
        pass

    values = _enum_strings(value)
    return any(
        token in values
        for token in (
            "video",
            "kind_video",
            "kindvideo",
            "trackkindvideo",
            "trackkind_video",
        )
    ) or any(token.endswith("kindvideo") for token in values)


def is_camera_source(value: Any) -> bool:
    """Recognize LiveKit's ``SOURCE_CAMERA`` spelling."""

    # See ``is_video_kind``: protobuf enum values are integer constants in the
    # current rtc binding and must be compared with the SDK's explicit value.
    try:
        from livekit import rtc  # type: ignore[import-not-found]

        expected = getattr(getattr(rtc, "TrackSource", None), "SOURCE_CAMERA", None)
        if expected is not None and value == expected:
            return True
    except (ImportError, AttributeError, TypeError, ValueError):
        pass

    values = _enum_strings(value)
    return any(
        token in values
        for token in (
            "camera",
            "source_camera",
            "sourcecamera",
            "tracksourcesourcecamera",
            "tracksourcesource_camera",
        )
    ) or any(token.endswith("sourcecamera") for token in values)


def _publication_kind(publication: Any, track: Any = None) -> Any:
    kind = getattr(publication, "kind", None)
    if kind is None:
        kind = getattr(track, "kind", None)
    return kind


def _publication_source(publication: Any, track: Any = None) -> Any:
    source = getattr(publication, "source", None)
    if source is None:
        source = getattr(track, "source", None)
    return source


def is_camera_video_track(track: Any, publication: Any = None) -> bool:
    """Return true only for a video track sourced from the camera."""

    if publication is not None:
        kind = _publication_kind(publication, track)
        source = _publication_source(publication, track)
    else:
        kind = getattr(track, "kind", None)
        source = getattr(track, "source", None)
    return is_video_kind(kind) and is_camera_source(source)


def is_camera_video_publication(publication: Any) -> bool:
    """Predicate used before requesting a remote publication subscription."""

    return is_camera_video_track(getattr(publication, "track", None), publication)


def should_subscribe_to_publication(publication: Any) -> bool:
    return is_camera_video_publication(publication)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _set_publication_subscribed(publication: Any, subscribed: bool) -> None:
    setter = getattr(publication, "set_subscribed", None)
    if callable(setter):
        await _maybe_await(setter(subscribed))
        return
    # Some lightweight fakes expose only a mutable property.  LiveKit's real
    # publication has set_subscribed, so this is only a test/integration aid.
    if hasattr(publication, "subscribed"):
        setattr(publication, "subscribed", subscribed)


def _iter_values(value: Any) -> Iterable[Any]:
    if value is None:
        return ()
    if hasattr(value, "values"):
        return value.values()
    if isinstance(value, dict):
        return value.values()
    return value


class CameraVideoTrackSubscriber:
    """Subscribe to camera video publications and feed their frames onward."""

    def __init__(
        self,
        processor: LatestFrameProcessor[Any, Any],
        *,
        stream_factory: Optional[Callable[[Any], Any]] = None,
    ) -> None:
        self.processor = processor
        self.stream_factory = stream_factory
        self._stream_tasks: set[asyncio.Task[None]] = set()
        self._event_tasks: set[asyncio.Task[Any]] = set()
        self._track_tasks: dict[int, asyncio.Task[None]] = {}
        self._closed = False

    @property
    def active_stream_count(self) -> int:
        return sum(not task.done() for task in self._stream_tasks)

    async def subscribe_publication(self, publication: Any) -> bool:
        """Subscribe to a camera video publication and reject all others."""

        if self._closed:
            return False
        if not should_subscribe_to_publication(publication):
            await _set_publication_subscribed(publication, False)
            return False

        await _set_publication_subscribed(publication, True)
        track = getattr(publication, "track", None)
        if track is not None:
            await self.track_subscribed(track, publication)
        return True

    async def track_subscribed(
        self,
        track: Any,
        publication: Any = None,
        participant: Any = None,
    ) -> bool:
        del participant  # participant identity is transport metadata only.
        if self._closed or not is_camera_video_track(track, publication):
            if publication is not None:
                await _set_publication_subscribed(publication, False)
            return False

        track_key = id(track)
        existing_task = self._track_tasks.get(track_key)
        if existing_task is not None and not existing_task.done():
            return True
        stream = await self._make_stream(track)
        task = asyncio.create_task(
            self._consume_stream(stream),
            name="camera-video-stream",
        )
        self._stream_tasks.add(task)
        self._track_tasks[track_key] = task

        def forget_track(done_task: asyncio.Task[None]) -> None:
            self._stream_tasks.discard(done_task)
            if self._track_tasks.get(track_key) is done_task:
                self._track_tasks.pop(track_key, None)

        task.add_done_callback(forget_track)
        return True

    async def _make_stream(self, track: Any) -> Any:
        if self.stream_factory is not None:
            return await _maybe_await(self.stream_factory(track))

        try:
            from livekit import rtc  # type: ignore[import-not-found]
        except ImportError as error:  # pragma: no cover - exercised by env check
            raise RuntimeError(
                "livekit.rtc is required for a live camera stream; "
                "inject stream_factory for offline tests"
            ) from error

        video_stream = getattr(rtc, "VideoStream", None)
        if video_stream is None:
            raise RuntimeError("the installed livekit SDK does not provide VideoStream")
        from_track = getattr(video_stream, "from_track", None)
        if callable(from_track):
            # ``from_track`` is keyword-only in current livekit-rtc releases.
            # Keep capacity explicit: falling back to the SDK default (zero,
            # i.e. unbounded) would defeat the latest-frame backpressure
            # guarantee.
            return await _maybe_await(from_track(track=track, capacity=1))
        try:
            return video_stream(track=track, capacity=1)
        except TypeError as error:
            raise RuntimeError(
                "the installed livekit SDK cannot create a bounded VideoStream"
            ) from error

    async def _consume_stream(self, stream: AsyncIterable[Any]) -> None:
        try:
            async for event in stream:
                frame = getattr(event, "frame", event)
                await self.processor.submit(frame)
        finally:
            close = getattr(stream, "aclose", None)
            if callable(close):
                await _maybe_await(close())
            else:
                close = getattr(stream, "close", None)
                if callable(close):
                    await _maybe_await(close())

    async def track_unsubscribed(
        self,
        track: Any,
        publication: Any = None,
        participant: Any = None,
    ) -> None:
        del publication, participant
        task = self._track_tasks.pop(id(track), None)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def track_published(
        self,
        publication: Any,
        participant: Any = None,
    ) -> bool:
        """Apply camera-only policy when a remote publication first appears."""

        del participant
        return await self.subscribe_publication(publication)

    async def subscribe_existing_publications(self, room: Any) -> int:
        """Inspect current remote participants without subscribing to audio/screen."""

        subscribed = 0
        participants = _iter_values(getattr(room, "remote_participants", None))
        for participant in participants:
            publications = _iter_values(getattr(participant, "track_publications", None))
            for publication in publications:
                if await self.subscribe_publication(publication):
                    subscribed += 1
        return subscribed

    def attach_room(self, room: Any) -> None:
        """Register track events on a LiveKit room.

        ``Room.on`` accepts either event enum values or string names depending
        on the SDK release.  Registering the string first keeps this helper
        usable with small fakes; when the enum is available the fallback is
        attempted as well.
        """

        on = getattr(room, "on", None)
        if not callable(on):
            return

        def schedule(coroutine: Awaitable[Any]) -> None:
            task = asyncio.create_task(coroutine)
            self._event_tasks.add(task)
            task.add_done_callback(self._event_tasks.discard)

        def handle_subscribed(track: Any, publication: Any, participant: Any) -> None:
            schedule(self.track_subscribed(track, publication, participant))

        def handle_unsubscribed(track: Any, publication: Any, participant: Any) -> None:
            schedule(self.track_unsubscribed(track, publication, participant))
            schedule(_set_publication_subscribed(publication, False))

        def handle_published(publication: Any, participant: Any) -> None:
            schedule(self.track_published(publication, participant))

        for event, callback in (
            ("track_published", handle_published),
            ("track_subscribed", handle_subscribed),
            ("track_unsubscribed", handle_unsubscribed),
        ):
            try:
                on(event, callback)
            except (TypeError, ValueError):
                try:
                    from livekit import rtc  # type: ignore[import-not-found]

                    event_names = {
                        "track_published": "TrackPublished",
                        "track_subscribed": "TrackSubscribed",
                        "track_unsubscribed": "TrackUnsubscribed",
                    }
                    room_event = getattr(
                        getattr(rtc, "RoomEvent", None),
                        event_names[event],
                        None,
                    )
                    if room_event is not None:
                        on(room_event, callback)
                except ImportError:
                    continue

    async def stop(self) -> None:
        self._closed = True
        tasks = tuple(self._stream_tasks) + tuple(self._event_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Await the processor as well as the VideoStream consumers.  This
        # guarantees that no in-flight inference can outlive a room shutdown.
        close_processor = getattr(self.processor, "aclose", None)
        if callable(close_processor):
            await _maybe_await(close_processor())
        else:  # pragma: no cover - defensive support for injected processors
            stop_processor = getattr(self.processor, "stop", None)
            if callable(stop_processor):
                await _maybe_await(stop_processor())

    close = stop


@dataclass(frozen=True)
class AgentRuntime:
    """Runtime handles returned after an Agent context joins a room."""

    room: Any
    subscriber: CameraVideoTrackSubscriber
    current_shot: Shot = "front"


def _auto_subscribe_none() -> Any:
    """Resolve the SDK enum without making it a module import requirement."""

    try:
        from livekit.agents import AutoSubscribe  # type: ignore[import-not-found]

        value = getattr(AutoSubscribe, "SUBSCRIBE_NONE", None)
        if value is not None:
            return value
    except ImportError:
        pass
    return "subscribe_none"


async def connect_agent_context(ctx: Any) -> Any:
    """Join a LiveKit room with automatic subscription disabled.

    Manual publication filtering is required because ``VIDEO_ONLY`` would also
    subscribe to screen-share video tracks.  The subscriber enables only
    ``SOURCE_CAMERA`` publications after the room has connected.
    """

    connect = getattr(ctx, "connect", None)
    if not callable(connect):
        raise RuntimeError("LiveKit Agent context does not provide connect()")
    try:
        await _maybe_await(connect(auto_subscribe=_auto_subscribe_none()))
    except TypeError as error:
        # Retrying without the explicit policy would make the SDK default to
        # SUBSCRIBE_ALL and could receive audio or screen-share tracks.
        raise RuntimeError(
            "LiveKit context must support explicit SUBSCRIBE_NONE; "
            "refusing an implicit subscription policy"
        ) from error
    room = getattr(ctx, "room", None)
    if room is None:
        raise RuntimeError("LiveKit Agent context has no room after connect")
    return room


async def start_agent_runtime(
    ctx: Any,
    inference: Inference[Any, Any],
    *,
    on_result: Optional[ResultSink[Any, Any]] = None,
    on_error: Optional[ErrorSink] = None,
    stream_factory: Optional[Callable[[Any], Any]] = None,
) -> AgentRuntime:
    """Connect, attach camera-only handlers, and subscribe existing tracks."""

    room = await connect_agent_context(ctx)
    processor = LatestFrameProcessor(inference, on_result=on_result, on_error=on_error)
    subscriber = CameraVideoTrackSubscriber(processor, stream_factory=stream_factory)
    subscriber.attach_room(room)
    add_shutdown_callback = getattr(ctx, "add_shutdown_callback", None)
    if callable(add_shutdown_callback):
        async def shutdown_callback(*_args: Any) -> None:
            await subscriber.stop()

        # JobContext currently registers this callback synchronously, while a
        # fake context may expose an async registration method.  Accommodate
        # both without making shutdown itself fire-and-forget.
        await _maybe_await(add_shutdown_callback(shutdown_callback))
    await subscriber.subscribe_existing_publications(room)
    return AgentRuntime(room=room, subscriber=subscriber)


async def entrypoint(
    ctx: Any,
    *,
    inference: Optional[Inference[Any, Any]] = None,
    on_result: Optional[ResultSink[Any, Any]] = None,
    on_error: Optional[ErrorSink] = None,
    stream_factory: Optional[Callable[[Any], Any]] = None,
) -> AgentRuntime:
    """LiveKit Agents room entrypoint.

    The default inference is intentionally a no-op.  Task 3.10 supplies the
    ``VisionGuidanceProvider`` callback; keeping it injectable lets transport
    and backpressure be tested without credentials or an external model.
    """

    async def noop(frame: Any) -> None:
        del frame
        return None

    runtime = await start_agent_runtime(
        ctx,
        inference or noop,
        on_result=on_result,
        on_error=on_error,
        stream_factory=stream_factory,
    )
    wait_for_shutdown = getattr(ctx, "wait_for_shutdown", None)
    if callable(wait_for_shutdown):
        try:
            await _maybe_await(wait_for_shutdown())
        finally:
            # Real JobContext uses its registered shutdown callbacks.  The
            # fallback also makes lightweight test contexts deterministic when
            # they only provide wait_for_shutdown and do not invoke callbacks.
            await runtime.subscriber.stop()
    return runtime


def create_agent_server(
    *,
    inference: Optional[Inference[Any, Any]] = None,
    on_result: Optional[ResultSink[Any, Any]] = None,
    on_error: Optional[ErrorSink] = None,
) -> Any:
    """Build an ``AgentServer`` when LiveKit Agents is installed.

    Importing this module remains possible without the optional runtime.  The
    returned fallback object is useful to callers that want to report a clear
    startup error instead of failing at import time.
    """

    try:
        from livekit.agents import AgentServer  # type: ignore[import-not-found]
    except ImportError:
        return None

    server = AgentServer()
    register = getattr(server, "rtc_session", None)
    if not callable(register):
        raise RuntimeError("the installed livekit-agents SDK does not provide rtc_session")

    @register()
    async def live_session(ctx: Any) -> AgentRuntime:
        return await entrypoint(
            ctx,
            inference=inference,
            on_result=on_result,
            on_error=on_error,
        )

    return server


def main() -> None:
    """Run the worker through the LiveKit Agents CLI."""

    settings = BackendSettings.from_env()
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("backend.agent").info(
        "agent_starting provider_mode=%s provider_schema=VisionGuidanceProvider",
        settings.provider_mode.value,
    )

    try:
        from livekit.agents import cli  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError(
            "livekit-agents is required to run the Agent; install the locked Python dependencies"
        ) from error
    server = create_agent_server()
    if server is None:  # defensive; create_agent_server already imports it
        raise RuntimeError("unable to create the LiveKit Agent server")
    cli.run_app(server)


# Short aliases keep the policy usable from a small worker entrypoint without
# coupling callers to the transport-specific class names.
LatestFrameQueue = LatestFrameSlot
FrameProcessor = LatestFrameProcessor
CameraTrackSubscriber = CameraVideoTrackSubscriber
run_agent = entrypoint


__all__ = [
    "AgentRuntime",
    "CameraTrackSubscriber",
    "CameraVideoTrackSubscriber",
    "FrameProcessor",
    "FrameSlotClosed",
    "LatestFrameProcessor",
    "LatestFrameQueue",
    "LatestFrameSlot",
    "Shot",
    "connect_agent_context",
    "create_agent_server",
    "entrypoint",
    "is_camera_source",
    "is_camera_video_publication",
    "is_camera_video_track",
    "is_video_kind",
    "main",
    "run_agent",
    "should_subscribe_to_publication",
    "start_agent_runtime",
]


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    main()
