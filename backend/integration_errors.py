"""Finite error payloads for non-HTTP backend integration boundaries.

FastAPI routes already serialize their own provider failures.  Agent transport
and the background generator are also used directly by backend smoke/E2E code,
so this module gives those exception boundaries the same closed public shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .guidance_transport import GuidanceTransportError
from .providers.background_generator import (
    BackgroundGenerationContractError,
    BackgroundGenerationProviderError,
    BackgroundGenerationTimeoutError,
)
from .providers.runtime import ProviderUnavailableError
from .providers.vision_guidance import GuidanceContractError


class IntegrationProvider(str, Enum):
    VISION_GUIDANCE = "vision-guidance"
    BACKGROUND_GENERATOR = "background-generator"


class IntegrationErrorCode(str, Enum):
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class IntegrationError:
    provider: IntegrationProvider
    code: IntegrationErrorCode
    message: str
    retryable: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", IntegrationProvider(self.provider))
        object.__setattr__(self, "code", IntegrationErrorCode(self.code))
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("integration error message must be non-empty")
        if not isinstance(self.retryable, bool):
            raise ValueError("integration error retryable must be a boolean")

    def to_payload(self) -> dict[str, object]:
        return {
            "provider": self.provider.value,
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
        }


def guidance_integration_error(error: BaseException) -> IntegrationError:
    """Sanitize an Agent/provider failure without returning fixture guidance."""

    if isinstance(error, GuidanceContractError):
        return IntegrationError(
            IntegrationProvider.VISION_GUIDANCE,
            IntegrationErrorCode.INVALID_RESPONSE,
            "Vision guidance provider returned an invalid response",
            True,
        )
    if isinstance(error, (GuidanceTransportError, ProviderUnavailableError)):
        return IntegrationError(
            IntegrationProvider.VISION_GUIDANCE,
            IntegrationErrorCode.UNAVAILABLE,
            "Vision guidance is unavailable",
            True,
        )
    return IntegrationError(
        IntegrationProvider.VISION_GUIDANCE,
        IntegrationErrorCode.UNKNOWN,
        "Vision guidance failed",
        True,
    )


def background_integration_error(error: BaseException) -> IntegrationError:
    """Sanitize background generation exceptions for smoke/E2E callers."""

    if isinstance(error, BackgroundGenerationTimeoutError):
        return IntegrationError(
            IntegrationProvider.BACKGROUND_GENERATOR,
            IntegrationErrorCode.TIMEOUT,
            "Background generation timed out",
            True,
        )
    if isinstance(error, BackgroundGenerationContractError):
        return IntegrationError(
            IntegrationProvider.BACKGROUND_GENERATOR,
            IntegrationErrorCode.INVALID_RESPONSE,
            "Background generator returned an invalid response",
            True,
        )
    if isinstance(error, BackgroundGenerationProviderError):
        return IntegrationError(
            IntegrationProvider.BACKGROUND_GENERATOR,
            IntegrationErrorCode.UNAVAILABLE,
            "Background generation is unavailable",
            True,
        )
    return IntegrationError(
        IntegrationProvider.BACKGROUND_GENERATOR,
        IntegrationErrorCode.UNKNOWN,
        "Background generation failed",
        True,
    )


def is_finite_integration_error(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != {
        "provider",
        "code",
        "message",
        "retryable",
    }:
        return False
    try:
        IntegrationProvider(value["provider"])
        IntegrationErrorCode(value["code"])
    except (TypeError, ValueError):
        return False
    return (
        isinstance(value["message"], str)
        and bool(value["message"].strip())
        and isinstance(value["retryable"], bool)
    )


__all__ = [
    "IntegrationError",
    "IntegrationErrorCode",
    "IntegrationProvider",
    "background_integration_error",
    "guidance_integration_error",
    "is_finite_integration_error",
]
