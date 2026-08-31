"""Backend-only segmentation quality gate for the production rembg boundary.

The evaluator accepts the Open Images manifest shape produced by
``scripts/evaluation/open_images_garment_subset.py`` when segmentation is
enabled.  Every case must contain ``image`` and ``groundTruthMask`` paths.
All source images and ground-truth masks are decoded before the first provider
call, preventing a broken dataset from causing a partial live sidecar run.

The JSON result is aggregate-only: paths, image bytes, source URLs, manifest
metadata, and provider exception messages are never included.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import math
import os
import re
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import TextIO

from PIL import Image, ImageChops, ImageOps

from .providers.garment_masker import (
    GarmentMask,
    GarmentMaskContractError,
    GarmentMaskInput,
    GarmentMaskProviderError,
    GarmentMasker,
    HttpxGarmentMaskHttpClient,
    validate_garment_mask_png,
)
from .remove_background import _render_transparent_preview
from .settings import BackendSettings, SettingsError


DEFAULT_MIN_MEAN_IOU = 0.70
DEFAULT_MIN_P50_IOU = 0.85
DEFAULT_MIN_MIN_IOU = 0.90
DEFAULT_MIN_MEAN_DICE = 0.80
DEFAULT_MIN_MEAN_PRECISION = 0.70
DEFAULT_MIN_MIN_PRECISION = 0.90
DEFAULT_MIN_MEAN_RECALL = 0.70
DEFAULT_MIN_MIN_RECALL = 0.90
DEFAULT_MIN_PREVIEW_PASS_RATE = 1.0
DEFAULT_MAX_PROVIDER_ERROR_RATE = 0.0
DEFAULT_MAX_EVALUATION_ERROR_RATE = 0.0
DEFAULT_MAX_PROVIDER_P95_MS = 35_000.0
DEFAULT_MIN_SAMPLES = 16
DEFAULT_BINARY_THRESHOLD = 128

_IMAGE_FORMAT_TO_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
_ERROR_CODES = (
    "TIMEOUT",
    "CONTRACT_ERROR",
    "PROVIDER_ERROR",
    "EVALUATION_ERROR",
)


class MaskDatasetError(ValueError):
    """A finite, non-sensitive dataset validation failure."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class MaskDatasetCase:
    case_id: str
    image_path: Path
    ground_truth_mask_path: Path


@dataclass(frozen=True, slots=True)
class PreparedMaskCase:
    case_id: str
    original: bytes
    original_mime_type: str
    original_size: tuple[int, int]
    ground_truth_mask: bytes


@dataclass(frozen=True, slots=True)
class MaskGateThresholds:
    min_mean_iou: float = DEFAULT_MIN_MEAN_IOU
    min_p50_iou: float = DEFAULT_MIN_P50_IOU
    min_min_iou: float = DEFAULT_MIN_MIN_IOU
    min_mean_dice: float = DEFAULT_MIN_MEAN_DICE
    min_mean_precision: float = DEFAULT_MIN_MEAN_PRECISION
    min_min_precision: float = DEFAULT_MIN_MIN_PRECISION
    min_mean_recall: float = DEFAULT_MIN_MEAN_RECALL
    min_min_recall: float = DEFAULT_MIN_MIN_RECALL
    min_preview_pass_rate: float = DEFAULT_MIN_PREVIEW_PASS_RATE
    max_provider_error_rate: float = DEFAULT_MAX_PROVIDER_ERROR_RATE
    max_evaluation_error_rate: float = DEFAULT_MAX_EVALUATION_ERROR_RATE
    max_provider_p95_ms: float = DEFAULT_MAX_PROVIDER_P95_MS
    min_samples: int = DEFAULT_MIN_SAMPLES
    binary_threshold: int = DEFAULT_BINARY_THRESHOLD

    def __post_init__(self) -> None:
        for field_name in (
            "min_mean_iou",
            "min_p50_iou",
            "min_min_iou",
            "min_mean_dice",
            "min_mean_precision",
            "min_min_precision",
            "min_mean_recall",
            "min_min_recall",
            "min_preview_pass_rate",
            "max_provider_error_rate",
            "max_evaluation_error_rate",
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise ValueError(f"{field_name} must be between 0 and 1")
        if (
            isinstance(self.max_provider_p95_ms, bool)
            or not isinstance(self.max_provider_p95_ms, (int, float))
            or not math.isfinite(float(self.max_provider_p95_ms))
            or float(self.max_provider_p95_ms) <= 0
        ):
            raise ValueError("max_provider_p95_ms must be positive")
        if (
            isinstance(self.min_samples, bool)
            or not isinstance(self.min_samples, int)
            or self.min_samples < 1
        ):
            raise ValueError("min_samples must be a positive integer")
        if (
            isinstance(self.binary_threshold, bool)
            or not isinstance(self.binary_threshold, int)
            or not 1 <= self.binary_threshold <= 255
        ):
            raise ValueError("binary_threshold must be between 1 and 255")

    def to_payload(self) -> dict[str, object]:
        return {
            "minMeanIoU": float(self.min_mean_iou),
            "minP50IoU": float(self.min_p50_iou),
            "minMinimumIoU": float(self.min_min_iou),
            "minMeanDice": float(self.min_mean_dice),
            "minMeanPrecision": float(self.min_mean_precision),
            "minMinimumPrecision": float(self.min_min_precision),
            "minMeanRecall": float(self.min_mean_recall),
            "minMinimumRecall": float(self.min_min_recall),
            "minPreviewPassRate": float(self.min_preview_pass_rate),
            "maxProviderErrorRate": float(self.max_provider_error_rate),
            "maxEvaluationErrorRate": float(self.max_evaluation_error_rate),
            "maxProviderP95Ms": float(self.max_provider_p95_ms),
            "minSamples": self.min_samples,
            "binaryThreshold": self.binary_threshold,
            "latencyComparison": "p95 strictly less than threshold",
        }


def _safe_resolved_file(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise MaskDatasetError("MANIFEST_CASE_INVALID")
    relative = Path(value)
    if relative.is_absolute():
        raise MaskDatasetError("DATASET_PATH_INVALID")
    try:
        resolved = (root / relative).resolve(strict=True)
    except OSError as error:
        raise MaskDatasetError("DATASET_FILE_UNAVAILABLE") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise MaskDatasetError("DATASET_PATH_INVALID")
    return resolved


def _load_manifest(manifest_path: Path, data_dir: Path) -> list[MaskDatasetCase]:
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MaskDatasetError("MANIFEST_UNREADABLE") from error
    schema_version = raw.get("schemaVersion") if isinstance(raw, Mapping) else None
    if (
        not isinstance(raw, Mapping)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        raise MaskDatasetError("MANIFEST_SCHEMA_UNSUPPORTED")
    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise MaskDatasetError("MANIFEST_CASES_INVALID")
    try:
        root = data_dir.resolve(strict=True)
    except OSError as error:
        raise MaskDatasetError("DATASET_DIRECTORY_UNAVAILABLE") from error
    if not root.is_dir():
        raise MaskDatasetError("DATASET_DIRECTORY_UNAVAILABLE")

    seen_ids: set[str] = set()
    cases: list[MaskDatasetCase] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, Mapping):
            raise MaskDatasetError("MANIFEST_CASE_INVALID")
        case_id = raw_case.get("id")
        if (
            not isinstance(case_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", case_id) is None
            or case_id in seen_ids
        ):
            raise MaskDatasetError("MANIFEST_CASE_INVALID")
        seen_ids.add(case_id)
        cases.append(
            MaskDatasetCase(
                case_id=case_id,
                image_path=_safe_resolved_file(root, raw_case.get("image")),
                ground_truth_mask_path=_safe_resolved_file(
                    root, raw_case.get("groundTruthMask")
                ),
            )
        )
    return cases


def _decode_original(path: Path) -> tuple[bytes, str, tuple[int, int]]:
    try:
        data = path.read_bytes()
        with Image.open(BytesIO(data)) as image:
            image_format = image.format
            image.verify()
        with Image.open(BytesIO(data)) as image:
            oriented = ImageOps.exif_transpose(image)
            try:
                oriented.load()
                size = oriented.size
            finally:
                if oriented is not image:
                    oriented.close()
    except (OSError, ValueError, SyntaxError) as error:
        raise MaskDatasetError("ORIGINAL_IMAGE_INVALID") from error
    mime_type = _IMAGE_FORMAT_TO_MIME.get(image_format or "")
    if mime_type is None or size[0] <= 0 or size[1] <= 0:
        raise MaskDatasetError("ORIGINAL_IMAGE_FORMAT_UNSUPPORTED")
    return data, mime_type, size


def _decode_ground_truth(
    path: Path,
    original_size: tuple[int, int],
    threshold: int,
) -> bytes:
    try:
        data = path.read_bytes()
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG":
                raise MaskDatasetError("GROUND_TRUTH_MASK_FORMAT_UNSUPPORTED")
            image.verify()
        with Image.open(BytesIO(data)) as image:
            image.load()
            if image.width <= 0 or image.height <= 0:
                raise MaskDatasetError("GROUND_TRUTH_MASK_INVALID")
            if "A" in image.getbands():
                grayscale = image.getchannel("A")
            elif "transparency" in image.info:
                rgba = image.convert("RGBA")
                try:
                    grayscale = rgba.getchannel("A")
                finally:
                    rgba.close()
            else:
                grayscale = image.convert("L")
            try:
                if grayscale.size != original_size:
                    resized = grayscale.resize(
                        original_size,
                        resample=Image.Resampling.NEAREST,
                    )
                    grayscale.close()
                    grayscale = resized
                binary = _binary_mask(grayscale, threshold)
                try:
                    foreground_count = _foreground_count(binary)
                finally:
                    binary.close()
                output = BytesIO()
                grayscale.save(output, format="PNG")
            finally:
                grayscale.close()
    except MaskDatasetError:
        raise
    except (OSError, ValueError, SyntaxError) as error:
        raise MaskDatasetError("GROUND_TRUTH_MASK_INVALID") from error
    if foreground_count == 0:
        raise MaskDatasetError("GROUND_TRUTH_MASK_EMPTY")
    return output.getvalue()


def _prepare_cases(
    cases: Sequence[MaskDatasetCase],
    threshold: int,
) -> list[PreparedMaskCase]:
    prepared: list[PreparedMaskCase] = []
    for case in cases:
        original, mime_type, size = _decode_original(case.image_path)
        ground_truth = _decode_ground_truth(
            case.ground_truth_mask_path,
            size,
            threshold,
        )
        prepared.append(
            PreparedMaskCase(
                case_id=case.case_id,
                original=original,
                original_mime_type=mime_type,
                original_size=size,
                ground_truth_mask=ground_truth,
            )
        )
    return prepared


class _FixtureMasker:
    """No-cost deterministic mask used only for CLI contract checks."""

    async def mask(self, front: GarmentMaskInput) -> GarmentMask:
        with Image.open(BytesIO(front.data)) as source:
            oriented = ImageOps.exif_transpose(source)
            try:
                width, height = oriented.size
            finally:
                if oriented is not source:
                    oriented.close()
        mask = Image.new("L", (width, height), 0)
        try:
            left = max(0, width // 4)
            top = max(0, height // 4)
            right = max(left + 2, (width * 3) // 4)
            bottom = max(top + 2, (height * 3) // 4)
            mask.paste(255, (left, top, min(width, right), min(height, bottom)))
            output = BytesIO()
            mask.save(output, format="PNG")
        finally:
            mask.close()
        return GarmentMask(output.getvalue(), width, height)


def _create_live_masker(environ: Mapping[str, str]) -> GarmentMasker:
    settings = BackendSettings.from_env(environ)
    return GarmentMasker(
        HttpxGarmentMaskHttpClient(),
        remove_url=settings.rembg_remove_url,
    )


async def _call_masker(masker: object, front: GarmentMaskInput) -> GarmentMask:
    callback = getattr(masker, "mask", None)
    if not callable(callback):
        raise GarmentMaskContractError("masker must expose mask")
    result = callback(front)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, GarmentMask):
        raise GarmentMaskContractError("masker must return GarmentMask")
    validate_garment_mask_png(result.data, (result.width, result.height))
    return result


def _binary_mask(image: Image.Image, threshold: int) -> Image.Image:
    grayscale = image.convert("L")
    try:
        return grayscale.point(
            lambda value: 255 if value >= threshold else 0,
            mode="1",
        )
    finally:
        grayscale.close()


def _foreground_count(binary: Image.Image) -> int:
    histogram = binary.histogram()
    return sum(histogram[1:])


def _segmentation_metrics(
    predicted: Image.Image,
    ground_truth: Image.Image,
    threshold: int,
) -> dict[str, float]:
    predicted_binary = _binary_mask(predicted, threshold)
    ground_truth_binary = _binary_mask(ground_truth, threshold)
    intersection = ImageChops.logical_and(predicted_binary, ground_truth_binary)
    try:
        predicted_count = _foreground_count(predicted_binary)
        ground_truth_count = _foreground_count(ground_truth_binary)
        true_positive = _foreground_count(intersection)
    finally:
        intersection.close()
        ground_truth_binary.close()
        predicted_binary.close()
    false_positive = predicted_count - true_positive
    false_negative = ground_truth_count - true_positive
    union = true_positive + false_positive + false_negative
    if ground_truth_count <= 0 or union <= 0:
        raise GarmentMaskContractError("ground-truth mask has no foreground")
    precision_denominator = true_positive + false_positive
    precision = (
        true_positive / precision_denominator
        if precision_denominator > 0
        else 0.0
    )
    return {
        "iou": true_positive / union,
        "dice": (2 * true_positive) / (2 * true_positive + false_positive + false_negative),
        "precision": precision,
        "recall": true_positive / (true_positive + false_negative),
    }


def _preview_checks(
    case: PreparedMaskCase,
    mask: GarmentMask,
    threshold: int,
) -> tuple[bool, bool, bool]:
    preview_bytes = _render_transparent_preview(
        case.original,
        case.original_mime_type,
        mask,
        case.original_size,
    )
    with Image.open(BytesIO(case.original)) as source:
        oriented = ImageOps.exif_transpose(source)
        try:
            oriented.load()
            original_rgb = oriented.convert("RGB")
        finally:
            if oriented is not source:
                oriented.close()
    try:
        with Image.open(BytesIO(mask.data)) as predicted_source:
            predicted_source.load()
            predicted_alpha = predicted_source.convert("L")
        try:
            with Image.open(BytesIO(preview_bytes)) as preview_source:
                if preview_source.format != "PNG" or preview_source.mode != "RGBA":
                    return False, False, False
                preview_source.load()
                preview_rgb = preview_source.convert("RGB")
                preview_alpha = preview_source.getchannel("A")
            try:
                extrema = preview_alpha.getextrema()
                transparent_and_opaque = extrema == (0, 255)
                alpha_difference = ImageChops.difference(
                    preview_alpha, predicted_alpha
                )
                try:
                    alpha_matches = alpha_difference.getbbox() is None
                finally:
                    alpha_difference.close()
                foreground = _binary_mask(predicted_alpha, threshold)
                difference = ImageChops.difference(preview_rgb, original_rgb)
                try:
                    retained_difference = Image.new("RGB", difference.size, (0, 0, 0))
                    try:
                        retained_difference.paste(difference, mask=foreground)
                        rgb_retained = retained_difference.getbbox() is None
                    finally:
                        retained_difference.close()
                finally:
                    difference.close()
                    foreground.close()
                return transparent_and_opaque, rgb_retained, alpha_matches
            finally:
                preview_alpha.close()
                preview_rgb.close()
        finally:
            predicted_alpha.close()
    finally:
        original_rgb.close()


def _evaluate_case(
    case: PreparedMaskCase,
    mask: GarmentMask,
    threshold: int,
) -> tuple[dict[str, float], tuple[bool, bool, bool]]:
    if (mask.width, mask.height) != case.original_size:
        raise GarmentMaskContractError("mask dimensions do not match original")
    with Image.open(BytesIO(mask.data)) as predicted_source:
        predicted_source.load()
        predicted = predicted_source.convert("L")
    try:
        with Image.open(BytesIO(case.ground_truth_mask)) as ground_truth_source:
            ground_truth_source.load()
            ground_truth = ground_truth_source.convert("L")
        try:
            if ground_truth.size != case.original_size:
                resized = ground_truth.resize(
                    case.original_size,
                    resample=Image.Resampling.NEAREST,
                )
                ground_truth.close()
                ground_truth = resized
            metrics = _segmentation_metrics(predicted, ground_truth, threshold)
        finally:
            ground_truth.close()
    finally:
        predicted.close()
    return metrics, _preview_checks(case, mask, threshold)


def _rounded(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(value, digits)


def _nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return ordered[index]


def _quality_distribution(values: Sequence[float]) -> dict[str, object]:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return {
        "count": len(finite),
        "min": _rounded(min(finite)) if finite else None,
        "mean": _rounded(sum(finite) / len(finite)) if finite else None,
        "p50": _rounded(_nearest_rank(finite, 0.50)) if finite else None,
        "p95": _rounded(_nearest_rank(finite, 0.95)) if finite else None,
        "max": _rounded(max(finite)) if finite else None,
    }


def _latency_distribution(values: Sequence[float]) -> dict[str, object]:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return {
        "count": len(finite),
        "minMs": _rounded(min(finite), 3) if finite else None,
        "p50Ms": _rounded(_nearest_rank(finite, 0.50), 3) if finite else None,
        "p95Ms": _rounded(_nearest_rank(finite, 0.95), 3) if finite else None,
        "maxMs": _rounded(max(finite), 3) if finite else None,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 6)


def _error_code(error: BaseException) -> str:
    if isinstance(error, TimeoutError):
        return "TIMEOUT"
    if isinstance(error, GarmentMaskContractError):
        return "CONTRACT_ERROR"
    if isinstance(error, GarmentMaskProviderError):
        return "PROVIDER_ERROR"
    return "PROVIDER_ERROR"


def _empty_report(
    mode: str,
    status: str,
    reason_code: str,
    thresholds: MaskGateThresholds,
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "mode": mode,
        "status": status,
        "reasonCode": reason_code,
        "thresholds": thresholds.to_payload(),
        "counts": {
            "samples": 0,
            "providerCalls": 0,
            "providerErrors": 0,
            "evaluationErrors": 0,
            "evaluated": 0,
        },
        "providerErrorRate": None,
        "evaluationErrorRate": None,
        "errorsByCode": {code: 0 for code in _ERROR_CODES},
        "quality": {
            metric: _quality_distribution([])
            for metric in ("iou", "dice", "precision", "recall")
        },
        "preview": {
            "evaluated": 0,
            "transparentAndOpaquePass": 0,
            "originalRgbRetentionPass": 0,
            "alphaMatchesMaskPass": 0,
            "passRate": None,
        },
        "latencyMs": {"provider": _latency_distribution([])},
        "evaluation": {
            "minimumSamplesPass": False,
            "meanIoUPass": False,
            "p50IoUPass": False,
            "minimumIoUPass": False,
            "meanDicePass": False,
            "meanPrecisionPass": False,
            "minimumPrecisionPass": False,
            "meanRecallPass": False,
            "minimumRecallPass": False,
            "previewPassRatePass": False,
            "providerErrorRatePass": False,
            "evaluationErrorRatePass": False,
            "providerP95Pass": False,
            "allPass": False,
        },
    }


async def run_mask_dataset_verification(
    *,
    manifest_path: Path | str,
    data_dir: Path | str | None = None,
    mode: str = "fixture",
    thresholds: MaskGateThresholds | None = None,
    environ: Mapping[str, str] | None = None,
    masker: object | None = None,
    case_id_suffix: str | None = None,
) -> dict[str, object]:
    """Evaluate production-shaped garment masks against local GT masks."""

    selected_thresholds = thresholds or MaskGateThresholds()
    if mode not in {"fixture", "live"}:
        raise ValueError("mode must be fixture or live")
    manifest = Path(manifest_path)
    root = Path(data_dir) if data_dir is not None else manifest.parent
    cases = _load_manifest(manifest, root)
    prepared = _prepare_cases(cases, selected_thresholds.binary_threshold)
    if case_id_suffix is not None:
        if not isinstance(case_id_suffix, str) or not case_id_suffix.strip():
            raise MaskDatasetError("CASE_FILTER_INVALID")
        prepared = [
            case for case in prepared if case.case_id.endswith(case_id_suffix)
        ]
        if not prepared:
            raise MaskDatasetError("CASE_FILTER_EMPTY")

    selected_masker = masker
    if selected_masker is None:
        if mode == "fixture":
            selected_masker = _FixtureMasker()
        else:
            try:
                selected_masker = _create_live_masker(
                    os.environ if environ is None else environ
                )
            except (SettingsError, ValueError):
                return _empty_report(
                    mode,
                    "failed",
                    "LIVE_MASKER_CONSTRUCTION_FAILED",
                    selected_thresholds,
                )

    values = {
        "iou": [],
        "dice": [],
        "precision": [],
        "recall": [],
    }
    errors = {code: 0 for code in _ERROR_CODES}
    latencies: list[float] = []
    provider_errors = 0
    evaluation_errors = 0
    preview_evaluated = 0
    transparent_passes = 0
    rgb_passes = 0
    alpha_passes = 0
    all_preview_passes = 0

    for case in prepared:
        started = time.perf_counter()
        mask: GarmentMask | None = None
        try:
            mask = await _call_masker(
                selected_masker,
                GarmentMaskInput(case.original, case.original_mime_type),
            )
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise
            provider_errors += 1
            errors[_error_code(error)] += 1
        finally:
            latencies.append((time.perf_counter() - started) * 1_000)
        if mask is None:
            continue
        try:
            metrics, preview = await asyncio.to_thread(
                _evaluate_case,
                case,
                mask,
                selected_thresholds.binary_threshold,
            )
        except BaseException as error:
            if isinstance(
                error,
                (KeyboardInterrupt, SystemExit, asyncio.CancelledError),
            ):
                raise
            evaluation_errors += 1
            errors["EVALUATION_ERROR"] += 1
        else:
            for metric, value in metrics.items():
                values[metric].append(value)
            preview_evaluated += 1
            transparent_passes += int(preview[0])
            rgb_passes += int(preview[1])
            alpha_passes += int(preview[2])
            all_preview_passes += int(all(preview))

    sample_count = len(prepared)
    evaluated = len(values["iou"])
    error_rate = _ratio(provider_errors, sample_count)
    evaluation_error_rate = _ratio(evaluation_errors, sample_count)
    preview_pass_rate = _ratio(all_preview_passes, preview_evaluated)
    quality = {
        metric: _quality_distribution(metric_values)
        for metric, metric_values in values.items()
    }
    latency = _latency_distribution(latencies)

    minimum_samples_pass = sample_count >= selected_thresholds.min_samples
    iou_pass = bool(
        isinstance(quality["iou"]["mean"], (int, float))
        and quality["iou"]["mean"] >= selected_thresholds.min_mean_iou
    )
    p50_iou_pass = bool(
        isinstance(quality["iou"]["p50"], (int, float))
        and quality["iou"]["p50"] >= selected_thresholds.min_p50_iou
    )
    minimum_iou_pass = bool(
        isinstance(quality["iou"]["min"], (int, float))
        and quality["iou"]["min"] >= selected_thresholds.min_min_iou
    )
    dice_pass = bool(
        isinstance(quality["dice"]["mean"], (int, float))
        and quality["dice"]["mean"] >= selected_thresholds.min_mean_dice
    )
    precision_pass = bool(
        isinstance(quality["precision"]["mean"], (int, float))
        and quality["precision"]["mean"] >= selected_thresholds.min_mean_precision
    )
    minimum_precision_pass = bool(
        isinstance(quality["precision"]["min"], (int, float))
        and quality["precision"]["min"]
        >= selected_thresholds.min_min_precision
    )
    recall_pass = bool(
        isinstance(quality["recall"]["mean"], (int, float))
        and quality["recall"]["mean"] >= selected_thresholds.min_mean_recall
    )
    minimum_recall_pass = bool(
        isinstance(quality["recall"]["min"], (int, float))
        and quality["recall"]["min"] >= selected_thresholds.min_min_recall
    )
    preview_pass = bool(
        preview_pass_rate is not None
        and preview_pass_rate >= selected_thresholds.min_preview_pass_rate
    )
    error_pass = bool(
        error_rate is not None
        and error_rate <= selected_thresholds.max_provider_error_rate
    )
    evaluation_error_pass = bool(
        evaluation_error_rate is not None
        and evaluation_error_rate
        <= selected_thresholds.max_evaluation_error_rate
    )
    p95 = latency["p95Ms"]
    latency_pass = bool(
        isinstance(p95, (int, float))
        and p95 < selected_thresholds.max_provider_p95_ms
    )
    all_pass = all(
        (
            minimum_samples_pass,
            iou_pass,
            p50_iou_pass,
            minimum_iou_pass,
            dice_pass,
            precision_pass,
            minimum_precision_pass,
            recall_pass,
            minimum_recall_pass,
            preview_pass,
            error_pass,
            evaluation_error_pass,
            latency_pass,
        )
    )

    report = {
        "schemaVersion": 1,
        "mode": mode,
        "status": "passed" if all_pass else "failed",
        "reasonCode": None,
        "thresholds": selected_thresholds.to_payload(),
        "counts": {
            "samples": sample_count,
            "providerCalls": len(latencies),
            "providerErrors": provider_errors,
            "evaluationErrors": evaluation_errors,
            "evaluated": evaluated,
        },
        "providerErrorRate": error_rate,
        "evaluationErrorRate": evaluation_error_rate,
        "errorsByCode": errors,
        "quality": quality,
        "preview": {
            "evaluated": preview_evaluated,
            "transparentAndOpaquePass": transparent_passes,
            "originalRgbRetentionPass": rgb_passes,
            "alphaMatchesMaskPass": alpha_passes,
            "passRate": preview_pass_rate,
        },
        "latencyMs": {"provider": latency},
        "evaluation": {
            "minimumSamplesPass": minimum_samples_pass,
            "meanIoUPass": iou_pass,
            "p50IoUPass": p50_iou_pass,
            "minimumIoUPass": minimum_iou_pass,
            "meanDicePass": dice_pass,
            "meanPrecisionPass": precision_pass,
            "minimumPrecisionPass": minimum_precision_pass,
            "meanRecallPass": recall_pass,
            "minimumRecallPass": minimum_recall_pass,
            "previewPassRatePass": preview_pass,
            "providerErrorRatePass": error_pass,
            "evaluationErrorRatePass": evaluation_error_pass,
            "providerP95Pass": latency_pass,
            "allPass": all_pass,
        },
    }
    json.dumps(report, allow_nan=False)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate production garment masks against segmentation GT"
    )
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--manifest", required=True)
    parser.add_argument(
        "--data-dir",
        help="base directory for image and mask paths (defaults to manifest directory)",
    )
    parser.add_argument(
        "--case-id-suffix",
        help="evaluate only cases whose safe manifest id ends with this value",
    )
    parser.add_argument("--min-mean-iou", type=float, default=DEFAULT_MIN_MEAN_IOU)
    parser.add_argument("--min-p50-iou", type=float, default=DEFAULT_MIN_P50_IOU)
    parser.add_argument("--min-minimum-iou", type=float, default=DEFAULT_MIN_MIN_IOU)
    parser.add_argument("--min-mean-dice", type=float, default=DEFAULT_MIN_MEAN_DICE)
    parser.add_argument(
        "--min-mean-precision", type=float, default=DEFAULT_MIN_MEAN_PRECISION
    )
    parser.add_argument(
        "--min-minimum-precision", type=float, default=DEFAULT_MIN_MIN_PRECISION
    )
    parser.add_argument(
        "--min-mean-recall", type=float, default=DEFAULT_MIN_MEAN_RECALL
    )
    parser.add_argument(
        "--min-minimum-recall", type=float, default=DEFAULT_MIN_MIN_RECALL
    )
    parser.add_argument(
        "--min-preview-pass-rate",
        type=float,
        default=DEFAULT_MIN_PREVIEW_PASS_RATE,
    )
    parser.add_argument(
        "--max-provider-error-rate",
        type=float,
        default=DEFAULT_MAX_PROVIDER_ERROR_RATE,
    )
    parser.add_argument(
        "--max-evaluation-error-rate",
        type=float,
        default=DEFAULT_MAX_EVALUATION_ERROR_RATE,
    )
    parser.add_argument(
        "--max-provider-p95-ms", type=float, default=DEFAULT_MAX_PROVIDER_P95_MS
    )
    parser.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES)
    parser.add_argument(
        "--binary-threshold", type=int, default=DEFAULT_BINARY_THRESHOLD
    )
    parser.add_argument(
        "--output",
        help="also atomically save aggregate JSON in an existing directory",
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


def _serialize(report: Mapping[str, object], *, pretty: bool) -> str:
    return json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        allow_nan=False,
    ) + "\n"


def _write_output(path_value: str, content: str) -> None:
    destination = Path(path_value)
    parent = destination.parent.resolve(strict=True)
    if not parent.is_dir() or (
        destination.exists() and destination.is_dir()
    ):
        raise OSError("output unavailable")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=parent,
            prefix=".mask-report-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    masker: object | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    output = stdout or sys.stdout
    try:
        thresholds = MaskGateThresholds(
            min_mean_iou=args.min_mean_iou,
            min_p50_iou=args.min_p50_iou,
            min_min_iou=args.min_minimum_iou,
            min_mean_dice=args.min_mean_dice,
            min_mean_precision=args.min_mean_precision,
            min_min_precision=args.min_minimum_precision,
            min_mean_recall=args.min_mean_recall,
            min_min_recall=args.min_minimum_recall,
            min_preview_pass_rate=args.min_preview_pass_rate,
            max_provider_error_rate=args.max_provider_error_rate,
            max_evaluation_error_rate=args.max_evaluation_error_rate,
            max_provider_p95_ms=args.max_provider_p95_ms,
            min_samples=args.min_samples,
            binary_threshold=args.binary_threshold,
        )
        report = asyncio.run(
            run_mask_dataset_verification(
                manifest_path=args.manifest,
                data_dir=args.data_dir,
                mode=args.mode,
                thresholds=thresholds,
                environ=environ,
                masker=masker,
                case_id_suffix=args.case_id_suffix,
            )
        )
    except (MaskDatasetError, ValueError) as error:
        reason_code = (
            error.reason_code
            if isinstance(error, MaskDatasetError)
            else "INVALID_GATE_CONFIGURATION"
        )
        report = _empty_report(
            args.mode,
            "invalid",
            reason_code,
            MaskGateThresholds(),
        )
    serialized = _serialize(report, pretty=args.pretty)
    if args.output:
        try:
            _write_output(args.output, serialized)
        except OSError:
            report = _empty_report(
                args.mode,
                "invalid",
                "OUTPUT_UNWRITABLE",
                MaskGateThresholds(),
            )
            serialized = _serialize(report, pretty=args.pretty)
    print(serialized, file=output, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":  # pragma: no cover - subprocess entrypoint
    raise SystemExit(main())


__all__ = [
    "DEFAULT_BINARY_THRESHOLD",
    "DEFAULT_MAX_EVALUATION_ERROR_RATE",
    "DEFAULT_MAX_PROVIDER_ERROR_RATE",
    "DEFAULT_MAX_PROVIDER_P95_MS",
    "DEFAULT_MIN_MEAN_DICE",
    "DEFAULT_MIN_MEAN_IOU",
    "DEFAULT_MIN_MEAN_PRECISION",
    "DEFAULT_MIN_MIN_IOU",
    "DEFAULT_MIN_MIN_PRECISION",
    "DEFAULT_MIN_MEAN_RECALL",
    "DEFAULT_MIN_MIN_RECALL",
    "DEFAULT_MIN_P50_IOU",
    "DEFAULT_MIN_PREVIEW_PASS_RATE",
    "DEFAULT_MIN_SAMPLES",
    "MaskDatasetError",
    "MaskGateThresholds",
    "build_parser",
    "main",
    "run_mask_dataset_verification",
]
