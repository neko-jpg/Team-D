"""OpenSpec 4.5 contract: live errors never become fixture successes."""

from __future__ import annotations

import asyncio
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import create_app
from backend.config import BackendSettings
from backend.providers.shot_assessor import AssessmentImage, RequestedShot, ShotAssessorInput
from backend.providers.shot_assessor_factory import create_shot_assessor


def _settings(mode: str) -> BackendSettings:
    return BackendSettings(provider_mode=mode, api_host="127.0.0.1", api_port=3001)  # type: ignore[arg-type]


class FailingLiveShotAssessor:
    async def assess(self, input: ShotAssessorInput) -> object:
        raise TimeoutError("simulated provider timeout")


def jpeg_image() -> bytes:
    output = BytesIO()
    Image.new("RGB", (4, 3), (180, 40, 70)).save(output, format="JPEG")
    return output.getvalue()


def test_fixture_mode_returns_its_deterministic_success_response() -> None:
    assessor = create_shot_assessor(_settings("fixture"))

    result = asyncio.run(
        assessor.assess(
            ShotAssessorInput(AssessmentImage(b"fixture-image"), RequestedShot.FRONT)
        )
    ).to_payload()

    assert result == {
        "shotType": "front",
        "quality": "ok",
        "issues": [],
        "missingShots": ["back", "tag"],
        "nextAction": "REQUEST_NEXT",
    }


def test_fixture_route_requires_the_explicit_fixture_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_MODE", "fixture")
    app = create_app()

    with TestClient(app) as client:
        response = client.post(
            "/api/analyze-shot",
            data={"requestedShot": "front"},
            files={"file": ("front.jpg", jpeg_image(), "image/jpeg")},
        )

    assert response.status_code == 200
    assert response.json() == {
        "shotType": "front",
        "quality": "ok",
        "issues": [],
        "missingShots": ["back", "tag"],
        "nextAction": "REQUEST_NEXT",
    }


def test_live_error_propagates_and_is_never_replaced_with_fixture_success() -> None:
    assessor = create_shot_assessor(
        _settings("live"), live_assessor=FailingLiveShotAssessor()  # type: ignore[arg-type]
    )

    with pytest.raises(TimeoutError, match="simulated provider timeout"):
        asyncio.run(
            assessor.assess(
                ShotAssessorInput(AssessmentImage(b"live-image"), RequestedShot.FRONT)
            )
        )


def test_live_route_reports_provider_failure_instead_of_fixture_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_MODE", "live")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_app()

    with TestClient(app) as client:
        response = client.post(
            "/api/analyze-shot",
            data={"requestedShot": "front"},
            files={"file": ("front.jpg", jpeg_image(), "image/jpeg")},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "UNAVAILABLE"
    assert "shotType" not in response.json()
