"""FastAPI application assembly for the local Python backend."""

from __future__ import annotations

from fastapi import FastAPI, Response

from .analyze_shot import analyze_shot_router, get_shot_assessor
from .config import BackendSettings
from .livekit_token import livekit_token_router
from .providers.shot_assessor_factory import get_configured_shot_assessor


def create_app() -> FastAPI:
    """Build an app with the legacy health contract and token route."""

    app = FastAPI(title="Team-D listing photo assistant")

    @app.get("/api/health")
    async def health(response: Response) -> dict[str, str]:
        # Keep the existing Node API response stable while the Python backend
        # is introduced.
        response.headers["cache-control"] = "no-store"
        return {"status": "ok"}

    app.include_router(livekit_token_router)
    app.include_router(analyze_shot_router)
    # The endpoint remains provider-agnostic.  This application-level wiring
    # makes fixture/live selection explicit and preserves provider exceptions.
    app.dependency_overrides[get_shot_assessor] = get_configured_shot_assessor
    return app


app = create_app()


__all__ = ["BackendSettings", "app", "create_app"]
