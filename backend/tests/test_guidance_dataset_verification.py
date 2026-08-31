"""Aggregate-only real-image dataset verification contract."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from io import StringIO
from pathlib import Path

import pytest
from PIL import Image

from backend.guidance_dataset_verification import (
    DatasetManifestError,
    GateThresholds,
    main,
    run_dataset_verification,
)
from backend.providers.vision_guidance import GuidanceInput
from backend.providers.vision_guidance_realtime import RealtimeGuidanceTimeoutError


def _image(path: Path, color: str = "navy") -> None:
    Image.new("RGB", (160, 120), color).save(path, "JPEG")


def _manifest(
    directory: Path,
    cases: Sequence[dict[str, object]],
    *,
    source_marker: str = "private-source-must-not-leak",
) -> Path:
    normalized_cases: list[dict[str, object]] = []
    for index, case in enumerate(cases):
        normalized = dict(case)
        normalized.setdefault("reviewStatus", "human_reviewed")
        normalized.setdefault("scope", "human_reviewed")
        normalized.setdefault(
            "mustNotReturn",
            [] if normalized.get("expectedCode") == "READY" else ["READY"],
        )
        normalized_cases.append(normalized)
        image_path = directory / str(case["image"])
        image_path.parent.mkdir(parents=True, exist_ok=True)
        _image(image_path, color=("navy" if index % 2 == 0 else "white"))
    manifest = directory / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "dataset": "local-private-dataset",
                "generatedAt": "2026-09-01T00:00:00Z",
                "sourceMarker": source_marker,
                "cases": normalized_cases,
            }
        ),
        encoding="utf-8",
    )
    return manifest


class FakeSessionAnalyzer:
    def __init__(self, results: Sequence[object]) -> None:
        self.results = list(results)
        self.connect_count = 0
        self.request_count = 0
        self.prewarm_calls = 0
        self.close_calls = 0
        self.shots: list[str] = []

    async def prewarm(self) -> None:
        self.prewarm_calls += 1
        self.connect_count = 1
        self.request_count += 1

    async def __call__(self, input_value: GuidanceInput) -> object:
        self.request_count += 1
        self.shots.append(input_value.requested_shot.value)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return {"code": result, "confidence": 1.0}

    async def aclose(self) -> None:
        self.close_calls += 1


def test_injected_analyzer_reports_accuracy_confusion_errors_and_session_counts(
    tmp_path: Path,
) -> None:
    cases = [
        {
            "id": "center-correct",
            "image": "images/center.jpg",
            "shot": "front",
            "expectedCode": "CENTER_GARMENT",
            "source": {"url": "https://private.invalid/one"},
            "derivation": {"kind": "bbox"},
        },
        {
            "id": "critical-false-ready",
            "image": "images/closer.jpg",
            "shot": "front",
            "expectedCode": "MOVE_CLOSER",
        },
        {
            "id": "ready-correct",
            "image": "images/ready.jpg",
            "shot": "back",
            "expectedCode": "READY",
        },
        {
            "id": "provider-timeout",
            "image": "images/farther.jpg",
            "shot": "front",
            "expectedCode": "MOVE_FARTHER",
        },
    ]
    manifest = _manifest(tmp_path, cases)
    analyzer = FakeSessionAnalyzer(
        [
            "CENTER_GARMENT",
            "READY",
            "READY",
            RealtimeGuidanceTimeoutError("private-provider-message"),
        ]
    )

    report = asyncio.run(
        run_dataset_verification(
            manifest_path=manifest,
            images_dir=tmp_path,
            mode="live",
            analyzer=analyzer,
            thresholds=GateThresholds(
                min_exact_accuracy=0.5,
                max_provider_error_rate=0.25,
                max_false_ready_rate=0.34,
                max_forbidden_code_rate=0.34,
                max_provider_p95_ms=1_000,
                min_samples=4,
                min_non_ready_samples=3,
                max_connect_count=1,
            ),
            environ={"OPENAI_API_KEY": "credential-must-not-leak"},
        )
    )

    assert report["status"] == "passed"
    assert report["counts"] == {
        "samples": 4,
        "providerCalls": 4,
        "providerErrors": 1,
        "predictions": 3,
        "correct": 2,
    }
    assert report["accuracy"] == {
        "exact": 0.5,
        "providerErrorRate": 0.25,
        "falseReadyRate": 0.333333,
        "forbiddenCodeRate": 0.333333,
    }
    assert report["criticalFalseReady"] == {
        "count": 1,
        "eligibleSamples": 3,
    }
    assert report["forbiddenCode"] == {
        "count": 1,
        "eligibleSamples": 3,
    }
    assert report["confusion"]["MOVE_CLOSER"]["READY"] == 1
    assert report["confusion"]["MOVE_FARTHER"]["PROVIDER_ERROR"] == 1
    assert report["perCode"]["CENTER_GARMENT"]["recall"] == 1.0
    assert report["providerErrorsByCode"]["TIMEOUT"] == 1
    assert report["latencyMs"]["provider"]["count"] == 4
    assert report["latencyMs"]["provider"]["p50Ms"] is not None
    assert report["latencyMs"]["provider"]["p95Ms"] is not None
    assert report["realtimeSession"] == {
        "connectCount": 1,
        "requestCount": 5,
        "singleConnectionPass": True,
    }
    assert analyzer.shots == ["front", "front", "back", "front"]
    assert analyzer.prewarm_calls == 1
    assert analyzer.close_calls == 1

    serialized = json.dumps(report, allow_nan=False)
    for forbidden in (
        "images/center.jpg",
        "private-source-must-not-leak",
        "private.invalid",
        "private-provider-message",
        "credential-must-not-leak",
    ):
        assert forbidden not in serialized


def test_false_ready_is_an_independent_default_gate(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        [
            {
                "id": "false-ready-1",
                "image": "garment.jpg",
                "shot": "front",
                "expectedCode": "CENTER_GARMENT",
            }
        ],
    )
    analyzer = FakeSessionAnalyzer(["READY"])

    report = asyncio.run(
        run_dataset_verification(
            manifest_path=manifest,
            mode="live",
            analyzer=analyzer,
            thresholds=GateThresholds(
                min_exact_accuracy=0.0,
                min_samples=1,
                min_non_ready_samples=1,
            ),
        )
    )

    assert report["evaluation"]["exactAccuracyPass"] is True
    assert report["evaluation"]["falseReadyRatePass"] is False
    assert report["evaluation"]["forbiddenCodeRatePass"] is False
    assert report["evaluation"]["allPass"] is False
    assert report["status"] == "failed"


def test_non_ready_forbidden_code_is_an_independent_gate(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        [
            {
                "id": "bad-show-full",
                "image": "garment.jpg",
                "shot": "front",
                "expectedCode": "CENTER_GARMENT",
                "mustNotReturn": ["READY", "SHOW_FULL_GARMENT"],
            }
        ],
    )
    analyzer = FakeSessionAnalyzer(["SHOW_FULL_GARMENT"])

    report = asyncio.run(
        run_dataset_verification(
            manifest_path=manifest,
            mode="live",
            analyzer=analyzer,
            thresholds=GateThresholds(
                min_exact_accuracy=0.0,
                min_samples=1,
                min_non_ready_samples=1,
            ),
        )
    )

    assert report["accuracy"]["falseReadyRate"] == 0.0
    assert report["accuracy"]["forbiddenCodeRate"] == 1.0
    assert report["evaluation"]["falseReadyRatePass"] is True
    assert report["evaluation"]["forbiddenCodeRatePass"] is False
    assert report["status"] == "failed"


def test_fixture_cli_saves_the_same_safe_json_atomically(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        [
            {
                "id": "fixture-ready",
                "image": "fixture.jpg",
                "shot": "front",
                "expectedCode": "READY",
            }
        ],
    )
    output_path = tmp_path / "report.json"
    stdout = StringIO()

    exit_code = main(
        [
            "--mode",
            "fixture",
            "--manifest",
            str(manifest),
            "--images-dir",
            str(tmp_path),
            "--output",
            str(output_path),
            "--min-samples",
            "1",
            "--min-non-ready-samples",
            "1",
            "--max-false-ready-rate",
            "1",
            "--min-exact-accuracy",
            "0",
        ],
        environ={"OPENAI_API_KEY": "must-not-be-used-or-reported"},
        stdout=stdout,
    )

    assert exit_code == 1
    assert output_path.read_text(encoding="utf-8") == stdout.getvalue()
    report = json.loads(stdout.getvalue())
    assert report["mode"] == "fixture"
    assert report["status"] == "failed"
    assert report["realtimeSession"]["connectCount"] is None
    assert str(manifest) not in stdout.getvalue()
    assert "must-not-be-used-or-reported" not in stdout.getvalue()


def test_live_without_credentials_returns_finite_skipped_report(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        [
            {
                "id": "no-credential",
                "image": "fixture.jpg",
                "shot": "front",
                "expectedCode": "READY",
            }
        ],
    )

    report = asyncio.run(
        run_dataset_verification(
            manifest_path=manifest,
            mode="live",
            environ={},
        )
    )

    assert report["status"] == "skipped"
    assert report["reasonCode"] == "LIVE_CREDENTIALS_UNAVAILABLE"
    assert report["counts"]["providerCalls"] == 0
    json.dumps(report, allow_nan=False)


def test_manifest_rejects_path_escape_before_calling_provider(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    _image(tmp_path / "outside.jpg")
    manifest = dataset / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "cases": [
                    {
                        "id": "escape",
                        "image": "../outside.jpg",
                        "shot": "front",
                        "expectedCode": "READY",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    analyzer = FakeSessionAnalyzer(["READY"])

    with pytest.raises(DatasetManifestError, match="IMAGE_PATH_INVALID"):
        asyncio.run(
            run_dataset_verification(
                manifest_path=manifest,
                images_dir=dataset,
                analyzer=analyzer,
            )
        )

    assert analyzer.request_count == 0


def test_manifest_rejects_unsafe_case_id(tmp_path: Path) -> None:
    manifest = _manifest(
        tmp_path,
        [
            {
                "id": "images/private.jpg",
                "image": "private.jpg",
                "shot": "front",
                "expectedCode": "READY",
            }
        ],
    )

    with pytest.raises(DatasetManifestError, match="MANIFEST_CASE_INVALID"):
        asyncio.run(run_dataset_verification(manifest_path=manifest))
