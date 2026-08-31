"""Offline async contract tests for the LiveKit camera agent boundary.

These tests intentionally model only the small transport surface consumed by
``backend.live_agent``.  No LiveKit SDK, room, network, model, or credential is
needed: the fakes make it possible to prove the backpressure and subscription
policy deterministically.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from types import ModuleType
import unittest
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from backend.live_agent import (
    CameraVideoTrackSubscriber,
    LatestFrameProcessor,
    LatestFrameSlot,
    is_camera_video_track,
    is_video_kind,
    start_agent_runtime,
    entrypoint,
)


_END = object()


class FakeVideoStream:
    """Minimal async video stream used instead of LiveKit's ``VideoStream``."""

    def __init__(self) -> None:
        self._events: asyncio.Queue[object] = asyncio.Queue()
        self.closed = False

    def __aiter__(self) -> "FakeVideoStream":
        return self

    async def __anext__(self) -> Any:
        event = await self._events.get()
        if event is _END:
            raise StopAsyncIteration
        return event

    async def push(self, frame: Any) -> None:
        await self._events.put(FakeVideoEvent(frame=frame))

    async def aclose(self) -> None:
        self.closed = True
        await self._events.put(_END)


@dataclass(frozen=True)
class FakeVideoEvent:
    frame: Any


@dataclass
class FakeTrack:
    kind: str
    source: str


@dataclass
class FakePublication:
    kind: str
    source: str
    track: FakeTrack
    subscribed: bool = False
    subscription_calls: list[bool] = field(default_factory=list)

    async def set_subscribed(self, subscribed: bool) -> None:
        self.subscribed = subscribed
        self.subscription_calls.append(subscribed)


@dataclass
class FakeParticipant:
    track_publications: list[FakePublication]


@dataclass
class FakeRoom:
    remote_participants: list[FakeParticipant]
    handlers: dict[str, Any] = field(default_factory=dict)

    def on(self, event: str, callback: Any) -> None:
        self.handlers[event] = callback


class FakeAgentContext:
    def __init__(self, room: FakeRoom) -> None:
        self.room = room
        self.connect_calls: list[dict[str, Any]] = []
        self.shutdown_callbacks: list[Any] = []

    async def connect(self, **kwargs: Any) -> None:
        self.connect_calls.append(kwargs)

    def add_shutdown_callback(self, callback: Any) -> None:
        self.shutdown_callbacks.append(callback)


async def _run_room_handler(room: FakeRoom, event: str, *args: Any) -> None:
    """Invoke a sync LiveKit-style handler and allow scheduled work to drain."""

    callback = room.handlers[event]
    result = callback(*args)
    if inspect.isawaitable(result):
        await result
    # ``attach_room`` schedules async handlers from LiveKit's synchronous event
    # emitter.  A few turns cover the handler, stream creation, and consumer.
    for _ in range(4):
        await asyncio.sleep(0)


async def _invoke_shutdown_callback(callback: Any) -> None:
    """Support both JobContext callback spellings (with or without reason)."""

    try:
        result = callback("offline test")
    except TypeError:
        result = callback()
    if inspect.isawaitable(result):
        await result


class LiveAgentBackpressureTests(unittest.IsolatedAsyncioTestCase):
    async def test_latest_frame_slot_stays_bounded_and_processes_only_newest_pending_frame(
        self,
    ) -> None:
        """Three-plus arrivals during one inference coalesce to the newest frame."""

        slot = LatestFrameSlot[str]()
        inference_started = asyncio.Event()
        release_inference = asyncio.Event()
        processed: list[str] = []
        active = 0
        max_active = 0
        max_pending_observed = 0

        async def infer(frame: str) -> str:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            processed.append(frame)
            if frame == "frame-1":
                inference_started.set()
                await release_inference.wait()
            active -= 1
            return frame

        processor = LatestFrameProcessor(infer, slot=slot)
        self.assertEqual(slot.maxsize, 1)

        self.assertTrue(processor.submit_nowait("frame-1"))
        await asyncio.wait_for(inference_started.wait(), timeout=1)

        # Arrivals happen while frame-1 is still in flight.  Every put is
        # observed so this proves the bound throughout the burst, not merely
        # at the end of it.
        for index in range(2, 8):
            self.assertTrue(processor.submit_nowait(f"frame-{index}"))
            max_pending_observed = max(max_pending_observed, processor.queue_size)
            self.assertLessEqual(processor.queue_size, 1)
            self.assertLessEqual(processor.pending_count, 1)

        self.assertEqual(processor.queue_size, 1)
        self.assertEqual(processor.latest_frame, "frame-7")
        self.assertLessEqual(max_pending_observed, 1)

        release_inference.set()
        await processor.wait_idle()

        self.assertEqual(processed, ["frame-1", "frame-7"])
        self.assertEqual(processor.last_processed_frame, "frame-7")
        self.assertEqual(max_active, 1)
        self.assertEqual(processor.max_in_flight, 1)
        self.assertGreaterEqual(processor.dropped_count, 5)
        processor.stop()

    async def test_camera_video_is_the_only_publication_subscribed(self) -> None:
        """Audio, screen share, and non-camera video are explicitly rejected."""

        processor = LatestFrameProcessor(lambda frame: frame)
        streams: dict[int, FakeVideoStream] = {}

        async def make_stream(track: FakeTrack) -> FakeVideoStream:
            stream = FakeVideoStream()
            streams[id(track)] = stream
            return stream

        subscriber = CameraVideoTrackSubscriber(processor, stream_factory=make_stream)
        camera_track = FakeTrack(kind="video", source="camera")
        camera = FakePublication(
            kind="video", source="camera", track=camera_track
        )
        audio = FakePublication(
            kind="audio", source="microphone", track=FakeTrack("audio", "microphone")
        )
        screen_share = FakePublication(
            kind="video",
            source="screen_share",
            track=FakeTrack("video", "screen_share"),
        )
        non_camera_video = FakePublication(
            kind="video",
            source="unknown",
            track=FakeTrack("video", "unknown"),
        )

        self.assertTrue(await subscriber.subscribe_publication(camera))
        self.assertFalse(await subscriber.subscribe_publication(audio))
        self.assertFalse(await subscriber.subscribe_publication(screen_share))
        self.assertFalse(await subscriber.subscribe_publication(non_camera_video))

        self.assertEqual(camera.subscription_calls, [True])
        self.assertEqual(audio.subscription_calls, [False])
        self.assertEqual(screen_share.subscription_calls, [False])
        self.assertEqual(non_camera_video.subscription_calls, [False])
        self.assertEqual(len(streams), 1)
        self.assertIn(id(camera_track), streams)

        # Let the consumer task enter its async iterator before shutdown so
        # cancellation exercises the stream's normal ``aclose`` path.
        await asyncio.sleep(0)
        await subscriber.stop()
        self.assertTrue(streams[id(camera_track)].closed)

    async def test_runtime_connects_with_implicit_subscriptions_disabled_and_filters_existing_tracks(
        self,
    ) -> None:
        """Agent startup uses manual camera-only subscription for room tracks."""

        camera = FakePublication(
            kind="video",
            source="camera",
            track=FakeTrack("video", "camera"),
        )
        audio = FakePublication(
            kind="audio",
            source="microphone",
            track=FakeTrack("audio", "microphone"),
        )
        screen = FakePublication(
            kind="video",
            source="screen_share",
            track=FakeTrack("video", "screen_share"),
        )
        room = FakeRoom(
            remote_participants=[FakeParticipant([camera, audio, screen])]
        )
        context = FakeAgentContext(room)
        streams: list[FakeVideoStream] = []

        async def make_stream(track: FakeTrack) -> FakeVideoStream:
            del track
            stream = FakeVideoStream()
            streams.append(stream)
            return stream

        runtime = await start_agent_runtime(
            context,
            lambda frame: frame,
            stream_factory=make_stream,
        )

        self.assertEqual(context.connect_calls, [{"auto_subscribe": "subscribe_none"}])
        self.assertEqual(camera.subscription_calls, [True])
        self.assertEqual(audio.subscription_calls, [False])
        self.assertEqual(screen.subscription_calls, [False])
        self.assertEqual(runtime.subscriber.active_stream_count, 1)
        self.assertEqual(len(streams), 1)

        await asyncio.sleep(0)
        await runtime.subscriber.stop()
        self.assertTrue(streams[0].closed)

    async def test_actual_livekit_track_enums_accept_camera_and_reject_audio_or_screen(
        self,
    ) -> None:
        """Use the installed SDK's protobuf enum values when the probe has it."""

        try:
            from livekit import rtc  # type: ignore[import-not-found]
        except ImportError:
            self.skipTest("LiveKit SDK is not installed in this offline environment")

        screen_source = getattr(rtc.TrackSource, "SOURCE_SCREENSHARE", None)
        if screen_source is None:
            screen_source = getattr(rtc.TrackSource, "SOURCE_SCREEN_SHARE", None)
        if screen_source is None:
            self.skipTest("installed LiveKit SDK has no screen-share source enum")

        camera = FakeTrack(rtc.TrackKind.KIND_VIDEO, rtc.TrackSource.SOURCE_CAMERA)
        audio = FakeTrack(
            rtc.TrackKind.KIND_AUDIO,
            getattr(rtc.TrackSource, "SOURCE_MICROPHONE", "microphone"),
        )
        screen = FakeTrack(rtc.TrackKind.KIND_VIDEO, screen_source)

        self.assertTrue(is_video_kind(rtc.TrackKind.KIND_VIDEO))
        self.assertTrue(is_camera_video_track(camera))
        self.assertFalse(is_camera_video_track(audio))
        self.assertFalse(is_camera_video_track(screen))

    async def test_make_stream_uses_keyword_only_track_and_capacity_one(self) -> None:
        """The current rtc.VideoStream API requires ``track=`` and capacity 1."""

        calls: list[tuple[Any, int]] = []

        class FakeSDKVideoStream:
            @classmethod
            async def from_track(
                cls,
                *,
                track: Any,
                capacity: int,
            ) -> "FakeSDKVideoStream":
                calls.append((track, capacity))
                return cls()

        fake_livekit = ModuleType("livekit")
        fake_rtc = ModuleType("livekit.rtc")
        fake_rtc.VideoStream = FakeSDKVideoStream  # type: ignore[attr-defined]
        fake_livekit.rtc = fake_rtc  # type: ignore[attr-defined]

        processor = LatestFrameProcessor(lambda frame: frame)
        subscriber = CameraVideoTrackSubscriber(processor)
        track = FakeTrack(kind="video", source="camera")

        with patch.dict(
            sys.modules,
            {"livekit": fake_livekit, "livekit.rtc": fake_rtc},
        ):
            stream = await subscriber._make_stream(track)

        self.assertIsInstance(stream, FakeSDKVideoStream)
        self.assertEqual(calls, [(track, 1)])
        processor.stop()

    async def test_track_published_after_startup_subscribes_camera_only(self) -> None:
        """New publications after startup use the same camera-only filter."""

        camera = FakePublication(
            kind="video",
            source="camera",
            track=FakeTrack("video", "camera"),
        )
        audio = FakePublication(
            kind="audio",
            source="microphone",
            track=FakeTrack("audio", "microphone"),
        )
        screen = FakePublication(
            kind="video",
            source="screen_share",
            track=FakeTrack("video", "screen_share"),
        )
        participant = FakeParticipant([])
        room = FakeRoom(remote_participants=[])
        context = FakeAgentContext(room)
        streams: list[FakeVideoStream] = []

        async def make_stream(track: FakeTrack) -> FakeVideoStream:
            del track
            stream = FakeVideoStream()
            streams.append(stream)
            return stream

        runtime = await start_agent_runtime(
            context,
            lambda frame: frame,
            stream_factory=make_stream,
        )
        self.assertIn("track_published", room.handlers)

        await _run_room_handler(room, "track_published", camera, participant)
        await _run_room_handler(room, "track_published", audio, participant)
        await _run_room_handler(room, "track_published", screen, participant)

        self.assertEqual(camera.subscription_calls, [True])
        self.assertEqual(audio.subscription_calls, [False])
        self.assertEqual(screen.subscription_calls, [False])
        self.assertEqual(len(streams), 1)
        self.assertEqual(runtime.subscriber.active_stream_count, 1)

        await asyncio.sleep(0)
        await runtime.subscriber.stop()
        self.assertTrue(streams[0].closed)

    async def test_entrypoint_registers_shutdown_and_closes_runtime_resources(self) -> None:
        """Invoking the registered JobContext shutdown callback cleans up all handles."""

        camera = FakePublication(
            kind="video",
            source="camera",
            track=FakeTrack("video", "camera"),
        )
        room = FakeRoom(remote_participants=[FakeParticipant([camera])])
        context = FakeAgentContext(room)
        streams: list[FakeVideoStream] = []

        async def make_stream(track: FakeTrack) -> FakeVideoStream:
            del track
            stream = FakeVideoStream()
            streams.append(stream)
            return stream

        runtime = await entrypoint(
            context,
            inference=lambda frame: frame,
            stream_factory=make_stream,
        )
        self.assertEqual(len(context.shutdown_callbacks), 1)
        self.assertEqual(len(streams), 1)

        # Ensure the stream consumer has entered its iterator before the
        # shutdown callback is invoked.
        await asyncio.sleep(0)
        await _invoke_shutdown_callback(context.shutdown_callbacks[0])

        self.assertTrue(runtime.subscriber._closed)
        self.assertTrue(runtime.subscriber.processor.closed)
        self.assertEqual(runtime.subscriber.active_stream_count, 0)
        self.assertTrue(streams[0].closed)

    async def test_connect_rejecting_auto_subscribe_fails_closed_without_retry(self) -> None:
        """Never fall back to an argumentless/default (possibly broad) subscription."""

        class RejectsAutoSubscribe(FakeAgentContext):
            def __init__(self) -> None:
                super().__init__(FakeRoom(remote_participants=[]))
                self.argumentless_attempts = 0

            async def connect(self, **kwargs: Any) -> None:
                if "auto_subscribe" in kwargs:
                    self.connect_calls.append(kwargs)
                    raise TypeError("auto_subscribe is unsupported")
                self.argumentless_attempts += 1
                raise AssertionError("argumentless connect would use an unsafe default")

        context = RejectsAutoSubscribe()

        with self.assertRaises(RuntimeError):
            await start_agent_runtime(context, lambda frame: frame)

        self.assertEqual(context.connect_calls, [{"auto_subscribe": "subscribe_none"}])
        self.assertEqual(context.argumentless_attempts, 0)


if __name__ == "__main__":  # pragma: no cover - unittest entrypoint
    unittest.main()
