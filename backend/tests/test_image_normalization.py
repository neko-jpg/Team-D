"""Regression coverage for the post-capture analysis-copy boundary.

The generated fixtures intentionally exercise three source color modes and
three EXIF orientations.  They live in memory so the tests remain deterministic
and do not introduce opaque binary assets into the repository.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageCms

from backend.analyze_shot import get_image_normalizer, get_shot_assessor
from backend.app import create_app
from backend.image_normalization import (
    ImageNormalizationError,
    MAX_ANALYSIS_PIXELS,
    NormalizedAnalysisImage,
    PillowImageNormalizer,
)
from backend.providers.shot_assessor import ShotAssessorInput


EXIF_ORIENTATION = 274


@dataclass(frozen=True, slots=True)
class SourceImageFixture:
    name: str
    data: bytes
    mime_type: str
    source_format: str
    source_mode: str
    source_size: tuple[int, int]
    orientation: int
    expected_size: tuple[int, int]


def _encoded_image(
    *,
    image: Image.Image,
    image_format: str,
    orientation: int,
    icc_profile: bytes | None = None,
) -> bytes:
    exif = Image.Exif()
    exif[EXIF_ORIENTATION] = orientation
    output = BytesIO()
    save_options: dict[str, Any] = {"exif": exif}
    if image_format == "JPEG":
        save_options.update({"quality": 95, "subsampling": 0})
    if icc_profile is not None:
        save_options["icc_profile"] = icc_profile
    image.save(output, format=image_format, **save_options)
    return output.getvalue()


def _srgb_profile_bytes() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


@pytest.fixture(scope="module")
def source_images() -> dict[str, SourceImageFixture]:
    front = Image.new("RGB", (7, 5), (205, 45, 80))
    back = Image.new("CMYK", (8, 6), (12, 90, 30, 5))
    tag = Image.new("L", (9, 4), 176)

    fixtures = (
        SourceImageFixture(
            name="front",
            data=_encoded_image(
                image=front,
                image_format="JPEG",
                orientation=6,
                icc_profile=_srgb_profile_bytes(),
            ),
            mime_type="image/jpeg",
            source_format="JPEG",
            source_mode="RGB",
            source_size=(7, 5),
            orientation=6,
            expected_size=(5, 7),
        ),
        SourceImageFixture(
            name="back",
            data=_encoded_image(image=back, image_format="JPEG", orientation=3),
            mime_type="image/jpeg",
            source_format="JPEG",
            source_mode="CMYK",
            source_size=(8, 6),
            orientation=3,
            expected_size=(8, 6),
        ),
        SourceImageFixture(
            name="tag",
            data=_encoded_image(image=tag, image_format="PNG", orientation=8),
            mime_type="image/png",
            source_format="PNG",
            source_mode="L",
            source_size=(9, 4),
            orientation=8,
            expected_size=(4, 9),
        ),
    )
    return {fixture.name: fixture for fixture in fixtures}


@pytest.mark.parametrize("fixture_name", ("front", "back", "tag"))
def test_normalizer_preserves_source_and_returns_oriented_srgb_png(
    source_images: dict[str, SourceImageFixture], fixture_name: str
) -> None:
    source = source_images[fixture_name]
    before_hash = hashlib.sha256(source.data).hexdigest()

    with Image.open(BytesIO(source.data)) as original:
        assert original.format == source.source_format
        assert original.mode == source.source_mode
        assert original.size == source.source_size
        assert original.getexif()[EXIF_ORIENTATION] == source.orientation

    normalized = PillowImageNormalizer().normalize(source.data, source.mime_type)

    assert hashlib.sha256(source.data).hexdigest() == before_hash
    assert normalized.mime_type == "image/png"
    assert normalized.is_srgb is True
    assert (normalized.width, normalized.height) == source.expected_size
    assert normalized.data != source.data

    with Image.open(BytesIO(normalized.data)) as analysis_copy:
        assert analysis_copy.format == "PNG"
        assert analysis_copy.mode == "RGB"
        assert analysis_copy.size == source.expected_size
        assert analysis_copy.getexif().get(EXIF_ORIENTATION, 1) == 1
        profile_bytes = analysis_copy.info.get("icc_profile")
        assert isinstance(profile_bytes, bytes) and profile_bytes
        profile = ImageCms.ImageCmsProfile(BytesIO(profile_bytes))
        assert "srgb" in ImageCms.getProfileDescription(profile).casefold()


def test_normalizer_reports_decode_failure_explicitly() -> None:
    with pytest.raises(ImageNormalizationError) as raised:
        PillowImageNormalizer().normalize(b"not an encoded image", "image/jpeg")

    assert raised.value.code == "DECODE_FAILED"


def test_normalizer_rejects_pixel_count_before_decoding(
    source_images: dict[str, SourceImageFixture], monkeypatch: pytest.MonkeyPatch
) -> None:
    source = source_images["tag"]
    assert MAX_ANALYSIS_PIXELS >= 48_000_000
    decode_calls = 0

    def fail_if_decoded(_image: Image.Image) -> None:
        nonlocal decode_calls
        decode_calls += 1
        raise AssertionError("pixel limit must be checked before source.load()")

    monkeypatch.setattr(
        "backend.image_normalization.MAX_ANALYSIS_PIXELS",
        source.source_size[0] * source.source_size[1] - 1,
    )
    monkeypatch.setattr(
        "PIL.PngImagePlugin.PngImageFile.load",
        fail_if_decoded,
    )

    with pytest.raises(ImageNormalizationError) as raised:
        PillowImageNormalizer().normalize(source.data, source.mime_type)

    assert raised.value.code == "DECODE_FAILED"
    assert str(raised.value) == "image dimensions exceed the safe decode limit"
    assert decode_calls == 0


def test_normalizer_returns_identical_bytes_for_identical_input(
    source_images: dict[str, SourceImageFixture],
) -> None:
    source = source_images["front"]
    normalizer = PillowImageNormalizer()

    first = normalizer.normalize(source.data, source.mime_type)
    second = normalizer.normalize(source.data, source.mime_type)

    assert second == first
    assert hashlib.sha256(second.data).digest() == hashlib.sha256(first.data).digest()


def test_normalizer_routes_embedded_icc_through_littlecms(
    source_images: dict[str, SourceImageFixture], monkeypatch: pytest.MonkeyPatch
) -> None:
    original_profile_to_profile = ImageCms.profileToProfile
    source_descriptions: list[str] = []
    converted_pixels: list[tuple[int, ...] | int] = []

    def record_profile_conversion(*args: Any, **kwargs: Any) -> Image.Image:
        source_descriptions.append(
            ImageCms.getProfileDescription(args[1]).strip().casefold()
        )
        converted = original_profile_to_profile(*args, **kwargs)
        converted.load()
        converted_pixels.append(converted.getpixel((0, 0)))
        return converted

    monkeypatch.setattr(
        "PIL.ImageCms.profileToProfile",
        record_profile_conversion,
    )

    source = source_images["front"]
    normalized = PillowImageNormalizer().normalize(source.data, source.mime_type)

    assert len(source_descriptions) == 1
    assert "srgb" in source_descriptions[0]
    with Image.open(BytesIO(normalized.data)) as analysis_copy:
        assert analysis_copy.getpixel((0, 0)) == converted_pixels[0]


def test_normalizer_rejects_a_declared_and_detected_format_mismatch(
    source_images: dict[str, SourceImageFixture],
) -> None:
    with pytest.raises(ImageNormalizationError) as raised:
        PillowImageNormalizer().normalize(source_images["tag"].data, "image/jpeg")

    assert raised.value.code == "MIME_MISMATCH"


def test_normalizer_rejects_an_unsupported_format() -> None:
    image = Image.new("RGB", (3, 2), (1, 2, 3))
    encoded = BytesIO()
    image.save(encoded, format="GIF")

    with pytest.raises(ImageNormalizationError) as raised:
        PillowImageNormalizer().normalize(encoded.getvalue(), "image/gif")

    assert raised.value.code == "UNSUPPORTED_FORMAT"


def test_normalizer_reports_transform_failure_explicitly(
    source_images: dict[str, SourceImageFixture], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_transform(_image: Image.Image) -> Image.Image:
        raise RuntimeError("simulated transform failure")

    monkeypatch.setattr(
        "PIL.ImageOps.exif_transpose",
        fail_transform,
    )

    with pytest.raises(ImageNormalizationError) as raised:
        PillowImageNormalizer().normalize(
            source_images["front"].data,
            source_images["front"].mime_type,
        )

    assert raised.value.code == "CONVERSION_FAILED"


@dataclass(slots=True)
class RecordingNormalizer:
    delegate: PillowImageNormalizer = field(default_factory=PillowImageNormalizer)
    results: list[NormalizedAnalysisImage] = field(default_factory=list)

    def normalize(self, data: bytes, mime_type: str) -> NormalizedAnalysisImage:
        result = self.delegate.normalize(data, mime_type)
        self.results.append(result)
        return result


@dataclass(slots=True)
class RecordingAssessor:
    requests: list[ShotAssessorInput] = field(default_factory=list)

    async def assess(self, input: ShotAssessorInput) -> Mapping[str, object]:
        self.requests.append(input)
        shot = input.requested_shot.value
        return {
            "shotType": shot,
            "quality": "ok",
            "issues": [],
            "missingShots": {
                "front": ["back", "tag"],
                "back": ["tag"],
                "tag": [],
            }[shot],
            "nextAction": "COMPLETE" if shot == "tag" else "REQUEST_NEXT",
        }


class UnexpectedFailureNormalizer:
    def normalize(self, data: bytes, mime_type: str) -> NormalizedAnalysisImage:
        raise RuntimeError("simulated decoder internals")


@pytest.fixture
def api_client() -> TestClient:
    app = create_app()
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.parametrize("fixture_name", ("front", "back", "tag"))
def test_analyze_shot_passes_only_each_normalized_copy_to_the_assessor(
    api_client: TestClient,
    source_images: dict[str, SourceImageFixture],
    fixture_name: str,
) -> None:
    source = source_images[fixture_name]
    normalizer = RecordingNormalizer()
    assessor = RecordingAssessor()
    api_client.app.dependency_overrides[get_image_normalizer] = lambda: normalizer
    api_client.app.dependency_overrides[get_shot_assessor] = lambda: assessor

    response = api_client.post(
        "/api/analyze-shot",
        data={"requestedShot": source.name},
        files={"file": (f"{source.name}.image", source.data, source.mime_type)},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert len(normalizer.results) == 1
    assert len(assessor.requests) == 1
    normalized = normalizer.results[0]
    provider_image = assessor.requests[0].image
    assert provider_image.data == normalized.data
    assert provider_image.mime_type == normalized.mime_type == "image/png"
    assert provider_image.data != source.data
    assert assessor.requests[0].requested_shot.value == source.name


def test_analyze_shot_returns_finite_invalid_input_without_calling_provider(
    api_client: TestClient,
) -> None:
    normalizer = RecordingNormalizer()
    assessor = RecordingAssessor()
    api_client.app.dependency_overrides[get_image_normalizer] = lambda: normalizer
    api_client.app.dependency_overrides[get_shot_assessor] = lambda: assessor

    response = api_client.post(
        "/api/analyze-shot",
        data={"requestedShot": "front"},
        files={"file": ("front.jpg", b"not an encoded image", "image/jpeg")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "provider": "shot-assessor",
        "code": "INVALID_INPUT",
        "message": "Image could not be decoded",
        "retryable": False,
    }
    assert normalizer.results == []
    assert assessor.requests == []


def test_analyze_shot_does_not_call_provider_for_unsupported_detected_format(
    api_client: TestClient,
    source_images: dict[str, SourceImageFixture],
) -> None:
    assessor = RecordingAssessor()
    api_client.app.dependency_overrides[get_shot_assessor] = lambda: assessor
    source = source_images["tag"]

    response = api_client.post(
        "/api/analyze-shot",
        data={"requestedShot": "tag"},
        files={"file": ("tag.jpg", source.data, "image/jpeg")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "provider": "shot-assessor",
        "code": "INVALID_INPUT",
        "message": "Unsupported image format",
        "retryable": False,
    }
    assert assessor.requests == []


def test_analyze_shot_does_not_call_provider_for_transform_failure(
    api_client: TestClient,
    source_images: dict[str, SourceImageFixture],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assessor = RecordingAssessor()
    api_client.app.dependency_overrides[get_shot_assessor] = lambda: assessor

    def fail_transform(_image: Image.Image) -> Image.Image:
        raise RuntimeError("simulated transform failure")

    monkeypatch.setattr(
        "PIL.ImageOps.exif_transpose",
        fail_transform,
    )
    source = source_images["front"]

    response = api_client.post(
        "/api/analyze-shot",
        data={"requestedShot": "front"},
        files={"file": ("front.jpg", source.data, source.mime_type)},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "provider": "shot-assessor",
        "code": "INVALID_INPUT",
        "message": "Image normalization failed",
        "retryable": False,
    }
    assert assessor.requests == []


def test_analyze_shot_returns_finite_unavailable_for_unexpected_normalizer_error(
    api_client: TestClient,
    source_images: dict[str, SourceImageFixture],
) -> None:
    assessor = RecordingAssessor()
    api_client.app.dependency_overrides[get_image_normalizer] = (
        lambda: UnexpectedFailureNormalizer()
    )
    api_client.app.dependency_overrides[get_shot_assessor] = lambda: assessor
    source = source_images["front"]

    response = api_client.post(
        "/api/analyze-shot",
        data={"requestedShot": "front"},
        files={"file": ("front.jpg", source.data, source.mime_type)},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "provider": "shot-assessor",
        "code": "UNAVAILABLE",
        "message": "Image normalization is unavailable",
        "retryable": True,
    }
    assert assessor.requests == []
