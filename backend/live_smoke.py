"""Backend-only live smoke for LiveKit, AI providers, rembg, and backgrounds.

Run after loading server-only environment variables::

    python -m backend.live_smoke

The command uses a synthetic Python camera track, not a browser or iPhone.  It
prints only finite public diagnostics and never prints credentials or provider
response internals.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from .app import create_app
from .guidance_transport import GuidanceTransportAdapter
from .live_agent import CameraVideoTrackSubscriber, LatestFrameProcessor
from .livekit_token import LiveKitConfig, mint_livekit_token
from .providers.background_generator import BackgroundGenerator
from .providers.runtime import (
    LiveVisionGuidanceProvider,
    create_provider_inference,
    create_vision_guidance_provider,
)
from .providers.vision_guidance import (
    GUIDANCE_CODES,
    GuidanceShot,
    VisionDecision,
    validate_vision_decision,
)
from .settings import BackendSettings, ProviderMode


_REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
_GARMENT_FIXTURES = _REPOSITORY_ROOT / "fixtures" / "garment"


class LiveSmokeError(RuntimeError):
    """A finite live-smoke failure safe to show without provider internals."""


@dataclass(frozen=True, slots=True)
class LiveSmokeResult:
    camera_subscribed: bool
    guidance_codes: tuple[str, ...]
    shot_type: str
    measurement_endpoint_count: int
    mask_sha256: str
    background_sha256: str

    def to_payload(self) -> dict[str, object]:
        return {
            "cameraSubscribed": self.camera_subscribed,
            "guidanceCodes": list(self.guidance_codes),
            "shotType": self.shot_type,
            "measurementEndpointCount": self.measurement_endpoint_count,
            "maskSha256": self.mask_sha256,
            "backgroundSha256": self.background_sha256,
        }


@dataclass(frozen=True, slots=True)
class _GuidanceSmokeResult:
    camera_subscribed: bool
    guidance_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ApiSmokeResult:
    shot_type: str
    measurement_endpoint_count: int
    mask_sha256: str


GuidanceCheck = Callable[[BackendSettings], Awaitable[_GuidanceSmokeResult]]
ApiCheck = Callable[[BackendSettings], Awaitable[_ApiSmokeResult]]
BackgroundCheck = Callable[[BackendSettings], Awaitable[str]]


def _livekit_config(settings: BackendSettings) -> LiveKitConfig:
    return LiveKitConfig(
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
        url=settings.livekit_url,
        token_ttl_seconds=settings.livekit_token_ttl_seconds,
        max_token_ttl_seconds=settings.livekit_token_max_ttl_seconds,
    )


def _mint_agent_token(settings: BackendSettings, room_name: str, identity: str) -> str:
    try:
        from livekit import api

        grants = api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=False,
            can_subscribe=True,
            can_publish_data=True,
        )
        return (
            api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
            .with_identity(identity)
            .with_ttl(timedelta(seconds=min(settings.livekit_token_ttl_seconds, 300)))
            .with_grants(grants)
            .to_jwt()
        )
    except Exception as error:
        raise LiveSmokeError("Agent token could not be created") from error


def _video_frame(path: Path, *, max_side: int = 640) -> Any:
    try:
        from livekit import rtc

        with Image.open(path) as source:
            image = source.convert("RGBA")
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            return rtc.VideoFrame(
                image.width,
                image.height,
                rtc.VideoBufferType.RGBA,
                image.tobytes(),
            )
    except Exception as error:
        raise LiveSmokeError("Camera fixture could not be encoded") from error


async def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float,
    message: str,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise LiveSmokeError(message)
        await asyncio.sleep(0.05)


async def _run_livekit_guidance_smoke(
    settings: BackendSettings,
) -> _GuidanceSmokeResult:
    settings.require_livekit()
    try:
        from livekit import rtc
    except ImportError as error:
        raise LiveSmokeError("LiveKit RTC is unavailable") from error

    provider = create_vision_guidance_provider(settings)
    if not isinstance(provider, LiveVisionGuidanceProvider) or not provider.available:
        raise LiveSmokeError("Live vision guidance provider is unavailable")

    room_name = f"listing-photo-backend-smoke-{uuid.uuid4().hex[:16]}"
    publisher_identity = f"smoke-camera-{uuid.uuid4().hex[:12]}"
    agent_identity = f"smoke-agent-{uuid.uuid4().hex[:12]}"
    publisher_token, _ = mint_livekit_token(
        config=_livekit_config(settings),
        identity=publisher_identity,
        room=room_name,
    )
    agent_token = _mint_agent_token(settings, room_name, agent_identity)

    agent_room = rtc.Room()
    publisher_room = rtc.Room()
    subscriber: CameraVideoTrackSubscriber | None = None
    transport: GuidanceTransportAdapter | None = None
    source: Any = None
    publication: Any = None
    decisions: list[VisionDecision] = []
    current_shot = [GuidanceShot.FRONT]

    try:
        await agent_room.connect(
            settings.livekit_url,
            agent_token,
            rtc.RoomOptions(auto_subscribe=False),
        )
        base_inference = create_provider_inference(
            provider,
            requested_shot=lambda: current_shot[0],
        )

        async def inference(frame: object) -> VisionDecision:
            decision = validate_vision_decision(await base_inference(frame))
            decisions.append(decision)
            return decision

        transport = GuidanceTransportAdapter(
            inference,
            agent_room.local_participant,
            session_id=room_name,
        )

        async def process_frame(frame: object) -> object:
            return await transport.process_frame(
                frame,
                shot=current_shot[0],
                observed_at=int(time.time() * 1000),
            )

        processor = LatestFrameProcessor(process_frame)
        subscriber = CameraVideoTrackSubscriber(processor)
        subscriber.attach_room(agent_room)

        await publisher_room.connect(settings.livekit_url, publisher_token)
        first_frame = _video_frame(_GARMENT_FIXTURES / "dark.png")
        source = rtc.VideoSource(first_frame.width, first_frame.height)
        track = rtc.LocalVideoTrack.create_video_track("backend-smoke-camera", source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)
        publication = await publisher_room.local_participant.publish_track(track, options)

        await subscriber.subscribe_existing_publications(agent_room)
        await _wait_until(
            lambda: subscriber is not None and subscriber.active_stream_count == 1,
            timeout=15,
            message="Agent did not subscribe to the camera track",
        )

        samples: Sequence[tuple[GuidanceShot, str]] = (
            (GuidanceShot.FRONT, "dark.png"),
            (GuidanceShot.FRONT, "front.png"),
            (GuidanceShot.BACK, "wrong-shot.png"),
            (GuidanceShot.TAG, "tag.png"),
        )
        for shot, filename in samples:
            current_shot[0] = shot
            expected_count = len(decisions) + 1
            source.capture_frame(_video_frame(_GARMENT_FIXTURES / filename))
            await _wait_until(
                lambda expected=expected_count: len(decisions) >= expected,
                timeout=45,
                message="Live guidance did not return a finite decision",
            )
            if len({decision.code.value for decision in decisions}) >= 2:
                break

        codes = tuple(decision.code.value for decision in decisions)
        if not codes or any(code not in GUIDANCE_CODES for code in codes):
            raise LiveSmokeError("Live guidance returned a non-finite code")
        if len(set(codes)) < 2:
            raise LiveSmokeError("Live guidance code did not change across smoke frames")
        return _GuidanceSmokeResult(True, codes)
    except LiveSmokeError:
        raise
    except Exception as error:
        raise LiveSmokeError("LiveKit guidance smoke failed") from error
    finally:
        if subscriber is not None:
            await subscriber.stop()
        if transport is not None:
            await transport.close()
        if publication is not None and publisher_room.isconnected():
            await publisher_room.local_participant.unpublish_track(publication.sid)
        if source is not None:
            await source.aclose()
        if publisher_room.isconnected():
            await publisher_room.disconnect()
        if agent_room.isconnected():
            await agent_room.disconnect()


def _api_failure_code(response: Any) -> str:
    try:
        detail = response.json().get("detail", {})
        code = detail.get("code")
        return code if isinstance(code, str) else "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def _run_live_api_smoke_sync(settings: BackendSettings) -> _ApiSmokeResult:
    front = (_GARMENT_FIXTURES / "front.png").read_bytes()
    measurement = (_GARMENT_FIXTURES / "back.png").read_bytes()
    app = create_app(settings)
    with TestClient(app) as client:
        shot = client.post(
            "/api/analyze-shot",
            data={"requestedShot": "front"},
            files={"file": ("front.png", front, "image/png")},
        )
        if shot.status_code != 200:
            raise LiveSmokeError(
                "Live shot assessment failed: " + _api_failure_code(shot)
            )

        endpoints = client.post(
            "/api/suggest-measurement-points",
            files={"file": ("measurement.png", measurement, "image/png")},
        )
        if endpoints.status_code != 200:
            raise LiveSmokeError(
                "Live measurement suggestion failed: " + _api_failure_code(endpoints)
            )

        mask = client.post(
            "/api/remove-background",
            files={"file": ("front.png", front, "image/png")},
        )
        if mask.status_code != 200 or not mask.headers.get(
            "content-type", ""
        ).startswith("image/png"):
            raise LiveSmokeError(
                "Live front masking failed: " + _api_failure_code(mask)
            )

    shot_payload = shot.json()
    endpoint_payload = endpoints.json()
    if set(endpoint_payload) != {
        "lengthStart",
        "lengthEnd",
        "widthStart",
        "widthEnd",
    }:
        raise LiveSmokeError("Live measurement response was not four endpoints")
    return _ApiSmokeResult(
        shot_type=shot_payload["shotType"],
        measurement_endpoint_count=len(endpoint_payload),
        mask_sha256=hashlib.sha256(mask.content).hexdigest(),
    )


async def _run_live_api_smoke(settings: BackendSettings) -> _ApiSmokeResult:
    return await asyncio.to_thread(_run_live_api_smoke_sync, settings)


async def _run_live_background_smoke(_settings: BackendSettings) -> str:
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise LiveSmokeError("OpenAI is not configured for background generation")
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI()
        try:
            model = os.environ.get("BACKGROUND_GENERATOR_MODEL", "").strip()
            background = await BackgroundGenerator(
                client.images,
                model or "gpt-image-1",
            ).generate("studio-white")
        finally:
            await client.close()
        return hashlib.sha256(background.data).hexdigest()
    except LiveSmokeError:
        raise
    except Exception as error:
        raise LiveSmokeError("Live background generation failed") from error


async def run_live_smoke(
    settings: BackendSettings | None = None,
    *,
    guidance_check: GuidanceCheck = _run_livekit_guidance_smoke,
    api_check: ApiCheck = _run_live_api_smoke,
    background_check: BackgroundCheck = _run_live_background_smoke,
) -> LiveSmokeResult:
    """Run the required live backend operations in their production order."""

    resolved = settings or BackendSettings.from_env(provider_mode=ProviderMode.LIVE)
    if resolved.provider_mode is not ProviderMode.LIVE:
        raise LiveSmokeError("Live smoke requires PROVIDER_MODE=live")

    guidance = await guidance_check(resolved)
    api = await api_check(resolved)
    background_sha256 = await background_check(resolved)
    return LiveSmokeResult(
        camera_subscribed=guidance.camera_subscribed,
        guidance_codes=guidance.guidance_codes,
        shot_type=api.shot_type,
        measurement_endpoint_count=api.measurement_endpoint_count,
        mask_sha256=api.mask_sha256,
        background_sha256=background_sha256,
    )


def main() -> int:
    try:
        result = asyncio.run(run_live_smoke())
    except LiveSmokeError as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False))
        return 1
    print(
        json.dumps(
            {"status": "ok", **result.to_payload()},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    raise SystemExit(main())


__all__ = ["LiveSmokeError", "LiveSmokeResult", "main", "run_live_smoke"]
