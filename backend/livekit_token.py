"""Short-lived, least-privilege LiveKit access tokens.

The browser only receives the signed access token and public connection
metadata.  LiveKit credentials stay in the Python process and are never part
of an API response.  The router in this module is intentionally standalone so
the application entrypoint can include it without coupling token issuance to
the Agent lifecycle.
"""

from __future__ import annotations

import base64
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictStr


# A 90-second default and five-minute absolute ceiling keep browser tokens
# short-lived while allowing a deployment to choose a smaller TTL.  The
# ceiling is intentionally a code constant: an environment variable cannot
# accidentally turn a short-lived credential into a long-lived one.
DEFAULT_TOKEN_TTL_SECONDS: Final[int] = 90
HARD_MAX_TOKEN_TTL_SECONDS: Final[int] = 300
DEFAULT_CONFIGURED_MAX_TTL_SECONDS: Final[int] = HARD_MAX_TOKEN_TTL_SECONDS

_SESSION_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$"
)
_ROOM_PREFIX: Final[str] = "listing-photo-session-"


class LiveKitConfigurationError(RuntimeError):
    """Raised when the server cannot safely issue a LiveKit token."""


class LiveKitTokenRequest(BaseModel):
    """Request body accepted by ``POST /api/livekit-token``.

    ``sessionId`` is the public contract used by the browser.  Extra fields
    are rejected so callers cannot influence room names or token grants.
    """

    sessionId: StrictStr = Field(min_length=1, max_length=96)

    model_config = ConfigDict(extra="forbid")


class LiveKitTokenResponse(BaseModel):
    """Public token response; no API key or secret is included."""

    token: str
    participantIdentity: str
    roomName: str
    expiresAt: int
    livekitUrl: str


@dataclass(frozen=True, slots=True)
class LiveKitConfig:
    """Validated settings used by the token issuer.

    ``token_ttl_seconds`` may be configured for a deployment, but
    :meth:`effective_ttl_seconds` always applies both the deployment bound and
    the hard code bound.
    """

    api_key: str
    api_secret: str
    url: str
    token_ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS
    max_token_ttl_seconds: int = DEFAULT_CONFIGURED_MAX_TTL_SECONDS

    @classmethod
    def from_env(cls) -> "LiveKitConfig":
        """Load credentials and TTL settings from process environment.

        Missing credentials are rejected only when the route is called.  This
        keeps importing the package and serving ``/api/health`` compatible
        with local fixture mode, while still preventing a token from being
        issued without all required secrets.
        """

        return cls(
            api_key=os.environ.get("LIVEKIT_API_KEY", ""),
            api_secret=os.environ.get("LIVEKIT_API_SECRET", ""),
            url=os.environ.get("LIVEKIT_URL", ""),
            token_ttl_seconds=_read_positive_int_env(
                "LIVEKIT_TOKEN_TTL_SECONDS", DEFAULT_TOKEN_TTL_SECONDS
            ),
            max_token_ttl_seconds=_read_positive_int_env(
                "LIVEKIT_TOKEN_MAX_TTL_SECONDS",
                DEFAULT_CONFIGURED_MAX_TTL_SECONDS,
            ),
        )

    def effective_ttl_seconds(self) -> int:
        """Return a positive TTL constrained by both configured bounds."""

        # ``max`` also protects directly constructed config objects from a
        # zero/negative value.  The environment parser rejects those values;
        # the fallback keeps this value safe if a caller constructs settings
        # directly in a fixture.
        configured_max = max(1, self.max_token_ttl_seconds)
        requested = max(1, self.token_ttl_seconds)
        return min(requested, configured_max, HARD_MAX_TOKEN_TTL_SECONDS)

    def validate_for_issuance(self) -> None:
        """Ensure all values needed to mint a token are present and safe."""

        if not self.api_key or not self.api_secret or not self.url:
            raise LiveKitConfigurationError("LiveKit credentials are not configured")
        if not _is_supported_livekit_url(self.url):
            raise LiveKitConfigurationError("LIVEKIT_URL must use https or wss")


def _read_positive_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value)
    except ValueError:
        # Treat malformed configuration as the safe default.  Most importantly
        # this can never expand the token lifetime.
        return default
    return value if value > 0 else default


def _is_supported_livekit_url(value: str) -> bool:
    return value.startswith("wss://") or value.startswith("https://")


def _validated_session_id(value: str) -> str:
    # Do not silently trim a caller-controlled identifier: accepting a value
    # different from what was supplied could result in an unexpected Room.
    if not _SESSION_ID_PATTERN.fullmatch(value):
        raise ValueError("sessionId contains unsupported characters")
    return value


def room_name_for_session(session_id: str) -> str:
    """Derive one stable, bounded Room name from a generated session ID."""

    validated = _validated_session_id(session_id)
    return f"{_ROOM_PREFIX}{validated}"


def _new_participant_identity() -> str:
    # UUID4 gives every token request a distinct browser participant even when
    # a user retries within the same session/Room.
    return f"browser-{uuid.uuid4().hex}"


def _camera_track_source(_livekit_api: Any) -> str:
    """Return the access-token wire value expected by ``VideoGrants``.

    ``livekit-api`` also exports a protobuf ``TrackSource`` enum for service
    APIs. Passing its integer CAMERA value into ``VideoGrants`` serializes the
    JWT as ``[1]`` instead of the required ``["camera"]`` claim.
    """

    return "camera"


def _sdk_token_has_required_claims(
    token: str,
    *,
    identity: str,
    room: str,
    ttl_seconds: int,
) -> int | None:
    """Check SDK output before exposing it to the browser.

    The API SDK has changed its enum serialization across releases.  Refuse
    output that does not carry the least-privilege wire claims we require and
    use the local signer instead.  This also prevents a dependency upgrade
    from accidentally dropping an explicit deny or widening publish sources.
    """

    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_bytes = base64.urlsafe_b64decode(
            parts[1] + ("=" * (-len(parts[1]) % 4))
        )
        payload = json.loads(payload_bytes)
        grants = payload.get("video")
        expires_at = payload.get("exp")
        not_before = payload.get("nbf", payload.get("iat"))
        valid = (
            isinstance(payload, dict)
            and payload.get("sub") == identity
            and isinstance(expires_at, int)
            and isinstance(not_before, int)
            and 0 < expires_at - not_before <= ttl_seconds
            and isinstance(grants, dict)
            and grants.get("roomJoin") is True
            and grants.get("room") == room
            and grants.get("canPublish") is True
            and grants.get("canSubscribe") is False
            and grants.get("canPublishData") is True
            and grants.get("canPublishSources") == ["camera"]
        )
        return expires_at if valid else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _sdk_hs256_token(
    *,
    config: LiveKitConfig,
    identity: str,
    room: str,
    ttl_seconds: int,
) -> tuple[str, int]:
    """Generate and validate a token with the installed official SDK."""

    try:
        from livekit import api as livekit_api
    except ImportError:
        raise LiveKitConfigurationError("livekit-api is not installed") from None

    try:
        grants = livekit_api.VideoGrants(
            room_join=True,
            room=room,
            can_publish=True,
            can_subscribe=False,
            can_publish_data=True,
            can_publish_sources=[_camera_track_source(livekit_api)],
        )
        access_token = (
            livekit_api.AccessToken(config.api_key, config.api_secret)
            .with_identity(identity)
            .with_ttl(timedelta(seconds=ttl_seconds))
            .with_grants(grants)
        )
        token = access_token.to_jwt()
        expires_at = _sdk_token_has_required_claims(
            token,
            identity=identity,
            room=room,
            ttl_seconds=ttl_seconds,
        )
        if expires_at is None:
            raise LiveKitConfigurationError(
                "livekit-api produced incompatible token claims"
            )
        return token, expires_at
    except LiveKitConfigurationError:
        raise
    except Exception:
        # Do not expose or log SDK exception text because it can contain
        # deployment-specific values. A dependency/API mismatch must fail
        # closed instead of silently minting a broader custom token.
        raise LiveKitConfigurationError("livekit-api token generation failed") from None


def mint_livekit_token(
    *,
    config: LiveKitConfig,
    identity: str,
    room: str,
) -> tuple[str, int]:
    """Create a short-lived token and return ``(token, expires_at)``."""

    config.validate_for_issuance()
    effective_ttl = config.effective_ttl_seconds()
    token, expires_at = _sdk_hs256_token(
        config=config,
        identity=identity,
        room=room,
        ttl_seconds=effective_ttl,
    )
    return token, expires_at


def get_livekit_config() -> LiveKitConfig:
    """FastAPI dependency for request-scoped environment configuration."""

    return LiveKitConfig.from_env()


def issue_livekit_token(
    payload: LiveKitTokenRequest,
    config: LiveKitConfig = Depends(get_livekit_config),
) -> LiveKitTokenResponse:
    """Issue one browser token for the session in ``payload``."""

    try:
        room = room_name_for_session(payload.sessionId)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid_session_id") from None

    identity = _new_participant_identity()
    try:
        token, expires_at = mint_livekit_token(
            config=config,
            identity=identity,
            room=room,
        )
    except LiveKitConfigurationError:
        # Never return SDK/configuration exception details: they may contain
        # deployment-specific values and are not useful to the browser.
        raise HTTPException(status_code=503, detail="livekit_unavailable") from None

    return LiveKitTokenResponse(
        token=token,
        participantIdentity=identity,
        roomName=room,
        expiresAt=expires_at,
        livekitUrl=config.url,
    )


livekit_token_router = APIRouter()
livekit_token_router.add_api_route(
    "/api/livekit-token",
    issue_livekit_token,
    methods=["POST"],
    response_model=LiveKitTokenResponse,
    response_model_by_alias=True,
)


__all__ = [
    "DEFAULT_TOKEN_TTL_SECONDS",
    "HARD_MAX_TOKEN_TTL_SECONDS",
    "LiveKitConfig",
    "LiveKitConfigurationError",
    "LiveKitTokenRequest",
    "LiveKitTokenResponse",
    "get_livekit_config",
    "issue_livekit_token",
    "livekit_token_router",
    "mint_livekit_token",
    "room_name_for_session",
]
