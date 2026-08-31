"""Configured entrypoint for the Python LiveKit camera Agent.

The transport implementation remains in :mod:`backend.live_agent`. This
module adds shared settings and provider selection while preserving the short
imports used by existing integrations.
"""

from __future__ import annotations

import logging
import sys
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from . import live_agent as _live_agent
from .guidance_transport import GuidanceTransportAdapter
from .live_agent import *  # noqa: F401,F403
from .providers.runtime import (
    LiveAnalyzer,
    ProviderInference,
    create_provider_inference,
    create_vision_guidance_provider,
)
from .providers.vision_guidance import VisionGuidanceProvider
from .settings import BackendSettings


LOGGER = logging.getLogger(__name__)
DEFAULT_GUIDANCE_DEADLINE_SECONDS = 0.95

AgentRunner = Callable[[Any], Any]
ServerFactory = Callable[..., Any]
TransportFactory = Callable[[Any, Callable[[], _live_agent.Shot]], GuidanceTransportAdapter]


def build_runtime_provider(
    settings: BackendSettings,
    *,
    live_analyzer: LiveAnalyzer | None = None,
) -> VisionGuidanceProvider:
    """Build the provider selected by the same settings used by the Agent."""

    return create_vision_guidance_provider(
        settings,
        live_analyzer=live_analyzer,
    )


def _build_server(
    server_factory: ServerFactory,
    inference: ProviderInference,
    transport_factory: TransportFactory | None = None,
) -> Any:
    server = server_factory(inference=inference, transport_factory=transport_factory)
    if server is None:
        raise RuntimeError(
            "unable to create the LiveKit Agent server; install the locked dependencies"
        )
    return server


def _guidance_session_id(room: Any) -> str:
    """Use the Room name as the session boundary, with a safe local fallback."""

    for candidate in (
        getattr(room, "name", None),
        getattr(getattr(room, "local_participant", None), "identity", None),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return "agent-session"


def build_transport_factory(
    provider: VisionGuidanceProvider,
    *,
    process_epoch: str | None = None,
    provider_deadline_seconds: float = DEFAULT_GUIDANCE_DEADLINE_SECONDS,
) -> TransportFactory:
    """Bind a Room's local participant to shot-aware provider inference."""

    resolved_process_epoch = (
        uuid.uuid4().hex if process_epoch is None else process_epoch
    )

    def factory(
        room: Any,
        current_shot: Callable[[], _live_agent.Shot],
    ) -> GuidanceTransportAdapter:
        publisher = getattr(room, "local_participant", None)
        if publisher is None:
            raise RuntimeError("LiveKit room has no local participant for guidance")
        session_factory = getattr(provider, "new_session", None)
        session_provider = session_factory() if callable(session_factory) else provider
        return GuidanceTransportAdapter(
            create_provider_inference(session_provider, requested_shot=current_shot),
            publisher,
            session_id=_guidance_session_id(room),
            process_epoch=resolved_process_epoch,
            provider_deadline_seconds=provider_deadline_seconds,
        )

    return factory


def check_agent(
    settings: BackendSettings,
    *,
    live_analyzer: LiveAnalyzer | None = None,
    server_factory: ServerFactory = _live_agent.create_agent_server,
) -> VisionGuidanceProvider:
    """Perform an offline construction check without joining a Room."""

    provider = build_runtime_provider(settings, live_analyzer=live_analyzer)
    inference = create_provider_inference(provider)
    _build_server(server_factory, inference, build_transport_factory(provider))
    LOGGER.info(
        "agent_check_ok provider_mode=%s provider=%s livekit_configured=%s",
        settings.provider_mode.value,
        type(provider).__name__,
        settings.livekit_configured,
    )
    return provider


def run_agent_worker(
    settings: BackendSettings,
    *,
    runner: AgentRunner,
    live_analyzer: LiveAnalyzer | None = None,
    server_factory: ServerFactory = _live_agent.create_agent_server,
) -> None:
    """Start an Agent worker with explicit configuration and injected I/O."""

    settings.require_livekit()
    provider = build_runtime_provider(settings, live_analyzer=live_analyzer)
    inference = create_provider_inference(provider)
    server = _build_server(server_factory, inference, build_transport_factory(provider))
    # Log only finite public diagnostics. Never interpolate settings or any
    # credential value into this line.
    LOGGER.info(
        "agent_worker_starting provider_mode=%s provider=%s livekit_configured=%s",
        settings.provider_mode.value,
        type(provider).__name__,
        settings.livekit_configured,
    )
    runner(server)


@contextmanager
def _worker_argv(arguments: Sequence[str] | None) -> Iterator[None]:
    if arguments is None:
        yield
        return
    previous = sys.argv
    sys.argv = [previous[0], *arguments]
    try:
        yield
    finally:
        sys.argv = previous


def main(
    settings: BackendSettings | None = None,
    *,
    runner: AgentRunner | None = None,
    live_analyzer: LiveAnalyzer | None = None,
    server_factory: ServerFactory = _live_agent.create_agent_server,
    worker_args: Sequence[str] | None = None,
) -> None:
    """Run the worker through the LiveKit Agents CLI.

    ``runner`` and ``server_factory`` are injectable so startup behavior can
    be verified without credentials, sockets, or a LiveKit service.
    """

    resolved_settings = settings or BackendSettings.from_env()
    selected_runner = runner
    if selected_runner is None:
        try:
            from livekit.agents import cli  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError(
                "livekit-agents is required to run the Agent; "
                "install the locked Python dependencies"
            ) from error
        selected_runner = cli.run_app

    with _worker_argv(worker_args):
        run_agent_worker(
            resolved_settings,
            runner=selected_runner,
            live_analyzer=live_analyzer,
            server_factory=server_factory,
        )


__all__ = [
    *[name for name in _live_agent.__all__ if name != "main"],
    "build_runtime_provider",
    "build_transport_factory",
    "DEFAULT_GUIDANCE_DEADLINE_SECONDS",
    "check_agent",
    "main",
    "run_agent_worker",
]


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    logging.basicConfig(level=logging.INFO)
    main()
