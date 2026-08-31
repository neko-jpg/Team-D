#!/usr/bin/env python3
"""Build deterministic garment-guidance geometry cases from Open Images masks.

The input is a schemaVersion=1 manifest produced with
``open_images_garment_subset.py --require-segmentation``.  Each annotated
garment instance is isolated with its official mask and composited onto a
solid 512px canvas in five deterministic arrangements.  Geometry is measured
again after rasterization; a case is rejected instead of publishing an
incorrect expected code.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
from tempfile import NamedTemporaryFile
from typing import Mapping, Sequence

from PIL import Image


SCHEMA_VERSION = 1
CANVAS_SIZE = 512
BACKGROUND_RGB = (245, 245, 245)
FINITE_EXPECTED_CODES = frozenset(
    {
        "READY",
        "MOVE_CLOSER",
        "CENTER_GARMENT",
        "SHOW_FULL_GARMENT",
        "MOVE_FARTHER",
    }
)
_SAFE_CASE_ID_RE = re.compile(r"[^a-zA-Z0-9._-]+")


class GeometryDatasetError(RuntimeError):
    """Raised when source data or generated geometry violates the contract."""


@dataclass(frozen=True, slots=True)
class SourceCase:
    case_id: str
    image_path: Path
    mask_path: Path
    source: dict[str, object]
    selection_class: str
    review_status: str
    annotation_flags: dict[str, bool]


@dataclass(frozen=True, slots=True)
class Variant:
    name: str
    expected_code: str
    must_not_return: tuple[str, ...]


VARIANTS = (
    Variant("move-closer", "MOVE_CLOSER", ("READY",)),
    Variant("center-garment", "CENTER_GARMENT", ("READY",)),
    Variant("show-full", "SHOW_FULL_GARMENT", ("READY",)),
    Variant("move-farther", "MOVE_FARTHER", ("READY",)),
)


def _safe_relative(root: Path, raw: object, field: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise GeometryDatasetError(f"{field} must be a non-empty relative path")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
        raise GeometryDatasetError(f"{field} is unsafe: {raw!r}")
    candidate = (root / Path(*pure.parts)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise GeometryDatasetError(f"{field} escapes the manifest directory") from error
    return candidate


def _require_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise GeometryDatasetError(f"{field} must be an object")
    return value


def _clean_source_annotation(
    case: Mapping[str, object], field: str
) -> tuple[str, dict[str, bool], bool]:
    review_status = case.get("reviewStatus")
    if review_status not in {"unreviewed", "reviewed_clean"}:
        raise GeometryDatasetError(f"{field}.reviewStatus is invalid")
    annotation = _require_mapping(
        case.get("originalAnnotation"), f"{field}.originalAnnotation"
    )
    raw_flags = _require_mapping(
        annotation.get("flags"), f"{field}.originalAnnotation.flags"
    )
    flag_names = (
        "isOccluded",
        "isTruncated",
        "isGroupOf",
        "isDepiction",
        "isInside",
    )
    flags: dict[str, bool] = {}
    for name in flag_names:
        value = raw_flags.get(name)
        if not isinstance(value, bool):
            raise GeometryDatasetError(
                f"{field}.originalAnnotation.flags.{name} must be boolean"
            )
        flags[name] = value
    raw_bbox = _require_mapping(
        annotation.get("bbox"), f"{field}.originalAnnotation.bbox"
    )
    bbox: dict[str, float] = {}
    for name in ("xmin", "ymin", "xmax", "ymax"):
        value = raw_bbox.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise GeometryDatasetError(
                f"{field}.originalAnnotation.bbox.{name} must be numeric"
            )
        bbox[name] = float(value)
    if not (
        0 <= bbox["xmin"] < bbox["xmax"] <= 1
        and 0 <= bbox["ymin"] < bbox["ymax"] <= 1
    ):
        raise GeometryDatasetError(f"{field}.originalAnnotation.bbox is invalid")
    clean = (
        not any(flags.values())
        and bbox["xmin"] > 0.01
        and bbox["ymin"] > 0.01
        and bbox["xmax"] < 0.99
        and bbox["ymax"] < 0.99
    )
    return review_status, flags, clean


def _load_source_cases(manifest_path: Path) -> tuple[dict[str, object], list[SourceCase]]:
    try:
        root_value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GeometryDatasetError(f"cannot read input manifest: {error}") from error
    manifest = dict(_require_mapping(root_value, "manifest"))
    if manifest.get("schemaVersion") != SCHEMA_VERSION:
        raise GeometryDatasetError("input manifest schemaVersion must be 1")
    raw_cases = manifest.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise GeometryDatasetError("input manifest cases must be a non-empty array")

    root = manifest_path.resolve().parent
    seen_ids: set[str] = set()
    cases: list[SourceCase] = []
    for index, raw_case in enumerate(raw_cases):
        case = _require_mapping(raw_case, f"cases[{index}]")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise GeometryDatasetError(f"cases[{index}].id must be non-empty")
        if case_id in seen_ids:
            raise GeometryDatasetError(f"duplicate source case id: {case_id}")
        seen_ids.add(case_id)
        source = dict(_require_mapping(case.get("source"), f"cases[{index}].source"))
        license_url = source.get("licenseUrl")
        if not isinstance(license_url, str) or not license_url.startswith("https://"):
            raise GeometryDatasetError(f"cases[{index}] is missing an HTTPS licenseUrl")
        selection_class = case.get("selectionClass")
        if not isinstance(selection_class, str) or not selection_class.strip():
            raise GeometryDatasetError(f"cases[{index}].selectionClass must be non-empty")
        review_status, annotation_flags, clean = _clean_source_annotation(
            case, f"cases[{index}]"
        )
        source_case = SourceCase(
            case_id=case_id,
            image_path=_safe_relative(root, case.get("image"), f"cases[{index}].image"),
            mask_path=_safe_relative(
                root, case.get("groundTruthMask"), f"cases[{index}].groundTruthMask"
            ),
            source=source,
            selection_class=selection_class,
            review_status=review_status,
            annotation_flags=annotation_flags,
        )
        _validate_source_pixels(source_case)
        if clean:
            cases.append(source_case)
    if not cases:
        raise GeometryDatasetError(
            "input manifest has no unoccluded, untruncated, edge-safe source cases"
        )
    return manifest, cases


def _validate_source_pixels(source: SourceCase) -> None:
    try:
        with Image.open(source.image_path) as image:
            image.verify()
        with Image.open(source.mask_path) as mask:
            if mask.format != "PNG":
                raise GeometryDatasetError(
                    f"source mask must be PNG for {source.case_id}"
                )
            mask.verify()
        with Image.open(source.image_path) as image, Image.open(source.mask_path) as mask:
            image_width, image_height = image.size
            mask_width, mask_height = mask.size
            image_ratio = image_width / image_height
            mask_ratio = mask_width / mask_height
            if abs(image_ratio - mask_ratio) / image_ratio > 0.02:
                raise GeometryDatasetError(
                    f"image/mask aspect ratios differ for {source.case_id}"
                )
            normalized = mask.convert("L")
            if normalized.getbbox() is None:
                raise GeometryDatasetError(f"source mask is empty for {source.case_id}")
    except (OSError, ValueError) as error:
        raise GeometryDatasetError(
            f"cannot decode source pixels for {source.case_id}: {error}"
        ) from error


def _load_instance(source: SourceCase) -> tuple[Image.Image, Image.Image]:
    with Image.open(source.image_path) as raw_image:
        image = raw_image.convert("RGB")
    with Image.open(source.mask_path) as raw_mask:
        mask = raw_mask.convert("L")
    if mask.size != image.size:
        mask = mask.resize(image.size, Image.Resampling.NEAREST)
    mask = mask.point(lambda value: 255 if value else 0, mode="L")
    bbox = mask.getbbox()
    if bbox is None:
        raise GeometryDatasetError(f"resized source mask is empty for {source.case_id}")
    return image.crop(bbox), mask.crop(bbox)


def _scaled_size(width: int, height: int, variant: Variant) -> tuple[int, int]:
    if variant.name == "move-closer":
        scale = (CANVAS_SIZE * 0.28) / max(width, height)
    elif variant.name == "move-farther":
        scale = (CANVAS_SIZE * 0.92) / max(width, height)
    elif variant.name == "center-garment":
        scale = min(
            (CANVAS_SIZE * 0.65) / max(width, height),
            (CANVAS_SIZE * 0.56) / width,
        )
    else:
        scale = (CANVAS_SIZE * (0.70 if variant.name == "show-full" else 0.65)) / max(
            width, height
        )
    return max(1, round(width * scale)), max(1, round(height * scale))


def _placement(width: int, height: int, variant: Variant) -> tuple[int, int]:
    if variant.name == "center-garment":
        center_x = CANVAS_SIZE * 0.29
        return round(center_x - width / 2), round((CANVAS_SIZE - height) / 2)
    if variant.name == "show-full":
        return -round(width * 0.12), round((CANVAS_SIZE - height) / 2)
    return round((CANVAS_SIZE - width) / 2), round((CANVAS_SIZE - height) / 2)


def _geometry(mask: Image.Image, *, clip_fraction: float) -> dict[str, object]:
    bbox = mask.getbbox()
    if bbox is None:
        raise GeometryDatasetError("generated mask has no foreground")
    xmin, ymin, xmax, ymax = bbox
    width = (xmax - xmin) / CANVAS_SIZE
    height = (ymax - ymin) / CANVAS_SIZE
    center_x = ((xmin + xmax) / 2) / CANVAS_SIZE
    center_y = ((ymin + ymax) / 2) / CANVAS_SIZE
    return {
        "bboxPixels": [xmin, ymin, xmax, ymax],
        "bboxNormalized": {
            "xmin": xmin / CANVAS_SIZE,
            "ymin": ymin / CANVAS_SIZE,
            "xmax": xmax / CANVAS_SIZE,
            "ymax": ymax / CANVAS_SIZE,
        },
        "span": max(width, height),
        "widthSpan": width,
        "heightSpan": height,
        "center": {"x": center_x, "y": center_y},
        "margins": {
            "left": xmin / CANVAS_SIZE,
            "right": (CANVAS_SIZE - xmax) / CANVAS_SIZE,
            "top": ymin / CANVAS_SIZE,
            "bottom": (CANVAS_SIZE - ymax) / CANVAS_SIZE,
        },
        "clipFraction": clip_fraction,
    }


def _number(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise AssertionError("generated geometry metric is not numeric")
    return float(value)


def _validate_geometry(variant: Variant, geometry: Mapping[str, object]) -> None:
    span = _number(geometry["span"])
    center = _require_mapping(geometry["center"], "geometry.center")
    margins = _require_mapping(geometry["margins"], "geometry.margins")
    center_x = _number(center["x"])
    center_y = _number(center["y"])
    margin_values = [_number(value) for value in margins.values()]
    clip_fraction = _number(geometry["clipFraction"])

    valid = False
    if variant.name == "move-closer":
        valid = (
            0.25 <= span <= 0.30
            and abs(center_x - 0.5) <= 0.06
            and abs(center_y - 0.5) <= 0.06
            and clip_fraction == 0
        )
    elif variant.name == "center-garment":
        valid = (
            0.55 <= span <= 0.75
            and 0.23 <= center_x <= 0.31
            and abs(center_y - 0.5) <= 0.06
            and min(margin_values) >= 0
            and clip_fraction == 0
        )
    elif variant.name == "show-full":
        valid = margins["left"] == 0 and clip_fraction >= 0.08
    elif variant.name == "move-farther":
        valid = (
            0.90 <= span <= 0.94
            and abs(center_x - 0.5) <= 0.06
            and abs(center_y - 0.5) <= 0.06
            and min(margin_values) < 0.045
            and clip_fraction == 0
        )
    if not valid:
        raise GeometryDatasetError(
            f"generated {variant.name} geometry violates its invariant: {geometry}"
        )


def _compose_variant(
    instance: Image.Image,
    instance_mask: Image.Image,
    variant: Variant,
) -> tuple[Image.Image, Image.Image, dict[str, object]]:
    width, height = _scaled_size(instance.width, instance.height, variant)
    resized_image = instance.resize((width, height), Image.Resampling.LANCZOS)
    resized_mask = instance_mask.resize((width, height), Image.Resampling.NEAREST)
    resized_mask = resized_mask.point(lambda value: 255 if value else 0, mode="L")
    x, y = _placement(width, height, variant)

    clip_fraction = 0.0
    if variant.name == "show-full":
        total_foreground = width * height - resized_mask.histogram()[0]
        if total_foreground <= 0:
            raise GeometryDatasetError("resized garment mask has no foreground")
        selected: tuple[int, float] | None = None
        for proposed_fraction in (0.12, 0.18, 0.24, 0.30, 0.36, 0.42, 0.48):
            proposed_x = -round(width * proposed_fraction)
            probe = Image.new("L", (CANVAS_SIZE, CANVAS_SIZE), 0)
            probe.paste(resized_mask, (proposed_x, y))
            probe_bbox = probe.getbbox()
            visible_foreground = CANVAS_SIZE * CANVAS_SIZE - probe.histogram()[0]
            actual_fraction = 1 - (visible_foreground / total_foreground)
            if (
                probe_bbox is not None
                and probe_bbox[0] == 0
                and 0.08 <= actual_fraction <= 0.50
            ):
                selected = (proposed_x, actual_fraction)
                break
        if selected is None:
            raise GeometryDatasetError("could not create a clearly clipped garment instance")
        x, clip_fraction = selected

    canvas = Image.new("RGB", (CANVAS_SIZE, CANVAS_SIZE), BACKGROUND_RGB)
    canvas_mask = Image.new("L", (CANVAS_SIZE, CANVAS_SIZE), 0)
    canvas.paste(resized_image, (x, y), resized_mask)
    canvas_mask.paste(resized_mask, (x, y))
    geometry = _geometry(canvas_mask, clip_fraction=clip_fraction)
    _validate_geometry(variant, geometry)
    geometry["requestedScale"] = {
        "widthPixels": width,
        "heightPixels": height,
        "placementX": x,
        "placementY": y,
        "preserveAspectRatio": True,
    }
    return canvas, canvas_mask, geometry


def _safe_output_stem(case_id: str) -> str:
    slug = _SAFE_CASE_ID_RE.sub("-", case_id).strip("-._") or "case"
    digest = hashlib.sha256(case_id.encode()).hexdigest()[:8]
    return f"{slug[:96]}-{digest}"


def _save_png_atomic(image: Image.Image, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".part",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            image.save(temporary, format="PNG", optimize=False)
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _write_manifest_atomic(destination: Path, value: object) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{destination.name}.",
            suffix=".part",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(value, temporary, ensure_ascii=False, indent=2, sort_keys=True)
            temporary.write("\n")
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def build_geometry_dataset(
    input_manifest: Path,
    output_dir: Path,
    *,
    overwrite: bool = False,
    generated_at: str | None = None,
) -> Path:
    input_manifest = input_manifest.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if output_dir == input_manifest.parent:
        raise GeometryDatasetError("output directory must differ from the input dataset")
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise GeometryDatasetError(
            f"output directory is not empty: {output_dir}; pass --overwrite explicitly"
        )

    input_value, source_cases = _load_source_cases(input_manifest)
    input_manifest_sha256 = hashlib.sha256(input_manifest.read_bytes()).hexdigest()
    images_dir = output_dir / "images"
    masks_dir = output_dir / "masks"
    generated_cases: list[dict[str, object]] = []
    generated_artifacts: list[tuple[Image.Image, Path]] = []
    for source in source_cases:
        instance, instance_mask = _load_instance(source)
        stem = _safe_output_stem(source.case_id)
        for variant in VARIANTS:
            if variant.expected_code not in FINITE_EXPECTED_CODES:
                raise AssertionError("variant uses a non-finite expected code")
            image, transformed_mask, geometry = _compose_variant(
                instance, instance_mask, variant
            )
            filename = f"{stem}__{variant.name}.png"
            mask_filename = f"{stem}__{variant.name}.png"
            destination = images_dir / filename
            mask_destination = masks_dir / mask_filename
            if destination.exists() and not overwrite:
                raise GeometryDatasetError(f"refusing to overwrite {destination}")
            if mask_destination.exists() and not overwrite:
                raise GeometryDatasetError(f"refusing to overwrite {mask_destination}")
            generated_artifacts.extend(
                ((image, destination), (transformed_mask, mask_destination))
            )
            source_value = deepcopy(source.source)
            source_value["derivedFromManifestSha256"] = input_manifest_sha256
            generated_cases.append(
                {
                    "id": f"{stem}__{variant.name}",
                    "image": f"images/{filename}",
                    "groundTruthMask": f"masks/{mask_filename}",
                    "shot": "front",
                    "scope": "geometry_transformed",
                    "expectedCode": variant.expected_code,
                    "mustNotReturn": list(variant.must_not_return),
                    "reviewStatus": "deterministic_transform",
                    "sourceReviewStatus": source.review_status,
                    "sourceAnnotationFlags": source.annotation_flags,
                    "sourceCaseId": source.case_id,
                    "selectionClass": source.selection_class,
                    "source": source_value,
                    "transform": {
                        "kind": variant.name,
                        "canvas": {
                            "width": CANVAS_SIZE,
                            "height": CANVAS_SIZE,
                            "format": "PNG",
                            "backgroundRgb": list(BACKGROUND_RGB),
                        },
                        "geometry": geometry,
                        "transparentPixels": "replaced_with_solid_background",
                        "garmentRgb": "source_pixels_resampled_without_recoloring",
                    },
                }
            )

    # Nothing is written until every source decoded and every rasterized
    # geometry invariant passed. This prevents a failed build from presenting
    # a partial directory as a usable evaluation dataset.
    for artifact, destination in generated_artifacts:
        _save_png_atomic(artifact, destination)
    generated_cases.sort(key=lambda case: str(case["id"]))
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "dataset": {
            "name": "Team-D deterministic garment geometry cases",
            "derivedFromManifestSha256": input_manifest_sha256,
            "sourceDataset": input_value.get("dataset"),
        },
        "generatedAt": generated_at or datetime.now(UTC).isoformat(),
        "selection": {
            "scope": "geometry_transformed",
            "sourceCaseCount": len(source_cases),
            "variantsPerSource": len(VARIANTS),
            "caseCount": len(generated_cases),
            "readyCasesIncluded": False,
        },
        "cases": generated_cases,
    }
    manifest_path = output_dir / "manifest.json"
    _write_manifest_atomic(manifest_path, manifest)
    return manifest_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_manifest", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/evaluation/garment_geometry_transformed"),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="explicitly replace generated files and manifest in a non-empty output",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest_path = build_geometry_dataset(
            args.input_manifest,
            args.output_dir,
            overwrite=args.overwrite,
        )
    except GeometryDatasetError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"wrote {len(manifest['cases'])} cases to {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
