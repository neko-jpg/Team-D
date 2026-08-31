"""Contract and API coverage for measurement endpoint suggestions.

The uploaded image is already perspective-corrected by the caller.  This
boundary therefore forwards exactly one image to the provider and accepts only
the four normalized endpoints defined by ``MeasurementLineProvider``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from io import BytesIO

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import create_app
from backend.providers.measurement_line import (
    MEASUREMENT_ENDPOINT_KEYS,
    MeasurementImage,
    MeasurementLineInput,
    ResponsesMeasurementLineProvider,
)
from backend.settings import BackendSettings
from backend.suggest_measurement_points import (
    MAX_MEASUREMENT_UPLOAD_BYTES,
    get_measurement_line_provider,
    get_measurement_timeout_seconds,
)


def valid_endpoints() -> dict[str, object]:
    return {
        "lengthStart": {"x": 0.50, "y": 0.14},
        "lengthEnd": {"x": 0.51, "y": 0.91},
        "widthStart": {"x": 0.19, "y": 0.36},
        "widthEnd": {"x": 0.82, "y": 0.37},
    }


def png_image() -> bytes:
    """Create a real, deterministic PNG rather than using fake image bytes."""

    output = BytesIO()
    Image.new("RGB", (7, 5), (48, 112, 176)).save(output, format="PNG")
    return output.getvalue()


def truncated_jpeg_image() -> bytes:
    output = BytesIO()
    Image.new("RGB", (100, 100), (48, 112, 176)).save(output, format="JPEG")
    return output.getvalue()[:-200]


def multipart(
    image: bytes | None = None,
    *,
    mime_type: str = "image/png",
) -> dict[str, tuple[str, bytes, str]]:
    return {
        "file": (
            "projected-measurement.png",
            png_image() if image is None else image,
            mime_type,
        )
    }


@dataclass
class RecordingProvider:
    response: object
    requests: list[MeasurementLineInput] = field(default_factory=list)

    async def suggest(self, input: MeasurementLineInput) -> object:
        self.requests.append(input)
        return self.response


@dataclass
class NeverCompletingProvider:
    requests: list[MeasurementLineInput] = field(default_factory=list)

    async def suggest(self, input: MeasurementLineInput) -> Mapping[str, object]:
        self.requests.append(input)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@dataclass
class FailingProvider:
    requests: list[MeasurementLineInput] = field(default_factory=list)

    async def suggest(self, input: MeasurementLineInput) -> Mapping[str, object]:
        self.requests.append(input)
        raise RuntimeError("private provider details must not escape")


class LeakyHttpExceptionProvider:
    async def suggest(self, input: MeasurementLineInput) -> Mapping[str, object]:
        raise HTTPException(
            status_code=418,
            detail={"secret": "provider internals must not escape"},
        )


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def install_provider(
    client: TestClient,
    provider: object,
    *,
    timeout: float | None = None,
) -> None:
    client.app.dependency_overrides[get_measurement_line_provider] = lambda: provider
    if timeout is not None:
        client.app.dependency_overrides[get_measurement_timeout_seconds] = lambda: timeout


def assert_finite_error(response: object, *, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    payload = response.json()
    assert set(payload) == {"detail"}
    assert payload["detail"]["provider"] == "measurement-line"
    assert payload["detail"]["code"] == code
    assert isinstance(payload["detail"]["message"], str)
    assert payload["detail"]["message"]
    assert isinstance(payload["detail"]["retryable"], bool)
    # Error serialization must never reflect NaN/Infinity or an invalid set of
    # measurement endpoints back to the client.
    json.dumps(payload, allow_nan=False)
    assert "lengthStart" not in payload
    assert "lengthEnd" not in payload
    assert "widthStart" not in payload
    assert "widthEnd" not in payload


def test_valid_four_endpoints_and_exact_image_are_returned_and_forwarded(
    client: TestClient,
) -> None:
    endpoints = valid_endpoints()
    provider = RecordingProvider(endpoints)
    install_provider(client, provider)
    image = png_image()

    response = client.post("/api/suggest-measurement-points", files=multipart(image))

    assert response.status_code == 200
    assert response.json() == endpoints
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert isinstance(request, MeasurementLineInput)
    assert request.image.data == image
    assert request.image.mime_type == "image/png"


@pytest.mark.parametrize(
    "invalid_response",
    [
        pytest.param({**valid_endpoints(), "confidence": 0.9}, id="unknown-top-level-field"),
        pytest.param(
            {
                **valid_endpoints(),
                "lengthStart": {"x": 0.5, "y": 0.14, "confidence": 0.9},
            },
            id="unknown-nested-field",
        ),
        pytest.param(
            {key: value for key, value in valid_endpoints().items() if key != "widthEnd"},
            id="missing-field",
        ),
        pytest.param(
            {**valid_endpoints(), "lengthEnd": {"x": 0.51, "y": 1.01}},
            id="out-of-range",
        ),
        pytest.param(
            {**valid_endpoints(), "widthStart": {"x": float("nan"), "y": 0.36}},
            id="nan",
        ),
        pytest.param(
            {**valid_endpoints(), "widthEnd": {"x": float("inf"), "y": 0.37}},
            id="infinity",
        ),
    ],
)
def test_invalid_provider_schema_returns_finite_error_without_invalid_body(
    client: TestClient,
    invalid_response: Mapping[str, object],
) -> None:
    provider = RecordingProvider(invalid_response)
    install_provider(client, provider)

    response = client.post("/api/suggest-measurement-points", files=multipart())

    assert_finite_error(response, status_code=502, code="INVALID_RESPONSE")
    assert len(provider.requests) == 1


def test_timeout_returns_finite_error(client: TestClient) -> None:
    provider = NeverCompletingProvider()
    install_provider(client, provider, timeout=0.001)

    response = client.post("/api/suggest-measurement-points", files=multipart())

    assert_finite_error(response, status_code=504, code="TIMEOUT")
    assert len(provider.requests) == 1


def test_provider_runtime_error_returns_unavailable_without_leaking_details(
    client: TestClient,
) -> None:
    provider = FailingProvider()
    install_provider(client, provider)

    response = client.post("/api/suggest-measurement-points", files=multipart())

    assert_finite_error(response, status_code=503, code="UNAVAILABLE")
    assert "private provider details" not in response.text
    assert len(provider.requests) == 1


def test_provider_http_exception_is_sanitized_as_unavailable(client: TestClient) -> None:
    install_provider(client, LeakyHttpExceptionProvider())

    response = client.post("/api/suggest-measurement-points", files=multipart())

    assert_finite_error(response, status_code=503, code="UNAVAILABLE")
    assert "provider internals" not in response.text


@pytest.mark.parametrize(
    ("files", "expected_status"),
    [
        pytest.param(
            multipart(mime_type="image/gif"),
            415,
            id="unsupported-mime",
        ),
        pytest.param(
            multipart(b""),
            422,
            id="empty-image",
        ),
        pytest.param(
            multipart(b"not-an-image"),
            422,
            id="undecodable-image",
        ),
        pytest.param(
            multipart(truncated_jpeg_image(), mime_type="image/jpeg"),
            422,
            id="truncated-jpeg",
        ),
        pytest.param(
            multipart(mime_type="image/jpeg"),
            422,
            id="mime-mismatch",
        ),
        pytest.param(
            multipart(b"x" * (MAX_MEASUREMENT_UPLOAD_BYTES + 1)),
            413,
            id="oversize-image",
        ),
    ],
)
def test_invalid_upload_is_rejected_before_provider(
    client: TestClient,
    files: dict[str, tuple[str, bytes, str]],
    expected_status: int,
) -> None:
    provider = RecordingProvider(valid_endpoints())
    install_provider(client, provider)

    response = client.post("/api/suggest-measurement-points", files=files)

    assert_finite_error(response, status_code=expected_status, code="INVALID_INPUT")
    assert provider.requests == []


def test_responses_request_uses_exact_strict_endpoint_schema() -> None:
    request = ResponsesMeasurementLineProvider.request_for(
        MeasurementLineInput(MeasurementImage(png_image(), "image/png")),
        "measurement-test-model",
    )

    assert request["store"] is False
    response_format = request["text"]["format"]
    assert response_format["type"] == "json_schema"
    assert response_format["strict"] is True
    schema = response_format["schema"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == list(MEASUREMENT_ENDPOINT_KEYS)
    assert set(schema["properties"]) == set(MEASUREMENT_ENDPOINT_KEYS)
    for point_schema in schema["properties"].values():
        assert point_schema["type"] == "object"
        assert point_schema["additionalProperties"] is False
        assert point_schema["required"] == ["x", "y"]
        assert set(point_schema["properties"]) == {"x", "y"}
        assert point_schema["properties"]["x"] == {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        }
        assert point_schema["properties"]["y"] == {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        }


def test_explicit_fixture_mode_uses_deterministic_provider() -> None:
    app = create_app(BackendSettings(provider_mode="fixture"))

    with TestClient(app) as client:
        first = client.post("/api/suggest-measurement-points", files=multipart())
        second = client.post("/api/suggest-measurement-points", files=multipart())

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json() == {
        "lengthStart": {"x": 0.5, "y": 0.16},
        "lengthEnd": {"x": 0.5, "y": 0.88},
        "widthStart": {"x": 0.24, "y": 0.36},
        "widthEnd": {"x": 0.76, "y": 0.36},
    }


def test_explicit_live_mode_never_falls_back_to_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_app(BackendSettings(provider_mode="live"))

    with TestClient(app) as client:
        response = client.post("/api/suggest-measurement-points", files=multipart())

    assert_finite_error(response, status_code=503, code="UNAVAILABLE")
    assert "fixture" not in response.json()["detail"]["message"].casefold()
