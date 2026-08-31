"""Contract tests for text-only background generation.

The live adapter is intentionally tested with a request spy: a selected style
may become a fixed text prompt, but no garment image, mask, tag, measurement,
or binary value is allowed to cross this provider boundary.  Invalid live
responses stay failures and are never replaced with a fixture image.
"""

from __future__ import annotations

import asyncio
import base64
import json
import unittest
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any
from unittest.mock import patch

import httpx
from PIL import Image
from openai import APITimeoutError, AsyncOpenAI

from backend.providers import background_generator as background_generator_module
from backend.providers.background_generator import (
    ALLOWED_BACKGROUND_STYLE_PROMPTS,
    BACKGROUND_GENERATION_TIMEOUT_SECONDS,
    BackgroundGenerationContractError,
    BackgroundGenerationProviderError,
    BackgroundGenerationTimeoutError,
    BackgroundGenerator,
    FixtureBackgroundGenerator,
    GeneratedBackground,
)


EXPECTED_STYLE_IDS = {"studio-white", "neutral-gray", "warm-wood"}
GPT_IMAGE_MODEL = "gpt-image-1"
DALL_E_MODEL = "dall-e-3"
FORBIDDEN_REQUEST_FIELD_TERMS = (
    "image",
    "mask",
    "tag",
    "measurement",
    "file",
    "binary",
)


def image_bytes(
    image_format: str = "PNG",
    *,
    mode: str = "RGB",
    color: object = (232, 228, 220),
    size: tuple[int, int] = (6, 4),
) -> bytes:
    output = BytesIO()
    Image.new(mode, size, color).save(output, format=image_format)
    return output.getvalue()


def encoded_image(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@dataclass(frozen=True)
class ImagePayload:
    b64_json: object


@dataclass(frozen=True)
class ImageResponse:
    data: object


@dataclass
class RequestSpyImagesClient:
    result: object
    calls: list[dict[str, object]] = field(default_factory=list)

    async def generate(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def valid_response(*, as_mapping: bool = False) -> object:
    b64_json = encoded_image(image_bytes())
    if as_mapping:
        return {"data": [{"b64_json": b64_json}]}
    return ImageResponse(data=[ImagePayload(b64_json=b64_json)])


def assert_text_only_request(
    test: unittest.TestCase,
    value: object,
    path: str = "request",
) -> None:
    """Recursively prove that a provider request contains only safe scalar text data."""

    test.assertNotIsInstance(value, (bytes, bytearray, memoryview), path)
    if isinstance(value, dict):
        for key, nested in value.items():
            test.assertIsInstance(key, str, path)
            normalized_key = key.casefold().replace("-", "_")
            for term in FORBIDDEN_REQUEST_FIELD_TERMS:
                test.assertNotIn(
                    term,
                    normalized_key,
                    f"forbidden request field at {path}.{key}",
                )
            assert_text_only_request(test, nested, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            assert_text_only_request(test, nested, f"{path}[{index}]")
        return
    if isinstance(value, str):
        test.assertFalse(value.casefold().startswith("data:"), f"data URL at {path}")
        return
    test.assertIsInstance(value, (int, float, bool, type(None)), path)


def assert_usable_png(test: unittest.TestCase, result: GeneratedBackground) -> None:
    test.assertIsInstance(result, GeneratedBackground)
    test.assertEqual(result.mime_type, "image/png")
    test.assertIsInstance(result.data, bytes)
    test.assertTrue(result.data)
    with Image.open(BytesIO(result.data)) as decoded:
        decoded.load()
        test.assertEqual(decoded.format, "PNG")
        test.assertEqual(decoded.size, (result.width, result.height))
        if "A" in decoded.getbands():
            alpha = decoded.getchannel("A")
            test.assertEqual(alpha.getextrema(), (255, 255))


class BackgroundGeneratorContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixture_is_deterministic_and_usable_for_every_allowed_style(self) -> None:
        self.assertEqual(set(ALLOWED_BACKGROUND_STYLE_PROMPTS), EXPECTED_STYLE_IDS)
        first_fixture = FixtureBackgroundGenerator()
        second_fixture = FixtureBackgroundGenerator()
        fixture_bytes: set[bytes] = set()

        for style_id in ALLOWED_BACKGROUND_STYLE_PROMPTS:
            with self.subTest(style_id=style_id):
                first = await first_fixture.generate(style_id)
                repeated = await first_fixture.generate(style_id)
                from_second_instance = await second_fixture.generate(style_id)

                self.assertEqual(first, repeated)
                self.assertEqual(first, from_second_instance)
                assert_usable_png(self, first)
                fixture_bytes.add(first.data)

        self.assertEqual(len(fixture_bytes), len(EXPECTED_STYLE_IDS))

    async def test_live_request_contains_exact_safe_fields_fixed_prompt_and_60_second_timeout(
        self,
    ) -> None:
        self.assertEqual(BACKGROUND_GENERATION_TIMEOUT_SECONDS, 60.0)

        for style_id, prompt_text in ALLOWED_BACKGROUND_STYLE_PROMPTS.items():
            with self.subTest(style_id=style_id):
                client = RequestSpyImagesClient(valid_response())
                generator = BackgroundGenerator(client, GPT_IMAGE_MODEL)
                recorded_timeouts: list[float | None] = []
                real_wait_for = asyncio.wait_for

                async def recording_wait_for(awaitable: Any, timeout: float | None) -> object:
                    recorded_timeouts.append(timeout)
                    return await real_wait_for(awaitable, timeout=timeout)

                with patch.object(
                    background_generator_module.asyncio,
                    "wait_for",
                    recording_wait_for,
                ):
                    result = await generator.generate(style_id)

                assert_usable_png(self, result)
                self.assertEqual(recorded_timeouts, [BACKGROUND_GENERATION_TIMEOUT_SECONDS])
                self.assertEqual(
                    client.calls,
                    [
                        {
                            "model": GPT_IMAGE_MODEL,
                            "prompt": prompt_text,
                            "n": 1,
                            "output_format": "png",
                            "timeout": BACKGROUND_GENERATION_TIMEOUT_SECONDS,
                        }
                    ],
                )
                assert_text_only_request(self, client.calls[0])

                normalized_prompt = prompt_text.casefold()
                self.assertIn("empty", normalized_prompt)
                self.assertIn("background", normalized_prompt)
                self.assertTrue(
                    "top-down" in normalized_prompt or "overhead" in normalized_prompt
                )
                self.assertIn("even lighting", normalized_prompt)
                self.assertTrue(
                    "no person" in normalized_prompt or "no people" in normalized_prompt
                )
                self.assertIn("no ", normalized_prompt)
                for forbidden_subject in ("clothing", "garment", "hanger", "text", "logo"):
                    self.assertIn(forbidden_subject, normalized_prompt)

    async def test_official_sdk_serializes_only_the_safe_text_generation_body(self) -> None:
        seen_request: dict[str, object] = {}

        async def respond(request: httpx.Request) -> httpx.Response:
            seen_request.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "created": 0,
                    "data": [{"b64_json": encoded_image(image_bytes())}],
                },
            )

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        client = AsyncOpenAI(
            api_key="offline-background-contract-key",
            base_url="https://openai.invalid/v1",
            http_client=http_client,
        )
        try:
            result = await BackgroundGenerator(client.images, GPT_IMAGE_MODEL).generate(
                "studio-white"
            )
        finally:
            await client.close()

        assert_usable_png(self, result)
        self.assertEqual(
            set(seen_request),
            {"model", "prompt", "n", "output_format"},
        )
        self.assertEqual(
            seen_request["prompt"],
            ALLOWED_BACKGROUND_STYLE_PROMPTS["studio-white"],
        )
        assert_text_only_request(self, seen_request)

    async def test_dall_e_request_uses_its_compatible_base64_parameter(self) -> None:
        client = RequestSpyImagesClient(valid_response())

        result = await BackgroundGenerator(client, DALL_E_MODEL).generate(
            "studio-white"
        )

        assert_usable_png(self, result)
        self.assertEqual(
            client.calls,
            [
                {
                    "model": DALL_E_MODEL,
                    "prompt": ALLOWED_BACKGROUND_STYLE_PROMPTS["studio-white"],
                    "n": 1,
                    "timeout": BACKGROUND_GENERATION_TIMEOUT_SECONDS,
                    "response_format": "b64_json",
                }
            ],
        )
        assert_text_only_request(self, client.calls[0])

    async def test_unsupported_model_is_rejected_before_any_provider_call(self) -> None:
        for model in ("custom-image-model", "gpt-image-", "gpt-image-not-real"):
            with self.subTest(model=model):
                client = RequestSpyImagesClient(valid_response())

                with self.assertRaises(BackgroundGenerationContractError):
                    BackgroundGenerator(client, model)

                self.assertEqual(client.calls, [])

    async def test_object_and_mapping_responses_are_accepted_without_rewriting_png(self) -> None:
        expected = image_bytes()
        responses = (
            ImageResponse(data=[ImagePayload(b64_json=encoded_image(expected))]),
            {"data": [{"b64_json": encoded_image(expected)}]},
        )

        for response in responses:
            with self.subTest(response_type=type(response).__name__):
                result = await BackgroundGenerator(
                    RequestSpyImagesClient(response), GPT_IMAGE_MODEL
                ).generate("studio-white")

                self.assertEqual(result.data, expected)
                self.assertEqual((result.width, result.height), (6, 4))
                self.assertEqual(result.mime_type, "image/png")

    async def test_invalid_style_ids_are_rejected_without_provider_calls(self) -> None:
        invalid_style_ids: tuple[object, ...] = (
            "",
            "   ",
            "unknown-style",
            "STUDIO-WHITE",
            " studio-white",
            "studio-white ",
            None,
            7,
            b"studio-white",
        )

        for style_id in invalid_style_ids:
            with self.subTest(style_id=style_id):
                client = RequestSpyImagesClient(valid_response())
                generator = BackgroundGenerator(client, GPT_IMAGE_MODEL)

                with self.assertRaises(BackgroundGenerationContractError):
                    await generator.generate(style_id)  # type: ignore[arg-type]

                self.assertEqual(client.calls, [])

    async def test_fixture_rejects_invalid_style_instead_of_hiding_input_error(self) -> None:
        fixture = FixtureBackgroundGenerator()

        for style_id in ("unknown-style", "STUDIO-WHITE", " studio-white", None):
            with self.subTest(style_id=style_id):
                with self.assertRaises(BackgroundGenerationContractError):
                    await fixture.generate(style_id)  # type: ignore[arg-type]

    async def test_timeout_is_explicit_and_never_becomes_fixture_success(self) -> None:
        client = RequestSpyImagesClient(TimeoutError("private provider timeout"))
        generator = BackgroundGenerator(client, GPT_IMAGE_MODEL)

        with self.assertRaises(BackgroundGenerationTimeoutError):
            await generator.generate("studio-white")

        self.assertEqual(len(client.calls), 1)

    async def test_local_deadline_cancels_a_hanging_provider(self) -> None:
        class HangingImagesClient:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            async def generate(self, **kwargs: object) -> object:
                self.calls.append(kwargs)
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        client = HangingImagesClient()
        with patch.object(
            background_generator_module,
            "BACKGROUND_GENERATION_TIMEOUT_SECONDS",
            0.001,
        ):
            with self.assertRaises(BackgroundGenerationTimeoutError):
                await BackgroundGenerator(client, GPT_IMAGE_MODEL).generate(
                    "studio-white"
                )

        self.assertEqual(len(client.calls), 1)

    async def test_official_sdk_timeout_is_reported_as_an_explicit_timeout(self) -> None:
        provider_timeout = APITimeoutError(
            request=httpx.Request("POST", "https://openai.invalid/v1/images/generations")
        )
        client = RequestSpyImagesClient(provider_timeout)

        with self.assertRaises(BackgroundGenerationTimeoutError) as raised:
            await BackgroundGenerator(client, GPT_IMAGE_MODEL).generate(
                "studio-white"
            )

        self.assertIs(raised.exception.__cause__, provider_timeout)
        self.assertEqual(len(client.calls), 1)

    async def test_provider_error_is_explicit_and_never_becomes_fixture_success(self) -> None:
        fixture_result = await FixtureBackgroundGenerator().generate("studio-white")
        provider_error = RuntimeError("private provider details")
        client = RequestSpyImagesClient(provider_error)
        generator = BackgroundGenerator(client, GPT_IMAGE_MODEL)

        with self.assertRaises(BackgroundGenerationProviderError) as raised:
            result = await generator.generate("studio-white")
            self.assertNotEqual(result, fixture_result)

        self.assertNotIsInstance(raised.exception, BackgroundGenerationTimeoutError)
        self.assertIs(raised.exception.__cause__, provider_error)
        self.assertEqual(len(client.calls), 1)

    async def test_missing_empty_or_multiple_response_data_is_rejected(self) -> None:
        valid_item = {"b64_json": encoded_image(image_bytes())}
        invalid_responses = (
            object(),
            {},
            {"data": None},
            {"data": []},
            {"data": [valid_item, valid_item]},
            ImageResponse(data=None),
            ImageResponse(data=[]),
            ImageResponse(
                data=[
                    ImagePayload(valid_item["b64_json"]),
                    ImagePayload(valid_item["b64_json"]),
                ]
            ),
        )

        for response in invalid_responses:
            with self.subTest(response=response):
                client = RequestSpyImagesClient(response)
                with self.assertRaises(BackgroundGenerationContractError):
                    await BackgroundGenerator(client, GPT_IMAGE_MODEL).generate(
                        "studio-white"
                    )
                self.assertEqual(len(client.calls), 1)

    async def test_missing_empty_non_string_or_invalid_base64_is_rejected(self) -> None:
        invalid_payloads = (
            {},
            {"b64_json": None},
            {"b64_json": ""},
            {"b64_json": b"not-text"},
            {"b64_json": "%%%not-base64%%%"},
            {"b64_json": "YWJjZA"},
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                client = RequestSpyImagesClient({"data": [payload]})
                with self.assertRaises(BackgroundGenerationContractError):
                    await BackgroundGenerator(client, GPT_IMAGE_MODEL).generate(
                        "studio-white"
                    )
                self.assertEqual(len(client.calls), 1)

    async def test_decodable_non_image_and_truncated_png_are_rejected(self) -> None:
        complete_png = image_bytes()
        invalid_images = (
            b"plain text is not an image",
            b"{\"still\": \"not an image\"}",
            complete_png[:-12],
        )

        for invalid_image in invalid_images:
            with self.subTest(prefix=invalid_image[:12]):
                response = {"data": [{"b64_json": encoded_image(invalid_image)}]}
                with self.assertRaises(BackgroundGenerationContractError):
                    await BackgroundGenerator(
                        RequestSpyImagesClient(response), GPT_IMAGE_MODEL
                    ).generate("studio-white")

    async def test_decodable_non_png_is_rejected(self) -> None:
        jpeg = image_bytes("JPEG")
        response = {"data": [{"b64_json": encoded_image(jpeg)}]}

        with self.assertRaises(BackgroundGenerationContractError):
            await BackgroundGenerator(
                RequestSpyImagesClient(response), GPT_IMAGE_MODEL
            ).generate("studio-white")

    async def test_fully_transparent_png_is_rejected_as_unusable(self) -> None:
        transparent_png = image_bytes(mode="RGBA", color=(0, 0, 0, 0))
        response = {"data": [{"b64_json": encoded_image(transparent_png)}]}

        with self.assertRaises(BackgroundGenerationContractError):
            await BackgroundGenerator(
                RequestSpyImagesClient(response), GPT_IMAGE_MODEL
            ).generate("studio-white")


if __name__ == "__main__":
    unittest.main()
