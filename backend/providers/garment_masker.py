"""Validated boundary for rembg's mask-only HTTP endpoint.

The rembg sidecar is untrusted: this module validates both the front original
before sending it and every successful-looking response before exposing a
mask.  Pillow is deliberately imported only while validating image bytes so a
missing optional image runtime does not make importing :mod:`backend` fail.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Protocol, runtime_checkable


REMBG_REMOVE_URL = "http://127.0.0.1:7000/api/remove"
REMBG_MODEL = "birefnet-general-lite"
REMBG_TIMEOUT_SECONDS = 35.0


class GarmentMaskContractError(ValueError):
    """Raised when an input or a rembg response violates the mask contract."""


class GarmentMaskProviderError(RuntimeError):
    """Raised when rembg cannot provide a usable mask."""


class GarmentMaskUnavailableError(GarmentMaskProviderError):
    """Raised when the local runtime needed to validate masks is unavailable."""


@dataclass(frozen=True, slots=True)
class GarmentMaskInput:
    """The unmodified front original sent to rembg."""

    data: bytes
    mime_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes) or not self.data:
            raise GarmentMaskContractError("front image data must be non-empty bytes")
        if not isinstance(self.mime_type, str) or not self.mime_type.startswith("image/"):
            raise GarmentMaskContractError("front image MIME type must be an image MIME type")


@dataclass(frozen=True, slots=True)
class GarmentMask:
    """A verified PNG mask whose dimensions match its front original."""

    data: bytes
    width: int
    height: int
    mime_type: str = "image/png"

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes) or not self.data:
            raise GarmentMaskContractError("mask data must be non-empty bytes")
        if not isinstance(self.width, int) or isinstance(self.width, bool) or self.width <= 0:
            raise GarmentMaskContractError("mask width must be a positive integer")
        if not isinstance(self.height, int) or isinstance(self.height, bool) or self.height <= 0:
            raise GarmentMaskContractError("mask height must be a positive integer")
        if self.mime_type != "image/png":
            raise GarmentMaskContractError("mask MIME type must be image/png")


@runtime_checkable
class GarmentMaskHttpResponse(Protocol):
    """Small response surface required from an injected async HTTP client."""

    status_code: int
    headers: Mapping[str, str]
    content: bytes


@runtime_checkable
class GarmentMaskHttpClient(Protocol):
    """Small testable subset of an async multipart HTTP client."""

    async def post(
        self,
        url: str,
        *,
        files: Mapping[str, object],
        data: Mapping[str, str],
        timeout: float,
    ) -> GarmentMaskHttpResponse:
        """POST a multipart body and return its response."""


@runtime_checkable
class GarmentMaskerProvider(Protocol):
    async def mask(self, front: GarmentMaskInput) -> GarmentMask:
        """Return a verified, mask-only PNG for one front original."""


class GarmentMasker:
    """rembg adapter that never returns an incomplete or invalid mask."""

    def __init__(
        self,
        client: GarmentMaskHttpClient,
    ) -> None:
        self._client = client

    async def mask(self, front: GarmentMaskInput) -> GarmentMask:
        if not isinstance(front, GarmentMaskInput):
            raise GarmentMaskContractError("front must be a GarmentMaskInput")

        original_width, original_height = _decode_image_size(front.data, "front image")
        files: dict[str, object] = {"file": ("front", front.data, front.mime_type)}
        data = {"model": REMBG_MODEL, "om": "true"}
        try:
            response = await self._client.post(
                REMBG_REMOVE_URL,
                files=files,
                data=data,
                timeout=REMBG_TIMEOUT_SECONDS,
            )
        except GarmentMaskProviderError:
            raise
        except Exception as exc:
            raise GarmentMaskProviderError("rembg request failed") from exc

        status_code = getattr(response, "status_code", None)
        if not isinstance(status_code, int) or isinstance(status_code, bool):
            raise GarmentMaskProviderError("rembg returned an invalid HTTP response")
        if not 200 <= status_code < 300:
            raise GarmentMaskProviderError(f"rembg returned HTTP {status_code}")

        content_type = _content_type(getattr(response, "headers", None))
        if content_type != "image/png":
            raise GarmentMaskContractError("rembg response Content-Type must be image/png")
        content = getattr(response, "content", None)
        if not isinstance(content, bytes) or not content:
            raise GarmentMaskContractError("rembg response body must be non-empty PNG bytes")

        mask_width, mask_height, extrema = _decode_png_mask(content)
        if (mask_width, mask_height) != (original_width, original_height):
            raise GarmentMaskContractError("rembg mask dimensions must match the front image")
        if extrema[0] == 0 and extrema[1] == 0:
            raise GarmentMaskContractError("rembg returned an empty mask")
        if extrema[0] == 255 and extrema[1] == 255:
            raise GarmentMaskContractError("rembg returned a full-image mask")
        return GarmentMask(data=content, width=mask_width, height=mask_height)

    async def remove_background(self, front: GarmentMaskInput) -> GarmentMask:
        """Compatibility spelling for the API operation; equivalent to :meth:`mask`."""

        return await self.mask(front)


class HttpxGarmentMaskHttpClient:
    """Lazy ``httpx`` implementation, kept separate from the provider contract."""

    async def post(
        self,
        url: str,
        *,
        files: Mapping[str, object],
        data: Mapping[str, str],
        timeout: float,
    ) -> GarmentMaskHttpResponse:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - depends on deployment extras
            raise GarmentMaskUnavailableError("httpx is required to contact rembg") from exc
        async with httpx.AsyncClient() as client:
            return await client.post(url, files=files, data=data, timeout=timeout)


def _pillow_image() -> Any:
    """Load Pillow only when validation is actually attempted."""

    try:
        from PIL import Image
    except ImportError as exc:
        raise GarmentMaskUnavailableError(
            "Pillow is required to validate front images and rembg masks"
        ) from exc
    return Image


def _decode_image_size(data: bytes, label: str) -> tuple[int, int]:
    image_module = _pillow_image()
    try:
        # ``verify`` catches truncated/corrupt payloads. Reopening afterwards
        # obtains dimensions from a decoder that has passed that integrity check.
        with image_module.open(BytesIO(data)) as image:
            image.verify()
        with image_module.open(BytesIO(data)) as image:
            width, height = image.size
            image.load()
    except GarmentMaskUnavailableError:
        raise
    except Exception as exc:
        raise GarmentMaskContractError(f"{label} must be decodable image bytes") from exc
    if width <= 0 or height <= 0:
        raise GarmentMaskContractError(f"{label} must have positive dimensions")
    return width, height


def _decode_png_mask(data: bytes) -> tuple[int, int, tuple[int, int]]:
    image_module = _pillow_image()
    try:
        with image_module.open(BytesIO(data)) as image:
            if image.format != "PNG":
                raise GarmentMaskContractError("rembg response body must be a PNG image")
            if image.mode not in {"1", "L"}:
                raise GarmentMaskContractError("rembg response must be a mask-only grayscale PNG")
            image.verify()
        with image_module.open(BytesIO(data)) as image:
            if image.format != "PNG":
                raise GarmentMaskContractError("rembg response body must be a PNG image")
            if image.mode not in {"1", "L"}:
                raise GarmentMaskContractError("rembg response must be a mask-only grayscale PNG")
            image.load()
            width, height = image.size
            extrema = image.convert("L").getextrema()
    except GarmentMaskContractError:
        raise
    except GarmentMaskUnavailableError:
        raise
    except Exception as exc:
        raise GarmentMaskContractError("rembg response body must be a decodable PNG image") from exc
    if width <= 0 or height <= 0 or not isinstance(extrema, tuple) or len(extrema) != 2:
        raise GarmentMaskContractError("rembg response mask is invalid")
    return width, height, extrema


def _content_type(headers: object) -> str | None:
    if not isinstance(headers, Mapping):
        return None
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == "content-type" and isinstance(value, str):
            return value.split(";", 1)[0].strip().lower()
    return None


RembgGarmentMasker = GarmentMasker


__all__ = [
    "GarmentMask",
    "GarmentMaskContractError",
    "GarmentMaskHttpClient",
    "GarmentMaskHttpResponse",
    "GarmentMaskProviderError",
    "GarmentMaskUnavailableError",
    "GarmentMasker",
    "GarmentMaskerProvider",
    "GarmentMaskInput",
    "HttpxGarmentMaskHttpClient",
    "REMBG_MODEL",
    "REMBG_REMOVE_URL",
    "REMBG_TIMEOUT_SECONDS",
    "RembgGarmentMasker",
]
