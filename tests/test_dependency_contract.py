"""Locked LiveKit dependency and notice contract for OpenSpec task 3.2."""

from __future__ import annotations

from importlib import metadata
import json
from pathlib import Path
import sys
import tomllib


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

PYTHON_LIVEKIT_VERSIONS = {
    "livekit": "1.1.15",
    "livekit-agents": "1.7.1",
    "livekit-api": "1.2.1",
}
LIVEKIT_CLIENT_VERSION = "2.22.1"


def test_python_311_and_livekit_distributions_match_the_lock() -> None:
    assert sys.version_info[:2] == (3, 11)

    for distribution, expected_version in PYTHON_LIVEKIT_VERSIONS.items():
        assert metadata.version(distribution) == expected_version

    import livekit.agents  # noqa: F401
    from livekit import api, rtc

    assert api is not None
    assert rtc is not None


def test_pyproject_and_uv_lock_pin_the_same_livekit_versions() -> None:
    pyproject = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text())
    direct_dependencies = set(pyproject["project"]["dependencies"])
    for distribution, expected_version in PYTHON_LIVEKIT_VERSIONS.items():
        assert f"{distribution}=={expected_version}" in direct_dependencies

    lock = tomllib.loads((REPOSITORY_ROOT / "uv.lock").read_text())
    locked_versions = {
        package["name"]: package["version"] for package in lock["package"]
    }
    for distribution, expected_version in PYTHON_LIVEKIT_VERSIONS.items():
        assert locked_versions[distribution] == expected_version


def test_npm_lock_pins_the_livekit_client_version() -> None:
    package = json.loads((REPOSITORY_ROOT / "package.json").read_text())
    package_lock = json.loads((REPOSITORY_ROOT / "package-lock.json").read_text())

    assert package["dependencies"]["livekit-client"] == LIVEKIT_CLIENT_VERSION
    assert (
        package_lock["packages"][""]["dependencies"]["livekit-client"]
        == LIVEKIT_CLIENT_VERSION
    )
    assert (
        package_lock["packages"]["node_modules/livekit-client"]["version"]
        == LIVEKIT_CLIENT_VERSION
    )


def test_third_party_notices_match_every_direct_livekit_sdk() -> None:
    notices = (REPOSITORY_ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    expected_fragments = (
        "## livekit-client (LiveKit JS SDK)",
        "Source: https://github.com/livekit/client-sdk-js",
        "Version: `2.22.1`",
        "tag `v2.22.1`",
        "4e7a3a017d607be9656258a34551f9614cc5b980",
        "## livekit-agents (LiveKit Agents, Python)",
        "Source: https://github.com/livekit/agents",
        "Version: `1.7.1`",
        "`livekit-agents@1.7.1`",
        "6e3af311381d11c5d6c065567e98d35bb54b85a9",
        "## livekit (LiveKit Python SDK, core RTC)",
        "Source: https://github.com/livekit/python-sdks",
        "Version: `1.1.15`",
        "tag `rtc-v1.1.15`",
        "3bad29d5957a988ebc53df16efd669f9f8a3c98c",
        "## livekit-api (LiveKit Server API SDK, Python)",
        "Version: `1.2.1`",
        "tag `api-v1.2.1`",
        "cb18f19d12e2c25d545893d755cf56f40110f771",
        "Copyright 2021 LiveKit, Inc.",
        "Copyright 2023 LiveKit, Inc.",
        "Apache License 2.0",
    )
    for fragment in expected_fragments:
        assert fragment in notices
