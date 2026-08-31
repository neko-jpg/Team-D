"""Contract and deterministic regression tests for local geometry guidance."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from backend.providers.geometry_guidance import (
    GEOMETRY_MASK_MODEL,
    GEOMETRY_MASK_TIMEOUT_SECONDS,
    GEOMETRY_PREWARM_TIMEOUT_SECONDS,
    GEOMETRY_IMAGE_MAX_EDGE,
    GEOMETRY_JPEG_QUALITY,
    GarmentGeometry,
    GeometryGuidanceContractError,
    GeometryGuidanceProvider,
    GeometryGuidanceProviderError,
    GeometryGuidanceTimeoutError,
    classify_geometry,
    classify_mask_geometry,
    geometry_from_mask_png,
)
from backend.providers.vision_guidance import (
    EncodedImage,
    GuidanceCode,
    GuidanceInput,
)


IMAGE_SIZE = (100, 100)
REMOVE_URL = "http://127.0.0.1:7000/api/remove"


def _image_png(size: tuple[int, int] = IMAGE_SIZE) -> bytes:
    output = BytesIO()
    with Image.new("RGB", size, (245, 245, 245)) as image:
        image.save(output, format="PNG")
    return output.getvalue()


def _mask_png(
    *,
    size: tuple[int, int] = IMAGE_SIZE,
    boxes: tuple[tuple[int, int, int, int], ...] = ((29, 29, 71, 71),),
    mode: str = "L",
) -> bytes:
    output = BytesIO()
    with Image.new(mode, size, 0) as mask:
        draw = ImageDraw.Draw(mask)
        for box in boxes:
            draw.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=255)
        mask.save(output, format="PNG")
    return output.getvalue()


def _input(shot: str = "front") -> GuidanceInput:
    return GuidanceInput(
        frame=EncodedImage(_image_png(), "image/png", *IMAGE_SIZE),
        requested_shot=shot,
    )


@dataclass
class FakeResponse:
    content: bytes
    status_code: int = 200
    headers: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if self.headers is None:
            self.headers = {"Content-Type": "image/png"}


class FakeClient:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.calls: list[dict[str, object]] = []

    async def post(self, url: str, **kwargs: object) -> Any:
        self.calls.append({"url": url, **kwargs})
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            return await result()
        return result


def _geometry(
    *,
    bbox: tuple[int, int, int, int],
    size: tuple[int, int] = IMAGE_SIZE,
) -> GarmentGeometry:
    left, top, right, bottom = bbox
    component_pixels = (right - left) * (bottom - top)
    return GarmentGeometry(
        image_width=size[0],
        image_height=size[1],
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        component_pixels=component_pixels,
        foreground_pixels=component_pixels,
    )


@pytest.mark.parametrize(
    ("bbox", "expected"),
    [
        ((1, 10, 99, 90), GuidanceCode.SHOW_FULL_GARMENT),
        ((2, 29, 43, 70), GuidanceCode.MOVE_CLOSER),
        ((11, 11, 89, 89), GuidanceCode.MOVE_FARTHER),
        ((42, 29, 84, 71), GuidanceCode.CENTER_GARMENT),
        ((29, 29, 71, 71), None),
    ],
)
def test_classifier_applies_strict_priority_and_never_returns_ready(
    bbox: tuple[int, int, int, int], expected: GuidanceCode | None
) -> None:
    decision = classify_geometry(_geometry(bbox=bbox))
    if expected is None:
        assert decision is None
    else:
        assert decision is not None
        assert decision.code is expected
    if decision is not None:
        assert decision.code is not GuidanceCode.READY
        assert decision.confidence == 1.0


def test_classifier_boundaries_are_exact() -> None:
    # Border distance 1 is unsafe, while 2 is not.
    assert classify_geometry(_geometry(bbox=(1, 29, 43, 71))).code is GuidanceCode.SHOW_FULL_GARMENT  # type: ignore[union-attr]

    # Span exactly 0.42 is PASS; only values below it ask the user to move closer.
    assert classify_geometry(_geometry(bbox=(29, 29, 71, 71))) is None
    assert classify_geometry(_geometry(bbox=(30, 30, 71, 71))).code is GuidanceCode.MOVE_CLOSER  # type: ignore[union-attr]

    # Span exactly 0.77 is PASS; only values above it ask the user to move farther.
    assert classify_geometry(_geometry(bbox=(11, 12, 88, 89))) is None
    assert classify_geometry(_geometry(bbox=(11, 11, 89, 89))).code is GuidanceCode.MOVE_FARTHER  # type: ignore[union-attr]

    # Axis offset exactly 0.12 is PASS; 0.13 is a centering correction.
    assert classify_geometry(_geometry(bbox=(41, 29, 83, 71))) is None
    assert classify_geometry(_geometry(bbox=(42, 29, 84, 71))).code is GuidanceCode.CENTER_GARMENT  # type: ignore[union-attr]


def test_largest_component_ignores_smaller_border_noise() -> None:
    geometry = geometry_from_mask_png(
        _mask_png(boxes=((0, 0, 2, 2), (29, 29, 71, 71))), IMAGE_SIZE
    )
    assert (geometry.left, geometry.top, geometry.right, geometry.bottom) == (
        29,
        29,
        71,
        71,
    )
    assert geometry.component_pixels == 42 * 42
    assert geometry.foreground_pixels == 42 * 42 + 4
    assert classify_geometry(geometry) is None


def test_equal_size_components_choose_first_scan_order_deterministically() -> None:
    geometry = geometry_from_mask_png(
        _mask_png(boxes=((2, 30, 12, 40), (70, 60, 80, 70))), IMAGE_SIZE
    )
    assert (geometry.left, geometry.top, geometry.right, geometry.bottom) == (
        2,
        30,
        12,
        40,
    )


@pytest.mark.parametrize(
    ("mask", "size", "message"),
    [
        (_mask_png(boxes=()), IMAGE_SIZE, "must not be empty"),
        (_mask_png(boxes=((0, 0, 100, 100),)), IMAGE_SIZE, "must not cover"),
        (_mask_png(size=(99, 100)), IMAGE_SIZE, "dimensions"),
        (_mask_png(mode="RGB"), IMAGE_SIZE, "grayscale"),
        (b"not-a-png", IMAGE_SIZE, "decodable PNG"),
    ],
)
def test_mask_contract_rejects_invalid_masks(
    mask: bytes, size: tuple[int, int], message: str
) -> None:
    with pytest.raises(GeometryGuidanceContractError, match=message):
        classify_mask_geometry(mask, size)


def test_geometry_dataclass_rejects_invalid_values() -> None:
    with pytest.raises(GeometryGuidanceContractError, match="bbox"):
        _geometry(bbox=(50, 10, 40, 20))
    with pytest.raises(GeometryGuidanceContractError, match="component size"):
        GarmentGeometry(100, 100, 10, 10, 20, 20, 101, 101)
    with pytest.raises(GeometryGuidanceContractError, match="foreground size"):
        GarmentGeometry(10, 10, 0, 0, 10, 10, 100, 100)
    with pytest.raises(GeometryGuidanceContractError, match="GarmentGeometry"):
        classify_geometry(object())  # type: ignore[arg-type]


@pytest.mark.parametrize("shot", ["front", "back"])
def test_provider_posts_fixed_u2netp_contract_and_returns_pass(shot: str) -> None:
    client = FakeClient(FakeResponse(_mask_png()))
    provider = GeometryGuidanceProvider(client, remove_url=REMOVE_URL)

    decision = asyncio.run(provider.analyze_geometry(_input(shot)))

    assert decision is None
    assert provider.model == GEOMETRY_MASK_MODEL == "u2netp"
    assert provider.timeout_seconds == GEOMETRY_MASK_TIMEOUT_SECONDS == 0.45
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == REMOVE_URL
    assert call["data"] == {"model": "u2netp", "om": "true"}
    assert call["timeout"] == 0.45
    file_part = call["files"]["file"]  # type: ignore[index]
    assert file_part[0] == "geometry-frame"
    assert file_part[2] == "image/jpeg"
    assert file_part[1] != _input(shot).frame.data
    with Image.open(BytesIO(file_part[1])) as prepared:
        assert prepared.format == "JPEG"
        assert max(prepared.size) <= GEOMETRY_IMAGE_MAX_EDGE == 256
    assert GEOMETRY_JPEG_QUALITY == 55


@pytest.mark.parametrize("shot", ["tag", "measurement"])
def test_provider_rejects_non_geometry_shots_before_http(shot: str) -> None:
    client = FakeClient(FakeResponse(_mask_png()))
    provider = GeometryGuidanceProvider(client, remove_url=REMOVE_URL)
    with pytest.raises(GeometryGuidanceContractError, match="front or back"):
        asyncio.run(provider.analyze_geometry(_input(shot)))
    assert client.calls == []


@pytest.mark.parametrize(
    ("response", "error", "message"),
    [
        (
            FakeResponse(_mask_png(), status_code=503),
            GeometryGuidanceProviderError,
            "HTTP 503",
        ),
        (
            FakeResponse(_mask_png(), headers={"Content-Type": "image/jpeg"}),
            GeometryGuidanceContractError,
            "Content-Type",
        ),
        (
            FakeResponse(b""),
            GeometryGuidanceContractError,
            "contain PNG bytes",
        ),
        (
            FakeResponse(_mask_png(size=(50, 50))),
            GeometryGuidanceContractError,
            "dimensions",
        ),
        (
            FakeResponse(_mask_png(boxes=())),
            GeometryGuidanceContractError,
            "must not be empty",
        ),
        (
            FakeResponse(_mask_png(boxes=((0, 0, 100, 100),))),
            GeometryGuidanceContractError,
            "must not cover",
        ),
    ],
)
def test_provider_rejects_unusable_http_or_mask_results(
    response: FakeResponse,
    error: type[Exception],
    message: str,
) -> None:
    provider = GeometryGuidanceProvider(FakeClient(response), remove_url=REMOVE_URL)
    with pytest.raises(error, match=message):
        asyncio.run(provider.analyze_geometry(_input()))


def test_provider_normalizes_timeout_and_other_client_failure() -> None:
    timeout_provider = GeometryGuidanceProvider(
        FakeClient(TimeoutError("private timeout")), remove_url=REMOVE_URL
    )
    with pytest.raises(GeometryGuidanceTimeoutError, match="deadline"):
        asyncio.run(timeout_provider.analyze_geometry(_input()))

    failed_provider = GeometryGuidanceProvider(
        FakeClient(RuntimeError("private upstream detail")), remove_url=REMOVE_URL
    )
    with pytest.raises(GeometryGuidanceProviderError, match="request failed") as captured:
        asyncio.run(failed_provider.analyze_geometry(_input()))
    assert "private upstream detail" not in str(captured.value)


def test_provider_enforces_deadline_even_when_client_ignores_timeout_argument() -> None:
    async def never() -> object:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    provider = GeometryGuidanceProvider(FakeClient(never), remove_url=REMOVE_URL)
    with pytest.raises(GeometryGuidanceTimeoutError, match="deadline"):
        asyncio.run(provider.analyze_geometry(_input()))


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:7000/api/remove",
        "http://example.com/api/remove",
        "",
    ],
)
def test_provider_requires_configured_http_loopback(url: str) -> None:
    with pytest.raises(GeometryGuidanceContractError, match="loopback"):
        GeometryGuidanceProvider(FakeClient(), remove_url=url)


def test_prewarm_validates_shape_but_not_subject_and_session_does_not_own_sidecar() -> None:
    empty_prewarm_mask = _mask_png(size=(8, 8), boxes=())
    client = FakeClient(FakeResponse(empty_prewarm_mask))
    provider = GeometryGuidanceProvider(client, remove_url=REMOVE_URL)

    asyncio.run(provider.prewarm())
    session = provider.new_session()
    asyncio.run(provider.aclose())
    asyncio.run(provider.close())

    assert session is not provider
    assert session.remove_url == provider.remove_url
    assert client.calls[0]["data"] == {"model": "u2netp", "om": "true"}
    assert client.calls[0]["timeout"] == GEOMETRY_PREWARM_TIMEOUT_SECONDS == 8.0


def test_checked_in_ground_truth_masks_classify_exactly_20_of_20() -> None:
    root = Path(__file__).resolve().parents[2] / "data/evaluation/garment_geometry_transformed"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    correct = 0

    for case in manifest["cases"]:
        expected = GuidanceCode(case["expectedCode"])
        mask_path = root / case["groundTruthMask"]
        with Image.open(mask_path) as mask_source:
            expected_size = mask_source.size
        decision = classify_mask_geometry(mask_path.read_bytes(), expected_size)
        assert decision is not None
        counts[expected.value] = counts.get(expected.value, 0) + 1
        correct += decision.code is expected

    assert counts == {
        "CENTER_GARMENT": 5,
        "MOVE_CLOSER": 5,
        "MOVE_FARTHER": 5,
        "SHOW_FULL_GARMENT": 5,
    }
    assert correct == 20
