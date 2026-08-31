"""Concrete provider selection for fixture and live backend modes."""

from __future__ import annotations

from io import BytesIO
import inspect
import os
from collections.abc import Awaitable, Callable, Mapping
from typing import TypeAlias

from backend.settings import BackendSettings, ProviderMode

from .vision_guidance import (
    EncodedImage,
    GuidanceCode,
    GuidanceInput,
    GuidanceShot,
    VisionDecision,
    VisionGuidanceProvider,
    validate_guidance_input,
    validate_vision_decision,
)


class ProviderUnavailableError(RuntimeError):
    """Raised when a selected provider cannot perform live analysis."""


LiveAnalyzerResult: TypeAlias = VisionDecision | Mapping[str, object]
LiveAnalyzer: TypeAlias = Callable[
    [GuidanceInput], LiveAnalyzerResult | Awaitable[LiveAnalyzerResult]
]
ProviderInference: TypeAlias = Callable[[object], Awaitable[LiveAnalyzerResult]]
RequestedShot: TypeAlias = GuidanceShot | str | Callable[[], GuidanceShot | str]


class FixtureVisionGuidanceProvider:
    """Deterministic offline provider used only when fixture is explicit."""

    async def analyze(self, input: GuidanceInput) -> VisionDecision:
        validate_guidance_input(input)
        return VisionDecision(code=GuidanceCode.READY, confidence=1.0)


class LiveVisionGuidanceProvider:
    """Validated adapter around an injected live image analyzer.

    The analyzer is deliberately injected at the provider boundary.  If it is
    absent or fails, the error is propagated and no fixture result is used.
    """

    def __init__(self, analyzer: LiveAnalyzer | None = None) -> None:
        self._analyzer = analyzer

    @property
    def available(self) -> bool:
        return self._analyzer is not None

    async def analyze(self, input: GuidanceInput) -> VisionDecision:
        validated_input = validate_guidance_input(input)
        if self._analyzer is None:
            raise ProviderUnavailableError("live guidance analyzer is not configured")

        result = self._analyzer(validated_input)
        if inspect.isawaitable(result):
            result = await result
        return validate_vision_decision(result)


def create_vision_guidance_provider(
    settings: BackendSettings,
    *,
    live_analyzer: LiveAnalyzer | None = None,
) -> VisionGuidanceProvider:
    """Select exactly one provider from validated settings."""

    if settings.provider_mode is ProviderMode.FIXTURE:
        return FixtureVisionGuidanceProvider()
    if settings.provider_mode is ProviderMode.LIVE:
        analyzer = live_analyzer
        if analyzer is None:
            analyzer = _create_responses_live_analyzer()
        return LiveVisionGuidanceProvider(analyzer)
    # ``BackendSettings`` already rejects this, but keep the boundary closed
    # if a non-standard object is supplied by an integration.
    raise ProviderUnavailableError("unsupported provider mode")


def _create_responses_live_analyzer() -> LiveAnalyzer | None:
    """Build the configured live analyzer without ever selecting a fixture."""

    if not os.environ.get("OPENAI_API_KEY", "").strip():
        return None
    try:
        from openai import AsyncOpenAI  # type: ignore[import-not-found]

        from .vision_guidance_responses import ResponsesVisionGuidanceAnalyzer

        client = AsyncOpenAI().responses
        model = os.environ.get("VISION_GUIDANCE_MODEL", "").strip()
        return ResponsesVisionGuidanceAnalyzer(client, model or "gpt-5.6-luna")
    except Exception:
        # Live mode stays selected.  ``LiveVisionGuidanceProvider`` reports an
        # explicit unavailable error when the analyzer cannot be constructed.
        return None


def _encode_livekit_video_frame(frame: object) -> EncodedImage | None:
    """Convert an actual LiveKit raw frame into one bounded JPEG provider input."""

    convert = getattr(frame, "convert", None)
    frame_type = getattr(frame, "type", None)
    if not callable(convert) or frame_type is None:
        return None
    try:
        from livekit import rtc  # type: ignore[import-not-found]
        from PIL import Image

        rgba = (
            frame
            if frame_type == rtc.VideoBufferType.RGBA
            else convert(rtc.VideoBufferType.RGBA)
        )
        width = int(getattr(rgba, "width"))
        height = int(getattr(rgba, "height"))
        if width <= 0 or height <= 0:
            raise ValueError("invalid LiveKit frame dimensions")
        image = Image.frombytes("RGBA", (width, height), bytes(rgba.data)).convert("RGB")
        image.thumbnail((640, 640), Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(output, format="JPEG", quality=85, optimize=True)
        return EncodedImage(
            output.getvalue(),
            "image/jpeg",
            width=image.width,
            height=image.height,
        )
    except ProviderUnavailableError:
        raise
    except Exception as error:
        raise ProviderUnavailableError(
            "LiveKit camera frame could not be encoded for guidance"
        ) from error


def guidance_input_from_frame(
    frame: object,
    *,
    requested_shot: GuidanceShot | str = GuidanceShot.FRONT,
) -> GuidanceInput:
    """Adapt bytes or a LiveKit ``VideoFrame`` to the provider contract.

    LiveKit frames expose a bytes-compatible ``data`` buffer plus positive
    ``width`` and ``height`` fields.  They are marked as an internal raw image
    MIME type; encoding/downscaling can later be injected at this single
    boundary without changing the provider or Agent server contracts.
    """

    if isinstance(frame, GuidanceInput):
        return validate_guidance_input(frame)
    if isinstance(frame, EncodedImage):
        return GuidanceInput(frame=frame, requested_shot=requested_shot)
    if isinstance(frame, (bytes, bytearray, memoryview)):
        encoded = EncodedImage(data=bytes(frame))
        return GuidanceInput(frame=encoded, requested_shot=requested_shot)

    encoded_livekit_frame = _encode_livekit_video_frame(frame)
    if encoded_livekit_frame is not None:
        return GuidanceInput(
            frame=encoded_livekit_frame,
            requested_shot=requested_shot,
        )

    raw_data = getattr(frame, "data", None)
    if raw_data is None:
        raise ProviderUnavailableError(
            "camera frame must be bytes, EncodedImage, GuidanceInput, "
            "or expose data/width/height"
        )
    try:
        data = bytes(raw_data)
    except (TypeError, ValueError) as error:
        raise ProviderUnavailableError("camera frame data is not bytes-compatible") from error

    mime_type = getattr(frame, "mime_type", None) or getattr(frame, "mimeType", None)
    if mime_type is None:
        mime_type = "image/x-livekit-frame"
    encoded = EncodedImage(
        data=data,
        mime_type=mime_type,
        width=getattr(frame, "width", None),
        height=getattr(frame, "height", None),
    )
    return GuidanceInput(frame=encoded, requested_shot=requested_shot)


def create_provider_inference(
    provider: VisionGuidanceProvider,
    *,
    requested_shot: RequestedShot = GuidanceShot.FRONT,
) -> ProviderInference:
    """Create the inference callback passed to ``create_agent_server``.

    ``requested_shot`` may be a zero-argument supplier for a long-lived Agent
    session.  Resolving it for each frame keeps the provider input aligned
    with the runtime's selected capture step without changing the legacy
    ``inference(frame)`` callback shape.
    """

    async def infer(frame: object) -> LiveAnalyzerResult:
        shot = requested_shot() if callable(requested_shot) else requested_shot
        input_value = guidance_input_from_frame(
            frame,
            requested_shot=shot,
        )
        return await provider.analyze(input_value)

    return infer


__all__ = [
    "FixtureVisionGuidanceProvider",
    "LiveAnalyzer",
    "LiveVisionGuidanceProvider",
    "ProviderInference",
    "ProviderUnavailableError",
    "RequestedShot",
    "create_provider_inference",
    "create_vision_guidance_provider",
    "guidance_input_from_frame",
]
