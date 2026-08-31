"""Text-only background image generation with strict response validation.

The provider boundary deliberately accepts only an allow-listed style ID.  It
never receives garment data, so an Images API request cannot accidentally
contain an original image, mask, tag, measurement, or another binary field.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable


BACKGROUND_GENERATION_TIMEOUT_SECONDS = 60.0
MAX_GENERATED_BACKGROUND_BYTES = 20 * 1024 * 1024
MAX_GENERATED_BACKGROUND_PIXELS = 4096 * 4096

_PROMPT_SUFFIX = (
    "The image must show only an empty photography background from a direct "
    "overhead, top-down viewpoint with soft, even lighting. It must contain "
    "no person, clothing, garment, hanger, text, or logo."
)

ALLOWED_BACKGROUND_STYLE_PROMPTS: Mapping[str, str] = MappingProxyType(
    {
        "studio-white": (
            "Create a clean matte white seamless studio surface. " + _PROMPT_SUFFIX
        ),
        "neutral-gray": (
            "Create a smooth neutral light-gray seamless studio surface. "
            + _PROMPT_SUFFIX
        ),
        "warm-wood": (
            "Create a clean light warm-wood photography surface with subtle, "
            "natural grain. "
            + _PROMPT_SUFFIX
        ),
    }
)


class BackgroundGenerationContractError(ValueError):
    """Raised when an input or generated response violates the contract."""


class BackgroundGenerationProviderError(RuntimeError):
    """Raised when the external Images API request fails."""


class BackgroundGenerationTimeoutError(TimeoutError):
    """Raised when background generation exceeds its local deadline."""


@dataclass(frozen=True, slots=True)
class GeneratedBackground:
    """A decoded and verified, single-frame PNG background."""

    data: bytes
    width: int
    height: int
    mime_type: str = "image/png"

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes) or not self.data:
            raise BackgroundGenerationContractError(
                "generated background data must be non-empty bytes"
            )
        if (
            not isinstance(self.width, int)
            or isinstance(self.width, bool)
            or self.width <= 0
        ):
            raise BackgroundGenerationContractError(
                "generated background width must be a positive integer"
            )
        if (
            not isinstance(self.height, int)
            or isinstance(self.height, bool)
            or self.height <= 0
        ):
            raise BackgroundGenerationContractError(
                "generated background height must be a positive integer"
            )
        if self.mime_type != "image/png":
            raise BackgroundGenerationContractError(
                "generated background MIME type must be image/png"
            )


@runtime_checkable
class ImagesClient(Protocol):
    """Small injectable subset of an asynchronous Images API client."""

    async def generate(self, **kwargs: object) -> object:
        """Generate one image from a text request."""


class BackgroundGenerator:
    """Generate one safe background using text derived from an allowed style."""

    def __init__(self, client: ImagesClient, model: str) -> None:
        if not callable(getattr(client, "generate", None)):
            raise BackgroundGenerationContractError(
                "client must provide an async generate method"
            )
        if not isinstance(model, str) or not model.strip():
            raise BackgroundGenerationContractError("model must be a non-empty string")
        self._client = client
        self._model = model.strip()

    async def generate(self, style_id: str) -> GeneratedBackground:
        prompt = _prompt_for_style(style_id)
        try:
            response = await asyncio.wait_for(
                self._client.generate(
                    model=self._model,
                    prompt=prompt,
                    n=1,
                    response_format="b64_json",
                ),
                timeout=BACKGROUND_GENERATION_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise BackgroundGenerationTimeoutError(
                "background generation exceeded the 60 second timeout"
            ) from exc
        except BackgroundGenerationProviderError:
            raise
        except Exception as exc:
            raise BackgroundGenerationProviderError(
                "background generation provider request failed"
            ) from exc

        encoded = _extract_single_base64_image(response)
        return _decode_generated_background(encoded)


class FixtureBackgroundGenerator:
    """Deterministic local background provider selected only by its caller."""

    async def generate(self, style_id: str) -> GeneratedBackground:
        _prompt_for_style(style_id)
        encoded = _FIXTURE_BACKGROUND_BASE64[style_id]
        return _decode_generated_background(encoded)


def _prompt_for_style(style_id: str) -> str:
    if not isinstance(style_id, str) or not style_id:
        raise BackgroundGenerationContractError(
            "background style ID must be a non-empty string"
        )
    try:
        return ALLOWED_BACKGROUND_STYLE_PROMPTS[style_id]
    except KeyError as exc:
        raise BackgroundGenerationContractError(
            f"unknown background style ID: {style_id}"
        ) from exc


def _extract_single_base64_image(response: object) -> str:
    data = _field(response, "data")
    if (
        not isinstance(data, Sequence)
        or isinstance(data, (str, bytes, bytearray))
        or len(data) != 1
    ):
        raise BackgroundGenerationContractError(
            "Images API response must contain exactly one generated image"
        )
    encoded = _field(data[0], "b64_json")
    if not isinstance(encoded, str) or not encoded:
        raise BackgroundGenerationContractError(
            "Images API response must contain non-empty base64 PNG data"
        )
    return encoded


def _field(value: object, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _decode_generated_background(encoded: str) -> GeneratedBackground:
    # Reject an excessive response before allocating the decoded byte buffer.
    max_base64_length = 4 * ((MAX_GENERATED_BACKGROUND_BYTES + 2) // 3)
    if len(encoded) > max_base64_length:
        raise BackgroundGenerationContractError(
            "generated background exceeds the encoded size limit"
        )
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BackgroundGenerationContractError(
            "generated background is not valid base64"
        ) from exc
    if not data:
        raise BackgroundGenerationContractError("generated background is empty")
    if len(data) > MAX_GENERATED_BACKGROUND_BYTES:
        raise BackgroundGenerationContractError(
            "generated background exceeds the decoded size limit"
        )

    width, height = _validate_png(data)
    return GeneratedBackground(data=data, width=width, height=height)


def _validate_png(data: bytes) -> tuple[int, int]:
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover - required by deployment
        raise BackgroundGenerationProviderError(
            "Pillow is required to validate generated backgrounds"
        ) from exc

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as candidate:
                if candidate.format != "PNG":
                    raise BackgroundGenerationContractError(
                        "generated background must be a PNG image"
                    )
                if candidate.width * candidate.height > MAX_GENERATED_BACKGROUND_PIXELS:
                    raise BackgroundGenerationContractError(
                        "generated background exceeds the pixel limit"
                    )
                candidate.verify()

            with Image.open(BytesIO(data)) as image:
                if image.format != "PNG":
                    raise BackgroundGenerationContractError(
                        "generated background must be a PNG image"
                    )
                if getattr(image, "n_frames", 1) != 1:
                    raise BackgroundGenerationContractError(
                        "generated background must contain exactly one frame"
                    )
                width, height = image.size
                if width <= 0 or height <= 0:
                    raise BackgroundGenerationContractError(
                        "generated background dimensions must be positive"
                    )
                if width * height > MAX_GENERATED_BACKGROUND_PIXELS:
                    raise BackgroundGenerationContractError(
                        "generated background exceeds the pixel limit"
                    )
                image.load()
                alpha_extrema = image.convert("RGBA").getchannel("A").getextrema()
                if alpha_extrema[1] == 0:
                    raise BackgroundGenerationContractError(
                        "generated background must not be fully transparent"
                    )
                return width, height
    except BackgroundGenerationContractError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise BackgroundGenerationContractError(
            "generated background is not a decodable PNG"
        ) from exc


_FIXTURE_BACKGROUND_BASE64: Mapping[str, str] = MappingProxyType(
    {
        "studio-white": (
            "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP89esH"
            "AzbAhFV00EoAAEmwAvyg/dtLAAAAAElFTkSuQmCC"
        ),
        "neutral-gray": (
            "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGO8cOkK"
            "AzbAhFV00EoAAO5XAobjPaSbAAAAAElFTkSuQmCC"
        ),
        "warm-wood": (
            "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGO8d3Qp"
            "AzbAhFV00EoAAMsAAlh284XuAAAAAElFTkSuQmCC"
        ),
    }
)


__all__ = [
    "ALLOWED_BACKGROUND_STYLE_PROMPTS",
    "BACKGROUND_GENERATION_TIMEOUT_SECONDS",
    "BackgroundGenerationContractError",
    "BackgroundGenerationProviderError",
    "BackgroundGenerationTimeoutError",
    "BackgroundGenerator",
    "FixtureBackgroundGenerator",
    "GeneratedBackground",
    "ImagesClient",
]
