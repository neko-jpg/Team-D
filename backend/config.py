"""Shared, validated runtime settings for the HTTP server and Agent worker.

Both entrypoints intentionally read the same small configuration surface.  In
particular, ``PROVIDER_MODE`` is explicit: a live provider failure must not be
silently converted to a fixture response.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, cast


ProviderMode = Literal["fixture", "live"]


class ConfigurationError(ValueError):
    """Raised when an entrypoint receives unsafe or unsupported settings."""


@dataclass(frozen=True, slots=True)
class BackendSettings:
    """Settings shared by FastAPI, provider implementations, and the Agent."""

    provider_mode: ProviderMode
    api_host: str
    api_port: int

    @classmethod
    def from_env(cls) -> "BackendSettings":
        raw_mode = os.environ.get("PROVIDER_MODE", "fixture").strip().lower()
        if raw_mode not in {"fixture", "live"}:
            raise ConfigurationError("PROVIDER_MODE must be fixture or live")

        raw_port = os.environ.get("API_PORT", "3001")
        try:
            api_port = int(raw_port)
        except ValueError as error:
            raise ConfigurationError("API_PORT must be an integer between 1 and 65535") from error
        if not 1 <= api_port <= 65_535:
            raise ConfigurationError("API_PORT must be an integer between 1 and 65535")

        return cls(
            provider_mode=cast(ProviderMode, raw_mode),
            api_host=os.environ.get("API_HOST", "127.0.0.1"),
            api_port=api_port,
        )


__all__ = ["BackendSettings", "ConfigurationError", "ProviderMode"]
