"""Backend-only fixture matrix for recoverable provider and Agent failures."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from io import BytesIO
from typing import Mapping

from fastapi.testclient import TestClient
from PIL import Image

from backend.analyze_shot import get_analysis_timeout_seconds, get_shot_assessor
from backend.app import create_app
from backend.guidance_transport import GuidanceTransportAdapter, GuidanceTransportError
from backend.integration_errors import (
    background_integration_error,
    guidance_integration_error,
    is_finite_integration_error,
)
from backend.providers.background_generator import (
    BackgroundGenerationProviderError,
    BackgroundGenerator,
)
from backend.providers.garment_masker import GarmentMask, GarmentMaskInput
from backend.providers.measurement_line import MeasurementLineInput
from backend.providers.vision_guidance import EncodedImage, GuidanceShot
from backend.remove_background import (
    get_garment_masker,
    get_remove_background_timeout_seconds,
)
from backend.settings import BackendSettings
from backend.suggest_measurement_points import get_measurement_line_provider


def _png(*, mode: str = "RGB", color: object = (80, 120, 160)) -> bytes:
    output = BytesIO()
    Image.new(mode, (8, 8), color).save(output, format="PNG")
    return output.getvalue()


def _api_error(response: object) -> dict[str, object]:
    assert response.status_code >= 400
    body = response.json()
    assert set(body) == {"detail"}
    error = body["detail"]
    assert set(error) == {"provider", "code", "message", "retryable"}
    assert isinstance(error["provider"], str) and error["provider"]
    assert error["code"] in {"TIMEOUT", "UNAVAILABLE", "INVALID_RESPONSE", "UNKNOWN"}
    assert isinstance(error["message"], str) and error["message"]
    assert error["retryable"] is True
    json.dumps(error, allow_nan=False)
    return error


@dataclass
class _NeverAssessor:
    calls: int = 0

    async def assess(self, _input: object) -> object:
        self.calls += 1
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@dataclass
class _FailingMeasurementProvider:
    calls: int = 0

    async def suggest(self, _input: MeasurementLineInput) -> Mapping[str, object]:
        self.calls += 1
        raise RuntimeError("private measurement provider failure")


@dataclass
class _NeverMasker:
    calls: int = 0

    async def mask(self, _front: GarmentMaskInput) -> GarmentMask:
        self.calls += 1
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@dataclass
class _InvalidMasker:
    calls: int = 0

    async def mask(self, _front: GarmentMaskInput) -> GarmentMask:
        self.calls += 1
        return GarmentMask(data=_png(mode="L", color=0), width=8, height=8)


@dataclass
class _FailingImagesClient:
    calls: list[dict[str, object]] = field(default_factory=list)

    async def generate(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        raise RuntimeError("private Images API failure")


@dataclass
class _Publisher:
    packets: list[bytes] = field(default_factory=list)

    def publish_data(self, payload: bytes, *, reliable: bool) -> None:
        del reliable
        self.packets.append(payload)


async def _stopped_agent_failure() -> dict[str, object]:
    async def inference(_frame: object) -> Mapping[str, object]:
        return {"code": "READY", "confidence": 1.0}

    publisher = _Publisher()
    transport = GuidanceTransportAdapter(
        inference,
        publisher,
        session_id="failure-matrix-agent",
    )
    await transport.close()
    packet_count_after_stop = len(publisher.packets)
    try:
        await transport.process_frame(
            EncodedImage(_png(), "image/png", width=8, height=8),
            shot=GuidanceShot.FRONT,
            observed_at=1_000,
        )
    except GuidanceTransportError as error:
        result = guidance_integration_error(error).to_payload()
    else:  # pragma: no cover - protects the no-success acceptance condition
        raise AssertionError("a stopped Agent must not return READY guidance")

    assert len(publisher.packets) == packet_count_after_stop
    return result


def test_backend_failure_matrix_never_turns_failures_into_fixture_success() -> None:
    image = _png()
    app = create_app(BackendSettings(provider_mode="fixture"))
    failures: dict[str, dict[str, object]] = {}

    never_assessor = _NeverAssessor()
    app.dependency_overrides[get_shot_assessor] = lambda: never_assessor
    app.dependency_overrides[get_analysis_timeout_seconds] = lambda: 0.001
    with TestClient(app) as client:
        response = client.post(
            "/api/analyze-shot",
            data={"requestedShot": "front"},
            files={"file": ("front.png", image, "image/png")},
        )
    failures["shot-timeout"] = _api_error(response)
    assert never_assessor.calls == 1
    assert "shotType" not in response.text

    failing_measurement = _FailingMeasurementProvider()
    app.dependency_overrides[get_measurement_line_provider] = lambda: failing_measurement
    with TestClient(app) as client:
        response = client.post(
            "/api/suggest-measurement-points",
            files={"file": ("measurement.png", image, "image/png")},
        )
    failures["measurement-provider"] = _api_error(response)
    assert failing_measurement.calls == 1
    assert "lengthStart" not in response.text
    assert "private measurement" not in response.text

    never_masker = _NeverMasker()
    app.dependency_overrides[get_garment_masker] = lambda: never_masker
    app.dependency_overrides[get_remove_background_timeout_seconds] = lambda: 0.001
    with TestClient(app) as client:
        response = client.post(
            "/api/remove-background",
            files={"file": ("front.png", image, "image/png")},
        )
    failures["rembg-timeout"] = _api_error(response)
    assert never_masker.calls == 1
    assert not response.headers["content-type"].startswith("image/png")

    invalid_masker = _InvalidMasker()
    app.dependency_overrides[get_garment_masker] = lambda: invalid_masker
    app.dependency_overrides.pop(get_remove_background_timeout_seconds, None)
    with TestClient(app) as client:
        response = client.post(
            "/api/remove-background",
            files={"file": ("front.png", image, "image/png")},
        )
    failures["rembg-invalid-mask"] = _api_error(response)
    assert invalid_masker.calls == 1
    assert not response.headers["content-type"].startswith("image/png")

    images_client = _FailingImagesClient()
    try:
        asyncio.run(
            BackgroundGenerator(images_client, "gpt-image-1").generate("studio-white")
        )
    except BackgroundGenerationProviderError as error:
        failures["background-provider"] = background_integration_error(error).to_payload()
    else:  # pragma: no cover - protects the no-success acceptance condition
        raise AssertionError("background failure must not return a fixture image")
    assert len(images_client.calls) == 1

    failures["agent-stopped"] = asyncio.run(_stopped_agent_failure())

    assert {name: error["code"] for name, error in failures.items()} == {
        "shot-timeout": "TIMEOUT",
        "measurement-provider": "UNAVAILABLE",
        "rembg-timeout": "TIMEOUT",
        "rembg-invalid-mask": "INVALID_RESPONSE",
        "background-provider": "UNAVAILABLE",
        "agent-stopped": "UNAVAILABLE",
    }
    assert all(
        is_finite_integration_error(error)
        if error["provider"] in {"vision-guidance", "background-generator"}
        else error["retryable"] is True
        for error in failures.values()
    )
    json.dumps(failures, allow_nan=False)
