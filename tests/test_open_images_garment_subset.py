"""Tests for the small, reproducible Open Images evaluation dataset builder."""

from __future__ import annotations

import csv
from io import BytesIO
import json
from pathlib import Path
import shutil
from zipfile import ZipFile

from PIL import Image
import pytest

from scripts.evaluation.open_images_garment_subset import (
    BBOX_URLS,
    CLASS_DESCRIPTION_URL,
    HUMAN_IMAGE_LABEL_URLS,
    IMAGE_BUCKET_BASE_URL,
    IMAGE_INFO_URLS,
    MASK_SHARD_URL_TEMPLATE,
    SEGMENTATION_ANNOTATION_URLS,
    BuildConfig,
    DatasetBuildError,
    build_subset,
    load_segmentation_annotations,
    main,
)


BOX_FIELDS = (
    "ImageID",
    "Source",
    "LabelName",
    "Confidence",
    "XMin",
    "XMax",
    "YMin",
    "YMax",
    "IsOccluded",
    "IsTruncated",
    "IsGroupOf",
    "IsDepiction",
    "IsInside",
)


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _box(
    image_id: str,
    mid: str,
    *,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    truncated: int = 0,
) -> dict[str, object]:
    return {
        "ImageID": image_id,
        "Source": "xclick",
        "LabelName": mid,
        "Confidence": 1,
        "XMin": xmin,
        "XMax": xmax,
        "YMin": ymin,
        "YMax": ymax,
        "IsOccluded": 0,
        "IsTruncated": truncated,
        "IsGroupOf": 0,
        "IsDepiction": 0,
        "IsInside": 0,
    }


def _fake_sources(tmp_path: Path) -> tuple[dict[str, Path], Path]:
    sources = tmp_path / "sources"
    classes = sources / "classes.csv"
    classes.parent.mkdir(parents=True)
    classes.write_text("/m/shirt,Shirt\n/m/person,Person\n", encoding="utf-8")
    boxes = sources / "boxes.csv"
    rows = [
        _box("0000000000000001", "/m/shirt", xmin=0.4, xmax=0.6, ymin=0.4, ymax=0.6),
        _box("0000000000000002", "/m/shirt", xmin=0.0, xmax=0.6, ymin=0.2, ymax=0.8, truncated=1),
        _box("0000000000000003", "/m/shirt", xmin=0.02, xmax=0.52, ymin=0.2, ymax=0.8),
        _box("0000000000000004", "/m/shirt", xmin=0.3, xmax=0.7, ymin=0.2, ymax=0.8),
        _box("0000000000000004", "/m/person", xmin=0.1, xmax=0.9, ymin=0.0, ymax=1.0),
    ]
    _write_csv(boxes, BOX_FIELDS, rows)
    image_info = sources / "images.csv"
    image_fields = (
        "ImageID",
        "Subset",
        "OriginalURL",
        "OriginalLandingURL",
        "License",
        "AuthorProfileURL",
        "Author",
        "Title",
        "OriginalSize",
        "OriginalMD5",
        "Thumbnail300KURL",
        "Rotation",
    )
    _write_csv(
        image_info,
        image_fields,
        [
            {
                "ImageID": f"{number:016x}",
                "Subset": "validation",
                "OriginalURL": f"https://images.example/{number}.jpg",
                "OriginalLandingURL": f"https://photos.example/{number}",
                "License": "https://creativecommons.org/licenses/by/2.0/",
                "AuthorProfileURL": "https://photos.example/author",
                "Author": "Example Author",
                "Title": f"Garment {number}",
                "OriginalSize": "123",
                "OriginalMD5": "unused",
                "Thumbnail300KURL": "",
                "Rotation": "0",
            }
            for number in range(1, 5)
        ],
    )
    image = sources / "image.jpg"
    Image.new("RGB", (48, 32), (120, 80, 50)).save(image, format="JPEG")
    human_labels = sources / "human-labels.csv"
    _write_csv(
        human_labels,
        ("ImageID", "Source", "LabelName", "Confidence"),
        [],
    )
    return {
        CLASS_DESCRIPTION_URL: classes,
        BBOX_URLS["validation"]: boxes,
        IMAGE_INFO_URLS["validation"]: image_info,
        HUMAN_IMAGE_LABEL_URLS["validation"]: human_labels,
    }, image


def _add_fake_segmentations(
    tmp_path: Path, sources: dict[str, Path]
) -> None:
    segmentation = tmp_path / "sources" / "segmentations.csv"
    fields = (
        "MaskPath",
        "ImageID",
        "LabelName",
        "BoxID",
        "BoxXMin",
        "BoxXMax",
        "BoxYMin",
        "BoxYMax",
        "PredictedIoU",
        "Clicks",
    )
    boxes = {
        1: (0.4, 0.6, 0.4, 0.6),
        2: (0.0, 0.6, 0.2, 0.8),
        3: (0.02, 0.52, 0.2, 0.8),
    }
    rows = []
    mask_names = []
    for number, (xmin, xmax, ymin, ymax) in boxes.items():
        image_id = f"{number:016x}"
        mask_name = f"{image_id}_mshirt_abcd{number}.png"
        mask_names.append(mask_name)
        rows.append(
            {
                "MaskPath": mask_name,
                "ImageID": image_id,
                "LabelName": "/m/shirt",
                "BoxID": f"abcd{number}",
                "BoxXMin": xmin,
                "BoxXMax": xmax,
                "BoxYMin": ymin,
                "BoxYMax": ymax,
                "PredictedIoU": 0.95,
                "Clicks": "",
            }
        )
    _write_csv(segmentation, fields, rows)
    archive_path = tmp_path / "sources" / "validation-masks-0.zip"
    with ZipFile(archive_path, "w") as archive:
        for mask_name in mask_names:
            encoded = BytesIO()
            Image.new("L", (24, 16), 255).save(encoded, format="PNG")
            archive.writestr(mask_name, encoded.getvalue())
    sources[SEGMENTATION_ANNOTATION_URLS["validation"]] = segmentation
    sources[MASK_SHARD_URL_TEMPLATE.format(shard="0")] = archive_path


def test_build_subset_writes_attributable_finite_manifest(tmp_path: Path) -> None:
    sources, fake_image = _fake_sources(tmp_path)
    requested_urls: list[str] = []

    def retrieve(url: str, destination: Path, timeout: float) -> None:
        requested_urls.append(url)
        source = sources.get(url, fake_image)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    output_dir = tmp_path / "dataset"
    manifest_path = build_subset(
        BuildConfig(
            output_dir=output_dir,
            classes=("Shirt",),
            limit_per_class=3,
            seed="fixed",
        ),
        retrieve_url=retrieve,
        generated_at="2026-09-01T00:00:00+00:00",
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schemaVersion"] == 1
    assert manifest["generatedAt"] == "2026-09-01T00:00:00+00:00"
    assert len(manifest["cases"]) == 3
    assert manifest["selection"]["scope"] == "geometry_only"
    assert manifest["selection"]["personFilteredImageCount"] == 1
    assert {case["expectedCode"] for case in manifest["cases"]} == {
        "MOVE_CLOSER",
        "SHOW_FULL_GARMENT",
        "CENTER_GARMENT",
    }
    for case in manifest["cases"]:
        assert case["image"].startswith("images/")
        assert (output_dir / case["image"]).is_file()
        assert case["shot"] == "front"
        assert case["reviewStatus"] == "unreviewed"
        assert case["scope"] == "geometry_only"
        assert case["source"]["personAnnotationPresent"] is False
        assert case["source"]["humanPresence"] == "unknown"
        assert case["source"]["licenseUrl"].endswith("/by/2.0/")
        assert case["source"]["url"].startswith("https://photos.example/")
        assert case["source"]["pixelUrl"].startswith(IMAGE_BUCKET_BASE_URL)
        assert case["originalAnnotation"]["bbox"]
        assert case["derivation"]["reviewRequired"] is True
        assert len(case["file"]["sha256"]) == 64
    assert len([url for url in requested_urls if url.startswith(IMAGE_BUCKET_BASE_URL)]) == 3
    assert SEGMENTATION_ANNOTATION_URLS["validation"] not in requested_urls


def test_build_is_reproducible_and_reuses_cached_files(tmp_path: Path) -> None:
    sources, fake_image = _fake_sources(tmp_path)
    calls = 0

    def retrieve(url: str, destination: Path, timeout: float) -> None:
        nonlocal calls
        calls += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sources.get(url, fake_image), destination)

    config = BuildConfig(
        output_dir=tmp_path / "dataset",
        classes=("Shirt",),
        limit_per_class=2,
        seed="fixed",
    )
    first = build_subset(config, retrieve_url=retrieve, generated_at="fixed")
    first_value = json.loads(first.read_text(encoding="utf-8"))
    first_calls = calls
    second = build_subset(config, retrieve_url=retrieve, generated_at="fixed")
    second_value = json.loads(second.read_text(encoding="utf-8"))

    assert second_value == first_value
    assert calls == first_calls


def test_require_segmentation_downloads_only_needed_shard_and_records_gt_mask(
    tmp_path: Path,
) -> None:
    sources, fake_image = _fake_sources(tmp_path)
    _add_fake_segmentations(tmp_path, sources)
    requested_urls: list[str] = []

    def retrieve(url: str, destination: Path, timeout: float) -> None:
        requested_urls.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sources.get(url, fake_image), destination)

    output_dir = tmp_path / "segmented"
    manifest_path = build_subset(
        BuildConfig(
            output_dir=output_dir,
            classes=("Shirt",),
            limit_per_class=3,
            seed="fixed",
            require_segmentation=True,
        ),
        retrieve_url=retrieve,
        generated_at="fixed",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["selection"]["requireSegmentation"] is True
    assert manifest["selection"]["scope"] == "geometry_and_segmentation"
    assert requested_urls.count(MASK_SHARD_URL_TEMPLATE.format(shard="0")) == 1
    for case in manifest["cases"]:
        assert case["scope"] == "geometry_and_segmentation"
        assert case["groundTruthMask"].startswith("masks/")
        assert (output_dir / case["groundTruthMask"]).is_file()
        assert case["segmentationAnnotation"]["predictedIoU"] == 0.95
        assert case["groundTruthMaskFile"]["width"] == 24
        assert len(case["groundTruthMaskFile"]["sha256"]) == 64


def test_segmentation_loader_rejects_path_traversal(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.csv"
    _write_csv(
        path,
        (
            "MaskPath",
            "ImageID",
            "LabelName",
            "BoxID",
            "BoxXMin",
            "BoxXMax",
            "BoxYMin",
            "BoxYMax",
            "PredictedIoU",
            "Clicks",
        ),
        [
            {
                "MaskPath": "../outside.png",
                "ImageID": "0000000000000001",
                "LabelName": "/m/shirt",
                "BoxID": "bad",
                "BoxXMin": 0.1,
                "BoxXMax": 0.9,
                "BoxYMin": 0.1,
                "BoxYMax": 0.9,
                "PredictedIoU": 0.9,
                "Clicks": "",
            }
        ],
    )

    with pytest.raises(DatasetBuildError, match="unsafe segmentation MaskPath"):
        load_segmentation_annotations(path, {"/m/shirt"})


def test_positive_person_image_labels_are_excluded(tmp_path: Path) -> None:
    sources, fake_image = _fake_sources(tmp_path)
    _write_csv(
        sources[HUMAN_IMAGE_LABEL_URLS["validation"]],
        ("ImageID", "Source", "LabelName", "Confidence"),
        [
            {
                "ImageID": f"{number:016x}",
                "Source": "verification",
                "LabelName": "/m/person",
                "Confidence": 1,
            }
            for number in range(1, 4)
        ],
    )

    def retrieve(url: str, destination: Path, timeout: float) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sources.get(url, fake_image), destination)

    with pytest.raises(DatasetBuildError, match="only 0 selectable images"):
        build_subset(
            BuildConfig(
                output_dir=tmp_path / "person-positive",
                classes=("Shirt",),
                limit_per_class=1,
            ),
            retrieve_url=retrieve,
        )


def test_build_rejects_unknown_class_and_missing_license(tmp_path: Path) -> None:
    sources, fake_image = _fake_sources(tmp_path)

    def retrieve(url: str, destination: Path, timeout: float) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sources.get(url, fake_image), destination)

    with pytest.raises(DatasetBuildError, match="unknown boxable class"):
        build_subset(
            BuildConfig(
                output_dir=tmp_path / "unknown",
                classes=("Not a garment",),
                limit_per_class=1,
            ),
            retrieve_url=retrieve,
        )

    image_info = sources[IMAGE_INFO_URLS["validation"]]
    contents = image_info.read_text(encoding="utf-8")
    image_info.write_text(
        contents.replace("https://creativecommons.org/licenses/by/2.0/", ""),
        encoding="utf-8",
    )
    with pytest.raises(DatasetBuildError, match="license metadata is missing"):
        build_subset(
            BuildConfig(
                output_dir=tmp_path / "missing-license",
                classes=("Shirt",),
                limit_per_class=1,
            ),
            retrieve_url=retrieve,
        )


def test_cli_requires_explicit_license_acknowledgement(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "--acknowledge-license" in capsys.readouterr().err
