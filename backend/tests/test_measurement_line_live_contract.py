"""Live-boundary contract tests for measurement endpoint suggestions.

These tests deliberately use a deterministic Responses client rather than the
fixture provider: a live failure must remain a failure and must never turn into
made-up measurement endpoints.
"""

from __future__ import annotations

import base64
import math
import unittest
from dataclasses import dataclass

from backend.providers.measurement_line import (
    MEASUREMENT_ENDPOINT_KEYS,
    MeasurementEndpoints,
    MeasurementImage,
    MeasurementLineContractError,
    MeasurementLineInput,
    ResponsesMeasurementLineProvider,
)


VALID_ENDPOINTS = {
    "lengthStart": {"x": 0.5, "y": 0.1},
    "lengthEnd": {"x": 0.5, "y": 0.9},
    "widthStart": {"x": 0.2, "y": 0.45},
    "widthEnd": {"x": 0.8, "y": 0.45},
}


@dataclass
class FakeResponse:
    output_parsed: object | None = None
    output_text: object | None = None


class RecordingResponsesClient:
    def __init__(self, result: object) -> None:
        self._result = result
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


def measurement_input() -> MeasurementLineInput:
    return MeasurementLineInput(MeasurementImage(b"projected-image", "image/jpeg"))


class MeasurementLineLiveContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_is_propagated_not_replaced_with_fixture_endpoints(self) -> None:
        timeout = TimeoutError("provider timed out")
        client = RecordingResponsesClient(timeout)
        provider = ResponsesMeasurementLineProvider(client, "test-model")

        with self.assertRaisesRegex(TimeoutError, "provider timed out"):
            await provider.suggest(measurement_input())

        self.assertEqual(len(client.calls), 1)

    async def test_provider_and_http_errors_are_propagated_not_replaced(self) -> None:
        for error in (RuntimeError("provider unavailable"), ConnectionError("HTTP 503")):
            with self.subTest(error=type(error).__name__):
                client = RecordingResponsesClient(error)
                provider = ResponsesMeasurementLineProvider(client, "test-model")

                with self.assertRaises(type(error)) as raised:
                    await provider.suggest(measurement_input())

                self.assertIs(raised.exception, error)
                self.assertEqual(len(client.calls), 1)

    async def test_invalid_live_schema_is_rejected(self) -> None:
        invalid_payloads = {
            "unknown endpoint": {**VALID_ENDPOINTS, "confidence": 1},
            "missing endpoint": {key: value for key, value in VALID_ENDPOINTS.items() if key != "widthEnd"},
            "out of range": {**VALID_ENDPOINTS, "lengthEnd": {"x": 1.01, "y": 0.9}},
            "NaN": {**VALID_ENDPOINTS, "widthStart": {"x": math.nan, "y": 0.45}},
            "Infinity": {**VALID_ENDPOINTS, "widthEnd": {"x": math.inf, "y": 0.45}},
        }

        for description, payload in invalid_payloads.items():
            with self.subTest(description=description):
                provider = ResponsesMeasurementLineProvider(
                    RecordingResponsesClient(FakeResponse(output_parsed=payload)), "test-model"
                )
                with self.assertRaises(MeasurementLineContractError):
                    await provider.suggest(measurement_input())

    async def test_only_a_valid_set_of_four_endpoints_succeeds(self) -> None:
        provider = ResponsesMeasurementLineProvider(
            RecordingResponsesClient(FakeResponse(output_parsed=VALID_ENDPOINTS)), "test-model"
        )

        endpoints = await provider.suggest(measurement_input())

        self.assertIsInstance(endpoints, MeasurementEndpoints)
        self.assertEqual(endpoints.to_payload(), VALID_ENDPOINTS)

    async def test_live_request_uses_strict_endpoint_schema_and_no_measurement_ui_data(self) -> None:
        client = RecordingResponsesClient(FakeResponse(output_parsed=VALID_ENDPOINTS))
        provider = ResponsesMeasurementLineProvider(client, "test-model")

        await provider.suggest(measurement_input())

        request = client.calls[0]
        self.assertEqual(set(request), {"model", "store", "instructions", "input", "text"})
        self.assertFalse(request["store"])
        self.assertEqual(set(request["text"]), {"format"})
        format_ = request["text"]["format"]  # type: ignore[index]
        self.assertEqual(format_["type"], "json_schema")
        self.assertTrue(format_["strict"])
        schema = format_["schema"]
        self.assertEqual(schema["required"], list(MEASUREMENT_ENDPOINT_KEYS))
        self.assertEqual(set(schema["properties"]), set(MEASUREMENT_ENDPOINT_KEYS))
        self.assertFalse(schema["additionalProperties"])
        for endpoint_schema in schema["properties"].values():
            self.assertEqual(set(endpoint_schema), {"type", "additionalProperties", "required", "properties"})
            self.assertFalse(endpoint_schema["additionalProperties"])
            self.assertEqual(endpoint_schema["required"], ["x", "y"])
            self.assertEqual(set(endpoint_schema["properties"]), {"x", "y"})
            for coordinate_schema in endpoint_schema["properties"].values():
                self.assertEqual(
                    coordinate_schema,
                    {"type": "number", "minimum": 0, "maximum": 1},
                )

        self.assertEqual(len(request["input"]), 1)
        message = request["input"][0]  # type: ignore[index]
        self.assertEqual(set(message), {"role", "content"})
        self.assertEqual(message["role"], "user")
        content = message["content"]
        self.assertEqual([item["type"] for item in content], ["input_text", "input_image"])
        self.assertEqual(set(content[0]), {"type", "text"})
        self.assertEqual(set(content[1]), {"type", "image_url"})
        expected_image_url = "data:image/jpeg;base64," + base64.b64encode(b"projected-image").decode(
            "ascii"
        )
        self.assertEqual(content[1]["image_url"], expected_image_url)


if __name__ == "__main__":
    unittest.main()
