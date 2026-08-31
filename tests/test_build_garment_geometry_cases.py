"""Tests for deterministic garment geometry evaluation cases."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from scripts.evaluation.build_garment_geometry_cases import (
    BACKGROUND_RGB,
    GeometryDatasetError,
    build_geometry_dataset,
)


def _source_manifest(tmp_path: Path, *, second_invalid: bool = False) -> Path:
    source = tmp_path / "source"
    images = source / "images"
    masks = source / "masks"
    images.mkdir(parents=True)
    masks.mkdir(parents=True)

    image = Image.new("RGB", (100, 160), (20, 30, 40))
    draw = ImageDraw.Draw(image)
    draw.rectangle((25, 10, 75, 150), fill=(210, 40, 30))
    image.save(images / "garment.jpg", format="JPEG", quality=100)
    mask = Image.new("L", (200, 320), 0)
    ImageDraw.Draw(mask).rectangle((50, 20, 150, 300), fill=255)
    mask.save(masks / "garment.png", format="PNG")

    cases = [
        {
            "id": "source-case",
            "image": "images/garment.jpg",
            "groundTruthMask": "masks/garment.png",
            "selectionClass": "Shirt",
            "reviewStatus": "unreviewed",
            "originalAnnotation": {
                "bbox": {"xmin": 0.2, "ymin": 0.1, "xmax": 0.8, "ymax": 0.9},
                "flags": {
                    "isOccluded": False,
                    "isTruncated": False,
                    "isGroupOf": False,
                    "isDepiction": False,
                    "isInside": False,
                },
            },
            "source": {
                "dataset": "Open Images V7",
                "url": "https://example.test/photo",
                "licenseUrl": "https://creativecommons.org/licenses/by/2.0/",
                "author": "Example Author",
                "title": "Example garment",
            },
        }
    ]
    if second_invalid:
        (masks / "empty.png").write_bytes(b"not a PNG")
        cases.append(
            {
                **cases[0],
                "id": "invalid-case",
                "groundTruthMask": "masks/empty.png",
            }
        )
    manifest = {
        "schemaVersion": 1,
        "dataset": {"name": "fixture"},
        "cases": cases,
    }
    path = source / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_builds_four_negative_geometry_cases_with_attribution(
    tmp_path: Path,
) -> None:
    input_manifest = _source_manifest(tmp_path)
    output = tmp_path / "output"
    manifest_path = build_geometry_dataset(
        input_manifest,
        output,
        generated_at="2026-09-01T00:00:00+00:00",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["schemaVersion"] == 1
    assert str(tmp_path) not in json.dumps(manifest)
    assert manifest["selection"]["caseCount"] == 4
    assert manifest["selection"]["readyCasesIncluded"] is False
    assert {case["expectedCode"] for case in manifest["cases"]} == {
        "MOVE_CLOSER",
        "CENTER_GARMENT",
        "SHOW_FULL_GARMENT",
        "MOVE_FARTHER",
    }
    by_code = {case["expectedCode"]: case for case in manifest["cases"]}
    closer = by_code["MOVE_CLOSER"]["transform"]["geometry"]
    assert 0.25 <= closer["span"] <= 0.30
    centered = by_code["CENTER_GARMENT"]["transform"]["geometry"]
    assert 0.23 <= centered["center"]["x"] <= 0.31
    clipped = by_code["SHOW_FULL_GARMENT"]["transform"]["geometry"]
    assert clipped["margins"]["left"] == 0
    assert clipped["clipFraction"] >= 0.08
    farther = by_code["MOVE_FARTHER"]["transform"]["geometry"]
    assert 0.90 <= farther["span"] <= 0.94

    for case in manifest["cases"]:
        assert case["shot"] == "front"
        assert case["scope"] == "geometry_transformed"
        assert case["mustNotReturn"]
        assert case["sourceCaseId"] == "source-case"
        assert case["source"]["licenseUrl"].endswith("/by/2.0/")
        assert len(case["source"]["derivedFromManifestSha256"]) == 64
        with (
            Image.open(output / case["image"]) as generated,
            Image.open(output / case["groundTruthMask"]) as generated_mask,
        ):
            assert generated.size == (512, 512)
            assert generated.mode == "RGB"
            assert generated_mask.size == generated.size
            assert generated_mask.mode == "L"
            histogram = generated_mask.histogram()
            assert histogram[0] + histogram[255] == 512 * 512
            foreground = histogram[255]
            assert 0 < foreground < 512 * 512
            if case["expectedCode"] != "SHOW_FULL_GARMENT":
                assert generated.getpixel((0, 0)) == BACKGROUND_RGB


def test_refuses_nonempty_output_without_explicit_overwrite(tmp_path: Path) -> None:
    input_manifest = _source_manifest(tmp_path)
    output = tmp_path / "output"
    build_geometry_dataset(input_manifest, output, generated_at="fixed")

    with pytest.raises(GeometryDatasetError, match="pass --overwrite"):
        build_geometry_dataset(input_manifest, output, generated_at="fixed")

    second = build_geometry_dataset(
        input_manifest, output, overwrite=True, generated_at="fixed"
    )
    assert second.is_file()


def test_rejects_unsafe_source_path(tmp_path: Path) -> None:
    path = _source_manifest(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["cases"][0]["groundTruthMask"] = "../outside.png"
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(GeometryDatasetError, match="groundTruthMask is unsafe"):
        build_geometry_dataset(path, tmp_path / "output")


def test_validates_every_source_before_creating_output(tmp_path: Path) -> None:
    path = _source_manifest(tmp_path, second_invalid=True)
    output = tmp_path / "output"

    with pytest.raises(GeometryDatasetError, match="cannot decode source pixels"):
        build_geometry_dataset(path, output)
    assert not output.exists()
