"""Cross-field and current-step validation for untrusted AI results."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.analyze_shot import get_shot_assessor
from backend.app import create_app
from backend.providers.runtime import LiveVisionGuidanceProvider
from backend.providers.shot_assessor import (
    ShotAssessmentContractError,
    ShotAssessorInput,
    validate_shot_assessment,
    validate_shot_assessment_for_requested_shot,
)
from backend.providers.vision_guidance import (
    EncodedImage,
    GuidanceContractError,
    GuidanceInput,
    GuidanceShot,
    validate_vision_decision_for_shot,
)
from backend.providers.vision_guidance_responses import ResponsesVisionGuidanceAnalyzer


VALID_FRONT = {
    "shotType": "front",
    "quality": "ok",
    "issues": [],
    "missingShots": ["back", "tag"],
    "nextAction": "REQUEST_NEXT",
}


@pytest.mark.parametrize(
    "payload",
    [
        {**VALID_FRONT, "issues": ["TOO_DARK"]},
        {**VALID_FRONT, "nextAction": "RETAKE"},
        {
            **VALID_FRONT,
            "quality": "retry",
            "issues": [],
            "missingShots": ["front", "back", "tag"],
            "nextAction": "RETAKE",
        },
        {
            **VALID_FRONT,
            "quality": "retry",
            "issues": ["TOO_DARK"],
            "missingShots": ["front", "back", "tag"],
            "nextAction": "REQUEST_NEXT",
        },
    ],
)
def test_shot_assessment_rejects_cross_field_contradictions(payload: object) -> None:
    with pytest.raises(ShotAssessmentContractError):
        validate_shot_assessment(payload)


def test_shot_assessment_is_bound_to_requested_step_and_fixed_remaining_sequence() -> None:
    with pytest.raises(ShotAssessmentContractError, match="match requestedShot"):
        validate_shot_assessment_for_requested_shot(
            {**VALID_FRONT, "shotType": "back", "missingShots": ["tag"]},
            "front",
        )

    retry_wrong_shot = {
        "shotType": "back",
        "quality": "retry",
        "issues": ["WRONG_SHOT"],
        "missingShots": ["front", "back", "tag"],
        "nextAction": "RETAKE",
    }
    assert (
        validate_shot_assessment_for_requested_shot(retry_wrong_shot, "front").quality.value
        == "retry"
    )


@dataclass
class _Assessor:
    payload: dict[str, object]

    async def assess(self, input: ShotAssessorInput) -> dict[str, object]:
        return self.payload


def _jpeg() -> bytes:
    output = BytesIO()
    Image.new("RGB", (6, 4), (120, 40, 80)).save(output, format="JPEG")
    return output.getvalue()


def test_analyze_shot_rejects_semantically_valid_but_wrong_step_response() -> None:
    app = create_app()
    app.dependency_overrides[get_shot_assessor] = lambda: _Assessor(
        {
            "shotType": "back",
            "quality": "ok",
            "issues": [],
            "missingShots": ["tag"],
            "nextAction": "REQUEST_NEXT",
        }
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/analyze-shot",
            data={"requestedShot": "front"},
            files={"file": ("garment.jpg", _jpeg(), "image/jpeg")},
        )
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "INVALID_RESPONSE"


@pytest.mark.parametrize(
    ("shot", "code"),
    [
        (GuidanceShot.FRONT, "PLACE_MARKER"),
        (GuidanceShot.BACK, "MOVE_TO_TAG"),
        (GuidanceShot.TAG, "CAMERA_OVERHEAD"),
        (GuidanceShot.MEASUREMENT, "MOVE_TO_TAG"),
    ],
)
def test_guidance_code_must_belong_to_current_shot(shot: GuidanceShot, code: str) -> None:
    with pytest.raises(GuidanceContractError, match="not valid"):
        validate_vision_decision_for_shot({"code": code, "confidence": 0.8}, shot)


@dataclass
class _Response:
    output_parsed: object


class _ResponsesClient:
    async def create(self, **kwargs: object) -> _Response:
        return _Response({"code": "PLACE_MARKER", "confidence": 0.9})


def test_responses_and_live_provider_reject_cross_step_guidance() -> None:
    input_value = GuidanceInput(EncodedImage(_jpeg()), GuidanceShot.FRONT)
    analyzer = ResponsesVisionGuidanceAnalyzer(_ResponsesClient(), "test-model")
    with pytest.raises(GuidanceContractError):
        asyncio.run(analyzer(input_value))

    provider = LiveVisionGuidanceProvider(
        lambda _input: {"code": "PLACE_MARKER", "confidence": 0.9}
    )
    with pytest.raises(GuidanceContractError):
        asyncio.run(provider.analyze(input_value))
