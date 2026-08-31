"""Aggregate-only quality gate for deterministic garment geometry guidance.

The verifier consumes the checked-in ``schemaVersion=1`` transformed garment
dataset, decodes every image before making a provider call, and invokes the
same ``analyze_geometry(GuidanceInput)`` boundary used by the live backend.
Only aggregate counts and latency distributions are emitted.  Case IDs,
paths, source metadata, image bytes, provider payloads, and exception messages
are deliberately excluded from the report.

The gate is intentionally fixed because it protects a safety-critical split:
the local geometry path must classify all four framing corrections exactly and
must never manufacture ``READY`` before the semantic analyzer has approved the
frame.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import math
import re
import sys
import tempfile
import time
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import TextIO

from PIL import Image, ImageOps

from .providers.geometry_guidance import (
    GeometryGuidanceContractError,
    GeometryGuidanceProvider,
    GeometryGuidanceProviderError,
    GeometryGuidanceTimeoutError,
)
from .providers.garment_masker import HttpxGarmentMaskHttpClient
from .providers.vision_guidance import (
    EncodedImage,
    GuidanceCode,
    GuidanceContractError,
    GuidanceInput,
    GuidanceShot,
    validate_model_vision_decision_for_shot,
)
from .settings import BackendSettings, SettingsError


EXPECTED_CODES = (
    GuidanceCode.CENTER_GARMENT,
    GuidanceCode.MOVE_CLOSER,
    GuidanceCode.MOVE_FARTHER,
    GuidanceCode.SHOW_FULL_GARMENT,
)
EXPECTED_CODE_VALUES = tuple(code.value for code in EXPECTED_CODES)

MINIMUM_SAMPLES = 20
MINIMUM_SAMPLES_PER_CODE = 5
MAX_PROVIDER_P95_MS = 400.0
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000

_SUPPORTED_FORMATS = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}
_PREDICTED_VALUES = (*EXPECTED_CODE_VALUES, GuidanceCode.READY.value, "PASS")
_ERROR_CODES = ("TIMEOUT", "CONTRACT_ERROR", "PROVIDER_ERROR")


class GeometryVerificationDatasetError(ValueError):
    """A finite, non-sensitive manifest or image validation failure."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class GeometryVerificationCase:
    case_id: str
    image_path: Path
    shot: GuidanceShot
    expected_code: GuidanceCode


@dataclass(frozen=True, slots=True)
class PreparedGeometryVerificationCase:
    image: EncodedImage
    shot: GuidanceShot
    expected_code: GuidanceCode


def _safe_image_path(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise GeometryVerificationDatasetError("MANIFEST_CASE_INVALID")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise GeometryVerificationDatasetError("IMAGE_PATH_INVALID")
    try:
        resolved = (root / relative).resolve(strict=True)
    except OSError as error:
        raise GeometryVerificationDatasetError("IMAGE_UNAVAILABLE") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise GeometryVerificationDatasetError("IMAGE_PATH_INVALID")
    return resolved


def _load_manifest(
    manifest_path: Path,
    images_dir: Path,
) -> list[GeometryVerificationCase]:
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GeometryVerificationDatasetError("MANIFEST_UNREADABLE") from error
    schema_version = raw.get("schemaVersion") if isinstance(raw, Mapping) else None
    if (
        not isinstance(raw, Mapping)
        or isinstance(schema_version, bool)
        or schema_version != 1
    ):
        raise GeometryVerificationDatasetError("MANIFEST_SCHEMA_UNSUPPORTED")
    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise GeometryVerificationDatasetError("MANIFEST_CASES_INVALID")

    try:
        root = images_dir.resolve(strict=True)
    except OSError as error:
        raise GeometryVerificationDatasetError(
            "IMAGE_DIRECTORY_UNAVAILABLE"
        ) from error
    if not root.is_dir():
        raise GeometryVerificationDatasetError("IMAGE_DIRECTORY_UNAVAILABLE")

    cases: list[GeometryVerificationCase] = []
    seen_ids: set[str] = set()
    expected_counts = {code: 0 for code in EXPECTED_CODES}
    for raw_case in raw_cases:
        if not isinstance(raw_case, Mapping):
            raise GeometryVerificationDatasetError("MANIFEST_CASE_INVALID")
        case_id = raw_case.get("id")
        if (
            not isinstance(case_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", case_id) is None
            or case_id in seen_ids
        ):
            raise GeometryVerificationDatasetError("MANIFEST_CASE_INVALID")
        seen_ids.add(case_id)

        expected_value = raw_case.get("expectedCode")
        if expected_value == GuidanceCode.READY.value:
            raise GeometryVerificationDatasetError("READY_EXPECTATION_FORBIDDEN")
        try:
            expected_code = GuidanceCode(expected_value)
            shot = GuidanceShot(raw_case.get("shot"))
        except (TypeError, ValueError) as error:
            raise GeometryVerificationDatasetError("MANIFEST_LABEL_INVALID") from error
        if expected_code not in EXPECTED_CODES or shot not in {
            GuidanceShot.FRONT,
            GuidanceShot.BACK,
        }:
            raise GeometryVerificationDatasetError("MANIFEST_LABEL_INVALID")
        expected_counts[expected_code] += 1
        cases.append(
            GeometryVerificationCase(
                case_id=case_id,
                image_path=_safe_image_path(root, raw_case.get("image")),
                shot=shot,
                expected_code=expected_code,
            )
        )

    if len(cases) < MINIMUM_SAMPLES:
        raise GeometryVerificationDatasetError("INSUFFICIENT_SAMPLES")
    if any(count < MINIMUM_SAMPLES_PER_CODE for count in expected_counts.values()):
        raise GeometryVerificationDatasetError("INSUFFICIENT_SAMPLES_PER_CODE")
    return cases


def _decode_image(path: Path) -> EncodedImage:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise GeometryVerificationDatasetError("IMAGE_UNAVAILABLE") from error
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise GeometryVerificationDatasetError("IMAGE_INVALID")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                image_format = source.format
                raw_width, raw_height = source.size
                if (
                    raw_width <= 0
                    or raw_height <= 0
                    or raw_width * raw_height > MAX_IMAGE_PIXELS
                ):
                    raise GeometryVerificationDatasetError("IMAGE_INVALID")
                source.verify()
            with Image.open(BytesIO(data)) as source:
                oriented = ImageOps.exif_transpose(source)
                try:
                    oriented.load()
                    width, height = oriented.size
                finally:
                    if oriented is not source:
                        oriented.close()
    except GeometryVerificationDatasetError:
        raise
    except Exception as error:
        raise GeometryVerificationDatasetError("IMAGE_INVALID") from error

    mime_type = _SUPPORTED_FORMATS.get(image_format or "")
    if (
        mime_type is None
        or width <= 0
        or height <= 0
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise GeometryVerificationDatasetError("IMAGE_FORMAT_UNSUPPORTED")
    return EncodedImage(data, mime_type, width, height)


def _prepare_cases(
    cases: Sequence[GeometryVerificationCase],
) -> list[PreparedGeometryVerificationCase]:
    # Decode the complete dataset before the first provider call.  A corrupt
    # final image must not leave behind a misleading partial live evaluation.
    return [
        PreparedGeometryVerificationCase(
            image=_decode_image(case.image_path),
            shot=case.shot,
            expected_code=case.expected_code,
        )
        for case in cases
    ]


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def _nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    value = _nearest_rank_value(values, percentile)
    return _rounded(value)


def _nearest_rank_value(
    values: Sequence[float], percentile: float
) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return None
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return ordered[index]


def _latency_distribution(values: Sequence[float]) -> dict[str, object]:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return {
        "count": len(finite),
        "p50Ms": _nearest_rank(finite, 0.50),
        "p95Ms": _nearest_rank(finite, 0.95),
        "maxMs": _rounded(max(finite)) if finite else None,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 6)


def _error_code(error: BaseException) -> str:
    if isinstance(error, (GeometryGuidanceTimeoutError, TimeoutError)):
        return "TIMEOUT"
    if isinstance(error, (GeometryGuidanceContractError, GuidanceContractError)):
        return "CONTRACT_ERROR"
    if isinstance(error, GeometryGuidanceProviderError):
        return "PROVIDER_ERROR"
    return "PROVIDER_ERROR"


async def _call_analyzer(
    analyzer: object,
    input_value: GuidanceInput,
) -> str:
    callback = getattr(analyzer, "analyze_geometry", None)
    if not callable(callback):
        raise GeometryGuidanceContractError(
            "geometry analyzer must expose analyze_geometry"
        )
    result = callback(input_value)
    if inspect.isawaitable(result):
        result = await result
    if result is None:
        return "PASS"
    decision = validate_model_vision_decision_for_shot(
        result, input_value.requested_shot
    )
    if decision.code not in {*EXPECTED_CODES, GuidanceCode.READY}:
        raise GeometryGuidanceContractError(
            "geometry analyzer returned a non-geometry code"
        )
    return decision.code.value


async def _lifecycle_call(analyzer: object, name: str) -> None:
    callback = getattr(analyzer, name, None)
    if callable(callback):
        result = callback()
        if inspect.isawaitable(result):
            await result


def _create_live_analyzer(environ: Mapping[str, str] | None = None) -> object:
    settings = BackendSettings.from_env(environ)
    return GeometryGuidanceProvider(
        HttpxGarmentMaskHttpClient(),
        remove_url=settings.rembg_remove_url,
    )


async def run_geometry_guidance_verification(
    *,
    manifest_path: Path | str,
    images_dir: Path | str | None = None,
    analyzer: object | None = None,
    environ: Mapping[str, str] | None = None,
    mode: str | None = None,
) -> dict[str, object]:
    """Run the fixed 20-case/400ms gate and return aggregate-only JSON data."""

    manifest = Path(manifest_path)
    root = Path(images_dir) if images_dir is not None else manifest.parent
    cases = _load_manifest(manifest, root)
    prepared = _prepare_cases(cases)
    selected_analyzer = (
        analyzer if analyzer is not None else _create_live_analyzer(environ)
    )
    selected_mode = mode or ("injected" if analyzer is not None else "live")
    if not isinstance(selected_mode, str) or not selected_mode:
        raise ValueError("mode must be a non-empty string")

    expected_counts = {
        code.value: sum(case.expected_code is code for case in prepared)
        for code in EXPECTED_CODES
    }
    correct_counts = {code.value: 0 for code in EXPECTED_CODES}
    predicted_counts = {value: 0 for value in _PREDICTED_VALUES}
    confusion = {
        expected.value: {
            predicted: 0 for predicted in (*_PREDICTED_VALUES, "PROVIDER_ERROR")
        }
        for expected in EXPECTED_CODES
    }
    errors_by_code = {code: 0 for code in _ERROR_CODES}
    provider_latencies: list[float] = []
    correct = 0
    provider_errors = 0
    false_ready = 0
    prewarm_ms: float | None = None
    prewarm_error = False

    try:
        if callable(getattr(selected_analyzer, "prewarm", None)):
            started = time.perf_counter()
            try:
                await _lifecycle_call(selected_analyzer, "prewarm")
            except BaseException as error:
                if isinstance(
                    error,
                    (KeyboardInterrupt, SystemExit, asyncio.CancelledError),
                ):
                    raise
                prewarm_error = True
                provider_errors += 1
                errors_by_code[_error_code(error)] += 1
            finally:
                prewarm_ms = (time.perf_counter() - started) * 1_000

        if not prewarm_error:
            for case in prepared:
                expected_value = case.expected_code.value
                input_value = GuidanceInput(
                    frame=case.image,
                    requested_shot=case.shot,
                )
                started = time.perf_counter()
                try:
                    predicted = await _call_analyzer(selected_analyzer, input_value)
                except BaseException as error:
                    if isinstance(
                        error,
                        (KeyboardInterrupt, SystemExit, asyncio.CancelledError),
                    ):
                        raise
                    provider_errors += 1
                    errors_by_code[_error_code(error)] += 1
                    confusion[expected_value]["PROVIDER_ERROR"] += 1
                else:
                    predicted_counts[predicted] += 1
                    confusion[expected_value][predicted] += 1
                    if predicted == GuidanceCode.READY.value:
                        false_ready += 1
                    if predicted == expected_value:
                        correct += 1
                        correct_counts[expected_value] += 1
                finally:
                    provider_latencies.append(
                        (time.perf_counter() - started) * 1_000
                    )
    finally:
        try:
            await _lifecycle_call(selected_analyzer, "aclose")
        except BaseException as error:
            if isinstance(
                error,
                (KeyboardInterrupt, SystemExit, asyncio.CancelledError),
            ):
                raise
            # Cleanup is outside the measured provider call.  Keep the report
            # aggregate-only and fail it without exposing close diagnostics.
            prewarm_error = True
            provider_errors += 1
            errors_by_code[_error_code(error)] += 1

    sample_count = len(prepared)
    exact_accuracy = _ratio(correct, sample_count)
    latency = _latency_distribution(provider_latencies)
    p95 = _nearest_rank_value(provider_latencies, 0.95)
    per_code = {
        code.value: {
            "samples": expected_counts[code.value],
            "correct": correct_counts[code.value],
            "exactAccuracy": _ratio(
                correct_counts[code.value], expected_counts[code.value]
            ),
        }
        for code in EXPECTED_CODES
    }

    minimum_samples_pass = sample_count >= MINIMUM_SAMPLES
    per_code_minimum_pass = all(
        expected_counts[code.value] >= MINIMUM_SAMPLES_PER_CODE
        for code in EXPECTED_CODES
    )
    per_code_exact_pass = all(
        correct_counts[code.value] == expected_counts[code.value]
        for code in EXPECTED_CODES
    )
    zero_errors_pass = provider_errors == 0 and not prewarm_error
    exact_accuracy_pass = correct == sample_count
    false_ready_pass = false_ready == 0
    provider_p95_pass = bool(
        isinstance(p95, (int, float)) and float(p95) < MAX_PROVIDER_P95_MS
    )
    all_pass = all(
        (
            minimum_samples_pass,
            per_code_minimum_pass,
            per_code_exact_pass,
            zero_errors_pass,
            exact_accuracy_pass,
            false_ready_pass,
            provider_p95_pass,
        )
    )

    return {
        "schemaVersion": 1,
        "mode": selected_mode,
        "status": "passed" if all_pass else "failed",
        "reasonCode": "ALL_GATES_PASSED" if all_pass else "QUALITY_GATE_FAILED",
        "thresholds": {
            "minimumSamples": MINIMUM_SAMPLES,
            "minimumSamplesPerCode": MINIMUM_SAMPLES_PER_CODE,
            "requiredExactAccuracy": 1.0,
            "maximumProviderErrors": 0,
            "maximumFalseReady": 0,
            "maximumProviderP95Ms": MAX_PROVIDER_P95_MS,
            "latencyComparison": "p95 strictly less than threshold",
        },
        "counts": {
            "samples": sample_count,
            "providerCalls": len(provider_latencies),
            "providerErrors": provider_errors,
            "predictions": sum(predicted_counts.values()),
            "correct": correct,
            "falseReady": false_ready,
        },
        "accuracy": {"exact": exact_accuracy},
        "perCode": per_code,
        "predictedCounts": predicted_counts,
        "confusion": confusion,
        "errorsByCode": errors_by_code,
        "latencyMs": {
            "prewarm": _rounded(prewarm_ms),
            "provider": latency,
        },
        "evaluation": {
            "minimumSamplesPass": minimum_samples_pass,
            "minimumSamplesPerCodePass": per_code_minimum_pass,
            "perCodeExactPass": per_code_exact_pass,
            "zeroProviderErrorsPass": zero_errors_pass,
            "exactAccuracyPass": exact_accuracy_pass,
            "falseReadyPass": false_ready_pass,
            "providerP95Pass": provider_p95_pass,
            "allPass": all_pass,
        },
    }


def _write_report(path: Path, report: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary_name = temporary.name
        Path(temporary_name).replace(path)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--images-dir", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = _parser().parse_args(argv)
    output_stream = sys.stdout if stdout is None else stdout
    error_stream = sys.stderr if stderr is None else stderr
    try:
        report = asyncio.run(
            run_geometry_guidance_verification(
                manifest_path=args.manifest,
                images_dir=args.images_dir,
                mode="live",
            )
        )
        if args.output is None:
            json.dump(
                report,
                output_stream,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            output_stream.write("\n")
        else:
            _write_report(args.output, report)
    except GeometryVerificationDatasetError as error:
        print(f"error: {error.reason_code}", file=error_stream)
        return 2
    except (SettingsError, ValueError, OSError):
        print("error: VERIFICATION_SETUP_FAILED", file=error_stream)
        return 2
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EXPECTED_CODES",
    "MAX_PROVIDER_P95_MS",
    "MINIMUM_SAMPLES",
    "MINIMUM_SAMPLES_PER_CODE",
    "GeometryVerificationDatasetError",
    "run_geometry_guidance_verification",
]
