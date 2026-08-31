"""Compatibility entrypoint for the Python LiveKit camera agent.

The implementation lives in :mod:`backend.live_agent`; this module keeps the
short ``backend.agent`` import path available to the worker command and to
offline integration tests.
"""

from .live_agent import *  # noqa: F401,F403
from .live_agent import __all__


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    main()
