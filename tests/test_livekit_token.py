"""Offline contract tests for the LiveKit token endpoint.

The Python backend is introduced by the LiveKit work and did not exist in the
initial Node scaffold.  These tests intentionally keep the integration
boundary small: an ASGI application is loaded from the conventional
``backend.app``/``backend.main`` modules and exposes either ``create_app()`` or
an ``app`` object.  The resolver also accepts a small set of equivalent module
names so moving the Python package does not change the security contract.

No LiveKit service, network, PyJWT, or browser is required.  JWTs are decoded
and their HS256 signatures are checked locally with the fake secret configured
by the tests.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any

import pytest

import backend.livekit_token as livekit_token_module


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

# The architecture leaves the Python package layout to the implementation.
# ``LIVEKIT_TOKEN_TEST_APP_MODULE`` is useful for a deployment-specific layout
# while these names cover the clean backend package layouts used in this repo.
APP_MODULE_CANDIDATES = (
    "backend.app",
    "backend.main",
    "server.app",
    "server.main",
)
APP_FACTORY_CANDIDATES = ("create_app", "build_app", "get_app")


def _urlsafe_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode((segment + padding).encode("ascii"))


def decode_and_verify_jwt(token: str, secret: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Decode an HS256 JWT without contacting a token or LiveKit service."""

    parts = token.split(".")
    assert len(parts) == 3, "access token must be a compact JWT"

    encoded_header, encoded_payload, encoded_signature = parts
    header = json.loads(_urlsafe_decode(encoded_header))
    payload = json.loads(_urlsafe_decode(encoded_payload))

    assert header.get("alg") == "HS256"
    assert header.get("typ", "JWT") == "JWT"

    signed = f"{encoded_header}.{encoded_payload}".encode("ascii")
    expected_signature = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).digest()
    assert hmac.compare_digest(_urlsafe_decode(encoded_signature), expected_signature)
    return header, payload


def _import_backend_module() -> Any:
    requested_module = os.getenv("LIVEKIT_TOKEN_TEST_APP_MODULE")
    candidates = (requested_module,) if requested_module else APP_MODULE_CANDIDATES
    import_errors: list[str] = []

    for module_name in candidates:
        if not module_name:
            continue
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError as error:
            import_errors.append(f"{module_name}: {error}")

    pytest.fail(
        "The Python LiveKit backend is not importable. Expected an ASGI app in "
        "backend.app/backend.main (override with LIVEKIT_TOKEN_TEST_APP_MODULE). "
        f"Import errors: {'; '.join(import_errors)}"
    )


def load_backend_app() -> Any:
    """Load the backend's ASGI app through the small intended clean interface."""

    module = _import_backend_module()
    for factory_name in APP_FACTORY_CANDIDATES:
        factory = getattr(module, factory_name, None)
        if callable(factory):
            return factory()

    app = getattr(module, "app", None)
    if callable(app):
        return app

    pytest.fail(
        f"{module.__name__} must expose create_app()/build_app()/get_app() or an ASGI app"
    )


async def _asgi_json_request(
    app: Any,
    *,
    method: str,
    path: str,
    body: bytes = b"",
) -> tuple[int, dict[str, str], bytes]:
    """Call an ASGI app in-process; this deliberately opens no network socket."""

    sent_messages: list[dict[str, Any]] = []
    request_delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal request_delivered
        if not request_delivered:
            request_delivered = True
            return {
                "type": "http.request",
                "body": body,
                "more_body": False,
            }
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
        "client": ("127.0.0.1", 0),
        "server": ("testserver", 80),
    }

    await app(scope, receive, send)

    start = next(message for message in sent_messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in sent_messages
        if message["type"] == "http.response.body"
    )
    headers = {
        key.decode("latin-1"): value.decode("latin-1")
        for key, value in start.get("headers", [])
    }
    return int(start["status"]), headers, response_body


def post_token(app: Any, session_id: str) -> tuple[int, dict[str, str], dict[str, Any], bytes]:
    body = json.dumps({"sessionId": session_id}).encode("utf-8")
    status, headers, raw_body = asyncio.run(
        _asgi_json_request(
            app,
            method="POST",
            path="/api/livekit-token",
            body=body,
        )
    )
    try:
        decoded_body = json.loads(raw_body)
    except json.JSONDecodeError as error:
        raise AssertionError(f"token endpoint returned non-JSON body: {raw_body!r}") from error
    assert isinstance(decoded_body, dict)
    return status, headers, decoded_body, raw_body


def get_json(app: Any, path: str) -> tuple[int, dict[str, str], dict[str, Any]]:
    status, headers, raw_body = asyncio.run(
        _asgi_json_request(app, method="GET", path=path)
    )
    decoded_body = json.loads(raw_body)
    assert isinstance(decoded_body, dict)
    return status, headers, decoded_body


def _response_field(response: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in response:
            return response[name]
    joined_names = ", ".join(names)
    raise AssertionError(f"response is missing one of: {joined_names}")


def _video_grants(payload: dict[str, Any]) -> dict[str, Any]:
    grants = payload.get("video")
    if not isinstance(grants, dict):
        grants = payload.get("grants")
    assert isinstance(grants, dict), "JWT must contain LiveKit video grants"
    return grants


def _configure_fake_credentials(monkeypatch: pytest.MonkeyPatch, *, ttl: int = 90) -> str:
    # Keep the value out of source and make accidental response/bundle leakage
    # unambiguous.  These are server-only names; Vite must not expose them.
    secret = "offline-only-livekit-secret-" + hashlib.sha256(os.urandom(32)).hexdigest()
    monkeypatch.setenv("LIVEKIT_API_KEY", "offline-api-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", secret)
    monkeypatch.setenv("LIVEKIT_URL", "wss://offline.livekit.invalid")
    monkeypatch.setenv("LIVEKIT_TOKEN_TTL_SECONDS", str(ttl))
    return secret


@pytest.fixture
def token_app(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, str]:
    secret = _configure_fake_credentials(monkeypatch)
    return load_backend_app(), secret


def test_python_health_endpoint_preserves_existing_contract(
    token_app: tuple[Any, str],
) -> None:
    app, _ = token_app
    status, headers, response = get_json(app, "/api/health")

    assert status == 200
    assert "application/json" in headers.get("content-type", "")
    assert response == {"status": "ok"}


def token_parts(response: dict[str, Any]) -> tuple[str, str, str, str]:
    identity = str(
        _response_field(
            response, "identity", "participantIdentity", "participant_identity"
        )
    )
    room = str(_response_field(response, "room", "roomName", "room_name"))
    token = str(_response_field(response, "token", "accessToken", "access_token"))
    url = str(_response_field(response, "url", "livekitUrl", "livekit_url"))
    return identity, room, token, url


def test_token_claims_decode_offline_and_match_response(token_app: tuple[Any, str]) -> None:
    app, secret = token_app
    status, headers, response, _ = post_token(app, "session-claim-decode")

    assert status == 200
    assert "application/json" in headers.get("content-type", "")
    identity, room, token, url = token_parts(response)
    header, payload = decode_and_verify_jwt(token, secret)
    grants = _video_grants(payload)

    assert header["alg"] == "HS256"
    assert payload["sub"] == identity
    assert grants["room"] == room
    assert int(response["expiresAt"]) == int(payload["exp"])
    assert url == "wss://offline.livekit.invalid"


def test_ttl_is_short_configurable_and_capped(
    token_app: tuple[Any, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, secret = token_app
    _, _, first_response, _ = post_token(app, "session-ttl-configured")
    _, _, first_token, _ = token_parts(first_response)
    _, first_payload = decode_and_verify_jwt(first_token, secret)

    now = int(time.time())
    first_exp = int(first_payload["exp"])
    first_iat = int(first_payload.get("iat", now))
    assert 0 < first_exp - now <= 90
    assert 0 < first_exp - first_iat <= 90

    # A user-controlled environment value must not turn the browser token into
    # a long-lived credential.  The contract's short upper bound is 5 minutes.
    monkeypatch.setenv("LIVEKIT_TOKEN_TTL_SECONDS", "86400")
    capped_app = load_backend_app()
    _, _, capped_response, _ = post_token(capped_app, "session-ttl-capped")
    _, _, capped_token, _ = token_parts(capped_response)
    _, capped_payload = decode_and_verify_jwt(capped_token, secret)
    capped_now = int(time.time())
    capped_iat = int(capped_payload.get("iat", capped_now))
    assert 0 < int(capped_payload["exp"]) - capped_now <= 300
    assert 0 < int(capped_payload["exp"]) - capped_iat <= 300

    monkeypatch.setenv("LIVEKIT_TOKEN_TTL_SECONDS", "180")
    monkeypatch.setenv("LIVEKIT_TOKEN_MAX_TTL_SECONDS", "60")
    configured_cap_app = load_backend_app()
    _, _, configured_cap_response, _ = post_token(
        configured_cap_app, "session-ttl-configured-cap"
    )
    _, _, configured_cap_token, _ = token_parts(configured_cap_response)
    _, configured_cap_payload = decode_and_verify_jwt(configured_cap_token, secret)
    configured_cap_iat = int(
        configured_cap_payload.get("iat", configured_cap_payload["nbf"])
    )
    assert int(configured_cap_payload["exp"]) - configured_cap_iat == 60


def test_installed_livekit_api_uses_compatible_official_sdk_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("livekit.api", reason="livekit-api is not installed")
    secret = _configure_fake_credentials(monkeypatch)
    config = livekit_token_module.LiveKitConfig.from_env()
    token, expires_at = livekit_token_module.mint_livekit_token(
        config=config,
        identity="browser-sdk-compatibility",
        room="listing-photo-session-sdk-compatibility",
    )
    _, payload = decode_and_verify_jwt(token, secret)

    assert payload["sub"] == "browser-sdk-compatibility"
    assert payload["exp"] == expires_at
    assert _video_grants(payload)["canPublishSources"] == ["camera"]


def test_room_is_derived_from_session_and_identity_is_unique(token_app: tuple[Any, str]) -> None:
    app, secret = token_app
    first_status, _, first_response, _ = post_token(app, "session-room-a")
    second_status, _, second_response, _ = post_token(app, "session-room-a")
    other_status, _, other_response, _ = post_token(app, "session-room-b")

    assert (first_status, second_status, other_status) == (200, 200, 200)
    first_identity, first_room, first_token, _ = token_parts(first_response)
    second_identity, second_room, second_token, _ = token_parts(second_response)
    other_identity, other_room, other_token, _ = token_parts(other_response)

    assert first_room == second_room
    assert first_room != other_room
    assert len({first_identity, second_identity, other_identity}) == 3

    for identity, room, token in (
        (first_identity, first_room, first_token),
        (second_identity, second_room, second_token),
        (other_identity, other_room, other_token),
    ):
        _, payload = decode_and_verify_jwt(token, secret)
        assert payload["sub"] == identity
        assert _video_grants(payload)["room"] == room


def test_token_allows_camera_and_required_data_but_denies_other_capabilities(
    token_app: tuple[Any, str],
) -> None:
    app, secret = token_app
    status, _, response, _ = post_token(app, "session-camera-only")
    assert status == 200
    _, _, token, _ = token_parts(response)
    _, payload = decode_and_verify_jwt(token, secret)
    grants = _video_grants(payload)

    assert grants.get("roomJoin") is True
    assert grants.get("canPublish") is True
    assert grants.get("canPublishData") is True
    assert grants.get("canSubscribe") is False

    publish_sources = grants.get("canPublishSources")
    assert isinstance(publish_sources, list)
    assert set(publish_sources) == {"camera"}
    assert not set(publish_sources) & {
        "microphone",
        "audio",
        "screen",
        "screen_share",
        "screen_share_audio",
    }


def test_token_response_never_contains_livekit_api_secret(token_app: tuple[Any, str]) -> None:
    app, secret = token_app
    status, _, response, raw_body = post_token(app, "session-secret-boundary")
    assert status == 200
    serialized_response = raw_body.decode("utf-8")
    assert secret not in serialized_response
    assert secret not in json.dumps(response, sort_keys=True)

    _, _, token, _ = token_parts(response)
    _, payload = decode_and_verify_jwt(token, secret)
    assert payload.get("iss") == "offline-api-key"
    assert secret not in token
    assert secret not in json.dumps(payload, sort_keys=True)


def _client_bundle_files() -> list[Path]:
    roots = (
        REPOSITORY_ROOT / "dist",
        REPOSITORY_ROOT / "app" / "dist",
        REPOSITORY_ROOT / "build",
    )
    suffixes = {".js", ".mjs", ".cjs", ".html", ".css", ".map"}
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(
                path
                for path in root.rglob("*")
                if path.is_file() and path.suffix in suffixes
            )
    return files


def test_server_only_livekit_secret_is_not_referenced_by_client_source() -> None:
    client_roots = (REPOSITORY_ROOT / "app" / "src", REPOSITORY_ROOT / "src")
    client_files = [
        path
        for root in client_roots
        if root.is_dir()
        for path in root.rglob("*")
        if path.is_file()
    ]
    client_files.extend(
        path
        for path in (
            REPOSITORY_ROOT / "app" / "vite.config.ts",
            REPOSITORY_ROOT / "vite.config.ts",
        )
        if path.is_file()
    )

    forbidden_names = ("LIVEKIT_API_SECRET", "VITE_LIVEKIT_API_SECRET")
    for client_file in client_files:
        contents = client_file.read_text(encoding="utf-8", errors="replace")
        assert not any(name in contents for name in forbidden_names), (
            f"server-only LiveKit secret referenced by client source {client_file}"
        )

    env_example = (REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "LIVEKIT_API_SECRET=" in env_example
    assert "VITE_LIVEKIT_API_SECRET" not in env_example


def test_livekit_api_secret_is_absent_from_browser_bundle_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = _configure_fake_credentials(monkeypatch)
    # Also set the Vite-prefixed name to catch accidental client exposure when
    # a developer aliases a server-only credential into import.meta.env.
    monkeypatch.setenv("VITE_LIVEKIT_API_SECRET", secret)

    npm = shutil.which("npm")
    vite_binary = REPOSITORY_ROOT / "node_modules" / ".bin" / "vite"
    if npm and vite_binary.exists():
        result = subprocess.run(
            [npm, "run", "build:web"],
            cwd=REPOSITORY_ROOT,
            env=os.environ.copy(),
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr[-4000:]

    artifacts = _client_bundle_files()
    if not artifacts:
        pytest.skip("browser bundle is not built; run npm install and npm run build:web")

    for artifact in artifacts:
        contents = artifact.read_text(encoding="utf-8", errors="replace")
        assert secret not in contents, f"server secret leaked into client artifact {artifact}"
