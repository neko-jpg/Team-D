"""Allow backend processes to be launched from the repository root."""

from .cli import main


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    raise SystemExit(main())
