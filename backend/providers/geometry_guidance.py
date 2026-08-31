"""Fast, deterministic garment geometry guidance from a rembg mask.

The live geometry path deliberately has a much smaller responsibility than the
semantic vision provider.  It sends one already-selected frame to the local
``u2netp`` rembg session, validates the mask, keeps only its largest connected
component, and returns a framing correction or ``None`` (PASS).  It can never
manufacture ``READY``.
"""

from __future__ import annotations

import asyncio
import base64
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from io import BytesIO
from typing import Protocol, runtime_checkable
from urllib.parse import urlparse

from PIL import Image, ImageOps

from .vision_guidance import (
    EncodedImage,
    GuidanceCode,
    GuidanceInput,
    GuidanceShot,
    VisionDecision,
    validate_guidance_input,
)


GEOMETRY_MASK_MODEL = "u2netp"
GEOMETRY_MASK_TIMEOUT_SECONDS = 0.45
GEOMETRY_PREWARM_TIMEOUT_SECONDS = 8.0
GEOMETRY_MASK_MAX_BYTES = 4 * 1024 * 1024
GEOMETRY_MASK_FOREGROUND_THRESHOLD = 128
GEOMETRY_IMAGE_MAX_EDGE = 256
GEOMETRY_JPEG_QUALITY = 55
GEOMETRY_BORDER_DISTANCE_PIXELS = 1
GEOMETRY_MIN_SPAN = Fraction(21, 50)  # 0.42
GEOMETRY_MAX_SPAN = Fraction(77, 100)  # 0.77
GEOMETRY_MAX_CENTER_AXIS_OFFSET = Fraction(3, 25)  # 0.12

_SUPPORTED_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
_GEOMETRY_SHOTS = frozenset({GuidanceShot.FRONT, GuidanceShot.BACK})
_PREWARM_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC"
)


class GeometryGuidanceContractError(ValueError):
    """An input, mask, or classifier value violates the finite contract."""


class GeometryGuidanceProviderError(RuntimeError):
    """The local geometry-mask provider did not return a usable response."""


class GeometryGuidanceTimeoutError(GeometryGuidanceProviderError, TimeoutError):
    """The local geometry-mask request exceeded its 450ms deadline."""


@runtime_checkable
class GeometryMaskHttpResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]
    content: bytes


@runtime_checkable
class GeometryMaskHttpClient(Protocol):
    async def post(
        self,
        url: str,
        *,
        files: Mapping[str, object],
        data: Mapping[str, str],
        timeout: float,
    ) -> GeometryMaskHttpResponse:
        """POST one multipart frame to the loopback rembg sidecar."""


@dataclass(frozen=True, slots=True)
class GarmentGeometry:
    """Pixel-exact bbox for the largest foreground connected component.

    ``right`` and ``bottom`` are exclusive, matching Pillow's bbox convention.
    Keeping integer geometry lets threshold comparisons remain exact at the
    specified 0.42, 0.77, and 0.12 boundaries.
    """

    image_width: int
    image_height: int
    left: int
    top: int
    right: int
    bottom: int
    component_pixels: int
    foreground_pixels: int

    def __post_init__(self) -> None:
        integer_fields = (
            "image_width",
            "image_height",
            "left",
            "top",
            "right",
            "bottom",
            "component_pixels",
            "foreground_pixels",
        )
        for field_name in integer_fields:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise GeometryGuidanceContractError(
                    f"geometry.{field_name} must be an integer"
                )
        if self.image_width <= 0 or self.image_height <= 0:
            raise GeometryGuidanceContractError("geometry image dimensions must be positive")
        if not (
            0 <= self.left < self.right <= self.image_width
            and 0 <= self.top < self.bottom <= self.image_height
        ):
            raise GeometryGuidanceContractError("geometry bbox must be inside the image")
        image_pixels = self.image_width * self.image_height
        bbox_pixels = (self.right - self.left) * (self.bottom - self.top)
        if not 1 <= self.component_pixels <= bbox_pixels:
            raise GeometryGuidanceContractError("geometry component size is invalid")
        if not self.component_pixels <= self.foreground_pixels < image_pixels:
            raise GeometryGuidanceContractError("geometry foreground size is invalid")

    @property
    def width_pixels(self) -> int:
        return self.right - self.left

    @property
    def height_pixels(self) -> int:
        return self.bottom - self.top

    @property
    def span(self) -> float:
        return float(
            max(
                Fraction(self.width_pixels, self.image_width),
                Fraction(self.height_pixels, self.image_height),
            )
        )

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / (2 * self.image_width)

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / (2 * self.image_height)

    @property
    def center_axis_offset(self) -> float:
        return max(abs(self.center_x - 0.5), abs(self.center_y - 0.5))

    @property
    def border_distance_pixels(self) -> int:
        return min(
            self.left,
            self.top,
            self.image_width - self.right,
            self.image_height - self.bottom,
        )


def _decode_mask_pixels(
    data: bytes,
    *,
    expected_size: tuple[int, int],
) -> tuple[int, int, bytes]:
    if not isinstance(data, bytes) or not data:
        raise GeometryGuidanceContractError("geometry mask must be non-empty PNG bytes")
    if len(data) > GEOMETRY_MASK_MAX_BYTES:
        raise GeometryGuidanceContractError("geometry mask exceeds the size limit")
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format != "PNG":
                raise GeometryGuidanceContractError("geometry mask must be a PNG image")
            if source.mode not in {"1", "L"}:
                raise GeometryGuidanceContractError(
                    "geometry mask must be a mask-only grayscale PNG"
                )
            if source.size != expected_size:
                raise GeometryGuidanceContractError(
                    "geometry mask dimensions must match the input frame"
                )
            source.load()
            grayscale = source.convert("L")
            try:
                pixels = grayscale.tobytes()
            finally:
                grayscale.close()
    except GeometryGuidanceContractError:
        raise
    except Exception as error:
        raise GeometryGuidanceContractError(
            "geometry mask must be a decodable PNG image"
        ) from error
    width, height = expected_size
    if len(pixels) != width * height:
        raise GeometryGuidanceContractError("geometry mask pixel data is invalid")
    return width, height, pixels


def _largest_connected_component(
    pixels: bytes,
    width: int,
    height: int,
) -> tuple[tuple[int, int, int, int], int, int]:
    """Return largest 4-connected bbox, component size, and all foreground size."""

    threshold = GEOMETRY_MASK_FOREGROUND_THRESHOLD
    foreground_pixels = sum(value >= threshold for value in pixels)
    total_pixels = width * height
    if foreground_pixels == 0:
        raise GeometryGuidanceContractError("geometry mask must not be empty")
    if foreground_pixels == total_pixels:
        raise GeometryGuidanceContractError("geometry mask must not cover the full image")

    visited = bytearray(total_pixels)
    largest_bbox: tuple[int, int, int, int] | None = None
    largest_size = 0

    for seed in range(total_pixels):
        if visited[seed] or pixels[seed] < threshold:
            continue
        visited[seed] = 1
        pending: deque[int] = deque((seed,))
        component_size = 0
        min_x = width
        min_y = height
        max_x = -1
        max_y = -1

        while pending:
            index = pending.popleft()
            y, x = divmod(index, width)
            component_size += 1
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)

            if x > 0:
                neighbor = index - 1
                if not visited[neighbor] and pixels[neighbor] >= threshold:
                    visited[neighbor] = 1
                    pending.append(neighbor)
            if x + 1 < width:
                neighbor = index + 1
                if not visited[neighbor] and pixels[neighbor] >= threshold:
                    visited[neighbor] = 1
                    pending.append(neighbor)
            if y > 0:
                neighbor = index - width
                if not visited[neighbor] and pixels[neighbor] >= threshold:
                    visited[neighbor] = 1
                    pending.append(neighbor)
            if y + 1 < height:
                neighbor = index + width
                if not visited[neighbor] and pixels[neighbor] >= threshold:
                    visited[neighbor] = 1
                    pending.append(neighbor)

        if component_size > largest_size:
            largest_size = component_size
            largest_bbox = (min_x, min_y, max_x + 1, max_y + 1)

    if largest_bbox is None or largest_size <= 0:
        raise GeometryGuidanceContractError("geometry mask has no usable component")
    return largest_bbox, largest_size, foreground_pixels


def geometry_from_mask_png(
    data: bytes,
    expected_size: tuple[int, int],
) -> GarmentGeometry:
    """Decode a mask and extract only its largest connected component."""

    if (
        not isinstance(expected_size, tuple)
        or len(expected_size) != 2
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in expected_size
        )
    ):
        raise GeometryGuidanceContractError(
            "expected mask dimensions must be positive integers"
        )
    width, height, pixels = _decode_mask_pixels(data, expected_size=expected_size)
    bbox, component_pixels, foreground_pixels = _largest_connected_component(
        pixels, width, height
    )
    return GarmentGeometry(
        image_width=width,
        image_height=height,
        left=bbox[0],
        top=bbox[1],
        right=bbox[2],
        bottom=bbox[3],
        component_pixels=component_pixels,
        foreground_pixels=foreground_pixels,
    )


def classify_geometry(geometry: GarmentGeometry) -> VisionDecision | None:
    """Return the first deterministic correction, or ``None`` for PASS."""

    if not isinstance(geometry, GarmentGeometry):
        raise GeometryGuidanceContractError("geometry must be GarmentGeometry")

    if geometry.border_distance_pixels <= GEOMETRY_BORDER_DISTANCE_PIXELS:
        code = GuidanceCode.SHOW_FULL_GARMENT
    else:
        width_span = Fraction(geometry.width_pixels, geometry.image_width)
        height_span = Fraction(geometry.height_pixels, geometry.image_height)
        span = max(width_span, height_span)
        if span < GEOMETRY_MIN_SPAN:
            code = GuidanceCode.MOVE_CLOSER
        elif span > GEOMETRY_MAX_SPAN:
            code = GuidanceCode.MOVE_FARTHER
        else:
            x_offset = Fraction(
                abs(geometry.left + geometry.right - geometry.image_width),
                2 * geometry.image_width,
            )
            y_offset = Fraction(
                abs(geometry.top + geometry.bottom - geometry.image_height),
                2 * geometry.image_height,
            )
            if max(x_offset, y_offset) > GEOMETRY_MAX_CENTER_AXIS_OFFSET:
                code = GuidanceCode.CENTER_GARMENT
            else:
                return None
    return VisionDecision(code=code, confidence=1.0)


def classify_mask_geometry(
    data: bytes,
    expected_size: tuple[int, int],
) -> VisionDecision | None:
    """Pure convenience boundary from a validated PNG mask to one correction."""

    return classify_geometry(geometry_from_mask_png(data, expected_size))


def _prepare_geometry_frame(frame: EncodedImage) -> EncodedImage:
    if frame.mime_type not in _SUPPORTED_IMAGE_MIME_TYPES:
        raise GeometryGuidanceContractError(
            "geometry frame must be JPEG, PNG, or WebP"
        )
    try:
        with Image.open(BytesIO(frame.data)) as source:
            oriented = ImageOps.exif_transpose(source).convert("RGB")
            try:
                oriented.load()
                oriented.thumbnail(
                    (GEOMETRY_IMAGE_MAX_EDGE, GEOMETRY_IMAGE_MAX_EDGE),
                    Image.Resampling.LANCZOS,
                )
                width, height = oriented.size
                output = BytesIO()
                oriented.save(
                    output,
                    format="JPEG",
                    quality=GEOMETRY_JPEG_QUALITY,
                    optimize=False,
                )
                encoded = output.getvalue()
            finally:
                if oriented is not source:
                    oriented.close()
    except Exception as error:
        raise GeometryGuidanceContractError(
            "geometry frame must be a decodable image"
        ) from error
    if width <= 0 or height <= 0 or not encoded:
        raise GeometryGuidanceContractError("geometry frame dimensions must be positive")
    return EncodedImage(encoded, "image/jpeg", width, height)


def _content_type(headers: object) -> str | None:
    if not isinstance(headers, Mapping):
        return None
    value = next(
        (
            candidate
            for name, candidate in headers.items()
            if isinstance(name, str) and name.lower() == "content-type"
        ),
        None,
    )
    if not isinstance(value, str):
        return None
    return value.split(";", 1)[0].strip().lower()


def _validate_loopback_url(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise GeometryGuidanceContractError(
            "geometry remove URL must be a non-empty loopback URL"
        )
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise GeometryGuidanceContractError(
            "geometry remove URL must use HTTP loopback"
        )
    return value


class GeometryGuidanceProvider:
    """Validated ``u2netp`` HTTP adapter for front/back live geometry."""

    def __init__(self, client: GeometryMaskHttpClient, *, remove_url: str) -> None:
        if not isinstance(client, GeometryMaskHttpClient):
            raise GeometryGuidanceContractError(
                "geometry client must provide an async post method"
            )
        self._client = client
        self._remove_url = _validate_loopback_url(remove_url)

    @property
    def model(self) -> str:
        return GEOMETRY_MASK_MODEL

    @property
    def timeout_seconds(self) -> float:
        return GEOMETRY_MASK_TIMEOUT_SECONDS

    @property
    def remove_url(self) -> str:
        return self._remove_url

    async def _request_mask(
        self,
        frame: EncodedImage,
        *,
        timeout_seconds: float = GEOMETRY_MASK_TIMEOUT_SECONDS,
    ) -> bytes:
        files: dict[str, object] = {
            "file": ("geometry-frame", frame.data, frame.mime_type)
        }
        form = {"model": GEOMETRY_MASK_MODEL, "om": "true"}
        try:
            async with asyncio.timeout(timeout_seconds):
                response = await self._client.post(
                    self._remove_url,
                    files=files,
                    data=form,
                    timeout=timeout_seconds,
                )
        except asyncio.CancelledError:
            raise
        except TimeoutError as error:
            raise GeometryGuidanceTimeoutError(
                "geometry mask request exceeded its deadline"
            ) from error
        except GeometryGuidanceProviderError:
            raise
        except Exception as error:
            raise GeometryGuidanceProviderError("geometry mask request failed") from error

        status_code = getattr(response, "status_code", None)
        if (
            isinstance(status_code, bool)
            or not isinstance(status_code, int)
            or not 200 <= status_code < 300
        ):
            if isinstance(status_code, int) and not isinstance(status_code, bool):
                raise GeometryGuidanceProviderError(
                    f"geometry mask provider returned HTTP {status_code}"
                )
            raise GeometryGuidanceProviderError(
                "geometry mask provider returned an invalid HTTP response"
            )
        if _content_type(getattr(response, "headers", None)) != "image/png":
            raise GeometryGuidanceContractError(
                "geometry mask response Content-Type must be image/png"
            )
        content = getattr(response, "content", None)
        if not isinstance(content, bytes) or not content:
            raise GeometryGuidanceContractError(
                "geometry mask response must contain PNG bytes"
            )
        if len(content) > GEOMETRY_MASK_MAX_BYTES:
            raise GeometryGuidanceContractError("geometry mask exceeds the size limit")
        return content

    async def analyze_geometry(
        self,
        input_value: GuidanceInput | Mapping[str, object],
    ) -> VisionDecision | None:
        validated = validate_guidance_input(input_value)
        if validated.requested_shot not in _GEOMETRY_SHOTS:
            raise GeometryGuidanceContractError(
                "geometry guidance supports only front or back"
            )
        prepared_frame = await asyncio.to_thread(
            _prepare_geometry_frame, validated.frame
        )
        expected_size = (prepared_frame.width, prepared_frame.height)
        if expected_size[0] is None or expected_size[1] is None:
            raise GeometryGuidanceContractError(
                "prepared geometry frame dimensions are unavailable"
            )
        mask = await self._request_mask(prepared_frame)
        return await asyncio.to_thread(classify_mask_geometry, mask, expected_size)

    async def prewarm(self) -> None:
        """Load the shared sidecar model without requiring a usable garment mask."""

        frame = EncodedImage(_PREWARM_PNG, "image/png", 8, 8)
        mask = await self._request_mask(
            frame,
            timeout_seconds=GEOMETRY_PREWARM_TIMEOUT_SECONDS,
        )
        await asyncio.to_thread(_decode_mask_pixels, mask, expected_size=(8, 8))

    async def aclose(self) -> None:
        """The provider does not own or stop the process-global rembg sidecar."""

        return None

    close = aclose

    def new_session(self) -> "GeometryGuidanceProvider":
        """Return a stateless session handle sharing the loopback HTTP client."""

        return GeometryGuidanceProvider(self._client, remove_url=self._remove_url)


__all__ = [
    "GEOMETRY_BORDER_DISTANCE_PIXELS",
    "GEOMETRY_IMAGE_MAX_EDGE",
    "GEOMETRY_JPEG_QUALITY",
    "GEOMETRY_MASK_FOREGROUND_THRESHOLD",
    "GEOMETRY_MASK_MODEL",
    "GEOMETRY_MASK_TIMEOUT_SECONDS",
    "GEOMETRY_PREWARM_TIMEOUT_SECONDS",
    "GarmentGeometry",
    "GeometryGuidanceContractError",
    "GeometryGuidanceProvider",
    "GeometryGuidanceProviderError",
    "GeometryGuidanceTimeoutError",
    "GeometryMaskHttpClient",
    "GeometryMaskHttpResponse",
    "classify_geometry",
    "classify_mask_geometry",
    "geometry_from_mask_png",
]
