"""Deterministic backend-only fixture E2E for the complete provider slice.

The browser, reducer, and Canvas composition deliberately stay outside this
test.  The test follows the Python boundary in production order: Agent
guidance transport, post-capture assessment, measurement endpoint suggestion,
front masking, and text-only background generation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from backend.app import create_app
from backend.guidance_transport import GuidanceTransportAdapter
from backend.providers.background_generator import FixtureBackgroundGenerator
from backend.providers.garment_masker import GarmentMask, GarmentMaskInput
from backend.providers.runtime import (
    FixtureVisionGuidanceProvider,
    create_provider_inference,
)
from backend.providers.vision_guidance import EncodedImage, GuidanceShot
from backend.remove_background import get_garment_masker
from backend.settings import BackendSettings


def _png(
    *,
    mode: str = "RGB",
    size: tuple[int, int] = (8, 8),
    color: object = (72, 104, 136),
) -> bytes:
    output = BytesIO()
    Image.new(mode, size, color).save(output, format="PNG")
    return output.getvalue()


def _mask_png() -> bytes:
    output = BytesIO()
    image = Image.new("L", (8, 8), 0)
    for y in range(2, 6):
        for x in range(2, 6):
            image.putpixel((x, y), 255)
    image.save(output, format="PNG")
    return output.getvalue()


@dataclass
class _FixtureMasker:
    requests: list[GarmentMaskInput] = field(default_factory=list)

    async def mask(self, front: GarmentMaskInput) -> GarmentMask:
        self.requests.append(front)
        return GarmentMask(data=_mask_png(), width=8, height=8)


@dataclass
class _Publisher:
    packets: list[dict[str, object]] = field(default_factory=list)

    def publish_data(self, payload: bytes, *, reliable: bool) -> None:
        self.packets.append(
            {
                "reliable": reliable,
                "payload": json.loads(payload.decode("utf-8")),
            }
        )


def _response_contract(response: Any) -> dict[str, object]:
    payload = response.json()
    json.dumps(payload, allow_nan=False)
    return {
        "status": response.status_code,
        "cacheControl": response.headers.get("cache-control"),
        "body": payload,
    }


async def _run_fixture_backend_e2e() -> dict[str, object]:
    front = _png()
    projected_measurement = _png(color=(116, 84, 52))
    publisher = _Publisher()
    guidance = GuidanceTransportAdapter(
        create_provider_inference(FixtureVisionGuidanceProvider()),
        publisher,
        session_id="fixture-backend-e2e",
    )
    event = await guidance.process_frame(
        EncodedImage(front, "image/png", width=8, height=8),
        shot=GuidanceShot.FRONT,
        observed_at=1_000,
    )
    await guidance.close()

    assert event is not None
    assert [packet["reliable"] for packet in publisher.packets] == [True, False]
    assert publisher.packets[-1]["payload"] == event.to_payload()

    masker = _FixtureMasker()
    app = create_app(BackendSettings(provider_mode="fixture"))
    app.dependency_overrides[get_garment_masker] = lambda: masker
    with TestClient(app) as client:
        assessments = []
        for shot in ("front", "back", "tag"):
            response = client.post(
                "/api/analyze-shot",
                data={"requestedShot": shot},
                files={"file": (f"{shot}.png", front, "image/png")},
            )
            assessments.append(_response_contract(response))

        measurement = client.post(
            "/api/suggest-measurement-points",
            files={
                "file": (
                    "projected-measurement.png",
                    projected_measurement,
                    "image/png",
                )
            },
        )
        mask = client.post(
            "/api/remove-background",
            files={"file": ("front.png", front, "image/png")},
        )
        finite_error = client.post(
            "/api/suggest-measurement-points",
            files={"file": ("measurement.gif", b"not-a-gif", "image/gif")},
        )

    assert len(masker.requests) == 1
    assert masker.requests[0].data == front
    assert masker.requests[0].mime_type == "image/png"

    background = await FixtureBackgroundGenerator().generate("studio-white")
    error_contract = _response_contract(finite_error)
    assert error_contract["status"] == 415
    assert error_contract["body"] == {
        "detail": {
            "provider": "measurement-line",
            "code": "INVALID_INPUT",
            "message": "Unsupported image MIME type",
            "retryable": False,
        }
    }

    return {
        "guidancePackets": publisher.packets,
        "assessmentResponses": assessments,
        "measurementResponse": _response_contract(measurement),
        "maskResponse": {
            "status": mask.status_code,
            "cacheControl": mask.headers.get("cache-control"),
            "contentType": mask.headers.get("content-type"),
            "sha256": hashlib.sha256(mask.content).hexdigest(),
        },
        "backgroundResponse": {
            "mimeType": background.mime_type,
            "width": background.width,
            "height": background.height,
            "sha256": hashlib.sha256(background.data).hexdigest(),
        },
        "finiteError": error_contract,
    }


def test_fixture_backend_e2e_is_identical_across_two_consecutive_runs() -> None:
    first = asyncio.run(_run_fixture_backend_e2e())
    second = asyncio.run(_run_fixture_backend_e2e())

    assert first == second
    assert first["guidancePackets"][-1]["payload"]["code"] == "READY"
    assert [response["body"]["shotType"] for response in first["assessmentResponses"]] == [
        "front",
        "back",
        "tag",
    ]
    assert first["measurementResponse"]["body"] == {
        "lengthStart": {"x": 0.5, "y": 0.16},
        "lengthEnd": {"x": 0.5, "y": 0.88},
        "widthStart": {"x": 0.24, "y": 0.36},
        "widthEnd": {"x": 0.76, "y": 0.36},
    }
    assert first["maskResponse"]["status"] == 200
    assert first["backgroundResponse"]["mimeType"] == "image/png"
