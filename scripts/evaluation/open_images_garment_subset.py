#!/usr/bin/env python3
"""Download a small, attributable Open Images V7 garment evaluation subset.

The script uses the same public ``open-images-dataset`` S3 bucket as the
official Open Images downloader, but only Python's standard library is needed
for transfer.  Pillow (already a backend dependency) validates the downloaded
pixels.  Dataset files are written below ``data/evaluation/`` by default; that
directory is intentionally ignored by Git.

Bounding boxes provide deterministic *heuristic* guidance labels, not reviewed
ground truth.  Every manifest case is marked ``reviewStatus=unreviewed`` so an
accuracy gate cannot accidentally present these labels as human judgements.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import shutil
import sys
from tempfile import NamedTemporaryFile
from typing import Callable, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

from PIL import Image


DATASET_NAME = "Open Images V7"
DATASET_PAGE = "https://storage.googleapis.com/openimages/web/download_v7.html"
DATASET_DESCRIPTION = "https://storage.googleapis.com/openimages/web/factsfigures_v7.html"
ANNOTATION_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
CLASS_DESCRIPTION_URL = (
    "https://storage.googleapis.com/openimages/v7/"
    "oidv7-class-descriptions-boxable.csv"
)
BBOX_URLS = {
    "train": (
        "https://storage.googleapis.com/openimages/v6/"
        "oidv6-train-annotations-bbox.csv"
    ),
    "validation": (
        "https://storage.googleapis.com/openimages/v5/"
        "validation-annotations-bbox.csv"
    ),
    "test": (
        "https://storage.googleapis.com/openimages/v5/test-annotations-bbox.csv"
    ),
}
IMAGE_INFO_URLS = {
    "train": (
        "https://storage.googleapis.com/openimages/2018_04/train/"
        "train-images-boxable-with-rotation.csv"
    ),
    "validation": (
        "https://storage.googleapis.com/openimages/2018_04/validation/"
        "validation-images-with-rotation.csv"
    ),
    "test": (
        "https://storage.googleapis.com/openimages/2018_04/test/"
        "test-images-with-rotation.csv"
    ),
}
HUMAN_IMAGE_LABEL_URLS = {
    "train": (
        "https://storage.googleapis.com/openimages/v7/"
        "oidv7-train-annotations-human-imagelabels.csv"
    ),
    "validation": (
        "https://storage.googleapis.com/openimages/v7/"
        "oidv7-val-annotations-human-imagelabels.csv"
    ),
    "test": (
        "https://storage.googleapis.com/openimages/v7/"
        "oidv7-test-annotations-human-imagelabels.csv"
    ),
}
IMAGE_BUCKET_BASE_URL = "https://open-images-dataset.s3.amazonaws.com"
SEGMENTATION_ANNOTATION_URLS = {
    "validation": (
        "https://storage.googleapis.com/openimages/v5/"
        "validation-annotations-object-segmentation.csv"
    ),
}
MASK_SHARD_URL_TEMPLATE = (
    "https://storage.googleapis.com/openimages/v5/"
    "validation-masks/validation-masks-{shard}.zip"
)
DEFAULT_CLASSES = ("Shirt", "Dress", "Jacket", "Trousers")
FINITE_FRONT_CODES = frozenset(
    {
        "MOVE_CLOSER",
        "MOVE_FARTHER",
        "CENTER_GARMENT",
        "SHOW_FULL_GARMENT",
        "READY",
    }
)
_IMAGE_ID_RE = re.compile(r"^[0-9a-fA-F]{16}$")
_MASK_NAME_RE = re.compile(r"^[0-9a-fA-F]{16}_[A-Za-z0-9_]+_[A-Za-z0-9]+\.png$")
_USER_AGENT = "Team-D-OpenImages-Evaluator/1.0"
_MAX_MASK_UNCOMPRESSED_BYTES = 50 * 1024 * 1024


class DatasetBuildError(RuntimeError):
    """Raised when an attributable, internally consistent subset cannot build."""


@dataclass(frozen=True, slots=True)
class BuildConfig:
    output_dir: Path
    split: str = "validation"
    classes: tuple[str, ...] = DEFAULT_CLASSES
    limit_per_class: int = 5
    seed: str = "team-d-open-images-v1"
    exclude_person: bool = True
    require_segmentation: bool = False
    refresh_metadata: bool = False
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.split not in BBOX_URLS:
            raise ValueError(f"split must be one of: {', '.join(BBOX_URLS)}")
        if not self.classes or any(not value.strip() for value in self.classes):
            raise ValueError("classes must contain non-empty names")
        if len({value.casefold() for value in self.classes}) != len(self.classes):
            raise ValueError("classes must not contain duplicates")
        if self.exclude_person and any(value.casefold() == "person" for value in self.classes):
            raise ValueError("Person cannot be requested while exclude_person is enabled")
        if self.require_segmentation and self.split not in SEGMENTATION_ANNOTATION_URLS:
            raise ValueError("require_segmentation currently supports validation only")
        if not 1 <= self.limit_per_class <= 100:
            raise ValueError("limit_per_class must be between 1 and 100")
        if not self.seed:
            raise ValueError("seed must be non-empty")
        if not 1.0 <= self.timeout_seconds <= 300.0:
            raise ValueError("timeout_seconds must be between 1 and 300")


@dataclass(frozen=True, slots=True)
class Annotation:
    image_id: str
    label_mid: str
    display_name: str
    confidence: float
    xmin: float
    xmax: float
    ymin: float
    ymax: float
    source: str
    is_occluded: bool
    is_truncated: bool
    is_group_of: bool
    is_depiction: bool
    is_inside: bool

    @property
    def width(self) -> float:
        return self.xmax - self.xmin

    @property
    def height(self) -> float:
        return self.ymax - self.ymin

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center(self) -> tuple[float, float]:
        return ((self.xmin + self.xmax) / 2, (self.ymin + self.ymax) / 2)

    def as_manifest_value(self) -> dict[str, object]:
        return {
            "labelMid": self.label_mid,
            "displayName": self.display_name,
            "confidence": self.confidence,
            "source": self.source,
            "bbox": {
                "xmin": self.xmin,
                "xmax": self.xmax,
                "ymin": self.ymin,
                "ymax": self.ymax,
            },
            "flags": {
                "isOccluded": self.is_occluded,
                "isTruncated": self.is_truncated,
                "isGroupOf": self.is_group_of,
                "isDepiction": self.is_depiction,
                "isInside": self.is_inside,
            },
        }


@dataclass(frozen=True, slots=True)
class SelectedCase:
    selection_class: str
    annotation: Annotation
    all_annotations: tuple[Annotation, ...]
    segmentation: SegmentationAnnotation | None = None


@dataclass(frozen=True, slots=True)
class SegmentationAnnotation:
    mask_path: str
    image_id: str
    label_mid: str
    box_id: str
    xmin: float
    xmax: float
    ymin: float
    ymax: float
    predicted_iou: float
    clicks: str

    def as_manifest_value(self) -> dict[str, object]:
        return {
            "maskPath": self.mask_path,
            "imageId": self.image_id,
            "labelMid": self.label_mid,
            "boxId": self.box_id,
            "bbox": {
                "xmin": self.xmin,
                "xmax": self.xmax,
                "ymin": self.ymin,
                "ymax": self.ymax,
            },
            "predictedIoU": self.predicted_iou,
            "clicks": self.clicks,
        }


RetrieveUrl = Callable[[str, Path, float], None]


def _retrieve_url(url: str, destination: Path, timeout_seconds: float) -> None:
    """Atomically retrieve one URL without leaking a partial final file."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    temporary_path: Path | None = None
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            with NamedTemporaryFile(
                mode="wb", prefix=f".{destination.name}.", suffix=".part",
                dir=destination.parent, delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                shutil.copyfileobj(response, temporary)
        if temporary_path.stat().st_size == 0:
            raise DatasetBuildError(f"download returned an empty file: {url}")
        os.replace(temporary_path, destination)
        temporary_path = None
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise DatasetBuildError(f"failed to download {url}: {error}") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _ensure_cached(
    url: str,
    destination: Path,
    *,
    refresh: bool,
    timeout_seconds: float,
    retrieve_url: RetrieveUrl,
) -> None:
    if refresh or not destination.is_file() or destination.stat().st_size == 0:
        retrieve_url(url, destination, timeout_seconds)


def _load_class_map(path: Path) -> dict[str, str]:
    classes: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        for row in csv.reader(source):
            if len(row) >= 2:
                classes[row[1].strip().casefold()] = row[0].strip()
    return classes


def _bool_field(row: Mapping[str, str], name: str) -> bool:
    return row.get(name, "0").strip() == "1"


def _parse_annotation(
    row: Mapping[str, str], display_names: Mapping[str, str]
) -> Annotation:
    image_id = row["ImageID"].strip().lower()
    if not _IMAGE_ID_RE.fullmatch(image_id):
        raise DatasetBuildError(f"invalid Open Images ImageID: {image_id!r}")
    try:
        annotation = Annotation(
            image_id=image_id,
            label_mid=row["LabelName"].strip(),
            display_name=display_names[row["LabelName"].strip()],
            confidence=float(row["Confidence"]),
            xmin=float(row["XMin"]),
            xmax=float(row["XMax"]),
            ymin=float(row["YMin"]),
            ymax=float(row["YMax"]),
            source=row.get("Source", "").strip(),
            is_occluded=_bool_field(row, "IsOccluded"),
            is_truncated=_bool_field(row, "IsTruncated"),
            is_group_of=_bool_field(row, "IsGroupOf"),
            is_depiction=_bool_field(row, "IsDepiction"),
            is_inside=_bool_field(row, "IsInside"),
        )
    except (KeyError, ValueError) as error:
        raise DatasetBuildError(f"invalid bounding-box annotation for {image_id}") from error
    if not (
        0 <= annotation.xmin < annotation.xmax <= 1
        and 0 <= annotation.ymin < annotation.ymax <= 1
    ):
        raise DatasetBuildError(f"out-of-range bounding box for {image_id}")
    return annotation


def load_matching_annotations(
    path: Path, requested: Mapping[str, str]
) -> dict[str, list[Annotation]]:
    """Read positive, individual object boxes for the requested exact classes."""

    mids_to_names = {mid: name for name, mid in requested.items()}
    by_image: dict[str, list[Annotation]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        for row in reader:
            mid = row.get("LabelName", "").strip()
            if mid not in mids_to_names or row.get("Confidence", "").strip() != "1":
                continue
            annotation = _parse_annotation(row, mids_to_names)
            if annotation.is_group_of or annotation.is_depiction or annotation.is_inside:
                continue
            by_image.setdefault(annotation.image_id, []).append(annotation)
    return by_image


def _safe_mask_name(value: str, image_id: str) -> str:
    """Return an official mask basename or reject archive/path traversal input."""

    normalized = value.strip()
    pure = PurePosixPath(normalized)
    if (
        not normalized
        or pure.is_absolute()
        or len(pure.parts) != 1
        or pure.name != normalized
        or not _MASK_NAME_RE.fullmatch(normalized)
        or not normalized.casefold().startswith(f"{image_id.casefold()}_")
    ):
        raise DatasetBuildError(f"unsafe segmentation MaskPath: {value!r}")
    return normalized


def load_segmentation_annotations(
    path: Path, requested_mids: Iterable[str]
) -> dict[str, list[SegmentationAnnotation]]:
    """Load requested instance masks and validate all archive-facing paths."""

    wanted = set(requested_mids)
    by_image: dict[str, list[SegmentationAnnotation]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            label_mid = row.get("LabelName", "").strip()
            if label_mid not in wanted:
                continue
            image_id = row.get("ImageID", "").strip().lower()
            if not _IMAGE_ID_RE.fullmatch(image_id):
                raise DatasetBuildError(
                    f"invalid segmentation ImageID: {image_id!r}"
                )
            mask_path = _safe_mask_name(row.get("MaskPath", ""), image_id)
            try:
                annotation = SegmentationAnnotation(
                    mask_path=mask_path,
                    image_id=image_id,
                    label_mid=label_mid,
                    box_id=row.get("BoxID", "").strip(),
                    xmin=float(row["BoxXMin"]),
                    xmax=float(row["BoxXMax"]),
                    ymin=float(row["BoxYMin"]),
                    ymax=float(row["BoxYMax"]),
                    predicted_iou=float(row.get("PredictedIoU", "0") or "0"),
                    clicks=row.get("Clicks", "").strip(),
                )
            except (KeyError, ValueError) as error:
                raise DatasetBuildError(
                    f"invalid segmentation annotation for {image_id}"
                ) from error
            if not (
                0 <= annotation.xmin < annotation.xmax <= 1
                and 0 <= annotation.ymin < annotation.ymax <= 1
                and 0 <= annotation.predicted_iou <= 1
            ):
                raise DatasetBuildError(
                    f"out-of-range segmentation annotation for {image_id}"
                )
            by_image.setdefault(image_id, []).append(annotation)
    return by_image


def _matching_segmentation(
    annotation: Annotation,
    segmentations: Sequence[SegmentationAnnotation],
) -> SegmentationAnnotation | None:
    matching = [
        item
        for item in segmentations
        if item.label_mid == annotation.label_mid
    ]
    if not matching:
        return None

    def bbox_distance(item: SegmentationAnnotation) -> float:
        return (
            abs(annotation.xmin - item.xmin)
            + abs(annotation.xmax - item.xmax)
            + abs(annotation.ymin - item.ymin)
            + abs(annotation.ymax - item.ymax)
        )

    best = min(matching, key=lambda item: (bbox_distance(item), -item.predicted_iou))
    # The mask annotation box is explicitly linked to the source object box.
    # A loose 1e-4 total tolerance accounts for CSV rounding only.
    if bbox_distance(best) > 0.0001:
        return None
    return best


def retain_masked_annotations(
    annotations_by_image: Mapping[str, Sequence[Annotation]],
    segmentations_by_image: Mapping[str, Sequence[SegmentationAnnotation]],
) -> dict[str, list[Annotation]]:
    retained: dict[str, list[Annotation]] = {}
    for image_id, annotations in annotations_by_image.items():
        segmentations = segmentations_by_image.get(image_id, ())
        masked = [
            item
            for item in annotations
            if _matching_segmentation(item, segmentations) is not None
        ]
        if masked:
            retained[image_id] = masked
    return retained


def _stable_rank(seed: str, display_name: str, image_id: str) -> str:
    value = f"{seed}\0{display_name.casefold()}\0{image_id}".encode()
    return hashlib.sha256(value).hexdigest()


def select_balanced_cases(
    annotations_by_image: Mapping[str, Sequence[Annotation]],
    classes: Sequence[str],
    *,
    limit_per_class: int,
    seed: str,
) -> list[SelectedCase]:
    """Select unique images deterministically and evenly across classes."""

    selected: list[SelectedCase] = []
    used_image_ids: set[str] = set()
    for display_name in classes:
        candidates: list[tuple[str, Annotation, tuple[Annotation, ...]]] = []
        for image_id, annotations in annotations_by_image.items():
            matching = [item for item in annotations if item.display_name == display_name]
            if not matching:
                continue
            primary = max(matching, key=lambda item: (item.area, item.label_mid))
            candidates.append((image_id, primary, tuple(annotations)))
        candidates.sort(key=lambda item: _stable_rank(seed, display_name, item[0]))
        class_count = 0
        for image_id, primary, all_annotations in candidates:
            if image_id in used_image_ids:
                continue
            used_image_ids.add(image_id)
            selected.append(SelectedCase(display_name, primary, all_annotations))
            class_count += 1
            if class_count == limit_per_class:
                break
        if class_count != limit_per_class:
            raise DatasetBuildError(
                f"class {display_name!r} has only {class_count} selectable images; "
                f"requested {limit_per_class}"
            )
    return selected


def load_image_metadata(
    path: Path, image_ids: Iterable[str]
) -> dict[str, dict[str, str]]:
    wanted = set(image_ids)
    found: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            image_id = row.get("ImageID", "").strip().lower()
            if image_id in wanted:
                found[image_id] = {key: value for key, value in row.items() if key}
    missing = sorted(wanted - found.keys())
    if missing:
        raise DatasetBuildError(
            "image metadata is missing for selected IDs: " + ", ".join(missing)
        )
    return found


def load_positive_label_image_ids(path: Path, label_mid: str) -> set[str]:
    """Load human-verified positive image labels for one exact class MID."""

    positive: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            if row.get("LabelName", "").strip() != label_mid:
                continue
            try:
                confidence = float(row.get("Confidence", ""))
            except ValueError as error:
                raise DatasetBuildError("invalid human image-label confidence") from error
            if confidence != 1.0:
                continue
            image_id = row.get("ImageID", "").strip().lower()
            if not _IMAGE_ID_RE.fullmatch(image_id):
                raise DatasetBuildError(f"invalid image-label ImageID: {image_id!r}")
            positive.add(image_id)
    return positive


def derive_expected_code(annotation: Annotation) -> tuple[str, str]:
    """Map one box to a finite front-shot code with an explicit rationale."""

    center_x, center_y = annotation.center
    touches_edge = (
        annotation.xmin <= 0.01
        or annotation.xmax >= 0.99
        or annotation.ymin <= 0.01
        or annotation.ymax >= 0.99
    )
    if annotation.is_occluded or annotation.is_truncated or touches_edge:
        result = (
            "SHOW_FULL_GARMENT",
            "bbox is occluded, truncated, or touches an image edge",
        )
    elif annotation.area < 0.08 or max(annotation.width, annotation.height) < 0.35:
        result = ("MOVE_CLOSER", "bbox occupies too little of the image")
    elif abs(center_x - 0.5) > 0.16 or abs(center_y - 0.5) > 0.18:
        result = ("CENTER_GARMENT", "bbox center is outside the central target band")
    elif annotation.area > 0.82:
        result = ("MOVE_FARTHER", "bbox occupies almost the entire image")
    else:
        result = ("READY", "bbox size and center pass the geometry heuristic")
    if result[0] not in FINITE_FRONT_CODES:
        raise AssertionError("derived a non-finite front-shot code")
    return result


def _image_dimensions_and_sha256(path: Path) -> tuple[int, int, str]:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
    except (OSError, ValueError) as error:
        raise DatasetBuildError(f"downloaded file is not a valid image: {path}") from error
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return width, height, digest


def _extract_mask_safely(
    archive_path: Path,
    annotation: SegmentationAnnotation,
    destination: Path,
) -> None:
    """Copy exactly one validated member without using ZipFile.extract()."""

    safe_name = _safe_mask_name(annotation.mask_path, annotation.image_id)
    temporary_path: Path | None = None
    try:
        with ZipFile(archive_path) as archive:
            try:
                info = archive.getinfo(safe_name)
            except KeyError as error:
                raise DatasetBuildError(
                    f"mask {safe_name!r} is missing from {archive_path.name}"
                ) from error
            if info.is_dir() or info.filename != safe_name:
                raise DatasetBuildError(f"invalid mask archive member: {info.filename!r}")
            if not 0 < info.file_size <= _MAX_MASK_UNCOMPRESSED_BYTES:
                raise DatasetBuildError(
                    f"mask archive member has unsafe size: {info.file_size}"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source:
                with NamedTemporaryFile(
                    mode="wb",
                    prefix=f".{destination.name}.",
                    suffix=".part",
                    dir=destination.parent,
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    shutil.copyfileobj(source, temporary)
        if temporary_path.stat().st_size != info.file_size:
            raise DatasetBuildError(f"mask size changed while extracting {safe_name}")
        os.replace(temporary_path, destination)
        temporary_path = None
    except (BadZipFile, OSError) as error:
        raise DatasetBuildError(f"invalid mask shard {archive_path}: {error}") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _validate_binary_mask(path: Path) -> tuple[int, int, str]:
    try:
        with Image.open(path) as mask:
            if mask.format != "PNG":
                raise DatasetBuildError(f"ground-truth mask is not PNG: {path}")
            mask.load()
            width, height = mask.size
            extrema = mask.convert("L").getextrema()
    except (OSError, ValueError) as error:
        raise DatasetBuildError(f"ground-truth mask is invalid: {path}") from error
    if width <= 0 or height <= 0 or extrema is None or extrema[1] == 0:
        raise DatasetBuildError(f"ground-truth mask has no foreground pixels: {path}")
    return width, height, hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_case(
    selected: SelectedCase,
    metadata: Mapping[str, str],
    *,
    split: str,
    image_path: Path,
    mask_path: Path | None = None,
) -> dict[str, object]:
    annotation = selected.annotation
    expected_code, rationale = derive_expected_code(annotation)
    pixel_url = f"{IMAGE_BUCKET_BASE_URL}/{split}/{annotation.image_id}.jpg"
    width, height, digest = _image_dimensions_and_sha256(image_path)
    license_url = metadata.get("License", "").strip()
    if not license_url:
        raise DatasetBuildError(f"license metadata is missing for {annotation.image_id}")
    source_url = (
        metadata.get("OriginalLandingURL", "").strip()
        or metadata.get("OriginalURL", "").strip()
    )
    if not source_url:
        raise DatasetBuildError(f"source URL is missing for {annotation.image_id}")
    relative_image = image_path.relative_to(image_path.parent.parent).as_posix()
    case: dict[str, object] = {
        "id": f"{split}-{annotation.image_id}-{selected.selection_class.casefold()}",
        "image": relative_image,
        "shot": "front",
        "expectedCode": expected_code,
        "reviewStatus": "unreviewed",
        "scope": "geometry_only",
        "source": {
            "dataset": DATASET_NAME,
            "datasetPage": DATASET_PAGE,
            "imageId": annotation.image_id,
            "split": split,
            "url": source_url,
            "originalUrl": metadata.get("OriginalURL", "").strip(),
            "pixelUrl": pixel_url,
            "licenseUrl": license_url,
            "author": metadata.get("Author", "").strip(),
            "authorProfileUrl": metadata.get("AuthorProfileURL", "").strip(),
            "title": metadata.get("Title", "").strip(),
            # Absence of a Person bbox is not proof that no human appears.  Open
            # Images annotations are not exhaustive, so retain that distinction.
            "personAnnotationPresent": False,
            "humanPresence": "unknown",
        },
        "file": {
            "sha256": digest,
            "width": width,
            "height": height,
        },
        "selectionClass": selected.selection_class,
        "originalAnnotation": annotation.as_manifest_value(),
        "matchingAnnotations": [item.as_manifest_value() for item in selected.all_annotations],
        "derivation": {
            "kind": "open_images_bbox_heuristic",
            "rationale": rationale,
            "bbox": annotation.as_manifest_value()["bbox"],
            "reviewRequired": True,
            "thresholds": {
                "edgeMargin": 0.01,
                "minimumArea": 0.08,
                "minimumMaxDimension": 0.35,
                "centerToleranceX": 0.16,
                "centerToleranceY": 0.18,
                "maximumArea": 0.82,
            },
        },
    }
    if selected.segmentation is not None:
        if mask_path is None:
            raise DatasetBuildError("selected segmentation is missing a local mask")
        mask_width, mask_height, mask_digest = _validate_binary_mask(mask_path)
        case["scope"] = "geometry_and_segmentation"
        case["groundTruthMask"] = mask_path.relative_to(
            mask_path.parent.parent
        ).as_posix()
        case["segmentationAnnotation"] = selected.segmentation.as_manifest_value()
        case["groundTruthMaskFile"] = {
            "sha256": mask_digest,
            "width": mask_width,
            "height": mask_height,
            "annotationLicenseUrl": ANNOTATION_LICENSE_URL,
        }
    return case


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=f".{path.name}.", suffix=".part",
            dir=path.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(value, temporary, ensure_ascii=False, indent=2, sort_keys=True)
            temporary.write("\n")
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def build_subset(
    config: BuildConfig,
    *,
    retrieve_url: RetrieveUrl = _retrieve_url,
    generated_at: str | None = None,
) -> Path:
    """Build the requested subset and return the absolute manifest path."""

    output_dir = config.output_dir.expanduser().resolve()
    cache_dir = output_dir / "_cache"
    images_dir = output_dir / "images"
    cache_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    class_path = cache_dir / "oidv7-class-descriptions-boxable.csv"
    bbox_path = cache_dir / f"{config.split}-annotations-bbox.csv"
    image_info_path = cache_dir / f"{config.split}-images-with-rotation.csv"
    for url, path in (
        (CLASS_DESCRIPTION_URL, class_path),
        (BBOX_URLS[config.split], bbox_path),
        (IMAGE_INFO_URLS[config.split], image_info_path),
    ):
        _ensure_cached(
            url,
            path,
            refresh=config.refresh_metadata,
            timeout_seconds=config.timeout_seconds,
            retrieve_url=retrieve_url,
        )
    segmentation_path: Path | None = None
    if config.require_segmentation:
        segmentation_path = cache_dir / f"{config.split}-object-segmentation.csv"
        _ensure_cached(
            SEGMENTATION_ANNOTATION_URLS[config.split],
            segmentation_path,
            refresh=config.refresh_metadata,
            timeout_seconds=config.timeout_seconds,
            retrieve_url=retrieve_url,
        )

    class_map = _load_class_map(class_path)
    requested: dict[str, str] = {}
    for raw_name in config.classes:
        display_name = raw_name.strip()
        mid = class_map.get(display_name.casefold())
        if mid is None:
            raise DatasetBuildError(f"unknown boxable class: {display_name!r}")
        requested[display_name] = mid

    scanned_labels = dict(requested)
    positive_person_image_ids: set[str] = set()
    if config.exclude_person:
        person_mid = class_map.get("person")
        if person_mid is None:
            raise DatasetBuildError("boxable class metadata does not contain Person")
        scanned_labels["Person"] = person_mid
        human_labels_path = cache_dir / f"{config.split}-human-imagelabels.csv"
        _ensure_cached(
            HUMAN_IMAGE_LABEL_URLS[config.split],
            human_labels_path,
            refresh=config.refresh_metadata,
            timeout_seconds=config.timeout_seconds,
            retrieve_url=retrieve_url,
        )
        positive_person_image_ids = load_positive_label_image_ids(
            human_labels_path, person_mid
        )
    by_image = load_matching_annotations(bbox_path, scanned_labels)
    person_filtered_count = 0
    person_image_label_filtered_count = 0
    if config.exclude_person:
        target_names = set(requested)
        filtered_by_image: dict[str, list[Annotation]] = {}
        for image_id, annotations in by_image.items():
            if any(item.display_name == "Person" for item in annotations):
                person_filtered_count += 1
                continue
            if image_id in positive_person_image_ids:
                person_image_label_filtered_count += 1
                continue
            targets = [item for item in annotations if item.display_name in target_names]
            if targets:
                filtered_by_image[image_id] = targets
        by_image = filtered_by_image
    segmentations_by_image: dict[str, list[SegmentationAnnotation]] = {}
    if config.require_segmentation:
        if segmentation_path is None:
            raise AssertionError("segmentation cache was not initialized")
        segmentations_by_image = load_segmentation_annotations(
            segmentation_path, requested.values()
        )
        by_image = retain_masked_annotations(by_image, segmentations_by_image)
    selected = select_balanced_cases(
        by_image,
        tuple(requested),
        limit_per_class=config.limit_per_class,
        seed=config.seed,
    )
    if config.require_segmentation:
        selected_with_masks: list[SelectedCase] = []
        for selected_case in selected:
            segmentation = _matching_segmentation(
                selected_case.annotation,
                segmentations_by_image.get(selected_case.annotation.image_id, ()),
            )
            if segmentation is None:
                raise AssertionError("masked selection lost its segmentation")
            selected_with_masks.append(
                replace(selected_case, segmentation=segmentation)
            )
        selected = selected_with_masks
    metadata = load_image_metadata(
        image_info_path, (case.annotation.image_id for case in selected)
    )

    cases: list[dict[str, object]] = []
    masks_dir = output_dir / "masks"
    for selected_case in selected:
        image_id = selected_case.annotation.image_id
        image_path = images_dir / f"{image_id}.jpg"
        pixel_url = f"{IMAGE_BUCKET_BASE_URL}/{config.split}/{image_id}.jpg"
        if not image_path.is_file() or image_path.stat().st_size == 0:
            retrieve_url(pixel_url, image_path, config.timeout_seconds)
        mask_path: Path | None = None
        if selected_case.segmentation is not None:
            segmentation = selected_case.segmentation
            shard = segmentation.image_id[0].lower()
            shard_url = MASK_SHARD_URL_TEMPLATE.format(shard=shard)
            shard_path = cache_dir / f"{config.split}-masks-{shard}.zip"
            _ensure_cached(
                shard_url,
                shard_path,
                refresh=config.refresh_metadata,
                timeout_seconds=config.timeout_seconds,
                retrieve_url=retrieve_url,
            )
            mask_path = masks_dir / segmentation.mask_path
            if not mask_path.is_file() or mask_path.stat().st_size == 0:
                _extract_mask_safely(shard_path, segmentation, mask_path)
        cases.append(
            _manifest_case(
                selected_case,
                metadata[image_id],
                split=config.split,
                image_path=image_path,
                mask_path=mask_path,
            )
        )

    cases.sort(key=lambda item: str(item["id"]))
    manifest = {
        "schemaVersion": 1,
        "dataset": {
            "name": DATASET_NAME,
            "downloadPage": DATASET_PAGE,
            "descriptionPage": DATASET_DESCRIPTION,
            "annotationLicenseUrl": ANNOTATION_LICENSE_URL,
            "imageLicenseNotice": (
                "Open Images lists images as CC BY 2.0 but disclaims warranties; "
                "verify each case's licenseUrl before redistribution or production use."
            ),
        },
        "generatedAt": generated_at or datetime.now(UTC).isoformat(),
        "selection": {
            "split": config.split,
            "classes": list(config.classes),
            "limitPerClass": config.limit_per_class,
            "seed": config.seed,
            "labels": "deterministic unreviewed bbox heuristics",
            "scope": (
                "geometry_and_segmentation"
                if config.require_segmentation
                else "geometry_only"
            ),
            "requireSegmentation": config.require_segmentation,
            "excludePerson": config.exclude_person,
            "personFilteredImageCount": person_filtered_count,
            "personPositiveImageLabelFilteredCount": (
                person_image_label_filtered_count
            ),
            "domainMismatch": (
                "Open Images is dominated by in-the-wild and worn garments, not "
                "the product's flat-lay front/back/tag capture domain."
            ),
        },
        "cases": cases,
    }
    manifest_path = output_dir / "manifest.json"
    _write_json_atomic(manifest_path, manifest)
    return manifest_path


def _parse_classes(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/evaluation/open_images_v7"),
        help="Git-ignored dataset directory (default: %(default)s)",
    )
    parser.add_argument("--split", choices=tuple(BBOX_URLS), default="validation")
    parser.add_argument(
        "--classes",
        type=_parse_classes,
        default=DEFAULT_CLASSES,
        help="comma-separated exact boxable class names",
    )
    parser.add_argument("--limit-per-class", type=int, default=5)
    parser.add_argument("--seed", default="team-d-open-images-v1")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--require-segmentation",
        action="store_true",
        help="validation only: require and download an official instance mask",
    )
    parser.add_argument(
        "--allow-person",
        action="store_true",
        help="allow images carrying a Person bounding-box annotation",
    )
    parser.add_argument("--refresh-metadata", action="store_true")
    parser.add_argument(
        "--acknowledge-license",
        action="store_true",
        help="required: acknowledge per-image license verification responsibility",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.acknowledge_license:
        print(
            "error: pass --acknowledge-license after reviewing Open Images' "
            "per-image license disclaimer",
            file=sys.stderr,
        )
        return 2
    try:
        config = BuildConfig(
            output_dir=args.output_dir,
            split=args.split,
            classes=args.classes,
            limit_per_class=args.limit_per_class,
            seed=args.seed,
            exclude_person=not args.allow_person,
            require_segmentation=args.require_segmentation,
            refresh_metadata=args.refresh_metadata,
            timeout_seconds=args.timeout_seconds,
        )
        manifest_path = build_subset(config)
    except (DatasetBuildError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"wrote {len(manifest['cases'])} cases to {manifest_path}")
    print("labels are unreviewed bbox heuristics; review before using as an accuracy gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
