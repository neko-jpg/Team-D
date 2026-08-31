"""Contract tests for the rembg prewarm helper."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest

from backend.providers.garment_masker import (
    GarmentMaskUnavailableError,
    REMBG_MODEL,
    REMBG_REMOVE_URL,
    REMBG_TIMEOUT_SECONDS,
)
from backend.rembg_prewarm import prewarm_rembg


# 2x2 PNG fixtures: an RGB front original and a non-empty/non-full L mask.
FRONT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP4z8DA8J+BkYHh////DAAe9gT9Ce00PgAAAABJRU5ErkJggg=="
)
MASK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAAAAABX3VL4AAAADklEQVR4nGNg+M/wnwEABgAB/4/x/JoAAAAASUVORK5CYII="
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
            "url": REMBG_REMOVE_URL,
            "files": {"file": ("front", original, "image/png")},
            "data": {"model": REMBG_MODEL, "om": "true"},
            "timeout": REMBG_TIMEOUT_SECONDS,
        }
    ]


@pytest.mark.skipif(importlib.util.find_spec("PIL") is not None, reason="requires Pillow to be absent")
def test_prewarm_reports_missing_image_runtime_without_calling_the_sidecar() -> None:
    client = RequestSpyClient(FakeResponse(MASK_PNG))

    with pytest.raises(GarmentMaskUnavailableError, match="Pillow"):
        asyncio.run(prewarm_rembg(FRONT_PNG, "image/png", client=client))

    assert client.calls == []
