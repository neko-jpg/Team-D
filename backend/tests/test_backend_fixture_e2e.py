"""Deterministic backend-only E2E for the fixture provider path.

The scenario deliberately uses the real Agent runtime/transport and FastAPI
routes while replacing only external AI/rembg boundaries with in-memory
fixtures.  Running the whole flow twice proves that events, HTTP responses,
finite errors, provider requests, and binary outputs do not depend on hidden
session state.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from backend import agent
from backend.app import create_app
from backend.live_agent import entrypoint
from backend.providers.background_generator import (
    BackgroundGenerationContractError,
    FixtureBackgroundGenerator,
)
from backend.providers.garment_masker import (
    REMBG_MODEL,
    REMBG_REMOVE_URL,
    REMBG_TIMEOUT_SECONDS,
    GarmentMasker,
)
from backend.providers.runtime import create_provider_inference
from backend.providers.vision_guidance import EncodedImage
from backend.remove_background import get_garment_masker
from backend.settings import BackendSettings, ProviderMode


IMAGE_SIZE = (7, 5)
MEASUREMENT_ENDPOINTS: dict[str, object] = {
    "lengthStart": {"x": 0.50, "y": 0.16},
    "lengthEnd": {"x": 0.50, "y": 0.88},
    "widthStart": {"x": 0.24, "y": 0.36},
    "widthEnd": {"x": 0.76, "y": 0.36},
}


def _png_image() -> bytes:
    output = BytesIO()
    Image.new("RGB", IMAGE_SIZE, (48, 112, 176)).save(output, format="PNG")
    return output.getvalue()


def _mask_png() -> bytes:
    output = BytesIO()
    mask = Image.new("L", IMAGE_SIZE, 0)
    mask.paste(255, (1, 1, 6, 4))
    mask.save(output, format="PNG")
    return output.getvalue()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(slots=True)
class FixturePublisher:
    calls: list[dict[str, object]] = field(default_factory=list)

    def publish_data(
        self,
        payload: bytes,
        *,
        reliable: bool = True,
    ) -> None:
        decoded = json.loads(payload)
        assert isinstance(decoded, dict)
        self.calls.append({"payload": decoded, "reliable": reliable})


@dataclass(slots=True)
class FixtureRoom:
    local_participant: FixturePublisher
    name: str = "backend-fixture-e2e"
    remote_participants: list[object] = field(default_factory=list)
    handlers: dict[str, Any] = field(default_factory=dict)

    def on(self, event: str, callback: Any) -> None:
        self.handlers[event] = callback


class FixtureAgentContext:
    def __init__(self, room: FixtureRoom) -> None:
        self.room = room
        self.shutdown_callbacks: list[Any] = []

    async def connect(self, **_kwargs: Any) -> None:
        return None

    def add_shutdown_callback(self, callback: Any) -> None:
        self.shutdown_callbacks.append(callback)


@dataclass(frozen=True, slots=True)
class FixtureRembgResponse:
    content: bytes
    status_code: int = 200
    headers: Mapping[str, str] = field(
        default_factory=lambda: {"content-type": "image/png"}
    )


@dataclass(slots=True)
class FixtureRembgClient:
    response: FixtureRembgResponse
    calls: list[dict[str, object]] = field(default_factory=list)

    async def post(
        self,
        url: str,
        *,
        files: Mapping[str, object],
        data: Mapping[str, str],
        timeout: float,
    ) -> FixtureRembgResponse:
        file_part = files.get("file")
        assert isinstance(file_part, tuple) and len(file_part) == 3
        filename, content, mime_type = file_part
        assert isinstance(filename, str)
        assert isinstance(content, bytes)
        assert isinstance(mime_type, str)
        self.calls.append(
            {
                "url": url,
                "file": {
                    "name": filename,
                    "mimeType": mime_type,
                    "sha256": _sha256(content),
                },
                "data": dict(data),
                "timeout": timeout,
            }
        )
        return self.response


async def _run_agent_guidance(image: bytes) -> dict[str, object]:
    settings = BackendSettings(provider_mode=ProviderMode.FIXTURE)
    provider = agent.build_runtime_provider(settings)
    publisher = FixturePublisher()
    runtime = await entrypoint(
        FixtureAgentContext(FixtureRoom(publisher)),
        inference=create_provider_inference(provider),
        transport_factory=agent.build_transport_factory(
            provider,
            process_epoch="backend-fixture-e2e-process",
        ),
        observation_clock=lambda: 1_000,
    )
    processor = runtime.subscriber.processor
    try:
        accepted = processor.submit_nowait(
            EncodedImage(
                data=image,
                mime_type="image/png",
                width=IMAGE_SIZE[0],
                height=IMAGE_SIZE[1],
            )
        )
        await asyncio.wait_for(processor.wait_idle(), timeout=1)
        before_close = {
            "accepted": accepted,
            "published": publisher.calls,
            "processedCount": processor.processed_count,
            "errorCount": processor.error_count,
            "currentShot": runtime.current_shot,
        }
    finally:
        await asyncio.wait_for(runtime.close(), timeout=1)
    return {
        **before_close,
        "closed": runtime.closed,
        "pendingCount": processor.pending_count,
        "inFlight": processor.in_flight,
        "workerStopped": processor.worker_task is None,
    }


async def _run_background_generation() -> dict[str, object]:
    generator = FixtureBackgroundGenerator()
    generated = await generator.generate("studio-white")
    try:
        await generator.generate("unknown-style")
    except BackgroundGenerationContractError as error:
        finite_error = {
            "type": type(error).__name__,
            "message": str(error),
        }
    else:  # pragma: no cover - the fixture must keep rejecting unknown styles
        raise AssertionError("unknown background style unexpectedly succeeded")
    return {
        "status": "ok",
        "mimeType": generated.mime_type,
        "width": generated.width,
        "height": generated.height,
        "sha256": _sha256(generated.data),
        "error": finite_error,
    }


def _json_response(response: Any) -> dict[str, object]:
    return {
        "status": response.status_code,
        "contentType": response.headers.get("content-type"),
        "cacheControl": response.headers.get("cache-control"),
        "body": response.json(),
    }


def _run_fixture_flow_once() -> dict[str, object]:
    image = _png_image()
    mask = _mask_png()
    agent_snapshot = asyncio.run(_run_agent_guidance(image))

    rembg_client = FixtureRembgClient(FixtureRembgResponse(mask))
    masker = GarmentMasker(rembg_client)
    app = create_app(BackendSettings(provider_mode=ProviderMode.FIXTURE))
    app.dependency_overrides[get_garment_masker] = lambda: masker

    with TestClient(app) as client:
        assessments: list[dict[str, object]] = []
        for shot in ("front", "back", "tag"):
            response = client.post(
                "/api/analyze-shot",
                data={"requestedShot": shot},
                files={"file": (f"{shot}.png", image, "image/png")},
            )
            assessments.append(_json_response(response))

        measurement_response = client.post(
            "/api/suggest-measurement-points",
            files={"file": ("measurement.png", image, "image/png")},
        )
        mask_response = client.post(
            "/api/remove-background",
            files={"file": ("front.png", image, "image/png")},
        )
        background_snapshot = asyncio.run(_run_background_generation())

        finite_errors = {
            "analyzeShot": _json_response(
                client.post(
                    "/api/analyze-shot",
                    data={"requestedShot": "front"},
                    files={"file": ("broken.png", b"not-an-image", "image/png")},
                )
            ),
            "measurementPoints": _json_response(
                client.post(
                    "/api/suggest-measurement-points",
                    files={"file": ("broken.png", b"not-an-image", "image/png")},
                )
            ),
            "removeBackground": _json_response(
                client.post(
                    "/api/remove-background",
                    files={"file": ("broken.png", b"not-an-image", "image/png")},
                )
            ),
        }

    return {
        "agent": agent_snapshot,
        "analyzeShot": assessments,
        "measurementPoints": _json_response(measurement_response),
        "removeBackground": {
            "status": mask_response.status_code,
            "contentType": mask_response.headers.get("content-type"),
            "cacheControl": mask_response.headers.get("cache-control"),
            "noSniff": mask_response.headers.get("x-content-type-options"),
            "sha256": _sha256(mask_response.content),
        },
        "background": background_snapshot,
        "errors": finite_errors,
        "providerRequests": {
            "rembg": rembg_client.calls,
        },
    }


def test_fixture_backend_e2e_is_identical_across_two_consecutive_runs() -> None:
    first = _run_fixture_flow_once()
    second = _run_fixture_flow_once()

    assert second == first
    json.dumps(first, allow_nan=False, sort_keys=True)

    agent_result = first["agent"]
    assert isinstance(agent_result, dict)
    published = agent_result["published"]
    assert isinstance(published, list)
    assert published == [
        {
            "payload": {
                "type": "shot_changed",
                "sessionId": "backend-fixture-e2e",
                "sequence": 1,
                "shot": "front",
                "code": None,
                "observedAt": 1_000,
                "processEpoch": "backend-fixture-e2e-process",
            },
            "reliable": True,
        },
        {
            "payload": {
                "sessionId": "backend-fixture-e2e",
                "sequence": 2,
                "shot": "front",
                "code": "READY",
                "message": "撮影できます。",
                "confidence": 1.0,
                "observedAt": 1_000,
                "expiresAt": 3_000,
                "processEpoch": "backend-fixture-e2e-process",
            },
            "reliable": False,
        },
    ]
    assert agent_result == {
        "accepted": True,
        "published": published,
        "processedCount": 1,
        "errorCount": 0,
        "currentShot": "front",
        "closed": True,
        "pendingCount": 0,
        "inFlight": 0,
        "workerStopped": True,
    }

    assessments = first["analyzeShot"]
    assert [item["status"] for item in assessments] == [200, 200, 200]
    assert [item["body"]["shotType"] for item in assessments] == [
        "front",
        "back",
        "tag",
    ]
    assert [item["body"] for item in assessments] == [
        {
            "shotType": "front",
            "quality": "ok",
            "issues": [],
            "missingShots": ["back", "tag"],
            "nextAction": "REQUEST_NEXT",
        },
        {
            "shotType": "back",
            "quality": "ok",
            "issues": [],
            "missingShots": ["tag"],
            "nextAction": "REQUEST_NEXT",
        },
        {
            "shotType": "tag",
            "quality": "ok",
            "issues": [],
            "missingShots": [],
            "nextAction": "COMPLETE",
        },
    ]
    assert all(item["cacheControl"] == "no-store" for item in assessments)
    assert all(
        str(item["contentType"]).startswith("application/json")
        for item in assessments
    )
    assert first["measurementPoints"]["status"] == 200
    assert first["measurementPoints"]["body"] == MEASUREMENT_ENDPOINTS
    assert first["measurementPoints"]["cacheControl"] == "no-store"
    assert first["removeBackground"]["status"] == 200
    assert str(first["removeBackground"]["contentType"]).startswith("image/png")
    assert first["removeBackground"]["cacheControl"] == "no-store"
    assert first["removeBackground"]["noSniff"] == "nosniff"
    assert first["removeBackground"]["sha256"] == _sha256(_mask_png())
    assert first["background"]["status"] == "ok"
    assert first["background"]["mimeType"] == "image/png"
    assert isinstance(first["background"]["width"], int)
    assert isinstance(first["background"]["height"], int)
    assert first["background"]["width"] > 0
    assert first["background"]["height"] > 0

    assert {
        name: error["body"]["detail"]["code"]
        for name, error in first["errors"].items()
    } == {
        "analyzeShot": "INVALID_INPUT",
        "measurementPoints": "INVALID_INPUT",
        "removeBackground": "INVALID_INPUT",
    }
    assert {
        name: (error["status"], error["body"]["detail"]["provider"])
        for name, error in first["errors"].items()
    } == {
        "analyzeShot": (422, "shot-assessor"),
        "measurementPoints": (422, "measurement-line"),
        "removeBackground": (422, "garment-masker"),
    }
    for error in first["errors"].values():
        assert set(error) == {"status", "contentType", "cacheControl", "body"}
        assert str(error["contentType"]).startswith("application/json")
        assert error["cacheControl"] in {None, "no-store"}
        assert set(error["body"]) == {"detail"}
        assert set(error["body"]["detail"]) == {
            "provider",
            "code",
            "message",
            "retryable",
        }
        assert error["body"]["detail"]["retryable"] is False
        assert error["body"]["detail"]["message"]
    assert first["background"]["error"]["type"] == (
        "BackgroundGenerationContractError"
    )
    assert "unknown-style" in first["background"]["error"]["message"]
    assert first["providerRequests"]["rembg"] == [
        {
            "url": REMBG_REMOVE_URL,
            "file": {
                "name": "front",
                "mimeType": "image/png",
                "sha256": _sha256(_png_image()),
            },
            "data": {"model": REMBG_MODEL, "om": "true"},
            "timeout": REMBG_TIMEOUT_SECONDS,
        }
    ]
