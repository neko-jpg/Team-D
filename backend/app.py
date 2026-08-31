"""FastAPI application assembly for the local Python backend."""

from __future__ import annotations

from fastapi import FastAPI, Response

from .analyze_shot import analyze_shot_router, get_shot_assessor
from .livekit_token import LiveKitConfig, get_livekit_config, livekit_token_router
from .providers.garment_masker import GarmentMasker, HttpxGarmentMaskHttpClient
from .providers.measurement_line_factory import create_measurement_line_provider
from .providers.shot_assessor_factory import create_shot_assessor
from .remove_background import get_garment_masker, remove_background_router
from .settings import BackendSettings
from .suggest_measurement_points import (
    get_measurement_line_provider,
    suggest_measurement_points_router,
)


def create_app(settings: BackendSettings | None = None) -> FastAPI:
    """Build the API while preserving the legacy health contract."""

    resolved_settings = settings or BackendSettings.from_env()
    garment_masker = GarmentMasker(HttpxGarmentMaskHttpClient())
    measurement_line_provider = create_measurement_line_provider(resolved_settings)
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
    app.include_router(analyze_shot_router)
    app.include_router(suggest_measurement_points_router)
    app.include_router(remove_background_router)
    # The endpoint remains provider-agnostic.  This application-level wiring
    # uses the same immutable settings instance as the token endpoint and
    # preserves provider exceptions instead of falling back implicitly.
    app.dependency_overrides[get_shot_assessor] = lambda: create_shot_assessor(
        resolved_settings
    )
    app.dependency_overrides[get_measurement_line_provider] = (
        lambda: measurement_line_provider
    )
    app.dependency_overrides[get_garment_masker] = lambda: garment_masker
    return app


app = create_app()


__all__ = ["BackendSettings", "app", "create_app"]
