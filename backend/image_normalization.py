"""Pure image-normalization boundary for post-capture analysis.

The capture bytes are deliberately accepted as an immutable value and are
never written to or returned as a modified original.  This module creates a
separate, canonical PNG analysis copy with EXIF orientation applied and an
sRGB ICC profile embedded.  Pillow is imported only when the boundary is
called so a missing optional image dependency cannot make ``backend`` imports
fail at process startup.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Final


SUPPORTED_INPUT_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {"image/jpeg", "image/png", "image/webp"}
)
"""The intentionally small set of raster upload types accepted for analysis."""

_FORMAT_TO_MIME: Final[dict[str, str]] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
CANONICAL_ANALYSIS_MIME_TYPE: Final[str] = "image/png"


class ImageNormalizationError(ValueError):
    """Base class for finite, safe image-normalization contract failures."""

    code: Final[str] = "NORMALIZATION_FAILED"


class InvalidImageInputError(ImageNormalizationError):
    """The caller did not provide a non-empty immutable byte payload."""

    code: Final[str] = "INVALID_INPUT"


class PillowUnavailableError(ImageNormalizationError):
    """Pillow is not installed or cannot be imported in this runtime."""

    code: Final[str] = "PILLOW_UNAVAILABLE"


class UnsupportedImageFormatError(ImageNormalizationError):
    """The declared or decoded image format is outside the upload allowlist."""

    code: Final[str] = "UNSUPPORTED_FORMAT"


class ImageMimeMismatchError(UnsupportedImageFormatError):
    """The supplied MIME type does not match the safely decoded image format."""

    code: Final[str] = "MIME_MISMATCH"


class ImageDecodeError(ImageNormalizationError):
    """The image could not be safely decoded, including a corrupt payload."""

    code: Final[str] = "DECODE_FAILED"


class ImageConversionError(ImageNormalizationError):
    """EXIF, ICC, color conversion, or canonical encoding failed."""

    code: Final[str] = "CONVERSION_FAILED"


@dataclass(frozen=True, slots=True)
class NormalizedAnalysisImage:
    """An immutable sRGB analysis copy, separate from the raw capture bytes."""

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


# A shorter name is useful at call sites that already refer to the analysis
# boundary.  It remains an alias, not a second mutable result type.
AnalysisImage = NormalizedAnalysisImage


def _normalized_declared_mime(mime_type: str | None) -> str | None:
    if mime_type is None:
        return None
    if not isinstance(mime_type, str):
        raise UnsupportedImageFormatError("image MIME type must be a string")
    normalized = mime_type.split(";", 1)[0].strip().lower()
    if normalized not in SUPPORTED_INPUT_MIME_TYPES:
        raise UnsupportedImageFormatError("unsupported image MIME type")
    return normalized


def normalize_upload_for_analysis(
    original_bytes: bytes,
    mime_type: str | None = None,
) -> NormalizedAnalysisImage:
    """Build an sRGB PNG copy for AI analysis without changing ``original_bytes``.

    ``mime_type`` is optional for non-HTTP callers.  When supplied it must be
    one of the supported types and must agree with the actual decoded format;
    this keeps a caller from treating arbitrary bytes as an allowed image.
    """

    if not isinstance(original_bytes, bytes) or not original_bytes:
        raise InvalidImageInputError("image data must be non-empty bytes")
    declared_mime = _normalized_declared_mime(mime_type)

    try:
        from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError
    except (ImportError, ModuleNotFoundError) as exc:
        raise PillowUnavailableError("Pillow is required to normalize images") from exc

    try:
        with Image.open(BytesIO(original_bytes)) as decoded:
            decoded_format = decoded.format
            actual_mime = _FORMAT_TO_MIME.get(decoded_format or "")
            if actual_mime is None:
                raise UnsupportedImageFormatError("unsupported decoded image format")
            if declared_mime is not None and declared_mime != actual_mime:
                raise ImageMimeMismatchError("declared MIME type does not match decoded image")

            # ``load`` is intentional: ``Image.open`` alone is lazy and would
            # otherwise let truncated/corrupt uploads cross this boundary.
            decoded.load()
            # Keep decoded pixels and metadata after the input buffer is
            # closed; ``Image.open`` owns a lazily-backed file object.
            decoded = decoded.copy()
    except UnsupportedImageFormatError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise ImageDecodeError("image bytes could not be decoded") from exc

    try:
        # No EXIF is copied into the generated PNG, so the applied orientation
        # cannot be applied a second time by a downstream consumer.
        oriented = ImageOps.exif_transpose(decoded)
        input_icc = decoded.info.get("icc_profile")
        alpha = oriented.getchannel("A") if "A" in oriented.getbands() else None
        # Pillow/ImageCms profiles transform colour bands, not alpha.  Split
        # alpha first so RGBA uploads work consistently across Pillow builds,
        # then restore the untouched coverage channel after RGB conversion.
        color_source = oriented.convert("RGB") if alpha is not None else oriented
        # Pillow 11 returns a low-level CmsProfile from createProfile(), whose
        # bytes are exposed by the ImageCmsProfile wrapper rather than directly.
        srgb_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))

        if input_icc:
            source_profile = ImageCms.ImageCmsProfile(BytesIO(input_icc))
            rgb = ImageCms.profileToProfile(
                color_source,
                source_profile,
                srgb_profile,
                outputMode="RGB",
            )
        else:
            # Absent an embedded profile, raster uploads conventionally use
            # sRGB; convert only their storage mode into an explicit RGB mode.
            rgb = color_source.convert("RGB")

        if alpha is not None:
            rgb.putalpha(alpha)

        output = BytesIO()
        rgb.save(
            output,
            format="PNG",
            icc_profile=srgb_profile.tobytes(),
        )
        data = output.getvalue()
        if not data:
            raise ImageConversionError("normalization produced no image data")
        return NormalizedAnalysisImage(
            data=data,
            width=rgb.width,
            height=rgb.height,
        )
    except ImageNormalizationError:
        raise
    except Exception as exc:
        # ImageCms exceptions vary across Pillow builds; keep this boundary's
        # public error finite without leaking implementation details.
        raise ImageConversionError("image could not be converted to sRGB") from exc


# Kept as deliberate aliases for HTTP and provider wiring that use either term.
normalize_image_for_analysis = normalize_upload_for_analysis
normalize_image = normalize_upload_for_analysis


__all__ = [
    "AnalysisImage",
    "CANONICAL_ANALYSIS_MIME_TYPE",
    "ImageConversionError",
    "ImageDecodeError",
    "ImageMimeMismatchError",
    "ImageNormalizationError",
    "InvalidImageInputError",
    "NormalizedAnalysisImage",
    "PillowUnavailableError",
    "SUPPORTED_INPUT_MIME_TYPES",
    "UnsupportedImageFormatError",
    "normalize_image",
    "normalize_image_for_analysis",
    "normalize_upload_for_analysis",
]
