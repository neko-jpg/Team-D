"""Contract and API coverage for the front-garment mask endpoint.

The API must return a verified mask-only PNG and must never turn a timeout,
provider failure, or incomplete mask into a successful preview response.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from io import BytesIO

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

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
        image.putpixel((size[0] // 2, size[1] // 2), 255)
    image.save(output, format="PNG")
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
