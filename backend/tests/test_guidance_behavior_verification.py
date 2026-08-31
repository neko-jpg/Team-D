"""OpenSpec 9.5 backend-only guidance latency and behavior regression."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from io import StringIO
from pathlib import Path

from backend.guidance_behavior_verification import (
    FIXTURE_REQUIRED_P95_MS,
    LIVE_TARGET_P95_MS,
    LIVE_UPPER_P95_MS,
    main,
    run_fixture_verification,
    run_live_verification,
)
from backend.providers.vision_guidance import GuidanceInput


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_fixture_gate_measures_latency_capacity_and_display_dedupe() -> None:
    report = asyncio.run(run_fixture_verification())

    assert report["mode"] == "fixture"
    assert report["status"] == "passed"
    assert report["reasonCode"] is None
    assert report["targets"] == {
        "comparison": "p95 strictly less than threshold",
        "fixtureRequiredP95Ms": FIXTURE_REQUIRED_P95_MS,
        "liveTargetP95Ms": LIVE_TARGET_P95_MS,
        "liveUpperP95Ms": LIVE_UPPER_P95_MS,
    }
    assert report["counts"] == {
        "framesSubmitted": 6,
        "framesProcessed": 5,
        "framesDropped": 1,
        "providerCalls": 5,
        "providerErrors": 0,
        "guidanceDisplayEvents": 3,
        "duplicateDisplayEventsSuppressed": 2,
    }
    assert report["concurrency"] == {"maxInFlight": 1, "maxPendingDepth": 1}
    assert report["behavior"] == {
        "capacityOnePass": True,
        "singleInferencePass": True,
        "sameDecisionNonResendPass": True,
    }
    assert report["evaluation"] == {
        "fixtureRequiredPass": True,
        "liveTargetPass": None,
        "liveUpperPass": None,
    }
    assert report["latencyMs"]["observedToPublish"]["p95Ms"] < 1_000
    assert report["latencyMs"]["provider"]["count"] == 5
    assert report["latencyMs"]["publish"]["count"] == 3

    frames = report["frames"]
    assert [frame["frameId"] for frame in frames] == [1, 3, 4, 5, 6]
    assert [frame["displayEventPublished"] for frame in frames] == [
        True,
        False,
        True,
        False,
        True,
    ]
    assert all(frame["providerFailed"] is False for frame in frames)


def test_fixture_report_is_identical_across_consecutive_runs() -> None:
    first = asyncio.run(run_fixture_verification())
    second = asyncio.run(run_fixture_verification())

    assert second == first


def test_fixture_gate_fails_when_observed_to_publish_p95_reaches_one_second() -> None:
    report = asyncio.run(
        run_fixture_verification(
            provider_latencies_ms=(1_000, 10, 10, 1_000, 10, 1_000)
        )
    )

    assert report["latencyMs"]["observedToPublish"]["p95Ms"] >= 1_000
    assert report["evaluation"]["fixtureRequiredPass"] is False
    assert report["status"] == "failed"


def test_live_mode_without_credentials_returns_same_finite_skipped_shape() -> None:
    report = asyncio.run(run_live_verification(environ={}, samples=2))

    assert report["mode"] == "live"
    assert report["status"] == "skipped"
    assert report["reasonCode"] == "LIVE_CREDENTIALS_UNAVAILABLE"
    assert report["counts"]["providerCalls"] == 0
    assert report["latencyMs"]["provider"]["p50Ms"] is None
    assert report["latencyMs"]["observedToPublish"]["p95Ms"] is None
    assert report["evaluation"] == {
        "fixtureRequiredPass": None,
        "liveTargetPass": None,
        "liveUpperPass": None,
    }
    json.dumps(report, allow_nan=False)


def test_injected_live_analyzer_reports_p50_p95_and_target_evaluation() -> None:
    calls: list[str] = []

    async def analyzer(input: GuidanceInput) -> object:
        calls.append(input.requested_shot.value)
        await asyncio.sleep(0)
        return {"code": "READY", "confidence": 1.0}

    report = asyncio.run(
        run_live_verification(
            environ={"OPENAI_API_KEY": "test-secret-that-must-not-be-reported"},
            samples=4,
            live_analyzer=analyzer,
        )
    )

    assert calls == ["front", "back", "tag", "measurement"]
    assert report["mode"] == "live"
    assert report["status"] == "completed"
    assert report["counts"]["providerCalls"] == 4
    assert report["counts"]["guidanceDisplayEvents"] == 4
    assert report["latencyMs"]["provider"]["p50Ms"] is not None
    assert report["latencyMs"]["provider"]["p95Ms"] is not None
    assert report["latencyMs"]["observedToPublish"]["p50Ms"] is not None
    assert report["latencyMs"]["observedToPublish"]["p95Ms"] is not None
    assert report["evaluation"] == {
        "fixtureRequiredPass": None,
        "liveTargetPass": True,
        "liveUpperPass": True,
    }
    for frame in report["frames"]:
        assert frame["observedAt"] <= frame["providerStartedAtMs"]
        assert frame["providerStartedAtMs"] <= frame["providerCompletedAtMs"]
        assert frame["providerCompletedAtMs"] <= frame["backendPublishedAtMs"]
    serialized = json.dumps(report, allow_nan=False)
    assert "test-secret" not in serialized
    assert "base64" not in serialized
    assert "image/png" not in serialized


def test_fixture_cli_is_one_command_json_gate() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "backend.guidance_behavior_verification",
            "--mode",
            "fixture",
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload["status"] == "passed"
    assert payload["evaluation"]["fixtureRequiredPass"] is True


def test_live_cli_skip_does_not_echo_unrelated_environment_values() -> None:
    output = StringIO()
    exit_code = main(
        ["--mode", "live", "--live-samples", "2"],
        environ={"UNRELATED_SECRET": "must-not-appear"},
        stdout=output,
    )

    assert exit_code == 0
    assert "must-not-appear" not in output.getvalue()
    assert json.loads(output.getvalue())["status"] == "skipped"
