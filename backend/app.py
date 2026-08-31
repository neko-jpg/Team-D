"""FastAPI application assembly for the local Python backend."""

from __future__ import annotations

from fastapi import FastAPI, Response

from .livekit_token import LiveKitConfig, get_livekit_config, livekit_token_router
from .settings import BackendSettings


def create_app(settings: BackendSettings | None = None) -> FastAPI:
    """Build an app with the legacy health contract and token route."""

    resolved_settings = settings or BackendSettings.from_env()
    app = FastAPI(title="Team-D listing photo assistant")
    app.state.settings = resolved_settings
    # Resolve the token issuer from the same immutable settings object used by
    # this process. This prevents request-time environment rereads from
    # diverging from the Agent's selected mode/configuration.
    app.dependency_overrides[get_livekit_config] = lambda: LiveKitConfig(
        api_key=resolved_settings.livekit_api_key,
        api_secret=resolved_settings.livekit_api_secret,
        url=resolved_settings.livekit_url,
        token_ttl_seconds=resolved_settings.livekit_token_ttl_seconds,
        max_token_ttl_seconds=resolved_settings.livekit_token_max_ttl_seconds,
    )

    @app.get("/api/health")
    async def health(response: Response) -> dict[str, str]:
        # Keep the existing Node API response stable while the Python backend
        # is introduced.
        response.headers["Cache-Control"] = "no-store"
        return {"status": "ok"}

    app.include_router(livekit_token_router)
    return app


app = create_app()


__all__ = ["app", "create_app"]
