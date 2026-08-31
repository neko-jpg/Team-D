"""Non-persistence contracts for image-backed external AI boundaries.

The backend keeps request images and inference results in process memory only.
These tests pin the provider-side retention flag and exercise both HTTP paths
from an isolated working directory so an accidental relative DB/file write is
visible as a regression.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend.analyze_shot import get_shot_assessor
from backend.app import create_app
from backend.providers.measurement_line import (
    MeasurementImage,
    MeasurementLineInput,
    ResponsesMeasurementLineProvider,
)
from backend.providers.shot_assessor import (
    AssessmentImage,
    RequestedShot,
    ResponsesShotAssessor,
    ShotAssessorInput,
)
from backend.settings import BackendSettings
from backend.suggest_measurement_points import get_measurement_line_provider


VALID_SHOT_ASSESSMENT = {
    "shotType": "front",
    "quality": "ok",
    "issues": [],
    "missingShots": ["back", "tag"],
    "nextAction": "REQUEST_NEXT",
}

VALID_MEASUREMENT_ENDPOINTS = {
    "lengthStart": {"x": 0.50, "y": 0.14},
    "lengthEnd": {"x": 0.51, "y": 0.91},
    "widthStart": {"x": 0.19, "y": 0.36},
    "widthEnd": {"x": 0.82, "y": 0.37},
}


@dataclass(slots=True)
class ParsedResponse:
    output_parsed: object


@dataclass(slots=True)
class RecordingResponsesClient:
    output: object
    calls: list[dict[str, object]] = field(default_factory=list)

    async def create(self, **kwargs: object) -> ParsedResponse:
        self.calls.append(kwargs)
        return ParsedResponse(self.output)


def png_image() -> bytes:
    output = BytesIO()
    Image.new("RGB", (7, 5), (48, 112, 176)).save(output, format="PNG")
    return output.getvalue()


def test_all_storage_controllable_external_ai_request_builders_disable_retention() -> None:
    requests = {
        "shot-assessor": ResponsesShotAssessor.request_for(
            ShotAssessorInput(
                image=AssessmentImage(b"shot-image", "image/png"),
                requested_shot=RequestedShot.FRONT,
            ),
            "shot-model",
        ),
        "measurement-line": ResponsesMeasurementLineProvider.request_for(
            MeasurementLineInput(
                image=MeasurementImage(b"measurement-image", "image/png")
            ),
            "measurement-model",
        ),
    }

    for provider_name, request in requests.items():
        assert "store" in request, f"{provider_name} must set an explicit retention policy"
        assert request["store"] is False, f"{provider_name} must disable provider retention"


def test_representative_ai_api_execution_creates_no_db_or_file_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful image/result flows must leave an isolated working tree empty."""

    monkeypatch.chdir(tmp_path)
    shot_client = RecordingResponsesClient(VALID_SHOT_ASSESSMENT)
    measurement_client = RecordingResponsesClient(VALID_MEASUREMENT_ENDPOINTS)
    shot_assessor = ResponsesShotAssessor(shot_client, "shot-model")
    measurement_provider = ResponsesMeasurementLineProvider(
        measurement_client,
        "measurement-model",
    )
    app = create_app(BackendSettings())
    app.dependency_overrides[get_shot_assessor] = lambda: shot_assessor
    app.dependency_overrides[get_measurement_line_provider] = (
        lambda: measurement_provider
    )
    image = png_image()
    before = tuple(tmp_path.rglob("*"))

    with TestClient(app) as client:
        shot_response = client.post(
            "/api/analyze-shot",
            data={"requestedShot": "front"},
            files={"file": ("front.png", image, "image/png")},
        )
        measurement_response = client.post(
            "/api/suggest-measurement-points",
            files={"file": ("measurement.png", image, "image/png")},
        )

    assert shot_response.status_code == 200
    assert shot_response.json() == VALID_SHOT_ASSESSMENT
    assert measurement_response.status_code == 200
    assert measurement_response.json() == VALID_MEASUREMENT_ENDPOINTS
    assert len(shot_client.calls) == 1
    assert shot_client.calls[0]["store"] is False
    assert len(measurement_client.calls) == 1
    assert measurement_client.calls[0]["store"] is False
    assert tuple(tmp_path.rglob("*")) == before == ()
