"""Offline tests for the aggregate-only garment geometry quality gate."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

import backend.geometry_guidance_verification as geometry_verification
from backend.geometry_guidance_verification import (
    EXPECTED_CODES,
    GeometryVerificationDatasetError,
    _create_live_analyzer,
    run_geometry_guidance_verification,
)
from backend.providers.vision_guidance import (
    GuidanceCode,
    GuidanceInput,
    VisionDecision,
)


def _image_bytes(color: tuple[int, int, int] = (31, 47, 59)) -> bytes:
    output = BytesIO()
    with Image.new("RGB", (32, 24), color) as image:
        image.save(output, format="PNG")
    return output.getvalue()


def _manifest_cases(
    *,
    image: str = "frame.png",
    repeats: int = 5,
) -> list[dict[str, object]]:
    return [
        {
            "id": f"case-{code.value.lower()}-{index}",
            "image": image,
            "shot": "front" if index % 2 == 0 else "back",
            "expectedCode": code.value,
            "mustNotReturn": ["READY"],
            "scope": "geometry_transformed",
            "reviewStatus": "deterministic_transform",
            "source": {"url": "https://private.invalid/source"},
        }
        for code in EXPECTED_CODES
        for index in range(repeats)
    ]


def _write_dataset(
    root: Path,
    *,
    cases: list[dict[str, object]] | None = None,
    schema_version: object = 1,
) -> tuple[Path, bytes]:
    root.mkdir(parents=True, exist_ok=True)
    image = _image_bytes()
    (root / "frame.png").write_bytes(image)
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": schema_version,
                "dataset": {"secret": "do-not-report"},
                "cases": _manifest_cases() if cases is None else cases,
            }
        ),
        encoding="utf-8",
    )
    return manifest, image


class SequenceAnalyzer:
    def __init__(self, results: list[object], expected_bytes: bytes) -> None:
        self.results = list(results)
        self.expected_bytes = expected_bytes
        self.inputs: list[GuidanceInput] = []
        self.prewarm_calls = 0
        self.close_calls = 0

    async def prewarm(self) -> None:
        self.prewarm_calls += 1

    async def analyze_geometry(self, input_value: GuidanceInput) -> object:
        assert input_value.frame.data == self.expected_bytes
        assert input_value.frame.mime_type == "image/png"
        assert (input_value.frame.width, input_value.frame.height) == (32, 24)
        self.inputs.append(input_value)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def aclose(self) -> None:
        self.close_calls += 1


def _correct_results(cases: list[Mapping[str, object]]) -> list[VisionDecision]:
    return [
        VisionDecision(GuidanceCode(case["expectedCode"]), 1.0)
        for case in cases
    ]


def test_gate_passes_only_exact_four_code_dataset_and_uses_real_bytes(
    tmp_path: Path,
) -> None:
    cases = _manifest_cases()
    manifest, image = _write_dataset(tmp_path, cases=cases)
    analyzer = SequenceAnalyzer(_correct_results(cases), image)

    report = asyncio.run(
        run_geometry_guidance_verification(
            manifest_path=manifest,
            analyzer=analyzer,
        )
    )

    assert report["status"] == "passed"
    assert report["reasonCode"] == "ALL_GATES_PASSED"
    assert report["counts"] == {
        "samples": 20,
        "providerCalls": 20,
        "providerErrors": 0,
        "predictions": 20,
        "correct": 20,
        "falseReady": 0,
    }
    assert report["accuracy"] == {"exact": 1.0}
    assert report["thresholds"]["maximumProviderP95Ms"] == 400.0
    assert report["evaluation"] == {
        "minimumSamplesPass": True,
        "minimumSamplesPerCodePass": True,
        "perCodeExactPass": True,
        "zeroProviderErrorsPass": True,
        "exactAccuracyPass": True,
        "falseReadyPass": True,
        "providerP95Pass": True,
        "allPass": True,
    }
    assert report["latencyMs"]["provider"]["count"] == 20
    for code in EXPECTED_CODES:
        value = code.value
        assert report["perCode"][value] == {
            "samples": 5,
            "correct": 5,
            "exactAccuracy": 1.0,
        }
        assert report["confusion"][value][value] == 5
    assert analyzer.prewarm_calls == 1
    assert analyzer.close_calls == 1
    assert len(analyzer.inputs) == 20

    serialized = json.dumps(report)
    assert str(tmp_path) not in serialized
    assert "private.invalid" not in serialized
    assert "do-not-report" not in serialized
    assert "case-center_garment" not in serialized


def test_errors_and_false_ready_fail_without_leaking_exception_details(
    tmp_path: Path,
) -> None:
    cases = _manifest_cases()
    manifest, image = _write_dataset(tmp_path, cases=cases)
    results: list[object] = _correct_results(cases)
    results[0] = RuntimeError("private upstream body /secret/path")
    results[5] = VisionDecision(GuidanceCode.READY, 1.0)
    analyzer = SequenceAnalyzer(results, image)

    report = asyncio.run(
        run_geometry_guidance_verification(
            manifest_path=manifest,
            analyzer=analyzer,
        )
    )

    assert report["status"] == "failed"
    assert report["counts"]["providerErrors"] == 1
    assert report["counts"]["falseReady"] == 1
    assert report["errorsByCode"] == {
        "TIMEOUT": 0,
        "CONTRACT_ERROR": 0,
        "PROVIDER_ERROR": 1,
    }
    assert report["confusion"]["CENTER_GARMENT"]["PROVIDER_ERROR"] == 1
    assert report["confusion"]["MOVE_CLOSER"]["READY"] == 1
    assert report["evaluation"]["zeroProviderErrorsPass"] is False
    assert report["evaluation"]["falseReadyPass"] is False
    serialized = json.dumps(report)
    assert "private upstream" not in serialized
    assert "/secret/path" not in serialized


def test_pass_result_is_a_wrong_prediction_but_not_a_provider_error(
    tmp_path: Path,
) -> None:
    cases = _manifest_cases()
    manifest, image = _write_dataset(tmp_path, cases=cases)
    results: list[object] = _correct_results(cases)
    results[10] = None

    report = asyncio.run(
        run_geometry_guidance_verification(
            manifest_path=manifest,
            analyzer=SequenceAnalyzer(results, image),
        )
    )

    assert report["status"] == "failed"
    assert report["counts"]["providerErrors"] == 0
    assert report["predictedCounts"]["PASS"] == 1
    assert report["confusion"]["MOVE_FARTHER"]["PASS"] == 1
    assert report["accuracy"]["exact"] == 0.95


def test_provider_p95_gate_is_strictly_less_than_400ms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = _manifest_cases()
    manifest, image = _write_dataset(tmp_path, cases=cases)
    analyzer = SequenceAnalyzer(_correct_results(cases), image)
    # Include prewarm timing first, then 18 fast image calls and two exactly at
    # the boundary. Decimal keeps the mocked elapsed values exact.
    timestamps = iter(
        [Decimal("0"), Decimal("0.001")]
        + [
            timestamp
            for index, duration in enumerate(
                [Decimal("0.001")] * 18 + [Decimal("0.400")] * 2,
                start=1,
            )
            for timestamp in (Decimal(index), Decimal(index) + duration)
        ]
    )
    monkeypatch.setattr(
        geometry_verification.time,
        "perf_counter",
        lambda: next(timestamps),
    )

    report = asyncio.run(
        run_geometry_guidance_verification(
            manifest_path=manifest,
            analyzer=analyzer,
        )
    )

    assert report["latencyMs"]["provider"]["p95Ms"] == 400.0
    assert report["evaluation"]["providerP95Pass"] is False
    assert report["status"] == "failed"


@pytest.mark.parametrize(
    ("mutate", "reason_code"),
    [
        (
            lambda cases: cases[:19],
            "INSUFFICIENT_SAMPLES",
        ),
        (
            lambda cases: [
                case
                for case in cases
                if not (
                    case["expectedCode"] == "MOVE_FARTHER"
                    and str(case["id"]).endswith("-4")
                )
            ]
            + [
                {
                    **cases[0],
                    "id": "extra-center-case",
                }
            ],
            "INSUFFICIENT_SAMPLES_PER_CODE",
        ),
        (
            lambda cases: [
                {**cases[0], "expectedCode": "READY"},
                *cases[1:],
            ],
            "READY_EXPECTATION_FORBIDDEN",
        ),
        (
            lambda cases: [
                {**cases[0], "image": "../outside.png"},
                *cases[1:],
            ],
            "IMAGE_PATH_INVALID",
        ),
    ],
)
def test_manifest_gate_rejects_invalid_or_unbalanced_datasets(
    tmp_path: Path,
    mutate: object,
    reason_code: str,
) -> None:
    cases = mutate(_manifest_cases())  # type: ignore[operator]
    manifest, image = _write_dataset(tmp_path, cases=cases)
    analyzer = SequenceAnalyzer([], image)

    with pytest.raises(GeometryVerificationDatasetError) as captured:
        asyncio.run(
            run_geometry_guidance_verification(
                manifest_path=manifest,
                analyzer=analyzer,
            )
        )

    assert captured.value.reason_code == reason_code
    assert analyzer.inputs == []


@pytest.mark.parametrize("schema_version", [True, 2, "1", None])
def test_manifest_requires_integer_schema_version_one(
    tmp_path: Path, schema_version: object
) -> None:
    manifest, image = _write_dataset(tmp_path, schema_version=schema_version)
    with pytest.raises(
        GeometryVerificationDatasetError, match="MANIFEST_SCHEMA_UNSUPPORTED"
    ):
        asyncio.run(
            run_geometry_guidance_verification(
                manifest_path=manifest,
                analyzer=SequenceAnalyzer([], image),
            )
        )


def test_all_images_are_decoded_before_first_analyzer_call(tmp_path: Path) -> None:
    cases = _manifest_cases()
    cases[-1] = {**cases[-1], "image": "corrupt.png"}
    manifest, image = _write_dataset(tmp_path, cases=cases)
    (tmp_path / "corrupt.png").write_bytes(b"not an image")
    analyzer = SequenceAnalyzer(_correct_results(cases), image)

    with pytest.raises(GeometryVerificationDatasetError, match="IMAGE_INVALID"):
        asyncio.run(
            run_geometry_guidance_verification(
                manifest_path=manifest,
                analyzer=analyzer,
            )
        )

    assert analyzer.inputs == []
    assert analyzer.prewarm_calls == 0


def test_live_factory_uses_backend_settings_rembg_remove_url() -> None:
    analyzer = _create_live_analyzer({"REMBG_PORT": "7312"})
    assert analyzer.remove_url == "http://127.0.0.1:7312/api/remove"
