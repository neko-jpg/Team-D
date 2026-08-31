"""FastAPI boundary for suggesting four garment measurement endpoints.

The route accepts exactly one perspective-corrected measurement image and
delegates endpoint inference to an injected ``MeasurementLineProvider``. It
does not calculate centimetres, mutate capture state, or silently replace a
live-provider failure with fixture output.
"""

from __future__ import annotations

import asyncio
import warnings
from io import BytesIO
from typing import Annotated, Final

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from .providers.measurement_line import (
    MeasurementImage,
    MeasurementLineContractError,
    MeasurementLineInput,
    MeasurementLineProvider,
    validate_measurement_endpoints,
)


MEASUREMENT_TIMEOUT_SECONDS: Final[float] = 20.0
MAX_MEASUREMENT_UPLOAD_BYTES: Final[int] = 10 * 1024 * 1024
MAX_MEASUREMENT_PIXELS: Final[int] = 50_000_000
ALLOWED_MEASUREMENT_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {"image/jpeg", "image/png", "image/webp"}
)
_MEASUREMENT_FORMAT_TO_MIME: Final[dict[str, str]] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


class _InvalidMeasurementImage(ValueError):
    """The upload is not a safe image matching its declared MIME type."""


class _MeasurementImageValidationUnavailable(RuntimeError):
    """The image decoder required at the HTTP boundary is unavailable."""


def _provider_error(code: str, message: str, *, retryable: bool) -> dict[str, object]:
    return {
        "provider": "measurement-line",
        "code": code,
        "message": message,
        "retryable": retryable,
    }


def get_measurement_line_provider() -> MeasurementLineProvider:
    """Dependency seam for explicit fixture or live provider wiring."""

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=_provider_error(
            "UNAVAILABLE",
            "Measurement line provider is not configured",
            retryable=True,
        ),
    )


def get_measurement_timeout_seconds() -> float:
    """Dependency seam so timeout handling is deterministic in tests."""

    return MEASUREMENT_TIMEOUT_SECONDS


def _validate_measurement_image(image: bytes, declared_mime: str) -> None:
    """Decode only for validation while preserving the projected source bytes."""

    try:
        from PIL import Image, UnidentifiedImageError
    except (ImportError, ModuleNotFoundError) as error:
        raise _MeasurementImageValidationUnavailable from error

    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(BytesIO(image)) as decoded:
                actual_mime = _MEASUREMENT_FORMAT_TO_MIME.get(decoded.format or "")
                if actual_mime != declared_mime:
                    raise _InvalidMeasurementImage
                pixel_count = decoded.width * decoded.height
                if pixel_count <= 0 or pixel_count > MAX_MEASUREMENT_PIXELS:
                    raise _InvalidMeasurementImage
                # ``verify()`` can accept a JPEG whose compressed pixel stream
                # is truncated. Fully decode so corrupt uploads never reach the
                # external provider while retaining the original bytes.
                decoded.load()
        except _InvalidMeasurementImage:
            raise
        except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
            raise _InvalidMeasurementImage from error
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as error:
            raise _InvalidMeasurementImage from error
        except Exception as error:
            raise _InvalidMeasurementImage from error


async def _read_measurement_upload(file: UploadFile) -> bytes:
    if file.content_type not in ALLOWED_MEASUREMENT_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=_provider_error(
                "INVALID_INPUT",
                "Unsupported image MIME type",
                retryable=False,
            ),
        )

    if file.size is not None and file.size > MAX_MEASUREMENT_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=_provider_error(
                "INVALID_INPUT",
                "Image exceeds the upload size limit",
                retryable=False,
            ),
        )

    image = await file.read(MAX_MEASUREMENT_UPLOAD_BYTES + 1)
    if not image:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_provider_error(
                "INVALID_INPUT",
                "Image file is empty",
                retryable=False,
            ),
        )
    if len(image) > MAX_MEASUREMENT_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=_provider_error(
                "INVALID_INPUT",
                "Image exceeds the upload size limit",
                retryable=False,
            ),
        )

    try:
        await asyncio.to_thread(
            _validate_measurement_image,
            image,
            file.content_type or "",
        )
    except _MeasurementImageValidationUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_provider_error(
                "UNAVAILABLE",
                "Image validation is unavailable",
                retryable=True,
            ),
        ) from None
    except _InvalidMeasurementImage:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_provider_error(
                "INVALID_INPUT",
                "Image could not be decoded or did not match its MIME type",
                retryable=False,
            ),
        ) from None
    return image


suggest_measurement_points_router = APIRouter()


@suggest_measurement_points_router.post("/api/suggest-measurement-points")
async def suggest_measurement_points(
    file: Annotated[UploadFile, File()],
    provider: Annotated[MeasurementLineProvider, Depends(get_measurement_line_provider)],
    timeout_seconds: Annotated[float, Depends(get_measurement_timeout_seconds)],
) -> dict[str, dict[str, float]]:
    """Return only four normalized endpoints for one projected image."""

    image = await _read_measurement_upload(file)
    provider_input = MeasurementLineInput(
        image=MeasurementImage(image, file.content_type or "")
    )
    try:
        raw_endpoints = await asyncio.wait_for(
            provider.suggest(provider_input),
            timeout=timeout_seconds,
        )
        return validate_measurement_endpoints(raw_endpoints).to_payload()
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=_provider_error(
                "TIMEOUT",
                "Measurement endpoint suggestion timed out",
                retryable=True,
            ),
        ) from None
    except MeasurementLineContractError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_provider_error(
                "INVALID_RESPONSE",
                "Measurement provider returned an invalid response",
                retryable=True,
            ),
        ) from None
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_provider_error(
                "UNAVAILABLE",
                "Measurement endpoint suggestion is unavailable",
                retryable=True,
            ),
        ) from None


__all__ = [
    "ALLOWED_MEASUREMENT_MIME_TYPES",
    "MAX_MEASUREMENT_UPLOAD_BYTES",
    "MAX_MEASUREMENT_PIXELS",
    "MEASUREMENT_TIMEOUT_SECONDS",
    "get_measurement_line_provider",
    "get_measurement_timeout_seconds",
    "suggest_measurement_points_router",
]
