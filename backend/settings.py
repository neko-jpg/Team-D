"""Shared, runtime-validated settings for the FastAPI server and Agent.

Only the two explicitly supported provider modes are accepted.  In
particular, selecting ``live`` never changes the mode to ``fixture`` when a
credential or analyzer is unavailable; the caller receives a configuration
or provider error instead.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum


class SettingsError(ValueError):
    """Raised when process configuration is missing or invalid."""


class ProviderMode(str, Enum):
    FIXTURE = "fixture"
    LIVE = "live"

    @classmethod
    def parse(cls, value: object) -> "ProviderMode":
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise SettingsError("PROVIDER_MODE must be fixture or live")
        try:
            return cls(value)
        except ValueError as error:
            raise SettingsError("PROVIDER_MODE must be fixture or live") from error


def _port(value: object, *, field_name: str = "API_PORT") -> int:
    if isinstance(value, bool):
        raise SettingsError(f"{field_name} must be an integer between 1 and 65535")
    try:
        converted = int(value)
    except (TypeError, ValueError) as error:
        raise SettingsError(
            f"{field_name} must be an integer between 1 and 65535"
        ) from error
    if not 1 <= converted <= 65_535:
        raise SettingsError(f"{field_name} must be an integer between 1 and 65535")
    return converted


def _positive_int(value: object, *, field_name: str, default: int | None = None) -> int:
    try:
        converted = int(value)
    except (TypeError, ValueError) as error:
        if default is not None:
            return default
        raise SettingsError(f"{field_name} must be a positive integer") from error
    if converted <= 0:
        if default is not None:
            return default
        raise SettingsError(f"{field_name} must be a positive integer")
    return converted


@dataclass(frozen=True, slots=True)
class BackendSettings:
    """Configuration shared by both Python process entrypoints.

    Credential fields are excluded from ``repr`` so an exception or startup
    diagnostic cannot accidentally print a LiveKit secret.
    """

    provider_mode: ProviderMode = ProviderMode.FIXTURE
    api_host: str = "127.0.0.1"
    api_port: int = 3001
    rembg_port: int = 7000
    livekit_url: str = field(default="", repr=False)
    livekit_api_key: str = field(default="", repr=False)
    livekit_api_secret: str = field(default="", repr=False)
    livekit_token_ttl_seconds: int = 90
    livekit_token_max_ttl_seconds: int = 300

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_mode", ProviderMode.parse(self.provider_mode))
        if not isinstance(self.api_host, str) or not self.api_host.strip():
            raise SettingsError("API_HOST must be a non-empty string")
        object.__setattr__(self, "api_port", _port(self.api_port))
        object.__setattr__(
            self,
            "rembg_port",
            _port(self.rembg_port, field_name="REMBG_PORT"),
        )
        for name in ("livekit_url", "livekit_api_key", "livekit_api_secret"):
            if not isinstance(getattr(self, name), str):
                raise SettingsError(f"{name.upper()} must be a string")
        object.__setattr__(
            self,
            "livekit_token_ttl_seconds",
            _positive_int(
                self.livekit_token_ttl_seconds,
                field_name="LIVEKIT_TOKEN_TTL_SECONDS",
            ),
        )
        object.__setattr__(
            self,
            "livekit_token_max_ttl_seconds",
            _positive_int(
                self.livekit_token_max_ttl_seconds,
                field_name="LIVEKIT_TOKEN_MAX_TTL_SECONDS",
            ),
        )

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        provider_mode: ProviderMode | str | None = None,
        api_host: str | None = None,
        api_port: int | str | None = None,
    ) -> "BackendSettings":
        """Load settings with optional command-line overrides.

        Fixture is the backwards-compatible local default when no mode is
        specified.  An explicitly selected live mode is never rewritten.
        """

        source = os.environ if env is None else env
        selected_mode = (
            provider_mode
            if provider_mode is not None
            else source.get("PROVIDER_MODE", ProviderMode.FIXTURE.value)
        )
        return cls(
            provider_mode=ProviderMode.parse(selected_mode),
            api_host=api_host if api_host is not None else source.get("API_HOST", "127.0.0.1"),
            api_port=api_port if api_port is not None else source.get("API_PORT", "3001"),
            rembg_port=source.get("REMBG_PORT", "7000"),
            livekit_url=source.get("LIVEKIT_URL", ""),
            livekit_api_key=source.get("LIVEKIT_API_KEY", ""),
            livekit_api_secret=source.get("LIVEKIT_API_SECRET", ""),
            # Keep the token issuer's existing safe-default behavior for a
            # malformed deployment value while centralizing the resolved
            # values for both Python entrypoints.
            livekit_token_ttl_seconds=_positive_int(
                source.get("LIVEKIT_TOKEN_TTL_SECONDS", "90"),
                field_name="LIVEKIT_TOKEN_TTL_SECONDS",
                default=90,
            ),
            livekit_token_max_ttl_seconds=_positive_int(
                source.get("LIVEKIT_TOKEN_MAX_TTL_SECONDS", "300"),
                field_name="LIVEKIT_TOKEN_MAX_TTL_SECONDS",
                default=300,
            ),
        )

    @property
    def livekit_configured(self) -> bool:
        return all(
            (self.livekit_url, self.livekit_api_key, self.livekit_api_secret)
        )

    @property
    def rembg_remove_url(self) -> str:
        """Keep the rembg boundary on loopback while allowing port conflicts."""

        return f"http://127.0.0.1:{self.rembg_port}/api/remove"

    def require_livekit(self) -> None:
        """Fail explicitly when an Agent worker cannot join LiveKit."""

        missing = [
            env_name
            for env_name, value in (
                ("LIVEKIT_URL", self.livekit_url),
                ("LIVEKIT_API_KEY", self.livekit_api_key),
                ("LIVEKIT_API_SECRET", self.livekit_api_secret),
            )
            if not value
        ]
        if missing:
            raise SettingsError(
                "LiveKit Agent settings are incomplete: " + ", ".join(missing)
            )
        if not (
            self.livekit_url.startswith("wss://")
            or self.livekit_url.startswith("https://")
        ):
            raise SettingsError("LIVEKIT_URL must use https or wss")


__all__ = ["BackendSettings", "ProviderMode", "SettingsError"]
