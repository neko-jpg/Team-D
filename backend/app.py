"""FastAPI application assembly for the local Python backend."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Response

from .analyze_shot import analyze_shot_router, get_shot_assessor
from .livekit_token import LiveKitConfig, get_livekit_config, livekit_token_router
from .providers.garment_masker import GarmentMasker, HttpxGarmentMaskHttpClient
from .providers.measurement_line_factory import create_measurement_line_provider
from .providers.shot_assessor_factory import create_shot_assessor
from .remove_background import get_garment_masker, remove_background_router
from .settings import BackendSettings, ProviderMode
from .suggest_measurement_points import (
    get_measurement_line_provider,
    suggest_measurement_points_router,
)


def _create_live_openai_client(settings: BackendSettings) -> Any | None:
    """Create one app-owned client shared by live Responses providers."""

    if settings.provider_mode is not ProviderMode.LIVE:
        return None
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        return None
    try:
        from openai import AsyncOpenAI

        return AsyncOpenAI()
    except Exception:
        return None


def create_app(settings: BackendSettings | None = None) -> FastAPI:
    """Build the API while preserving the legacy health contract."""

    resolved_settings = settings or BackendSettings.from_env()
    openai_client = _create_live_openai_client(resolved_settings)
    responses_client = (
        None if openai_client is None else openai_client.responses
    )
    garment_masker = GarmentMasker(
        HttpxGarmentMaskHttpClient(),
        remove_url=resolved_settings.rembg_remove_url,
    )
    measurement_line_provider = create_measurement_line_provider(
        resolved_settings,
        live_client=responses_client,
    )
    shot_assessor = create_shot_assessor(
        resolved_settings,
        live_client=responses_client,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if openai_client is not None:
                await openai_client.close()

    app = FastAPI(title="Team-D listing photo assistant", lifespan=lifespan)
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
    app.dependency_overrides[get_shot_assessor] = lambda: shot_assessor
    app.dependency_overrides[get_measurement_line_provider] = (
        lambda: measurement_line_provider
    )
    app.dependency_overrides[get_garment_masker] = lambda: garment_masker
    return app


app = create_app()


__all__ = ["BackendSettings", "app", "create_app"]
