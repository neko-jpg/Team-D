"""Pure image-normalization boundary for post-capture analysis.

The capture bytes are immutable input. This module creates a separate,
canonical PNG analysis copy with EXIF orientation applied and an sRGB ICC
profile embedded. Pillow is imported only when normalization is requested so
a missing runtime dependency does not prevent the backend from starting.
"""

from __future__ import annotations

import struct
import warnings
from dataclasses import dataclass
from io import BytesIO
from typing import Final, Protocol, runtime_checkable


SUPPORTED_INPUT_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {"image/jpeg", "image/png", "image/webp"}
)
_FORMAT_TO_MIME: Final[dict[str, str]] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
CANONICAL_ANALYSIS_MIME_TYPE: Final[str] = "image/png"

# A 48-megapixel iPhone capture remains valid, while a small compressed upload
# cannot expand to Pillow's much larger default decompression-bomb threshold.
MAX_ANALYSIS_PIXELS: Final[int] = 50_000_000


class ImageNormalizationError(ValueError):
    """Base class for finite, safe image-normalization contract failures."""

    code: Final[str] = "NORMALIZATION_FAILED"


class InvalidImageInputError(ImageNormalizationError):
    code: Final[str] = "INVALID_INPUT"


class PillowUnavailableError(ImageNormalizationError):
    code: Final[str] = "PILLOW_UNAVAILABLE"


class UnsupportedImageFormatError(ImageNormalizationError):
    code: Final[str] = "UNSUPPORTED_FORMAT"


class ImageMimeMismatchError(UnsupportedImageFormatError):
    code: Final[str] = "MIME_MISMATCH"


class ImageDecodeError(ImageNormalizationError):
    code: Final[str] = "DECODE_FAILED"


class ImageConversionError(ImageNormalizationError):
    code: Final[str] = "CONVERSION_FAILED"


@dataclass(frozen=True, slots=True)
class NormalizedAnalysisImage:
    """An immutable sRGB analysis copy, separate from raw capture bytes."""

    data: bytes
    width: int
    height: int
    mime_type: str = CANONICAL_ANALYSIS_MIME_TYPE
    is_srgb: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes) or not self.data:
            raise InvalidImageInputError("normalized image data must be non-empty bytes")
        if not isinstance(self.width, int) or self.width <= 0:
            raise ImageConversionError("normalized image width must be positive")
        if not isinstance(self.height, int) or self.height <= 0:
            raise ImageConversionError("normalized image height must be positive")
        if self.mime_type != CANONICAL_ANALYSIS_MIME_TYPE:
            raise ImageConversionError("normalized analysis images must be PNG")
        if self.is_srgb is not True:
            raise ImageConversionError("normalized analysis images must be sRGB")


AnalysisImage = NormalizedAnalysisImage


@runtime_checkable
class ImageNormalizer(Protocol):
    """Dependency boundary used by the HTTP route."""

    def normalize(self, data: bytes, mime_type: str) -> NormalizedAnalysisImage:
        """Return a disposable analysis copy without retaining the input."""


def _normalized_declared_mime(mime_type: str | None) -> str | None:
    if mime_type is None:
        return None
    if not isinstance(mime_type, str):
        raise UnsupportedImageFormatError("image MIME type must be a string")
    normalized = mime_type.split(";", 1)[0].strip().lower()
    if normalized not in SUPPORTED_INPUT_MIME_TYPES:
        raise UnsupportedImageFormatError("unsupported image MIME type")
    return normalized


def _canonical_srgb_profile(ImageCms: object) -> tuple[bytes, object]:
    """Create stable profile bytes so repeated PNG output is byte-identical."""

    generated_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    generated_bytes = generated_profile.tobytes()
    if len(generated_bytes) < 128 or generated_bytes[36:40] != b"acsp":
        raise ImageConversionError("Pillow did not create a valid sRGB profile")

    canonical = bytearray(generated_bytes)
    canonical[24:36] = struct.pack(">6H", 2000, 1, 1, 0, 0, 0)
    canonical[84:100] = bytes(16)
    profile_bytes = bytes(canonical)
    return profile_bytes, ImageCms.ImageCmsProfile(BytesIO(profile_bytes))


def normalize_upload_for_analysis(
    original_bytes: bytes,
    mime_type: str | None = None,
) -> NormalizedAnalysisImage:
    """Create an oriented sRGB PNG copy without changing ``original_bytes``."""

    if not isinstance(original_bytes, bytes) or not original_bytes:
        raise InvalidImageInputError("image data must be non-empty bytes")
    declared_mime = _normalized_declared_mime(mime_type)

    try:
        from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError
    except (ImportError, ModuleNotFoundError) as exc:
        raise PillowUnavailableError("Pillow is required to normalize images") from exc

    decoded_copy = None
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(BytesIO(original_bytes)) as decoded:
                actual_mime = _FORMAT_TO_MIME.get(decoded.format or "")
                if actual_mime is None:
                    raise UnsupportedImageFormatError("unsupported decoded image format")
                if declared_mime is not None and declared_mime != actual_mime:
                    raise ImageMimeMismatchError(
                        "declared MIME type does not match decoded image"
                    )

                pixel_count = decoded.width * decoded.height
                if pixel_count <= 0 or pixel_count > MAX_ANALYSIS_PIXELS:
                    raise ImageDecodeError("image dimensions exceed the safe decode limit")

                decoded.load()
                decoded_copy = decoded.copy()
        except ImageNormalizationError:
            raise
        except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
            raise ImageDecodeError("image dimensions exceed the safe decode limit") from exc
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
            raise ImageDecodeError("image bytes could not be decoded") from exc

    oriented = None
    color_source = None
    rgb = None
    try:
        oriented = ImageOps.exif_transpose(decoded_copy)
        input_icc = decoded_copy.info.get("icc_profile")
        alpha = oriented.getchannel("A") if "A" in oriented.getbands() else None
        color_source = oriented.convert("RGB") if alpha is not None else oriented
        profile_bytes, srgb_profile = _canonical_srgb_profile(ImageCms)

        if input_icc:
            source_profile = ImageCms.ImageCmsProfile(BytesIO(input_icc))
            rgb = ImageCms.profileToProfile(
                color_source,
                source_profile,
                srgb_profile,
                renderingIntent=ImageCms.Intent.PERCEPTUAL,
                outputMode="RGB",
            )
        else:
            rgb = color_source.convert("RGB")

        if alpha is not None:
            rgb.putalpha(alpha)
        rgb.info.clear()

        output = BytesIO()
        rgb.save(
            output,
            format="PNG",
            optimize=False,
            compress_level=9,
            icc_profile=profile_bytes,
        )
        data = output.getvalue()
        if not data:
            raise ImageConversionError("normalization produced no image data")
        return NormalizedAnalysisImage(data=data, width=rgb.width, height=rgb.height)
    except ImageNormalizationError:
        raise
    except Exception as exc:
        raise ImageConversionError("image could not be converted to sRGB") from exc
    finally:
        if rgb is not None and rgb is not color_source and rgb is not oriented:
            rgb.close()
        if color_source is not None and color_source is not oriented:
            color_source.close()
        if oriented is not None and oriented is not decoded_copy:
            oriented.close()
        if decoded_copy is not None:
            decoded_copy.close()


normalize_image_for_analysis = normalize_upload_for_analysis
normalize_image = normalize_upload_for_analysis


class PillowImageNormalizer:
    """Object adapter for FastAPI dependency injection."""

    def normalize(self, data: bytes, mime_type: str) -> NormalizedAnalysisImage:
        return normalize_upload_for_analysis(data, mime_type)


__all__ = [
    "AnalysisImage",
    "CANONICAL_ANALYSIS_MIME_TYPE",
    "ImageConversionError",
    "ImageDecodeError",
    "ImageMimeMismatchError",
    "ImageNormalizationError",
    "ImageNormalizer",
    "InvalidImageInputError",
    "MAX_ANALYSIS_PIXELS",
    "NormalizedAnalysisImage",
    "PillowImageNormalizer",
    "PillowUnavailableError",
    "SUPPORTED_INPUT_MIME_TYPES",
    "UnsupportedImageFormatError",
    "normalize_image",
    "normalize_image_for_analysis",
    "normalize_upload_for_analysis",
]
