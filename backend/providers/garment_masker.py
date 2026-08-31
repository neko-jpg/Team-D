"""Validated boundary for rembg's mask-only HTTP endpoint.

The rembg sidecar is untrusted: this module validates both the front original
before sending it and every successful-looking response before exposing a
mask.  Pillow is deliberately imported only while validating image bytes so a
missing optional image runtime does not make importing :mod:`backend` fail.
"""

from __future__ import annotations

import asyncio
import math
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Protocol, runtime_checkable


REMBG_REMOVE_URL = "http://127.0.0.1:7000/api/remove"
REMBG_MODEL = "birefnet-general-lite"
REMBG_TIMEOUT_SECONDS = 35.0
MAX_GARMENT_IMAGE_PIXELS = 50_000_000
MAX_GARMENT_MASK_BYTES = 10 * 1024 * 1024
MASK_VISIBLE_ALPHA_THRESHOLD = 16
MASK_OPAQUE_ALPHA_THRESHOLD = 255
# These relative floors are deliberately far below a normally framed garment:
# 0.05% visible area and 1% span on each axis still allow a very small subject,
# while rejecting isolated pixels and decoder/model noise at phone resolution.
MIN_GARMENT_MASK_VISIBLE_PIXELS = 4
MIN_GARMENT_MASK_VISIBLE_RATIO = 0.0005
MIN_GARMENT_MASK_BBOX_PIXELS = 2
MIN_GARMENT_MASK_BBOX_RATIO = 0.01


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
    """A verified PNG mask matching the EXIF-oriented front dimensions."""

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


@dataclass(frozen=True, slots=True)
class _BufferedGarmentMaskHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes


@runtime_checkable
class GarmentMaskerProvider(Protocol):
    async def mask(self, front: GarmentMaskInput) -> GarmentMask:
        """Return a verified, mask-only PNG for one front original."""


class GarmentMasker:
    """rembg adapter that never returns an incomplete or invalid mask."""

    def __init__(
        self,
        client: GarmentMaskHttpClient,
        *,
        remove_url: str = REMBG_REMOVE_URL,
    ) -> None:
        if not isinstance(remove_url, str) or not remove_url:
            raise GarmentMaskContractError("rembg remove URL must be a non-empty string")
        self._client = client
        self._remove_url = remove_url

    async def mask(self, front: GarmentMaskInput) -> GarmentMask:
        if not isinstance(front, GarmentMaskInput):
            raise GarmentMaskContractError("front must be a GarmentMaskInput")

        original_width, original_height = await asyncio.to_thread(
            _decode_display_image_size,
            front.data,
            "front image",
        )
        files: dict[str, object] = {"file": ("front", front.data, front.mime_type)}
        data = {"model": REMBG_MODEL, "om": "true"}
        try:
            response = await self._client.post(
                self._remove_url,
                files=files,
                data=data,
                timeout=REMBG_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            # Keep timeout identity so the HTTP boundary can return its finite
            # TIMEOUT contract instead of collapsing it into UNAVAILABLE.
            raise
        except GarmentMaskContractError:
            raise
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
        if len(content) > MAX_GARMENT_MASK_BYTES:
            raise GarmentMaskContractError("rembg response exceeds the mask size limit")

        mask_width, mask_height = await asyncio.to_thread(
            validate_garment_mask_png,
            content,
            (original_width, original_height),
        )
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
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    url,
                    files=files,
                    data=data,
                    timeout=timeout,
                ) as response:
                    if not 200 <= response.status_code < 300:
                        return _BufferedGarmentMaskHttpResponse(
                            status_code=response.status_code,
                            headers=response.headers,
                            content=b"",
                        )
                    if _content_type(response.headers) != "image/png":
                        return _BufferedGarmentMaskHttpResponse(
                            status_code=response.status_code,
                            headers=response.headers,
                            content=b"",
                        )

                    content_length = response.headers.get("content-length")
                    if content_length is not None:
                        try:
                            declared_length = int(content_length)
                        except ValueError:
                            declared_length = 0
                        if declared_length > MAX_GARMENT_MASK_BYTES:
                            raise GarmentMaskContractError(
                                "rembg response exceeds the mask size limit"
                            )

                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > MAX_GARMENT_MASK_BYTES:
                            raise GarmentMaskContractError(
                                "rembg response exceeds the mask size limit"
                            )
                        content.extend(chunk)
                    return _BufferedGarmentMaskHttpResponse(
                        status_code=response.status_code,
                        headers=response.headers,
                        content=bytes(content),
                    )
        except httpx.TimeoutException as exc:
            raise TimeoutError("rembg request timed out") from exc


def _pillow_modules() -> tuple[Any, Any]:
    """Load Pillow only when validation is actually attempted."""

    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise GarmentMaskUnavailableError(
            "Pillow is required to validate front images and rembg masks"
        ) from exc
    return Image, ImageOps


def _decode_display_image_size(data: bytes, label: str) -> tuple[int, int]:
    """Return the EXIF-oriented display dimensions without changing ``data``."""

    image_module, image_ops = _pillow_modules()
    with warnings.catch_warnings():
        warnings.simplefilter("error", image_module.DecompressionBombWarning)
        try:
            # ``verify`` catches truncated/corrupt payloads. Reopening afterwards
            # obtains dimensions from a decoder that has passed that integrity check.
            with image_module.open(BytesIO(data)) as image:
                width, height = image.size
                if width * height > MAX_GARMENT_IMAGE_PIXELS:
                    raise GarmentMaskContractError(
                        f"{label} dimensions exceed the safe decode limit"
                    )
                image.verify()
            with image_module.open(BytesIO(data)) as image:
                oriented = image_ops.exif_transpose(image)
                try:
                    width, height = oriented.size
                    if width * height > MAX_GARMENT_IMAGE_PIXELS:
                        raise GarmentMaskContractError(
                            f"{label} dimensions exceed the safe decode limit"
                        )
                    oriented.load()
                finally:
                    if oriented is not image:
                        oriented.close()
        except GarmentMaskUnavailableError:
            raise
        except GarmentMaskContractError:
            raise
        except (
            image_module.DecompressionBombWarning,
            image_module.DecompressionBombError,
        ) as exc:
            raise GarmentMaskContractError(
                f"{label} dimensions exceed the safe decode limit"
            ) from exc
        except Exception as exc:
            raise GarmentMaskContractError(f"{label} must be decodable image bytes") from exc
    if width <= 0 or height <= 0:
        raise GarmentMaskContractError(f"{label} must have positive dimensions")
    return width, height


def _decode_png_mask(
    data: bytes,
    expected_size: tuple[int, int] | None = None,
) -> tuple[int, int, tuple[int, int]]:
    image_module, _image_ops = _pillow_modules()
    with warnings.catch_warnings():
        warnings.simplefilter("error", image_module.DecompressionBombWarning)
        try:
            with image_module.open(BytesIO(data)) as image:
                if image.format != "PNG":
                    raise GarmentMaskContractError("rembg response body must be a PNG image")
                if image.mode not in {"1", "L"}:
                    raise GarmentMaskContractError(
                        "rembg response must be a mask-only grayscale PNG"
                    )
                width, height = image.size
                if expected_size is not None and (width, height) != expected_size:
                    raise GarmentMaskContractError(
                        "rembg mask dimensions must match the front image"
                    )
                if width * height > MAX_GARMENT_IMAGE_PIXELS:
                    raise GarmentMaskContractError(
                        "rembg response dimensions exceed the safe decode limit"
                    )
                image.verify()
            with image_module.open(BytesIO(data)) as image:
                image.load()
                width, height = image.size
                extrema = image.getextrema()
                histogram = image.histogram()
                visible_pixels = sum(histogram[MASK_VISIBLE_ALPHA_THRESHOLD:])
                opaque_pixels = sum(histogram[MASK_OPAQUE_ALPHA_THRESHOLD:])
                visible = image.point(
                    lambda alpha: 255
                    if alpha >= MASK_VISIBLE_ALPHA_THRESHOLD
                    else 0,
                    mode="L",
                )
                try:
                    foreground_bbox = visible.getbbox()
                finally:
                    visible.close()
        except GarmentMaskContractError:
            raise
        except GarmentMaskUnavailableError:
            raise
        except (
            image_module.DecompressionBombWarning,
            image_module.DecompressionBombError,
        ) as exc:
            raise GarmentMaskContractError(
                "rembg response dimensions exceed the safe decode limit"
            ) from exc
        except Exception as exc:
            raise GarmentMaskContractError(
                "rembg response body must be a decodable PNG image"
            ) from exc
    if width <= 0 or height <= 0 or not isinstance(extrema, tuple) or len(extrema) != 2:
        raise GarmentMaskContractError("rembg response mask is invalid")
    _validate_mask_sanity(
        width=width,
        height=height,
        extrema=extrema,
        visible_pixels=visible_pixels,
        opaque_pixels=opaque_pixels,
        foreground_bbox=foreground_bbox,
    )
    return width, height, extrema


def _validate_mask_sanity(
    *,
    width: int,
    height: int,
    extrema: tuple[int, int],
    visible_pixels: int,
    opaque_pixels: int,
    foreground_bbox: tuple[int, int, int, int] | None,
) -> None:
    if extrema[1] == 0:
        raise GarmentMaskContractError("rembg returned an empty mask")
    if extrema[0] > 0:
        raise GarmentMaskContractError("rembg returned a full-image mask")

    pixel_count = width * height
    minimum_visible_pixels = max(
        MIN_GARMENT_MASK_VISIBLE_PIXELS,
        math.ceil(pixel_count * MIN_GARMENT_MASK_VISIBLE_RATIO),
    )
    if visible_pixels < minimum_visible_pixels:
        raise GarmentMaskContractError(
            "rembg returned an effectively empty mask"
        )

    # A garment mask needs a small, fully opaque interior. This rejects a large
    # field of barely visible alpha noise without requiring the garment to
    # occupy a significant fraction of the frame.
    minimum_opaque_pixels = max(1, math.ceil(minimum_visible_pixels * 0.1))
    if opaque_pixels < minimum_opaque_pixels:
        raise GarmentMaskContractError("rembg mask foreground is too faint")

    if foreground_bbox is None:
        raise GarmentMaskContractError(
            "rembg returned an effectively empty mask"
        )
    left, top, right, bottom = foreground_bbox
    minimum_bbox_width = max(
        MIN_GARMENT_MASK_BBOX_PIXELS,
        math.ceil(width * MIN_GARMENT_MASK_BBOX_RATIO),
    )
    minimum_bbox_height = max(
        MIN_GARMENT_MASK_BBOX_PIXELS,
        math.ceil(height * MIN_GARMENT_MASK_BBOX_RATIO),
    )
    if right - left < minimum_bbox_width or bottom - top < minimum_bbox_height:
        raise GarmentMaskContractError(
            "rembg mask foreground bounding box is too small"
        )


def validate_garment_mask_png(
    data: bytes,
    expected_size: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """Validate one mask-only PNG and return its verified dimensions."""

    width, height, _extrema = _decode_png_mask(data, expected_size)
    return width, height


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
    "MAX_GARMENT_IMAGE_PIXELS",
    "MAX_GARMENT_MASK_BYTES",
    "MASK_OPAQUE_ALPHA_THRESHOLD",
    "MASK_VISIBLE_ALPHA_THRESHOLD",
    "MIN_GARMENT_MASK_BBOX_PIXELS",
    "MIN_GARMENT_MASK_BBOX_RATIO",
    "MIN_GARMENT_MASK_VISIBLE_PIXELS",
    "MIN_GARMENT_MASK_VISIBLE_RATIO",
    "REMBG_MODEL",
    "REMBG_REMOVE_URL",
    "REMBG_TIMEOUT_SECONDS",
    "RembgGarmentMasker",
    "validate_garment_mask_png",
]
