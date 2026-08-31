"""Strict live Responses adapter and LiveKit frame encoding contracts."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from io import BytesIO

import pytest
from PIL import Image

from backend.providers.runtime import guidance_input_from_frame
from backend.providers.vision_guidance import (
    GUIDANCE_CODES,
    EncodedImage,
    GuidanceContractError,
    GuidanceInput,
    GuidanceShot,
)
from backend.providers.vision_guidance_responses import (
    ResponsesVisionGuidanceAnalyzer,
)


@dataclass
class _Response:
    output_parsed: object | None = None
    output_text: object | None = None


@dataclass
class _Client:
    result: object
    calls: list[dict[str, object]] = field(default_factory=list)

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _jpeg() -> bytes:
    output = BytesIO()
    Image.new("RGB", (8, 6), (60, 100, 140)).save(output, format="JPEG")
    return output.getvalue()


def test_live_guidance_request_is_strict_finite_and_non_persistent() -> None:
    client = _Client(_Response(output_parsed={"code": "CENTER_GARMENT", "confidence": 0.8}))
    analyzer = ResponsesVisionGuidanceAnalyzer(client, "guidance-test-model")
    input_value = GuidanceInput(
        EncodedImage(_jpeg(), "image/jpeg", width=8, height=6),
        GuidanceShot.FRONT,
    )

    decision = asyncio.run(analyzer(input_value))

    assert decision.code.value == "CENTER_GARMENT"
    assert decision.confidence == 0.8
    request = client.calls[0]
    assert set(request) == {"model", "store", "instructions", "input", "text"}
    assert request["store"] is False
    schema = request["text"]["format"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["code", "confidence"]
    assert schema["properties"]["code"]["enum"] == list(GUIDANCE_CODES)
    assert request["text"]["format"]["strict"] is True
    image_url = request["input"][0]["content"][1]["image_url"]
    assert image_url == "data:image/jpeg;base64," + base64.b64encode(_jpeg()).decode("ascii")


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "FREE_TEXT", "confidence": 0.8},
        {"code": "READY", "confidence": float("nan")},
        {"code": "READY", "confidence": 0.8, "message": "provider copy"},
        {"code": "READY"},
    ],
)
def test_live_guidance_rejects_unknown_nonfinite_or_open_responses(payload: object) -> None:
    analyzer = ResponsesVisionGuidanceAnalyzer(
        _Client(_Response(output_parsed=payload)),
        "guidance-test-model",
    )
    input_value = GuidanceInput(EncodedImage(_jpeg()), GuidanceShot.BACK)

    with pytest.raises(GuidanceContractError):
        asyncio.run(analyzer(input_value))


def test_actual_livekit_video_frame_is_encoded_to_bounded_jpeg() -> None:
    from livekit import rtc

    width, height = 4, 3
    rgba = bytes([24, 72, 120, 255] * width * height)
    frame = rtc.VideoFrame(width, height, rtc.VideoBufferType.RGBA, rgba)

    guidance = guidance_input_from_frame(frame, requested_shot="front")

    assert guidance.frame.mime_type == "image/jpeg"
    assert guidance.frame.width == width
    assert guidance.frame.height == height
    with Image.open(BytesIO(guidance.frame.data)) as image:
        assert image.format == "JPEG"
        assert image.size == (width, height)
