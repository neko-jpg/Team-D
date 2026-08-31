"""Contract tests for OpenSpec task 4.1's strict post-capture provider."""

from __future__ import annotations

import json
import unittest

import httpx
from openai import AsyncOpenAI

from backend.providers.shot_assessor import (
    ASSESSED_SHOT_TYPES,
    NEXT_ACTIONS,
    REQUESTED_SHOTS,
    SHOT_ASSESSMENT_JSON_SCHEMA,
    SHOT_ISSUES,
    AssessmentImage,
    ResponsesShotAssessor,
    ShotAssessmentContractError,
    ShotAssessorInput,
    validate_requested_shot,
    validate_shot_assessment,
)


VALID = {
    "shotType": "front",
    "quality": "ok",
    "issues": [],
    "missingShots": ["back", "tag"],
    "nextAction": "REQUEST_NEXT",
}


class _Response:
    def __init__(self, output_parsed: object) -> None:
        self.output_parsed = output_parsed


class _RecordingResponsesClient:
    def __init__(self, output: object) -> None:
        self.output = output
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> _Response:
        self.calls.append(kwargs)
        return _Response(self.output)


class ShotAssessorContractTests(unittest.IsolatedAsyncioTestCase):
    def test_schema_is_closed_and_uses_only_finite_post_capture_enums(self) -> None:
        self.assertEqual(REQUESTED_SHOTS, ("front", "back", "tag"))
        self.assertEqual(ASSESSED_SHOT_TYPES, ("front", "back", "tag", "unknown"))
        self.assertEqual(NEXT_ACTIONS, ("RETAKE", "REQUEST_NEXT", "COMPLETE"))
        self.assertEqual(
            set(SHOT_ISSUES),
            {
                "TOO_DARK",
                "TOO_BRIGHT",
                "TOO_BLURRY",
                "BLURRY",
                "GARMENT_CROPPED",
                "TAG_UNREADABLE",
                "WRONG_SHOT",
            },
        )
        self.assertTrue(SHOT_ASSESSMENT_JSON_SCHEMA["additionalProperties"] is False)
        self.assertEqual(
            set(SHOT_ASSESSMENT_JSON_SCHEMA["required"]),  # type: ignore[arg-type]
            set(VALID),
        )

    def test_runtime_validation_rejects_measurement_unknown_enums_and_missing_fields(self) -> None:
        invalid = (
            {**VALID, "shotType": "side"},
            {**VALID, "shotType": "measurement"},
            {**VALID, "quality": "excellent"},
            {**VALID, "issues": ["MODEL_FREE_TEXT"]},
            {**VALID, "missingShots": ["measurement"]},
            {**VALID, "nextAction": "SKIP_TO_EDIT"},
            {key: value for key, value in VALID.items() if key != "issues"},
            {**VALID, "lengthCm": 68},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ShotAssessmentContractError):
                validate_shot_assessment(value)

        with self.assertRaises(ShotAssessmentContractError):
            validate_requested_shot("measurement")

    async def test_responses_adapter_requests_strict_schema_and_revalidates_output(self) -> None:
        client = _RecordingResponsesClient(VALID)
        assessor = ResponsesShotAssessor(client, "gpt-test")
        result = await assessor.assess(
            ShotAssessorInput(AssessmentImage(b"image", "image/png"), "front")  # type: ignore[arg-type]
        )
        self.assertEqual(result.to_payload(), VALID)
        request = client.calls[0]
        self.assertIs(request["store"], False)
        self.assertIn("requestedShot=front", request["input"][0]["content"][0]["text"])  # type: ignore[index]
        response_format = request["text"]["format"]  # type: ignore[index]
        self.assertEqual(response_format["type"], "json_schema")  # type: ignore[index]
        self.assertTrue(response_format["strict"] is True)  # type: ignore[index]
        self.assertEqual(response_format["schema"], SHOT_ASSESSMENT_JSON_SCHEMA)  # type: ignore[index]

        invalid_client = _RecordingResponsesClient({**VALID, "issues": ["unbounded"]})
        with self.assertRaises(ShotAssessmentContractError):
            await ResponsesShotAssessor(invalid_client, "gpt-test").assess(
                ShotAssessorInput(AssessmentImage(b"image"), "tag")  # type: ignore[arg-type]
            )

    async def test_official_openai_sdk_serializes_the_strict_image_request(self) -> None:
        """Exercise the real SDK surface without opening a network connection."""

        seen_request: dict[str, object] = {}

        async def respond(request: httpx.Request) -> httpx.Response:
            seen_request.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": "resp_contract_test",
                    "object": "response",
                    "created_at": 0,
                    "status": "completed",
                    "model": "gpt-4.1-mini-2025-04-14",
                    "output": [
                        {
                            "id": "msg_contract_test",
                            "type": "message",
                            "status": "completed",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps(VALID),
                                    "annotations": [],
                                }
                            ],
                        }
                    ],
                    "parallel_tool_calls": True,
                    "store": False,
                },
            )

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        client = AsyncOpenAI(
            api_key="offline-contract-key",
            base_url="https://openai.invalid/v1",
            http_client=http_client,
        )
        try:
            result = await ResponsesShotAssessor(
                client.responses, "gpt-4.1-mini-2025-04-14"
            ).assess(
                ShotAssessorInput(AssessmentImage(b"image", "image/jpeg"), "front")  # type: ignore[arg-type]
            )
        finally:
            await client.close()

        self.assertEqual(result.to_payload(), VALID)
        self.assertIs(seen_request["store"], False)
        self.assertTrue(seen_request["text"]["format"]["strict"] is True)  # type: ignore[index]
        self.assertTrue(
            seen_request["input"][0]["content"][1]["image_url"].startswith(  # type: ignore[index]
                "data:image/jpeg;base64,"
            )
        )
