"""Root command line interface for the FastAPI server and LiveKit Agent."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TextIO

from .settings import BackendSettings, ProviderMode, SettingsError


AppFactory = Callable[[BackendSettings], Any]
ApiRunner = Callable[..., Any]
AgentRunner = Callable[[Any], Any]
ServerFactory = Callable[..., Any]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m backend")
    commands = parser.add_subparsers(dest="command", required=True)

    api_parser = commands.add_parser("api", help="run the FastAPI server")
    api_parser.add_argument(
        "--provider-mode",
        choices=tuple(mode.value for mode in ProviderMode),
    )
    api_parser.add_argument("--host")
    api_parser.add_argument("--port", type=int)
    api_parser.add_argument(
        "--check",
        action="store_true",
        help="validate construction without opening a socket",
    )

    agent_parser = commands.add_parser("agent", help="run the LiveKit Agent worker")
    agent_parser.add_argument(
        "--provider-mode",
        choices=tuple(mode.value for mode in ProviderMode),
    )
    agent_parser.add_argument(
        "--worker-command",
        choices=("dev", "start", "console"),
        default="dev",
    )
    agent_parser.add_argument(
        "--check",
        action="store_true",
        help="validate construction without joining a Room",
    )
    return parser


def _app_factory(factory: AppFactory | None) -> AppFactory:
    if factory is not None:
        return factory
    from .app import create_app

    return create_app


def _run_api(
    settings: BackendSettings,
    *,
    app_factory: AppFactory | None,
    runner: ApiRunner | None,
) -> None:
    application = _app_factory(app_factory)(settings)
    selected_runner = runner
    if selected_runner is None:
        import uvicorn

        selected_runner = uvicorn.run
    selected_runner(
        application,
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    app_factory: AppFactory | None = None,
    api_runner: ApiRunner | None = None,
    agent_runner: AgentRunner | None = None,
    agent_server_factory: ServerFactory | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Run one configured entrypoint, with injectable process boundaries."""

    parser = build_parser()
    args = parser.parse_args(argv)
    output = stdout or sys.stdout
    try:
        settings = BackendSettings.from_env(
            env,
            provider_mode=args.provider_mode,
            api_host=getattr(args, "host", None),
            api_port=getattr(args, "port", None),
        )
    except SettingsError as error:
        parser.error(str(error))

    if args.command == "api":
        if args.check:
            application = _app_factory(app_factory)(settings)
            if application is None:
                raise RuntimeError("FastAPI application factory returned no app")
            print(
                f"api check ok provider_mode={settings.provider_mode.value}",
                file=output,
            )
            return 0
        _run_api(settings, app_factory=app_factory, runner=api_runner)
        return 0

    from . import agent

    selected_server_factory = agent_server_factory or agent.create_agent_server
    if args.check:
        provider = agent.check_agent(
            settings,
            server_factory=selected_server_factory,
        )
        print(
            "agent check ok "
            f"provider_mode={settings.provider_mode.value} "
            f"provider={type(provider).__name__} "
            f"livekit_configured={str(settings.livekit_configured).lower()}",
            file=output,
        )
        return 0

    logging.basicConfig(level=logging.INFO)
    agent.main(
        settings,
        runner=agent_runner,
        server_factory=selected_server_factory,
        worker_args=(args.worker_command,),
    )
    return 0


__all__ = ["build_parser", "main"]
