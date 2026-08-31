"""Contract and API coverage for the front-garment mask endpoint.

The API must return a verified mask-only PNG and must never turn a timeout,
provider failure, or incomplete mask into a successful preview response.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from io import BytesIO

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image, ImageOps

from backend.app import create_app
from backend.providers.garment_masker import (
    GarmentMask,
    GarmentMaskContractError,
    GarmentMaskInput,
    GarmentMasker,
    HttpxGarmentMaskHttpClient,
    MAX_GARMENT_MASK_BYTES,
    REMBG_MODEL,
    REMBG_REMOVE_URL,
    REMBG_TIMEOUT_SECONDS,
)
from backend.remove_background import (
    MAX_REMOVE_BACKGROUND_UPLOAD_BYTES,
    get_garment_masker,
    get_remove_background_timeout_seconds,
)


FRONT_SIZE = (7, 5)


def png_image(*, size: tuple[int, int] = FRONT_SIZE) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, (48, 112, 176)).save(output, format="PNG")
    return output.getvalue()


def mask_png(
    *,
    size: tuple[int, int] = FRONT_SIZE,
    fill: int = 0,
    add_foreground: bool = False,
) -> bytes:
    output = BytesIO()
    image = Image.new("L", size, fill)
    if add_foreground:
        left = max(0, size[0] // 4)
        top = max(0, size[1] // 4)
        right = min(size[0], max(left + 2, (size[0] * 3) // 4))
        bottom = min(size[1], max(top + 2, (size[1] * 3) // 4))
        image.paste(255, (left, top, right, bottom))
    image.save(output, format="PNG")
    return output.getvalue()


def custom_mask_png(
    *,
    size: tuple[int, int] = FRONT_SIZE,
    pixels: Mapping[tuple[int, int], int],
) -> bytes:
    output = BytesIO()
    image = Image.new("L", size, 0)
    for point, alpha in pixels.items():
        image.putpixel(point, alpha)
    image.save(output, format="PNG")
    return output.getvalue()


def exif_oriented_pattern_png() -> bytes:
    image = Image.new("RGB", (6, 4))
    for y in range(image.height):
        for x in range(image.width):
            image.putpixel(
                (x, y),
                (20 + x * 30, 15 + y * 45, 10 + (x + y) * 20),
            )
    exif = Image.Exif()
    exif[274] = 6
    output = BytesIO()
    image.save(output, format="PNG", exif=exif)
    return output.getvalue()


def exif_oriented_pattern_jpeg() -> bytes:
    image = Image.new("RGB", (6, 4), (42, 96, 168))
    exif = Image.Exif()
    exif[274] = 6
    output = BytesIO()
    image.save(
        output,
        format="JPEG",
        exif=exif,
        quality=100,
        subsampling=0,
    )
    return output.getvalue()


def jpeg_mask_body() -> bytes:
    output = BytesIO()
    Image.new("L", FRONT_SIZE, 127).save(output, format="JPEG")
    return output.getvalue()


def truncated_jpeg_image() -> bytes:
    output = BytesIO()
    Image.new("RGB", (100, 100), (48, 112, 176)).save(output, format="JPEG")
    return output.getvalue()[:-200]


def multipart(
    image: bytes | None = None,
    *,
    mime_type: str = "image/png",
) -> dict[str, tuple[str, bytes, str]]:
    return {
        "file": (
            "front.png",
            png_image() if image is None else image,
            mime_type,
        )
    }


@dataclass
class FakeResponse:
    content: bytes
    status_code: int = 200
    headers: Mapping[str, str] = field(
        default_factory=lambda: {"content-type": "image/png"}
    )


@dataclass
class RequestSpyClient:
    response: FakeResponse
    calls: list[dict[str, object]] = field(default_factory=list)

    async def post(
        self,
        url: str,
        *,
        files: Mapping[str, object],
        data: Mapping[str, str],
        timeout: float,
    ) -> FakeResponse:
        self.calls.append(
            {
                "url": url,
                "files": files,
                "data": data,
                "timeout": timeout,
            }
        )
        return self.response


@dataclass
class RaisingClient:
    error: Exception

    async def post(
        self,
        url: str,
        *,
        files: Mapping[str, object],
        data: Mapping[str, str],
        timeout: float,
    ) -> FakeResponse:
        raise self.error


@dataclass
class NeverCompletingMasker:
    requests: list[GarmentMaskInput] = field(default_factory=list)

    async def mask(self, front: GarmentMaskInput) -> GarmentMask:
        self.requests.append(front)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@dataclass
class FailingMasker:
    requests: list[GarmentMaskInput] = field(default_factory=list)

    async def mask(self, front: GarmentMaskInput) -> GarmentMask:
        self.requests.append(front)
        raise RuntimeError("private rembg host and credential details must not escape")


class LeakyHttpExceptionMasker:
    async def mask(self, front: GarmentMaskInput) -> GarmentMask:
        raise HTTPException(
            status_code=418,
            detail={"secret": "provider internals must not escape"},
        )


@dataclass
class RecordingMasker:
    requests: list[GarmentMaskInput] = field(default_factory=list)

    async def mask(self, front: GarmentMaskInput) -> GarmentMask:
        self.requests.append(front)
        return GarmentMask(
            data=mask_png(add_foreground=True),
            width=FRONT_SIZE[0],
            height=FRONT_SIZE[1],
        )


@dataclass
class StaticMasker:
    result: object
    requests: list[GarmentMaskInput] = field(default_factory=list)

    async def mask(self, front: GarmentMaskInput) -> object:
        self.requests.append(front)
        return self.result


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def install_masker(
    client: TestClient,
    masker: object,
    *,
    timeout: float | None = None,
) -> None:
    client.app.dependency_overrides[get_garment_masker] = lambda: masker
    if timeout is not None:
        client.app.dependency_overrides[get_remove_background_timeout_seconds] = (
            lambda: timeout
        )


def assert_finite_error(response: object, *, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert set(payload) == {"detail"}
    assert payload["detail"]["provider"] == "garment-masker"
    assert payload["detail"]["code"] == code
    assert isinstance(payload["detail"]["message"], str)
    assert payload["detail"]["message"]
    assert isinstance(payload["detail"]["retryable"], bool)
    json.dumps(payload, allow_nan=False)


def test_valid_mask_png_is_returned_and_front_original_is_forwarded_exactly(
    client: TestClient,
) -> None:
    front = png_image()
    mask = mask_png(add_foreground=True)
    http_client = RequestSpyClient(FakeResponse(mask))
    install_masker(client, GarmentMasker(http_client))

    response = client.post("/api/remove-background", files=multipart(front))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content == mask
    assert http_client.calls == [
        {
            "url": REMBG_REMOVE_URL,
            "files": {"file": ("front", front, "image/png")},
            "data": {"model": REMBG_MODEL, "om": "true"},
            "timeout": REMBG_TIMEOUT_SECONDS,
        }
    ]


def test_exif_oriented_dimensions_are_used_without_mutating_original_bytes(
    client: TestClient,
) -> None:
    front = exif_oriented_pattern_jpeg()
    before_hash = hashlib.sha256(front).hexdigest()
    mask = mask_png(size=(4, 6), add_foreground=True)
    http_client = RequestSpyClient(FakeResponse(mask))
    install_masker(client, GarmentMasker(http_client))

    response = client.post(
        "/api/remove-background",
        files={"file": ("front.jpg", front, "image/jpeg")},
    )

    assert response.status_code == 200
    assert response.content == mask
    assert hashlib.sha256(front).hexdigest() == before_hash
    with Image.open(BytesIO(response.content)) as decoded_mask:
        assert decoded_mask.size == (4, 6)
    assert http_client.calls[0]["files"] == {
        "file": ("front", front, "image/jpeg")
    }


def test_preview_returns_oriented_rgba_with_original_rgb_and_mask_alpha(
    client: TestClient,
) -> None:
    front = exif_oriented_pattern_png()
    before_hash = hashlib.sha256(front).hexdigest()
    mask = mask_png(size=(4, 6), add_foreground=True)
    http_client = RequestSpyClient(FakeResponse(mask))
    install_masker(client, GarmentMasker(http_client))

    response = client.post(
        "/api/remove-background-preview",
        files={"file": ("front.png", front, "image/png")},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert hashlib.sha256(front).hexdigest() == before_hash
    assert http_client.calls[0]["files"] == {
        "file": ("front", front, "image/png")
    }

    with Image.open(BytesIO(front)) as decoded_front:
        oriented_rgb = ImageOps.exif_transpose(decoded_front).convert("RGB")
    with Image.open(BytesIO(mask)) as decoded_mask:
        expected_alpha = decoded_mask.convert("L")
    with Image.open(BytesIO(response.content)) as preview:
        preview.load()
        assert preview.format == "PNG"
        assert preview.mode == "RGBA"
        assert preview.size == oriented_rgb.size == expected_alpha.size == (4, 6)
        assert preview.convert("RGB").tobytes() == oriented_rgb.tobytes()
        alpha = preview.getchannel("A")
        assert alpha.tobytes() == expected_alpha.tobytes()
        assert alpha.getextrema() == (0, 255)

    oriented_rgb.close()
    expected_alpha.close()


@pytest.mark.parametrize(
    "invalid_mask",
    [
        pytest.param(
            custom_mask_png(pixels={(FRONT_SIZE[0] // 2, FRONT_SIZE[1] // 2): 255}),
            id="one-pixel",
        ),
        pytest.param(
            custom_mask_png(
                pixels={(FRONT_SIZE[0] // 2, y): 255 for y in range(FRONT_SIZE[1])}
            ),
            id="one-pixel-wide-line",
        ),
        pytest.param(
            custom_mask_png(
                pixels={(x, y): 64 for x in range(2, 5) for y in range(1, 4)}
            ),
            id="faint-foreground",
        ),
        pytest.param(
            custom_mask_png(
                pixels={(x, y): 254 for x in range(2, 5) for y in range(1, 4)}
            ),
            id="no-fully-opaque-pixel",
        ),
    ],
)
def test_effectively_invisible_masks_are_rejected(
    client: TestClient,
    invalid_mask: bytes,
) -> None:
    http_client = RequestSpyClient(FakeResponse(invalid_mask))
    install_masker(client, GarmentMasker(http_client))

    response = client.post("/api/remove-background", files=multipart())

    assert_finite_error(response, status_code=502, code="INVALID_RESPONSE")
    assert len(http_client.calls) == 1


def test_small_but_visible_foreground_is_not_rejected(client: TestClient) -> None:
    image_size = (200, 200)
    small_mask = custom_mask_png(
        size=image_size,
        pixels={(x, y): 255 for x in range(90, 95) for y in range(90, 95)},
    )
    http_client = RequestSpyClient(FakeResponse(small_mask))
    install_masker(client, GarmentMasker(http_client))

    response = client.post(
        "/api/remove-background",
        files={"file": ("front.png", png_image(size=image_size), "image/png")},
    )

    assert response.status_code == 200
    assert response.content == small_mask


def test_masker_can_use_an_alternate_loopback_port() -> None:
    front = png_image()
    http_client = RequestSpyClient(FakeResponse(mask_png(add_foreground=True)))
    masker = GarmentMasker(
        http_client,
        remove_url="http://127.0.0.1:7001/api/remove",
    )

    asyncio.run(masker.mask(GarmentMaskInput(front, "image/png")))

    assert http_client.calls[0]["url"] == "http://127.0.0.1:7001/api/remove"


def test_timeout_returns_finite_error_without_mask_body(client: TestClient) -> None:
    masker = NeverCompletingMasker()
    install_masker(client, masker, timeout=0.001)

    response = client.post("/api/remove-background", files=multipart())

    assert_finite_error(response, status_code=504, code="TIMEOUT")
    assert len(masker.requests) == 1
    assert response.content != mask_png(add_foreground=True)


def test_rembg_client_timeout_keeps_timeout_error_contract(client: TestClient) -> None:
    install_masker(client, GarmentMasker(RaisingClient(TimeoutError("private timeout"))))

    response = client.post("/api/remove-background", files=multipart())

    assert_finite_error(response, status_code=504, code="TIMEOUT")
    assert "private timeout" not in response.text


def test_httpx_timeout_is_converted_to_finite_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    class TimeoutStream:
        async def __aenter__(self) -> object:
            raise httpx.ReadTimeout("private httpx timeout")

        async def __aexit__(self, *args: object) -> None:
            return None

    class TimeoutClient:
        async def __aenter__(self) -> "TimeoutClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def stream(self, *args: object, **kwargs: object) -> TimeoutStream:
            return TimeoutStream()

    monkeypatch.setattr(httpx, "AsyncClient", TimeoutClient)

    with pytest.raises(TimeoutError, match="rembg request timed out"):
        asyncio.run(
            HttpxGarmentMaskHttpClient().post(
                REMBG_REMOVE_URL,
                files={"file": ("front", png_image(), "image/png")},
                data={"model": REMBG_MODEL, "om": "true"},
                timeout=REMBG_TIMEOUT_SECONDS,
            )
        )


def test_httpx_stream_stops_when_mask_body_exceeds_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx
    import backend.providers.garment_masker as garment_masker_module

    class OversizeStream:
        status_code = 200
        headers: Mapping[str, str] = {"content-type": "image/png"}

        async def __aenter__(self) -> "OversizeStream":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def aiter_bytes(self) -> AsyncIterator[bytes]:
            yield b"1234"
            yield b"5"
            raise AssertionError("stream must stop at the configured limit")

    class StreamingClient:
        async def __aenter__(self) -> "StreamingClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def stream(self, *args: object, **kwargs: object) -> OversizeStream:
            return OversizeStream()

    monkeypatch.setattr(httpx, "AsyncClient", StreamingClient)
    monkeypatch.setattr(garment_masker_module, "MAX_GARMENT_MASK_BYTES", 4)

    with pytest.raises(GarmentMaskContractError, match="mask size limit"):
        asyncio.run(
            HttpxGarmentMaskHttpClient().post(
                REMBG_REMOVE_URL,
                files={"file": ("front", png_image(), "image/png")},
                data={"model": REMBG_MODEL, "om": "true"},
                timeout=REMBG_TIMEOUT_SECONDS,
            )
        )


@pytest.mark.parametrize(
    "sidecar_response",
    [
        pytest.param(
            FakeResponse(
                mask_png(add_foreground=True),
                headers={"content-type": "text/plain"},
            ),
            id="non-png-content-type",
        ),
        pytest.param(FakeResponse(jpeg_mask_body()), id="non-png-body"),
        pytest.param(
            FakeResponse(mask_png(size=(FRONT_SIZE[0] + 1, FRONT_SIZE[1]), add_foreground=True)),
            id="dimension-mismatch",
        ),
        pytest.param(FakeResponse(mask_png()), id="empty-mask"),
        pytest.param(FakeResponse(mask_png(fill=255)), id="full-image-mask"),
        pytest.param(
            FakeResponse(mask_png(fill=128)),
            id="full-image-soft-mask",
        ),
        pytest.param(
            FakeResponse(b"x" * (MAX_GARMENT_MASK_BYTES + 1)),
            id="oversize-mask-response",
        ),
    ],
)
def test_incomplete_or_invalid_mask_never_becomes_success_response(
    client: TestClient,
    sidecar_response: FakeResponse,
) -> None:
    http_client = RequestSpyClient(sidecar_response)
    install_masker(client, GarmentMasker(http_client))

    response = client.post("/api/remove-background", files=multipart())

    assert response.status_code != 200
    assert_finite_error(response, status_code=502, code="INVALID_RESPONSE")
    assert not response.headers["content-type"].startswith("image/png")
    assert response.content != sidecar_response.content
    assert len(http_client.calls) == 1


def test_provider_runtime_error_is_sanitized(client: TestClient) -> None:
    masker = FailingMasker()
    install_masker(client, masker)

    response = client.post("/api/remove-background", files=multipart())

    assert_finite_error(response, status_code=503, code="UNAVAILABLE")
    assert "private rembg" not in response.text
    assert "credential" not in response.text
    assert len(masker.requests) == 1


def test_provider_http_exception_is_sanitized(client: TestClient) -> None:
    install_masker(client, LeakyHttpExceptionMasker())

    response = client.post("/api/remove-background", files=multipart())

    assert_finite_error(response, status_code=503, code="UNAVAILABLE")
    assert "provider internals" not in response.text
    assert "secret" not in response.text


@pytest.mark.parametrize(
    "invalid_result",
    [
        pytest.param(object(), id="wrong-result-type"),
        pytest.param(
            GarmentMask(
                data=mask_png(add_foreground=True),
                width=FRONT_SIZE[0] + 1,
                height=FRONT_SIZE[1],
            ),
            id="false-dimension-metadata",
        ),
        pytest.param(
            GarmentMask(
                data=jpeg_mask_body(),
                width=FRONT_SIZE[0],
                height=FRONT_SIZE[1],
            ),
            id="non-png-result-body",
        ),
        pytest.param(
            GarmentMask(
                data=mask_png(fill=128),
                width=FRONT_SIZE[0],
                height=FRONT_SIZE[1],
            ),
            id="full-image-soft-mask-result",
        ),
    ],
)
def test_dependency_contract_is_revalidated_before_success(
    client: TestClient,
    invalid_result: object,
) -> None:
    masker = StaticMasker(invalid_result)
    install_masker(client, masker)

    response = client.post("/api/remove-background", files=multipart())

    assert_finite_error(response, status_code=502, code="INVALID_RESPONSE")
    assert len(masker.requests) == 1


@pytest.mark.parametrize(
    ("files", "expected_status"),
    [
        pytest.param(multipart(mime_type="image/gif"), 415, id="unsupported-mime"),
        pytest.param(multipart(b""), 422, id="empty-upload"),
        pytest.param(multipart(b"not-an-image"), 422, id="undecodable-upload"),
        pytest.param(
            multipart(mime_type="image/jpeg"),
            422,
            id="mime-mismatch",
        ),
        pytest.param(
            multipart(truncated_jpeg_image(), mime_type="image/jpeg"),
            422,
            id="truncated-jpeg",
        ),
        pytest.param(
            multipart(b"x" * (MAX_REMOVE_BACKGROUND_UPLOAD_BYTES + 1)),
            413,
            id="oversize-upload",
        ),
    ],
)
def test_invalid_upload_is_rejected_before_provider(
    client: TestClient,
    files: dict[str, tuple[str, bytes, str]],
    expected_status: int,
) -> None:
    masker = RecordingMasker()
    install_masker(client, masker)

    response = client.post("/api/remove-background", files=files)

    assert_finite_error(response, status_code=expected_status, code="INVALID_INPUT")
    assert masker.requests == []
