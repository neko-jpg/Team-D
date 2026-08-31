"""FastAPI process entrypoint used by the root npm scripts."""

from __future__ import annotations

import logging

import uvicorn

from .config import BackendSettings


def main() -> None:
    """Start the loopback FastAPI server with shared backend settings."""

    settings = BackendSettings.from_env()
    # Uvicorn configures logging only after this entrypoint starts.  Configure
    # the root logger first so the shared provider mode is always visible in
    # startup logs (including the fixture verification process).
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("backend.server").info(
        "backend_starting provider_mode=%s url=http://%s:%s",
        settings.provider_mode,
        settings.api_host,
        settings.api_port,
    )
    uvicorn.run(
        "backend.app:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
    )


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    main()
