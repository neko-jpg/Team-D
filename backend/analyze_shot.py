"""Post-capture ShotAssessor HTTP boundary.

The route owns no capture-session or reducer state. It only validates an
upload, invokes an injected provider, and returns a strictly validated result.
Consequently error responses cannot advance capture progress.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Final

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from .providers.shot_assessor import (
    AssessmentImage,
    RequestedShot,
    ShotAssessmentContractError,
    ShotAssessor,
    ShotAssessorInput,
    validate_shot_assessment,
)


ANALYZE_TIMEOUT_SECONDS: Final[float] = 20.0
MAX_UPLOAD_BYTES: Final[int] = 10 * 1024 * 1024
ALLOWED_IMAGE_MIME_TYPES: Final[frozenset[str]] = frozenset(
    {"image/jpeg", "image/png", "image/webp"}
)


def _provider_error(code: str, message: str, *, retryable: bool) -> dict[str, object]:
    return {
        "provider": "shot-assessor",
        "code": code,
        "message": message,
        "retryable": retryable,
    }


def get_shot_assessor() -> ShotAssessor:
    """Dependency seam for explicit provider wiring.

    This does not automatically replace a live failure with a fixture success;
    task 4.5 owns provider-mode construction and can override this seam.
    """

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=_provider_error("UNAVAILABLE", "ShotAssessor is not configured", retryable=True),
    )


def get_analysis_timeout_seconds() -> float:
    """Dependency seam so timeout behavior is testable without waiting 20 s."""

    return ANALYZE_TIMEOUT_SECONDS


async def _read_limited_upload(file: UploadFile) -> bytes:
    mime_type = file.content_type
    if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=_provider_error("INVALID_INPUT", "Unsupported image MIME type", retryable=False),
        )

    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=_provider_error("INVALID_INPUT", "Image exceeds the upload size limit", retryable=False),
        )

    # Read one byte past the limit so an omitted Content-Length cannot bypass it.
    image = await file.read(MAX_UPLOAD_BYTES + 1)
    if not image:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_provider_error("INVALID_INPUT", "Image file is empty", retryable=False),
        )
    if len(image) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=_provider_error("INVALID_INPUT", "Image exceeds the upload size limit", retryable=False),
        )
    return image


analyze_shot_router = APIRouter()


@analyze_shot_router.post("/api/analyze-shot")
async def analyze_shot(
    requested_shot: Annotated[RequestedShot, Form(alias="requestedShot")],
    file: Annotated[UploadFile, File()],
    assessor: Annotated[ShotAssessor, Depends(get_shot_assessor)],
    timeout_seconds: Annotated[float, Depends(get_analysis_timeout_seconds)],
) -> dict[str, object]:
    """Assess a front/back/tag multipart image within the 20-second budget."""

    image = await _read_limited_upload(file)
    try:
        raw_assessment = await asyncio.wait_for(
            assessor.assess(
                ShotAssessorInput(
                    image=AssessmentImage(image, file.content_type or ""),
                    requested_shot=requested_shot,
                )
            ),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=_provider_error("TIMEOUT", "Shot assessment timed out", retryable=True),
        ) from None
    except ShotAssessmentContractError:
        # A live Responses adapter validates before returning.  Classify that
        # path the same way as an invalid mapping returned by a test/provider.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_provider_error(
                "INVALID_RESPONSE", "Shot assessor returned an invalid response", retryable=True
            ),
        ) from None
    except HTTPException:
        raise
    except Exception:
        # Do not expose provider internals or turn an error into a success.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_provider_error("UNAVAILABLE", "Shot assessment is unavailable", retryable=True),
        ) from None

    try:
        return validate_shot_assessment(raw_assessment).to_payload()
    except ShotAssessmentContractError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_provider_error("INVALID_RESPONSE", "Shot assessor returned an invalid response", retryable=True),
        ) from None


__all__ = [
    "ALLOWED_IMAGE_MIME_TYPES",
    "ANALYZE_TIMEOUT_SECONDS",
    "MAX_UPLOAD_BYTES",
    "RequestedShot",
    "ShotAssessor",
    "analyze_shot_router",
    "get_analysis_timeout_seconds",
    "get_shot_assessor",
]
