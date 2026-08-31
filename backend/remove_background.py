"""FastAPI boundary for producing a verified mask of the front original.

The routes accept one unmodified garment image and delegate background removal
to an injected ``GarmentMaskerProvider``. The established endpoint returns the
validated mask-only PNG; the backend verification endpoint applies that mask
to EXIF-oriented original RGB pixels and returns an RGBA PNG. Timeouts and
invalid sidecar output stay finite errors and are never replaced with a fixture
success.
"""

from __future__ import annotations

import asyncio
import warnings
from io import BytesIO
from typing import Annotated, Final

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)

from .providers.garment_masker import (
    GarmentMask,
    GarmentMaskContractError,
    GarmentMaskInput,
    GarmentMaskProviderError,
    GarmentMaskUnavailableError,
    GarmentMaskerProvider,
    REMBG_TIMEOUT_SECONDS,
    validate_garment_mask_png,
)


REMOVE_BACKGROUND_TIMEOUT_SECONDS: Final[float] = REMBG_TIMEOUT_SECONDS
MAX_REMOVE_BACKGROUND_UPLOAD_BYTES: Final[int] = 10 * 1024 * 1024
MAX_FRONT_IMAGE_PIXELS: Final[int] = 50_000_000
ALLOWED_FRONT_IMAGE_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {"image/jpeg", "image/png", "image/webp"}
)
_FRONT_FORMAT_TO_MIME: Final[dict[str, str]] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


class _InvalidFrontImage(ValueError):
    """The front upload cannot be safely decoded under its declared MIME type."""


class _ImageValidationUnavailable(RuntimeError):
    """The image runtime required at the API boundary is unavailable."""


class _PreviewCreationError(RuntimeError):
    """A validated original and mask could not be rendered as a preview."""


def _masker_error(code: str, message: str, *, retryable: bool) -> dict[str, object]:
    return {
        "provider": "garment-masker",
        "code": code,
        "message": message,
        "retryable": retryable,
    }


def get_garment_masker() -> GarmentMaskerProvider:
    """Dependency seam for the configured rembg-backed masker."""

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=_masker_error(
            "UNAVAILABLE",
            "Garment masker is not configured",
            retryable=True,
        ),
    )


def get_remove_background_timeout_seconds() -> float:
    """Dependency seam so timeout handling remains deterministic in tests."""

    return REMOVE_BACKGROUND_TIMEOUT_SECONDS


def _pillow_types() -> tuple[object, object, type[Exception]]:
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except (ImportError, ModuleNotFoundError) as error:
        raise _ImageValidationUnavailable from error
    return Image, ImageOps, UnidentifiedImageError


def _validate_front_image(image: bytes, declared_mime: str) -> tuple[int, int]:
    image_module, image_ops, unidentified_image_error = _pillow_types()
    with warnings.catch_warnings():
        warnings.simplefilter("error", image_module.DecompressionBombWarning)
        try:
            with image_module.open(BytesIO(image)) as decoded:
                actual_mime = _FRONT_FORMAT_TO_MIME.get(decoded.format or "")
                if actual_mime != declared_mime:
                    raise _InvalidFrontImage
                oriented = image_ops.exif_transpose(decoded)
                try:
                    width, height = oriented.size
                    pixel_count = width * height
                    if pixel_count <= 0 or pixel_count > MAX_FRONT_IMAGE_PIXELS:
                        raise _InvalidFrontImage
                    oriented.load()
                finally:
                    if oriented is not decoded:
                        oriented.close()
        except _InvalidFrontImage:
            raise
        except (
            image_module.DecompressionBombWarning,
            image_module.DecompressionBombError,
            unidentified_image_error,
            OSError,
            ValueError,
            SyntaxError,
        ) as error:
            raise _InvalidFrontImage from error
        except Exception as error:
            raise _InvalidFrontImage from error
    return width, height


def _validate_mask_result(
    mask: object,
    expected_size: tuple[int, int],
) -> GarmentMask:
    if not isinstance(mask, GarmentMask):
        raise GarmentMaskContractError("masker must return a GarmentMask")
    if (mask.width, mask.height) != expected_size:
        raise GarmentMaskContractError("mask metadata must match the front image")

    try:
        decoded_size = validate_garment_mask_png(mask.data, expected_size)
    except GarmentMaskContractError:
        raise
    except (GarmentMaskUnavailableError, _ImageValidationUnavailable):
        raise
    except Exception as error:
        raise GarmentMaskContractError("mask must be a decodable PNG") from error
    if decoded_size != expected_size:
        raise GarmentMaskContractError("mask dimensions must match the front image")
    return mask


def _render_transparent_preview(
    original: bytes,
    declared_mime: str,
    mask: GarmentMask,
    expected_size: tuple[int, int],
) -> bytes:
    """Apply a verified mask as alpha to EXIF-oriented original RGB pixels."""

    image_module, image_ops, unidentified_image_error = _pillow_types()
    oriented = None
    rgb = None
    alpha = None
    rgba = None
    try:
        with image_module.open(BytesIO(original)) as decoded:
            actual_mime = _FRONT_FORMAT_TO_MIME.get(decoded.format or "")
            if actual_mime != declared_mime:
                raise _PreviewCreationError
            oriented = image_ops.exif_transpose(decoded)
            if oriented.size != expected_size:
                raise _PreviewCreationError
            oriented.load()
            rgb = oriented.convert("RGB")

        with image_module.open(BytesIO(mask.data)) as decoded_mask:
            if decoded_mask.format != "PNG" or decoded_mask.mode not in {"1", "L"}:
                raise _PreviewCreationError
            if decoded_mask.size != expected_size:
                raise _PreviewCreationError
            decoded_mask.load()
            alpha = decoded_mask.convert("L")

        rgba = rgb.convert("RGBA")
        rgba.putalpha(alpha)
        output = BytesIO()
        rgba.save(output, format="PNG", optimize=False, compress_level=9)
        preview = output.getvalue()
        if not preview:
            raise _PreviewCreationError
        return preview
    except _PreviewCreationError:
        raise
    except _ImageValidationUnavailable:
        raise
    except (
        image_module.DecompressionBombWarning,
        image_module.DecompressionBombError,
        unidentified_image_error,
        OSError,
        ValueError,
        SyntaxError,
    ) as error:
        raise _PreviewCreationError from error
    except Exception as error:
        raise _PreviewCreationError from error
    finally:
        if rgba is not None:
            rgba.close()
        if alpha is not None:
            alpha.close()
        if rgb is not None:
            rgb.close()
        if oriented is not None:
            oriented.close()


async def _read_front_upload(file: UploadFile) -> tuple[bytes, tuple[int, int]]:
    if file.content_type not in ALLOWED_FRONT_IMAGE_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=_masker_error(
                "INVALID_INPUT",
                "Unsupported image MIME type",
                retryable=False,
            ),
        )

    if file.size is not None and file.size > MAX_REMOVE_BACKGROUND_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=_masker_error(
                "INVALID_INPUT",
                "Image exceeds the upload size limit",
                retryable=False,
            ),
        )

    image = await file.read(MAX_REMOVE_BACKGROUND_UPLOAD_BYTES + 1)
    if not image:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_masker_error(
                "INVALID_INPUT",
                "Image file is empty",
                retryable=False,
            ),
        )
    if len(image) > MAX_REMOVE_BACKGROUND_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=_masker_error(
                "INVALID_INPUT",
                "Image exceeds the upload size limit",
                retryable=False,
            ),
        )
    try:
        size = await asyncio.to_thread(
            _validate_front_image,
            image,
            file.content_type or "",
        )
    except _ImageValidationUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_masker_error(
                "UNAVAILABLE",
                "Image validation is unavailable",
                retryable=True,
            ),
        ) from None
    except _InvalidFrontImage:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_masker_error(
                "INVALID_INPUT",
                "Image could not be decoded or did not match its MIME type",
                retryable=False,
            ),
        ) from None
    return image, size


remove_background_router = APIRouter()


async def _mask_front_original(
    file: Annotated[UploadFile, File()],
    masker: GarmentMaskerProvider,
    timeout_seconds: float,
) -> tuple[bytes, tuple[int, int], GarmentMask]:
    original, original_size = await _read_front_upload(file)
    try:
        mask = await asyncio.wait_for(
            masker.mask(
                GarmentMaskInput(
                    data=original,
                    mime_type=file.content_type or "",
                )
            ),
            timeout=timeout_seconds,
        )
        mask = await asyncio.to_thread(
            _validate_mask_result,
            mask,
            original_size,
        )
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=_masker_error(
                "TIMEOUT",
                "Background removal timed out",
                retryable=True,
            ),
        ) from None
    except GarmentMaskContractError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_masker_error(
                "INVALID_RESPONSE",
                "Garment masker returned an invalid mask",
                retryable=True,
            ),
        ) from None
    except GarmentMaskProviderError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_masker_error(
                "UNAVAILABLE",
                "Background removal is unavailable",
                retryable=True,
            ),
        ) from None
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_masker_error(
                "UNAVAILABLE",
                "Background removal is unavailable",
                retryable=True,
            ),
        ) from None

    return original, original_size, mask


@remove_background_router.post("/api/remove-background")
async def remove_background(
    file: Annotated[UploadFile, File()],
    masker: Annotated[GarmentMaskerProvider, Depends(get_garment_masker)],
    timeout_seconds: Annotated[
        float,
        Depends(get_remove_background_timeout_seconds),
    ],
) -> Response:
    """Return the backward-compatible mask-only PNG response."""

    _original, _original_size, mask = await _mask_front_original(
        file,
        masker,
        timeout_seconds,
    )
    return Response(
        content=mask.data,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@remove_background_router.post("/api/remove-background-preview")
async def remove_background_preview(
    file: Annotated[UploadFile, File()],
    masker: Annotated[GarmentMaskerProvider, Depends(get_garment_masker)],
    timeout_seconds: Annotated[
        float,
        Depends(get_remove_background_timeout_seconds),
    ],
) -> Response:
    """Return an EXIF-oriented RGBA PNG with the garment mask as alpha."""

    original, original_size, mask = await _mask_front_original(
        file,
        masker,
        timeout_seconds,
    )
    try:
        preview = await asyncio.to_thread(
            _render_transparent_preview,
            original,
            file.content_type or "",
            mask,
            original_size,
        )
    except _ImageValidationUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_masker_error(
                "UNAVAILABLE",
                "Image preview rendering is unavailable",
                retryable=True,
            ),
        ) from None
    except _PreviewCreationError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_masker_error(
                "INVALID_RESPONSE",
                "Garment preview could not be created",
                retryable=True,
            ),
        ) from None

    return Response(
        content=preview,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = [
    "ALLOWED_FRONT_IMAGE_MIME_TYPES",
    "MAX_FRONT_IMAGE_PIXELS",
    "MAX_REMOVE_BACKGROUND_UPLOAD_BYTES",
    "REMOVE_BACKGROUND_TIMEOUT_SECONDS",
    "get_garment_masker",
    "get_remove_background_timeout_seconds",
    "remove_background_preview",
    "remove_background_router",
]
