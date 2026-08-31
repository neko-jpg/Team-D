"""Contract tests for the rembg prewarm helper."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest

from backend.providers.garment_masker import GarmentMaskContractError, GarmentMaskUnavailableError
from backend.rembg_prewarm import prewarm_rembg


# 2x2 PNG fixtures: an RGB front original and a non-empty/non-full L mask.
FRONT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP4z8DA8J+BkYHh////DAAe9gT9Ce00PgAAAABJRU5ErkJggg=="
)
MASK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAAAAABX3VL4AAAADklEQVR4nGNg+M/wnwEABgAB/4/x/JoAAAAASUVORK5CYII="
)
NON_PNG_RESPONSE = b"this is not a PNG image"
MISMATCHED_MASK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGNoAAAAggCBd81ytgAAAABJRU5ErkJggg=="
)
EMPTY_MASK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAAAAABX3VL4AAAAC0lEQVR4nGNgAAEAAAYAAf6MZ8gAAAAASUVORK5CYII="
)
FULL_MASK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAAAAABX3VL4AAAADklEQVR4nGP4/5/h/38AC/oD/eAlihAAAAAASUVORK5CYII="
)


@dataclass
class FakeResponse:
    content: bytes
    status_code: int = 200
    headers: Mapping[str, str] = field(default_factory=lambda: {"content-type": "image/png"})


@dataclass
class RequestSpyClient:
    response: FakeResponse
    calls: list[dict[str, object]] = field(default_factory=list)

    async def post(
        self,
        url: str,
        *,
        files: Mapping[str, object],
        data: Mapping[str, str],
        timeout: float,
    ) -> FakeResponse:
        self.calls.append({"url": url, "files": files, "data": data, "timeout": timeout})
        return self.response


@pytest.mark.skipif(importlib.util.find_spec("PIL") is None, reason="Pillow is not installed")
def test_prewarm_uses_production_rembg_request_and_returns_verified_mask() -> None:
    original = bytes(FRONT_PNG)
    client = RequestSpyClient(FakeResponse(MASK_PNG))

    mask = asyncio.run(prewarm_rembg(original, "image/png", client=client))

    assert original == FRONT_PNG
    assert mask.data == MASK_PNG
    assert (mask.width, mask.height, mask.mime_type) == (2, 2, "image/png")
    assert client.calls == [
        {
            "url": "http://127.0.0.1:7000/api/remove",
            "files": {"file": ("front", original, "image/png")},
            "data": {"model": "birefnet-general-lite", "om": "true"},
            "timeout": 35.0,
        }
    ]


@pytest.mark.skipif(importlib.util.find_spec("PIL") is None, reason="Pillow is not installed")
@pytest.mark.parametrize(
    ("invalid_mask", "message"),
    [
        (NON_PNG_RESPONSE, "decodable PNG"),
        (MISMATCHED_MASK_PNG, "dimensions must match"),
        (EMPTY_MASK_PNG, "empty mask"),
        (FULL_MASK_PNG, "full-image mask"),
    ],
)
def test_prewarm_rejects_invalid_sidecar_masks_without_mutating_or_falling_back(
    invalid_mask: bytes, message: str
) -> None:
    original = bytes(FRONT_PNG)
    client = RequestSpyClient(FakeResponse(invalid_mask))

    with pytest.raises(GarmentMaskContractError, match=message):
        asyncio.run(prewarm_rembg(original, "image/png", client=client))

    assert original == FRONT_PNG
    assert client.calls == [
        {
            "url": "http://127.0.0.1:7000/api/remove",
            "files": {"file": ("front", original, "image/png")},
            "data": {"model": "birefnet-general-lite", "om": "true"},
            "timeout": 35.0,
        }
    ]


@pytest.mark.skipif(importlib.util.find_spec("PIL") is not None, reason="requires Pillow to be absent")
def test_prewarm_reports_missing_image_runtime_without_calling_the_sidecar() -> None:
    client = RequestSpyClient(FakeResponse(MASK_PNG))

    with pytest.raises(GarmentMaskUnavailableError, match="Pillow"):
        asyncio.run(prewarm_rembg(FRONT_PNG, "image/png", client=client))

    assert client.calls == []
