"""FastAPI application assembly for the local Python backend."""

from __future__ import annotations

from fastapi import FastAPI, Response

from .config import BackendSettings
from .livekit_token import livekit_token_router


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
    return app


app = create_app()


__all__ = ["BackendSettings", "app", "create_app"]
