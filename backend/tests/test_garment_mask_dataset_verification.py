"""Backend-only segmentation dataset evaluator tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from io import BytesIO, StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import backend.garment_mask_dataset_verification as evaluator_module
from backend.garment_mask_dataset_verification import (
    MaskDatasetError,
    MaskGateThresholds,
    main,
    run_mask_dataset_verification,
)
from backend.providers.garment_masker import GarmentMask, GarmentMaskInput


def _original(path: Path, size: tuple[int, int] = (8, 8)) -> bytes:
    image = Image.new("RGB", size)
    for y in range(size[1]):
        for x in range(size[0]):
            image.putpixel((x, y), (20 + x * 10, 30 + y * 10, 40 + x + y))
    output = BytesIO()
    image.save(output, format="PNG")
    image.close()
    data = output.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _mask(
    path: Path | None,
    *,
    size: tuple[int, int] = (8, 8),
    box: tuple[int, int, int, int] = (2, 2, 6, 6),
) -> bytes:
    image = Image.new("L", size, 0)
    image.paste(255, box)
    output = BytesIO()
    image.save(output, format="PNG")
    image.close()
    data = output.getvalue()
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return data


def _dataset(
    root: Path,
    count: int = 1,
    *,
    gt_size: tuple[int, int] = (8, 8),
    gt_box: tuple[int, int, int, int] = (2, 2, 6, 6),
) -> tuple[Path, list[bytes]]:
    cases: list[dict[str, object]] = []
    originals: list[bytes] = []
    for index in range(count):
        image_relative = f"images/{index}.png"
        mask_relative = f"masks/{index}.png"
        originals.append(_original(root / image_relative))
        _mask(root / mask_relative, size=gt_size, box=gt_box)
        cases.append(
            {
                "id": f"validation-000000000000000{index}-shirt",
                "image": image_relative,
                "groundTruthMask": mask_relative,
                "source": {"url": "https://private-source.invalid/image"},
                "segmentationAnnotation": {"private": "must-not-leak"},
            }
        )
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "dataset": "private-name-must-not-leak",
                "cases": cases,
            }
        ),
        encoding="utf-8",
    )
    return manifest, originals


@dataclass
class FakeMasker:
    results: list[object]
    requests: list[GarmentMaskInput] = field(default_factory=list)

    async def mask(self, front: GarmentMaskInput) -> object:
        self.requests.append(front)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _garment_mask(
    *,
    size: tuple[int, int] = (8, 8),
    box: tuple[int, int, int, int] = (2, 2, 6, 6),
) -> GarmentMask:
    return GarmentMask(_mask(None, size=size, box=box), *size)


def test_fake_evaluator_reports_quality_preview_errors_and_latency_without_leaks(
    tmp_path: Path,
) -> None:
    manifest, originals = _dataset(tmp_path, count=2)
    masker = FakeMasker(
        [
            _garment_mask(),
            TimeoutError("private rembg address must not leak"),
        ]
    )

    report = asyncio.run(
        run_mask_dataset_verification(
            manifest_path=manifest,
            mode="live",
            masker=masker,
            thresholds=MaskGateThresholds(
                max_provider_error_rate=0.5,
                min_samples=2,
            ),
        )
    )

    assert report["status"] == "passed"
    assert report["counts"] == {
        "samples": 2,
        "providerCalls": 2,
        "providerErrors": 1,
        "evaluationErrors": 0,
        "evaluated": 1,
    }
    assert report["providerErrorRate"] == 0.5
    assert report["errorsByCode"]["TIMEOUT"] == 1
    for metric in ("iou", "dice", "precision", "recall"):
        assert report["quality"][metric]["mean"] == 1.0
        assert report["quality"][metric]["count"] == 1
    assert report["preview"] == {
        "evaluated": 1,
        "transparentAndOpaquePass": 1,
        "originalRgbRetentionPass": 1,
        "alphaMatchesMaskPass": 1,
        "passRate": 1.0,
    }
    assert report["latencyMs"]["provider"]["count"] == 2
    assert report["latencyMs"]["provider"]["p50Ms"] is not None
    assert report["latencyMs"]["provider"]["p95Ms"] is not None
    assert [request.data for request in masker.requests] == originals

    serialized = json.dumps(report, allow_nan=False)
    for forbidden in (
        str(manifest),
        "images/0.png",
        "private-source.invalid",
        "must-not-leak",
        "private rembg address",
    ):
        assert forbidden not in serialized


def test_ground_truth_is_resized_with_nearest_neighbor(tmp_path: Path) -> None:
    manifest, _originals = _dataset(
        tmp_path,
        gt_size=(2, 2),
        gt_box=(0, 0, 1, 1),
    )
    masker = FakeMasker([_garment_mask(box=(0, 0, 4, 4))])

    report = asyncio.run(
        run_mask_dataset_verification(
            manifest_path=manifest,
            masker=masker,
            thresholds=MaskGateThresholds(min_samples=1),
        )
    )

    assert report["quality"]["iou"]["mean"] == 1.0
    assert report["quality"]["dice"]["mean"] == 1.0
    assert report["status"] == "passed"


def test_known_overlap_produces_expected_iou_dice_precision_and_recall(
    tmp_path: Path,
) -> None:
    manifest, _originals = _dataset(
        tmp_path,
        gt_size=(8, 8),
        gt_box=(0, 0, 4, 8),
    )
    masker = FakeMasker([_garment_mask(box=(0, 0, 8, 4))])

    report = asyncio.run(
        run_mask_dataset_verification(
            manifest_path=manifest,
            masker=masker,
            thresholds=MaskGateThresholds(
                min_mean_iou=0.3,
                min_p50_iou=0.3,
                min_min_iou=0.3,
                min_mean_dice=0.5,
                min_mean_precision=0.5,
                min_min_precision=0.5,
                min_mean_recall=0.5,
                min_min_recall=0.5,
                min_samples=1,
            ),
        )
    )

    assert report["quality"]["iou"]["mean"] == 0.333333
    assert report["quality"]["dice"]["mean"] == 0.5
    assert report["quality"]["precision"]["mean"] == 0.5
    assert report["quality"]["recall"]["mean"] == 0.5
    assert report["status"] == "passed"


def test_tail_gates_reject_one_bad_cutout_even_when_means_and_median_pass(
    tmp_path: Path,
) -> None:
    manifest, _originals = _dataset(tmp_path, count=3)
    masker = FakeMasker(
        [
            _garment_mask(),
            _garment_mask(),
            _garment_mask(box=(0, 0, 6, 6)),
        ]
    )

    report = asyncio.run(
        run_mask_dataset_verification(
            manifest_path=manifest,
            masker=masker,
            thresholds=MaskGateThresholds(min_samples=3),
        )
    )

    assert report["quality"]["iou"]["mean"] > 0.7
    assert report["quality"]["iou"]["p50"] == 1.0
    assert report["quality"]["precision"]["mean"] > 0.7
    assert report["evaluation"]["meanIoUPass"] is True
    assert report["evaluation"]["p50IoUPass"] is True
    assert report["evaluation"]["meanPrecisionPass"] is True
    assert report["evaluation"]["minimumIoUPass"] is False
    assert report["evaluation"]["minimumPrecisionPass"] is False
    assert report["status"] == "failed"


def test_live_mode_builds_production_masker_for_configured_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, _originals = _dataset(tmp_path)
    calls: list[dict[str, object]] = []
    response_mask = _mask(None)

    class FakeHttpClient:
        async def post(
            self,
            url: str,
            *,
            files: Mapping[str, object],
            data: Mapping[str, str],
            timeout: float,
        ) -> object:
            calls.append(
                {"url": url, "files": files, "data": data, "timeout": timeout}
            )
            return SimpleNamespace(
                status_code=200,
                headers={"content-type": "image/png"},
                content=response_mask,
            )

    monkeypatch.setattr(
        evaluator_module,
        "HttpxGarmentMaskHttpClient",
        FakeHttpClient,
    )

    report = asyncio.run(
        run_mask_dataset_verification(
            manifest_path=manifest,
            mode="live",
            environ={"REMBG_PORT": "7001"},
            thresholds=MaskGateThresholds(min_samples=1),
        )
    )

    assert report["status"] == "passed"
    assert len(calls) == 1
    assert calls[0]["url"] == "http://127.0.0.1:7001/api/remove"
    assert calls[0]["data"] == {"model": "birefnet-general-lite", "om": "true"}


def test_all_files_are_decoded_before_first_provider_call(tmp_path: Path) -> None:
    manifest, _originals = _dataset(tmp_path, count=2)
    (tmp_path / "masks/1.png").write_bytes(b"not a png")
    masker = FakeMasker([_garment_mask(), _garment_mask()])

    with pytest.raises(MaskDatasetError, match="GROUND_TRUTH_MASK_INVALID"):
        asyncio.run(
            run_mask_dataset_verification(
                manifest_path=manifest,
                masker=masker,
            )
        )

    assert masker.requests == []


def test_preview_failure_is_separate_from_provider_error_and_fails_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, _originals = _dataset(tmp_path)
    masker = FakeMasker([_garment_mask()])

    def fail_preview(*_args: object, **_kwargs: object) -> bytes:
        raise RuntimeError("private preview details must not leak")

    monkeypatch.setattr(
        evaluator_module,
        "_render_transparent_preview",
        fail_preview,
    )

    report = asyncio.run(
        run_mask_dataset_verification(
            manifest_path=manifest,
            masker=masker,
        )
    )

    assert report["counts"]["providerErrors"] == 0
    assert report["counts"]["evaluationErrors"] == 1
    assert report["errorsByCode"]["EVALUATION_ERROR"] == 1
    assert report["evaluation"]["evaluationErrorRatePass"] is False
    assert report["status"] == "failed"
    assert "private preview details" not in json.dumps(report)


def test_manifest_rejects_mask_path_escape(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    _original(dataset / "image.png")
    _mask(tmp_path / "outside.png")
    manifest = dataset / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "cases": [
                    {
                        "id": "escape",
                        "image": "image.png",
                        "groundTruthMask": "../outside.png",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(MaskDatasetError, match="DATASET_PATH_INVALID"):
        asyncio.run(run_mask_dataset_verification(manifest_path=manifest))


def test_fixture_cli_atomically_saves_same_aggregate_json(tmp_path: Path) -> None:
    manifest, _originals = _dataset(tmp_path)
    report_path = tmp_path / "mask-report.json"
    stdout = StringIO()

    exit_code = main(
        [
            "--mode",
            "fixture",
            "--manifest",
            str(manifest),
            "--output",
            str(report_path),
            "--min-samples",
            "1",
        ],
        stdout=stdout,
    )

    assert exit_code == 0
    assert report_path.read_text(encoding="utf-8") == stdout.getvalue()
    report = json.loads(stdout.getvalue())
    assert report["status"] == "passed"
    assert report["quality"]["iou"]["mean"] == 1.0
    assert str(manifest) not in stdout.getvalue()


def test_case_id_suffix_filters_only_after_full_dataset_decode(tmp_path: Path) -> None:
    manifest, originals = _dataset(tmp_path, count=2)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["cases"][0]["id"] += "__centered-ready"
    payload["cases"][1]["id"] += "__other"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    masker = FakeMasker([_garment_mask()])

    report = asyncio.run(
        run_mask_dataset_verification(
            manifest_path=manifest,
            masker=masker,
            case_id_suffix="__centered-ready",
            thresholds=MaskGateThresholds(min_samples=1),
        )
    )

    assert report["status"] == "passed"
    assert report["counts"]["samples"] == 1
    assert report["counts"]["providerCalls"] == 1
    assert len(masker.requests) == 1
    assert masker.requests[0].data == originals[0]
    serialized = json.dumps(report)
    assert "__centered-ready" not in serialized
    assert payload["cases"][0]["id"] not in serialized

    # Even a case excluded from provider evaluation must pass path/decode
    # validation before the suffix selection is applied.
    (tmp_path / "masks/1.png").write_bytes(b"broken unselected mask")
    untouched_masker = FakeMasker([_garment_mask()])
    with pytest.raises(MaskDatasetError, match="GROUND_TRUTH_MASK_INVALID"):
        asyncio.run(
            run_mask_dataset_verification(
                manifest_path=manifest,
                masker=untouched_masker,
                case_id_suffix="__centered-ready",
            )
        )
    assert untouched_masker.requests == []


def test_case_id_suffix_empty_match_is_finite_invalid_cli_result(
    tmp_path: Path,
) -> None:
    manifest, _originals = _dataset(tmp_path)
    masker = FakeMasker([_garment_mask()])
    stdout = StringIO()

    exit_code = main(
        [
            "--manifest",
            str(manifest),
            "--case-id-suffix",
            "__centered-ready",
        ],
        stdout=stdout,
        masker=masker,
    )

    assert exit_code == 1
    report = json.loads(stdout.getvalue())
    assert report["status"] == "invalid"
    assert report["reasonCode"] == "CASE_FILTER_EMPTY"
    assert report["counts"]["samples"] == 0
    assert "__centered-ready" not in stdout.getvalue()
    assert masker.requests == []
