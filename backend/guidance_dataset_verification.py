"""Backend-only accuracy and latency gate for real garment image datasets.

The command consumes a local manifest and image directory, sends each image
through the same finite guidance contract used by the live backend, and emits
aggregate-only JSON.  Image paths, image bytes, manifest metadata, provider
response bodies, and credentials are deliberately excluded from the report.

Example live gate::

    python -m backend.guidance_dataset_verification \
      --mode live \
      --manifest .local/evaluation/manifest.json \
      --images-dir .local/evaluation \
      --min-exact-accuracy 0.80 \
      --max-provider-error-rate 0 \
      --max-false-ready-rate 0 \
      --max-provider-p95-ms 1000 \
      --max-connect-count 1 \
      --output .local/evaluation/report.json

``fixture`` mode is a no-cost wiring check whose deterministic prediction is
``READY``.  Tests may inject any fake analyzer into
``run_dataset_verification`` to exercise every metric without API usage.
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
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, TextIO, TypeAlias

from PIL import Image

from .providers.runtime import FixtureVisionGuidanceProvider
from .providers.vision_guidance import (
    GUIDANCE_CODES,
    MODEL_GUIDANCE_CODES_BY_SHOT,
    EncodedImage,
    GuidanceCode,
    GuidanceContractError,
    GuidanceInput,
    GuidanceShot,
    VisionDecision,
    validate_guidance_code,
    validate_guidance_shot,
    validate_model_vision_decision_for_shot,
)
from .providers.vision_guidance_realtime import (
    DEFAULT_REALTIME_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_REALTIME_GUIDANCE_MODEL,
    DEFAULT_REALTIME_IMAGE_MAX_EDGE,
    DEFAULT_REALTIME_JPEG_QUALITY,
    DEFAULT_REALTIME_MAX_OUTPUT_TOKENS,
    DEFAULT_REALTIME_PREWARM_TIMEOUT_SECONDS,
    DEFAULT_REALTIME_RESPONSE_TIMEOUT_SECONDS,
    OpenAIRealtimeVisionGuidanceAnalyzer,
    RealtimeGuidanceTimeoutError,
)


AnalyzerResult: TypeAlias = VisionDecision | Mapping[str, object]
Analyzer: TypeAlias = Callable[
    [GuidanceInput], AnalyzerResult | Awaitable[AnalyzerResult]
]

DEFAULT_MIN_EXACT_ACCURACY = 0.80
DEFAULT_MAX_PROVIDER_ERROR_RATE = 0.0
DEFAULT_MAX_FALSE_READY_RATE = 0.0
DEFAULT_MAX_FORBIDDEN_CODE_RATE = 0.0
DEFAULT_MAX_PROVIDER_P95_MS = 1_000.0
DEFAULT_MIN_SAMPLES = 20
DEFAULT_MIN_NON_READY_SAMPLES = 20
DEFAULT_MAX_CONNECT_COUNT = 1
_SUPPORTED_FORMATS = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
_PROVIDER_ERROR_CODES = (
    "TIMEOUT",
    "CONTRACT_ERROR",
    "PROVIDER_ERROR",
)


class DatasetManifestError(ValueError):
    """A non-sensitive, finite manifest or image validation failure."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class DatasetCase:
    case_id: str
    image_path: Path
    shot: GuidanceShot
    expected_code: GuidanceCode
    must_not_return: tuple[GuidanceCode, ...]
    previous_code: GuidanceCode | None = None


@dataclass(frozen=True, slots=True)
class GateThresholds:
    min_exact_accuracy: float = DEFAULT_MIN_EXACT_ACCURACY
    max_provider_error_rate: float = DEFAULT_MAX_PROVIDER_ERROR_RATE
    max_false_ready_rate: float = DEFAULT_MAX_FALSE_READY_RATE
    max_forbidden_code_rate: float = DEFAULT_MAX_FORBIDDEN_CODE_RATE
    max_provider_p95_ms: float = DEFAULT_MAX_PROVIDER_P95_MS
    min_samples: int = DEFAULT_MIN_SAMPLES
    min_non_ready_samples: int = DEFAULT_MIN_NON_READY_SAMPLES
    max_connect_count: int = DEFAULT_MAX_CONNECT_COUNT

    def __post_init__(self) -> None:
        for field_name in (
            "min_exact_accuracy",
            "max_provider_error_rate",
            "max_false_ready_rate",
            "max_forbidden_code_rate",
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
        for field_name in (
            "min_samples",
            "min_non_ready_samples",
            "max_connect_count",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")

    def to_payload(self) -> dict[str, object]:
        return {
            "minExactAccuracy": float(self.min_exact_accuracy),
            "maxProviderErrorRate": float(self.max_provider_error_rate),
            "maxFalseReadyRate": float(self.max_false_ready_rate),
            "maxForbiddenCodeRate": float(self.max_forbidden_code_rate),
            "maxProviderP95Ms": float(self.max_provider_p95_ms),
            "minSamples": self.min_samples,
            "minNonReadySamples": self.min_non_ready_samples,
            "maxConnectCount": self.max_connect_count,
            "latencyComparison": "p95 strictly less than threshold",
        }


def _load_manifest(manifest_path: Path, images_dir: Path) -> list[DatasetCase]:
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DatasetManifestError("MANIFEST_UNREADABLE") from error
    schema_version = raw.get("schemaVersion") if isinstance(raw, Mapping) else None
    if (
        not isinstance(raw, Mapping)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        raise DatasetManifestError("MANIFEST_SCHEMA_UNSUPPORTED")
    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise DatasetManifestError("MANIFEST_CASES_INVALID")

    try:
        root = images_dir.resolve(strict=True)
    except OSError as error:
        raise DatasetManifestError("IMAGE_DIRECTORY_UNAVAILABLE") from error
    if not root.is_dir():
        raise DatasetManifestError("IMAGE_DIRECTORY_UNAVAILABLE")

    cases: list[DatasetCase] = []
    seen_ids: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, Mapping):
            raise DatasetManifestError("MANIFEST_CASE_INVALID")
        case_id = raw_case.get("id")
        image_value = raw_case.get("image")
        if (
            not isinstance(case_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", case_id) is None
            or case_id in seen_ids
            or not isinstance(image_value, str)
            or not image_value.strip()
        ):
            raise DatasetManifestError("MANIFEST_CASE_INVALID")
        seen_ids.add(case_id)

        relative_image = Path(image_value)
        if relative_image.is_absolute():
            raise DatasetManifestError("IMAGE_PATH_INVALID")
        try:
            image_path = (root / relative_image).resolve(strict=True)
        except OSError as error:
            raise DatasetManifestError("IMAGE_UNAVAILABLE") from error
        if not image_path.is_relative_to(root) or not image_path.is_file():
            raise DatasetManifestError("IMAGE_PATH_INVALID")

        try:
            shot = validate_guidance_shot(raw_case.get("shot"))
            expected_code = validate_guidance_code(raw_case.get("expectedCode"))
            previous_value = raw_case.get("previousCode")
            previous_code = (
                None
                if previous_value is None
                else validate_guidance_code(previous_value)
            )
        except GuidanceContractError as error:
            raise DatasetManifestError("MANIFEST_LABEL_INVALID") from error
        if expected_code not in MODEL_GUIDANCE_CODES_BY_SHOT[shot]:
            raise DatasetManifestError("MANIFEST_LABEL_INVALID")
        review_status = raw_case.get("reviewStatus")
        scope = raw_case.get("scope")
        if (scope, review_status) not in {
            ("geometry_transformed", "deterministic_transform"),
            ("human_reviewed", "human_reviewed"),
        }:
            raise DatasetManifestError("MANIFEST_REVIEW_REQUIRED")
        if scope == "geometry_transformed" and expected_code is GuidanceCode.READY:
            raise DatasetManifestError("MANIFEST_REVIEW_REQUIRED")
        raw_must_not_return = raw_case.get("mustNotReturn")
        if not isinstance(raw_must_not_return, list):
            raise DatasetManifestError("MANIFEST_LABEL_INVALID")
        try:
            must_not_return = [
                validate_guidance_code(value) for value in raw_must_not_return
            ]
        except GuidanceContractError as error:
            raise DatasetManifestError("MANIFEST_LABEL_INVALID") from error
        if (
            len(set(must_not_return)) != len(must_not_return)
            or expected_code in must_not_return
            or any(code not in MODEL_GUIDANCE_CODES_BY_SHOT[shot] for code in must_not_return)
            or (
                expected_code is not GuidanceCode.READY
                and GuidanceCode.READY not in must_not_return
            )
        ):
            raise DatasetManifestError("MANIFEST_LABEL_INVALID")
        cases.append(
            DatasetCase(
                case_id=case_id,
                image_path=image_path,
                shot=shot,
                expected_code=expected_code,
                must_not_return=tuple(must_not_return),
                previous_code=previous_code,
            )
        )
    return cases


def _encoded_image(image_path: Path) -> EncodedImage:
    try:
        data = image_path.read_bytes()
        with Image.open(BytesIO(data)) as image:
            image_format = image.format
            width, height = image.size
            image.verify()
    except (OSError, ValueError) as error:
        raise DatasetManifestError("IMAGE_INVALID") from error
    mime_type = _SUPPORTED_FORMATS.get(image_format or "")
    if mime_type is None or width <= 0 or height <= 0:
        raise DatasetManifestError("IMAGE_FORMAT_UNSUPPORTED")
    return EncodedImage(data, mime_type, width, height)


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def _nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return _rounded(ordered[index])


def _distribution(values: Sequence[float]) -> dict[str, object]:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return {
        "count": len(finite),
        "minMs": _rounded(min(finite)) if finite else None,
        "p50Ms": _nearest_rank(finite, 0.50),
        "p95Ms": _nearest_rank(finite, 0.95),
        "maxMs": _rounded(max(finite)) if finite else None,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 6)


def _error_code(error: BaseException) -> str:
    if isinstance(error, (RealtimeGuidanceTimeoutError, TimeoutError)):
        return "TIMEOUT"
    if isinstance(error, GuidanceContractError):
        return "CONTRACT_ERROR"
    return "PROVIDER_ERROR"


async def _call_analyzer(analyzer: object, input_value: GuidanceInput) -> VisionDecision:
    analyze = getattr(analyzer, "analyze", None)
    target = analyze if callable(analyze) else analyzer
    if not callable(target):
        raise TypeError("analyzer must be callable or expose analyze")
    result = target(input_value)
    if inspect.isawaitable(result):
        result = await result
    return validate_model_vision_decision_for_shot(
        result, input_value.requested_shot
    )


async def _lifecycle_call(analyzer: object, name: str) -> None:
    callback = getattr(analyzer, name, None)
    if callable(callback):
        result = callback()
        if inspect.isawaitable(result):
            await result


def _environment_float(
    environ: Mapping[str, str], name: str, default: float
) -> float:
    raw = environ.get(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0 else default


def _environment_int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        return default
    return value if value > 0 else default


def _create_live_analyzer(environ: Mapping[str, str]) -> object | None:
    api_key = environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    from openai import AsyncOpenAI  # type: ignore[import-not-found]

    model = environ.get("VISION_GUIDANCE_MODEL", "").strip()
    selected_model = model or DEFAULT_REALTIME_GUIDANCE_MODEL
    reasoning_effort = (
        environ.get("VISION_GUIDANCE_REASONING_EFFORT", "").strip() or None
    )
    if reasoning_effort is None and selected_model.startswith("gpt-realtime-2"):
        reasoning_effort = "minimal"
    return OpenAIRealtimeVisionGuidanceAnalyzer(
        AsyncOpenAI(api_key=api_key),
        selected_model,
        response_timeout_seconds=_environment_float(
            environ,
            "VISION_GUIDANCE_RESPONSE_TIMEOUT_SECONDS",
            DEFAULT_REALTIME_RESPONSE_TIMEOUT_SECONDS,
        ),
        connect_timeout_seconds=_environment_float(
            environ,
            "VISION_GUIDANCE_CONNECT_TIMEOUT_SECONDS",
            DEFAULT_REALTIME_CONNECT_TIMEOUT_SECONDS,
        ),
        prewarm_timeout_seconds=_environment_float(
            environ,
            "VISION_GUIDANCE_PREWARM_TIMEOUT_SECONDS",
            DEFAULT_REALTIME_PREWARM_TIMEOUT_SECONDS,
        ),
        image_max_edge=_environment_int(
            environ, "VISION_GUIDANCE_IMAGE_MAX_EDGE", DEFAULT_REALTIME_IMAGE_MAX_EDGE
        ),
        jpeg_quality=_environment_int(
            environ, "VISION_GUIDANCE_JPEG_QUALITY", DEFAULT_REALTIME_JPEG_QUALITY
        ),
        max_output_tokens=_environment_int(
            environ,
            "VISION_GUIDANCE_MAX_OUTPUT_TOKENS",
            (
                64
                if selected_model.startswith("gpt-realtime-2")
                else DEFAULT_REALTIME_MAX_OUTPUT_TOKENS
            ),
        ),
        reasoning_effort=reasoning_effort,
    )


def _empty_report(
    *,
    mode: str,
    status: str,
    reason_code: str,
    thresholds: GateThresholds,
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
            "predictions": 0,
            "correct": 0,
        },
        "accuracy": {
            "exact": None,
            "providerErrorRate": None,
            "falseReadyRate": None,
            "forbiddenCodeRate": None,
        },
        "criticalFalseReady": {
            "count": 0,
            "eligibleSamples": 0,
        },
        "forbiddenCode": {
            "count": 0,
            "eligibleSamples": 0,
        },
        "perCode": {},
        "confusion": {},
        "providerErrorsByCode": {code: 0 for code in _PROVIDER_ERROR_CODES},
        "latencyMs": {"prewarm": None, "provider": _distribution([])},
        "realtimeSession": {
            "connectCount": None,
            "requestCount": None,
            "singleConnectionPass": None,
        },
        "evaluation": {
            "minimumSamplesPass": False,
            "minimumNonReadySamplesPass": False,
            "exactAccuracyPass": False,
            "providerErrorRatePass": False,
            "falseReadyRatePass": False,
            "forbiddenCodeRatePass": False,
            "providerP95Pass": False,
            "maxConnectCountPass": None,
            "allPass": False,
        },
    }


async def run_dataset_verification(
    *,
    manifest_path: Path | str,
    images_dir: Path | str | None = None,
    mode: str = "fixture",
    thresholds: GateThresholds | None = None,
    environ: Mapping[str, str] | None = None,
    analyzer: object | None = None,
) -> dict[str, object]:
    """Evaluate one local dataset while retaining no image-level report data."""

    selected_thresholds = thresholds or GateThresholds()
    if mode not in {"fixture", "live"}:
        raise ValueError("mode must be fixture or live")
    manifest = Path(manifest_path)
    root = Path(images_dir) if images_dir is not None else manifest.parent
    cases = _load_manifest(manifest, root)
    # Decode every image before the first provider call.  A broken dataset must
    # not create a partial, billable live run.
    encoded_images = [_encoded_image(case.image_path) for case in cases]

    selected_analyzer = analyzer
    if selected_analyzer is None:
        if mode == "fixture":
            selected_analyzer = FixtureVisionGuidanceProvider()
        else:
            try:
                selected_analyzer = _create_live_analyzer(
                    os.environ if environ is None else environ
                )
            except Exception:
                return _empty_report(
                    mode=mode,
                    status="failed",
                    reason_code="LIVE_ANALYZER_CONSTRUCTION_FAILED",
                    thresholds=selected_thresholds,
                )
    if selected_analyzer is None:
        return _empty_report(
            mode=mode,
            status="skipped",
            reason_code="LIVE_CREDENTIALS_UNAVAILABLE",
            thresholds=selected_thresholds,
        )

    expected_counts = {code: 0 for code in GUIDANCE_CODES}
    predicted_counts = {code: 0 for code in GUIDANCE_CODES}
    correct_counts = {code: 0 for code in GUIDANCE_CODES}
    confusion = {
        expected: {predicted: 0 for predicted in (*GUIDANCE_CODES, "PROVIDER_ERROR")}
        for expected in GUIDANCE_CODES
    }
    errors_by_code = {code: 0 for code in _PROVIDER_ERROR_CODES}
    provider_latencies: list[float] = []
    correct = 0
    provider_errors = 0
    false_ready_count = 0
    forbidden_code_count = 0
    prewarm_ms: float | None = None
    setup_reason: str | None = None

    try:
        prewarm_started = time.perf_counter()
        try:
            await _lifecycle_call(selected_analyzer, "prewarm")
            prewarm_ms = (time.perf_counter() - prewarm_started) * 1_000
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise
            setup_reason = "PROVIDER_PREWARM_FAILED"

        if setup_reason is None:
            for case, encoded in zip(cases, encoded_images, strict=True):
                expected = case.expected_code.value
                expected_counts[expected] += 1
                input_value = GuidanceInput(
                    frame=encoded,
                    requested_shot=case.shot,
                    previous_code=case.previous_code,
                )
                started = time.perf_counter()
                try:
                    decision = await _call_analyzer(selected_analyzer, input_value)
                except BaseException as error:
                    if isinstance(error, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                        raise
                    provider_errors += 1
                    errors_by_code[_error_code(error)] += 1
                    confusion[expected]["PROVIDER_ERROR"] += 1
                else:
                    predicted = decision.code.value
                    predicted_counts[predicted] += 1
                    confusion[expected][predicted] += 1
                    if (
                        case.expected_code is not GuidanceCode.READY
                        and decision.code is GuidanceCode.READY
                    ):
                        false_ready_count += 1
                    if decision.code in case.must_not_return:
                        forbidden_code_count += 1
                    if predicted == expected:
                        correct += 1
                        correct_counts[expected] += 1
                finally:
                    provider_latencies.append(
                        (time.perf_counter() - started) * 1_000
                    )
    finally:
        try:
            await _lifecycle_call(selected_analyzer, "aclose")
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise

    sample_count = len(cases)
    provider_calls = len(provider_latencies)
    predictions = provider_calls - provider_errors
    exact_accuracy = _ratio(correct, sample_count)
    provider_error_rate = _ratio(provider_errors, sample_count)
    false_ready_eligible = sum(
        case.expected_code is not GuidanceCode.READY for case in cases
    )
    false_ready_rate = _ratio(false_ready_count, false_ready_eligible)
    forbidden_code_eligible = sum(bool(case.must_not_return) for case in cases)
    forbidden_code_rate = _ratio(
        forbidden_code_count, forbidden_code_eligible
    )
    provider_latency = _distribution(provider_latencies)
    provider_p95 = provider_latency["p95Ms"]
    connect_count = getattr(selected_analyzer, "connect_count", None)
    request_count = getattr(selected_analyzer, "request_count", None)
    if isinstance(connect_count, bool) or not isinstance(connect_count, int):
        connect_count = None
    if isinstance(request_count, bool) or not isinstance(request_count, int):
        request_count = None

    minimum_samples_pass = sample_count >= selected_thresholds.min_samples
    minimum_non_ready_samples_pass = (
        false_ready_eligible >= selected_thresholds.min_non_ready_samples
    )
    accuracy_pass = bool(
        exact_accuracy is not None
        and exact_accuracy >= selected_thresholds.min_exact_accuracy
    )
    provider_error_pass = bool(
        provider_error_rate is not None
        and provider_error_rate <= selected_thresholds.max_provider_error_rate
    )
    false_ready_pass = bool(
        false_ready_rate is not None
        and false_ready_rate <= selected_thresholds.max_false_ready_rate
    )
    forbidden_code_pass = bool(
        forbidden_code_rate is not None
        and forbidden_code_rate <= selected_thresholds.max_forbidden_code_rate
    )
    provider_p95_pass = bool(
        isinstance(provider_p95, (int, float))
        and not isinstance(provider_p95, bool)
        and float(provider_p95) < selected_thresholds.max_provider_p95_ms
    )
    if connect_count is None:
        connection_pass = None if mode == "fixture" else False
    else:
        connection_pass = connect_count <= selected_thresholds.max_connect_count
    all_pass = bool(
        setup_reason is None
        and minimum_samples_pass
        and minimum_non_ready_samples_pass
        and accuracy_pass
        and provider_error_pass
        and false_ready_pass
        and forbidden_code_pass
        and provider_p95_pass
        and connection_pass is not False
    )

    per_code: dict[str, object] = {}
    for code in GUIDANCE_CODES:
        expected = expected_counts[code]
        predicted = predicted_counts[code]
        code_correct = correct_counts[code]
        per_code[code] = {
            "expected": expected,
            "predicted": predicted,
            "correct": code_correct,
            "recall": _ratio(code_correct, expected),
            "precision": _ratio(code_correct, predicted),
        }

    report = {
        "schemaVersion": 1,
        "mode": mode,
        "status": "passed" if all_pass else "failed",
        "reasonCode": setup_reason,
        "thresholds": selected_thresholds.to_payload(),
        "counts": {
            "samples": sample_count,
            "providerCalls": provider_calls,
            "providerErrors": provider_errors,
            "predictions": predictions,
            "correct": correct,
        },
        "accuracy": {
            "exact": exact_accuracy,
            "providerErrorRate": provider_error_rate,
            "falseReadyRate": false_ready_rate,
            "forbiddenCodeRate": forbidden_code_rate,
        },
        "criticalFalseReady": {
            "count": false_ready_count,
            "eligibleSamples": false_ready_eligible,
        },
        "forbiddenCode": {
            "count": forbidden_code_count,
            "eligibleSamples": forbidden_code_eligible,
        },
        "perCode": per_code,
        "confusion": confusion,
        "providerErrorsByCode": errors_by_code,
        "latencyMs": {
            "prewarm": _rounded(prewarm_ms),
            "provider": provider_latency,
        },
        "realtimeSession": {
            "connectCount": connect_count,
            "requestCount": request_count,
            "singleConnectionPass": connection_pass,
        },
        "evaluation": {
            "minimumSamplesPass": minimum_samples_pass,
            "minimumNonReadySamplesPass": minimum_non_ready_samples_pass,
            "exactAccuracyPass": accuracy_pass,
            "providerErrorRatePass": provider_error_pass,
            "falseReadyRatePass": false_ready_pass,
            "forbiddenCodeRatePass": forbidden_code_pass,
            "providerP95Pass": provider_p95_pass,
            "maxConnectCountPass": connection_pass,
            "allPass": all_pass,
        },
    }
    # Treat serialization as part of the contract so NaN/Infinity can never
    # escape from provider timing or metric arithmetic.
    json.dumps(report, allow_nan=False)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate finite backend guidance codes on a local image manifest"
    )
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--manifest", required=True)
    parser.add_argument(
        "--images-dir",
        help="base directory for manifest image paths (defaults to manifest directory)",
    )
    parser.add_argument(
        "--min-exact-accuracy", type=float, default=DEFAULT_MIN_EXACT_ACCURACY
    )
    parser.add_argument(
        "--max-provider-error-rate",
        type=float,
        default=DEFAULT_MAX_PROVIDER_ERROR_RATE,
    )
    parser.add_argument(
        "--max-false-ready-rate",
        type=float,
        default=DEFAULT_MAX_FALSE_READY_RATE,
    )
    parser.add_argument(
        "--max-forbidden-code-rate",
        type=float,
        default=DEFAULT_MAX_FORBIDDEN_CODE_RATE,
    )
    parser.add_argument(
        "--max-provider-p95-ms", type=float, default=DEFAULT_MAX_PROVIDER_P95_MS
    )
    parser.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES)
    parser.add_argument(
        "--min-non-ready-samples",
        type=int,
        default=DEFAULT_MIN_NON_READY_SAMPLES,
    )
    parser.add_argument(
        "--max-connect-count", type=int, default=DEFAULT_MAX_CONNECT_COUNT
    )
    parser.add_argument(
        "--output",
        help="also atomically save the aggregate JSON to a file in an existing directory",
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


def _serialize_report(report: Mapping[str, object], *, pretty: bool) -> str:
    return json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        allow_nan=False,
    ) + "\n"


def _write_output_file(path_value: str, content: str) -> None:
    output_path = Path(path_value)
    parent = output_path.parent.resolve(strict=True)
    if not parent.is_dir() or (
        output_path.exists() and output_path.is_dir()
    ):
        raise OSError("output destination is unavailable")
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=parent,
            prefix=".guidance-report-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, output_path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    analyzer: object | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    output = stdout or sys.stdout
    try:
        thresholds = GateThresholds(
            min_exact_accuracy=args.min_exact_accuracy,
            max_provider_error_rate=args.max_provider_error_rate,
            max_false_ready_rate=args.max_false_ready_rate,
            max_forbidden_code_rate=args.max_forbidden_code_rate,
            max_provider_p95_ms=args.max_provider_p95_ms,
            min_samples=args.min_samples,
            min_non_ready_samples=args.min_non_ready_samples,
            max_connect_count=args.max_connect_count,
        )
        report = asyncio.run(
            run_dataset_verification(
                manifest_path=args.manifest,
                images_dir=args.images_dir,
                mode=args.mode,
                thresholds=thresholds,
                environ=environ,
                analyzer=analyzer,
            )
        )
    except (DatasetManifestError, ValueError) as error:
        reason_code = (
            error.reason_code
            if isinstance(error, DatasetManifestError)
            else "INVALID_GATE_CONFIGURATION"
        )
        fallback_thresholds = GateThresholds()
        report = _empty_report(
            mode=args.mode,
            status="invalid",
            reason_code=reason_code,
            thresholds=fallback_thresholds,
        )
    serialized = _serialize_report(report, pretty=args.pretty)
    if args.output:
        try:
            _write_output_file(args.output, serialized)
        except OSError:
            report = _empty_report(
                mode=args.mode,
                status="invalid",
                reason_code="OUTPUT_UNWRITABLE",
                thresholds=GateThresholds(),
            )
            serialized = _serialize_report(report, pretty=args.pretty)
    print(serialized, file=output, end="")
    if report["status"] == "passed":
        return 0
    if report["status"] == "skipped":
        return 2
    return 1


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())


__all__ = [
    "Analyzer",
    "DEFAULT_MAX_CONNECT_COUNT",
    "DEFAULT_MAX_FALSE_READY_RATE",
    "DEFAULT_MAX_FORBIDDEN_CODE_RATE",
    "DEFAULT_MAX_PROVIDER_ERROR_RATE",
    "DEFAULT_MAX_PROVIDER_P95_MS",
    "DEFAULT_MIN_EXACT_ACCURACY",
    "DEFAULT_MIN_NON_READY_SAMPLES",
    "DEFAULT_MIN_SAMPLES",
    "DatasetManifestError",
    "GateThresholds",
    "build_parser",
    "main",
    "run_dataset_verification",
]
