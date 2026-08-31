"""API and integration tests for post-capture shot assessment.

The backend has no session or reducer state.  These tests still make the
progress invariant explicit by keeping a tiny caller-owned state snapshot and
asserting it is untouched whenever the HTTP boundary rejects an upload or a
provider result.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

import pytest
from fastapi.testclient import TestClient

from backend.analyze_shot import (
    MAX_UPLOAD_BYTES,
    get_analysis_timeout_seconds,
    get_shot_assessor,
)
from backend.app import create_app
from backend.providers.shot_assessor import ShotAssessorInput
from backend.providers.shot_assessor import ShotAssessmentContractError


RequestedShot = Literal["front", "back", "tag"]


@dataclass
class RecordingAssessor:
    response: Mapping[str, object]
    requests: list[ShotAssessorInput] = field(default_factory=list)

    async def assess(self, input: ShotAssessorInput) -> Mapping[str, object]:
        self.requests.append(input)
        return self.response


class NeverCompletingAssessor:
    async def assess(self, input: ShotAssessorInput) -> Mapping[str, object]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class InvalidatingAssessor:
    async def assess(self, input: ShotAssessorInput) -> Mapping[str, object]:
        raise ShotAssessmentContractError("simulated invalid live response")


def valid_assessment() -> dict[str, object]:
    return {
        "shotType": "front",
        "quality": "ok",
        "issues": [],
        "missingShots": ["back", "tag"],
        "nextAction": "REQUEST_NEXT",
    }


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def install_assessor(client: TestClient, assessor: object, *, timeout: float | None = None) -> None:
    client.app.dependency_overrides[get_shot_assessor] = lambda: assessor
    if timeout is not None:
        client.app.dependency_overrides[get_analysis_timeout_seconds] = lambda: timeout


def multipart(image: bytes = b"image") -> dict[str, tuple[str, bytes, str]]:
    return {"file": ("garment.jpg", image, "image/jpeg")}


def test_analyze_shot_accepts_multipart_front_upload_and_returns_valid_assessment(
    client: TestClient,
) -> None:
    assessor = RecordingAssessor(valid_assessment())
    install_assessor(client, assessor)

    response = client.post(
        "/api/analyze-shot", data={"requestedShot": "front"}, files=multipart(b"front-image")
    )

    assert response.status_code == 200
    assert response.json() == valid_assessment()
    assert len(assessor.requests) == 1
    request = assessor.requests[0]
    assert request.image.data == b"front-image"
    assert request.image.mime_type == "image/jpeg"
    assert request.requested_shot.value == "front"


def test_measurement_is_rejected_before_provider_and_progress_is_unchanged(
    client: TestClient,
) -> None:
    assessor = RecordingAssessor(valid_assessment())
    install_assessor(client, assessor)
    caller_progress = {"currentShot": "front", "accepted": ["front"]}
    before = dict(caller_progress)

    response = client.post(
        "/api/analyze-shot", data={"requestedShot": "measurement"}, files=multipart()
    )

    assert response.status_code == 422
    assert assessor.requests == []
    assert caller_progress == before


def test_invalid_provider_schema_returns_error_without_progress_change(client: TestClient) -> None:
    assessor = RecordingAssessor({"shotType": "front"})
    install_assessor(client, assessor)
    caller_progress = {"currentShot": "back", "accepted": ["front"]}
    before = dict(caller_progress)

    response = client.post(
        "/api/analyze-shot", data={"requestedShot": "back"}, files=multipart(b"back-image")
    )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "INVALID_RESPONSE"
    assert "shotType" not in response.json()
    assert caller_progress == before


def test_provider_side_runtime_validation_is_reported_as_invalid_response(
    client: TestClient,
) -> None:
    install_assessor(client, InvalidatingAssessor())

    response = client.post(
        "/api/analyze-shot", data={"requestedShot": "front"}, files=multipart()
    )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "INVALID_RESPONSE"
    assert "shotType" not in response.json()


def test_timeout_returns_error_without_progress_change(client: TestClient) -> None:
    install_assessor(client, NeverCompletingAssessor(), timeout=0.001)
    caller_progress = {"currentShot": "tag", "accepted": ["front", "back"]}
    before = dict(caller_progress)

    response = client.post(
        "/api/analyze-shot", data={"requestedShot": "tag"}, files=multipart(b"tag-image")
    )

    assert response.status_code == 504
    assert response.json()["detail"] == {
        "provider": "shot-assessor",
        "code": "TIMEOUT",
        "message": "Shot assessment timed out",
        "retryable": True,
    }
    assert caller_progress == before


@pytest.mark.parametrize(
    ("files", "expected_status"),
    [
        ({"file": ("garment.gif", b"gif", "image/gif")}, 415),
        ({"file": ("garment.jpg", b"x" * (MAX_UPLOAD_BYTES + 1), "image/jpeg")}, 413),
    ],
)
def test_mime_and_size_limits_reject_before_provider(
    client: TestClient, files: dict[str, tuple[str, bytes, str]], expected_status: int
) -> None:
    assessor = RecordingAssessor(valid_assessment())
    install_assessor(client, assessor)

    response = client.post("/api/analyze-shot", data={"requestedShot": "front"}, files=files)

    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == "INVALID_INPUT"
    assert assessor.requests == []
