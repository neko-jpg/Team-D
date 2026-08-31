"""Persistent OpenAI Realtime adapter for finite live garment guidance.

One analyzer instance owns one WebSocket session.  Camera frames are sent as
out-of-band responses so neither inputs nor outputs accumulate in the default
conversation.  The model may return only one application-owned guidance code;
UI copy, confidence, deduplication, and capture progression remain outside the
model boundary.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
from io import BytesIO
import json
from math import isfinite
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from PIL import Image, ImageOps

from .vision_guidance import (
    GUIDANCE_CODES_BY_SHOT,
    EncodedImage,
    GuidanceCode,
    GuidanceContractError,
    GuidanceInput,
    GuidanceShot,
    VisionDecision,
    validate_guidance_input,
    validate_vision_decision_for_shot,
)


DEFAULT_REALTIME_GUIDANCE_MODEL = "gpt-realtime-mini"
DEFAULT_REALTIME_RESPONSE_TIMEOUT_SECONDS = 0.90
DEFAULT_REALTIME_CONNECT_TIMEOUT_SECONDS = 8.0
DEFAULT_REALTIME_PREWARM_TIMEOUT_SECONDS = 8.0
DEFAULT_REALTIME_IMAGE_MAX_EDGE = 256
DEFAULT_REALTIME_JPEG_QUALITY = 55
DEFAULT_REALTIME_MAX_OUTPUT_TOKENS = 32

_SUPPORTED_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
_PREWARM_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAFUlEQVR4nGP8//8/AzbAhFV00EoAAFbUAw037MyjAAAAAElFTkSuQmCC"
)


class RealtimeGuidanceError(RuntimeError):
    """A finite, non-secret failure at the Realtime provider boundary."""


class RealtimeGuidanceTimeoutError(RealtimeGuidanceError, TimeoutError):
    """The configured sub-second response deadline elapsed."""


@runtime_checkable
class RealtimeConnectResource(Protocol):
    def connect(self, **kwargs: object) -> object:
        """Return an async connection manager exposing ``enter``."""


@runtime_checkable
class RealtimeClient(Protocol):
    realtime: RealtimeConnectResource

    async def close(self) -> None:
        """Release the SDK client's network resources."""


def _positive_finite(value: object, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(float(value))
        or float(value) <= 0
    ):
        raise GuidanceContractError(f"{field} must be a finite positive number")
    return float(value)


def _bounded_integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise GuidanceContractError(f"{field} must be between {minimum} and {maximum}")
    return value


def _allowed_codes(shot: GuidanceShot) -> tuple[str, ...]:
    return tuple(sorted(code.value for code in GUIDANCE_CODES_BY_SHOT[shot]))


def _instruction_for(shot: GuidanceShot, previous_code: GuidanceCode | None) -> str:
    allowed = "|".join(_allowed_codes(shot))
    previous = "NONE" if previous_code is None else previous_code.value
    if shot in {GuidanceShot.FRONT, GuidanceShot.BACK}:
        criteria = "clipped=SHOW_FULL_GARMENT;off-center=CENTER_GARMENT;wrong-side=WRONG_SIDE;folded=FLATTEN_GARMENT;otherwise=READY"
    elif shot is GuidanceShot.TAG:
        criteria = "tag-not-subject=MOVE_TO_TAG;too-small=MOVE_CLOSER;off-center=CENTER_GARMENT;readable=READY"
    else:
        criteria = "clipped=SHOW_FULL_GARMENT;no-marker=MARKER_NOT_VISIBLE;bad-marker=PLACE_MARKER;not-top-down=CAMERA_OVERHEAD;folded=FLATTEN_GARMENT;otherwise=READY"
    return (
        f"shot={shot.value};prev={previous};{criteria}. "
        f"Call guidance once with one of:{allowed}"
    )


def _guidance_tool(shot: GuidanceShot) -> dict[str, object]:
    return {
        "type": "function",
        "name": "guidance",
        "description": "Report the single current camera guidance code.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["code"],
            "properties": {
                "code": {"type": "string", "enum": list(_allowed_codes(shot))}
            },
        },
    }


def _prepare_image(
    frame: EncodedImage,
    *,
    max_edge: int,
    jpeg_quality: int,
) -> tuple[str, bytes, int, int]:
    if frame.mime_type not in _SUPPORTED_IMAGE_MIME_TYPES:
        raise GuidanceContractError("Realtime guidance frame must be JPEG, PNG, or WebP")
    try:
        with Image.open(BytesIO(frame.data)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="JPEG", quality=jpeg_quality, optimize=False)
            encoded = output.getvalue()
            width, height = image.size
    except (OSError, ValueError) as error:
        raise GuidanceContractError("Realtime guidance frame is not a valid image") from error
    if not encoded or width <= 0 or height <= 0:
        raise GuidanceContractError("Realtime guidance frame could not be encoded")
    return "image/jpeg", encoded, width, height


class OpenAIRealtimeVisionGuidanceAnalyzer:
    """Analyze camera frames over one reusable OpenAI Realtime WebSocket."""

    def __init__(
        self,
        client: RealtimeClient,
        model: str = DEFAULT_REALTIME_GUIDANCE_MODEL,
        *,
        response_timeout_seconds: float = DEFAULT_REALTIME_RESPONSE_TIMEOUT_SECONDS,
        connect_timeout_seconds: float = DEFAULT_REALTIME_CONNECT_TIMEOUT_SECONDS,
        prewarm_timeout_seconds: float = DEFAULT_REALTIME_PREWARM_TIMEOUT_SECONDS,
        image_max_edge: int = DEFAULT_REALTIME_IMAGE_MAX_EDGE,
        jpeg_quality: int = DEFAULT_REALTIME_JPEG_QUALITY,
        max_output_tokens: int = DEFAULT_REALTIME_MAX_OUTPUT_TOKENS,
        reasoning_effort: str | None = None,
        prewarm: bool = True,
        confidence: float = 1.0,
    ) -> None:
        if not isinstance(client, RealtimeClient):
            raise GuidanceContractError("client must provide realtime.connect and async close")
        if not isinstance(model, str) or not model.strip():
            raise GuidanceContractError("model must be a non-empty string")
        if reasoning_effort not in {None, "minimal", "low", "medium", "high", "xhigh"}:
            raise GuidanceContractError("reasoning_effort is invalid")
        if not isinstance(prewarm, bool):
            raise GuidanceContractError("prewarm must be a boolean")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            raise GuidanceContractError("confidence must be between 0 and 1")

        self._client = client
        self._model = model.strip()
        self._response_timeout_seconds = _positive_finite(
            response_timeout_seconds, "response_timeout_seconds"
        )
        self._connect_timeout_seconds = _positive_finite(
            connect_timeout_seconds, "connect_timeout_seconds"
        )
        self._prewarm_timeout_seconds = _positive_finite(
            prewarm_timeout_seconds, "prewarm_timeout_seconds"
        )
        self._image_max_edge = _bounded_integer(
            image_max_edge, "image_max_edge", 32, 320
        )
        self._jpeg_quality = _bounded_integer(
            jpeg_quality, "jpeg_quality", 20, 95
        )
        self._max_output_tokens = _bounded_integer(
            max_output_tokens, "max_output_tokens", 1, 256
        )
        self._reasoning_effort = reasoning_effort
        self._prewarm_enabled = prewarm
        self._confidence = float(confidence)
        self._connection: Any | None = None
        self._connection_manager: Any | None = None
        self._lock = asyncio.Lock()
        self._warmed = False
        self._closed = False
        self._client_closed = False
        self._connect_count = 0
        self._request_count = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def connected(self) -> bool:
        return self._connection is not None and not self._closed

    @property
    def warmed(self) -> bool:
        return self._warmed and self.connected

    @property
    def connect_count(self) -> int:
        return self._connect_count

    @property
    def request_count(self) -> int:
        return self._request_count

    def new_session(self) -> "OpenAIRealtimeVisionGuidanceAnalyzer":
        """Create an unopened analyzer with the same immutable configuration."""

        client_type = type(self._client)
        try:
            client = client_type()
        except Exception as error:
            raise RealtimeGuidanceError(
                "Realtime client cannot create an isolated session"
            ) from error
        return OpenAIRealtimeVisionGuidanceAnalyzer(
            client,
            self._model,
            response_timeout_seconds=self._response_timeout_seconds,
            connect_timeout_seconds=self._connect_timeout_seconds,
            prewarm_timeout_seconds=self._prewarm_timeout_seconds,
            image_max_edge=self._image_max_edge,
            jpeg_quality=self._jpeg_quality,
            max_output_tokens=self._max_output_tokens,
            reasoning_effort=self._reasoning_effort,
            prewarm=self._prewarm_enabled,
            confidence=self._confidence,
        )

    def request_for(
        self,
        input_value: GuidanceInput,
        *,
        request_id: str,
        instructions: str | None = None,
    ) -> dict[str, object]:
        validated = validate_guidance_input(input_value)
        mime_type, encoded, _width, _height = _prepare_image(
            validated.frame,
            max_edge=self._image_max_edge,
            jpeg_quality=self._jpeg_quality,
        )
        response: dict[str, object] = {
            "conversation": "none",
            "metadata": {"request_id": request_id},
            "output_modalities": ["text"],
            "instructions": instructions
            or _instruction_for(validated.requested_shot, validated.previous_code),
            "max_output_tokens": self._max_output_tokens,
            "tools": [_guidance_tool(validated.requested_shot)],
            "tool_choice": {"type": "function", "name": "guidance"},
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": (
                                f"data:{mime_type};base64,"
                                + base64.b64encode(encoded).decode("ascii")
                            ),
                            "detail": "low",
                        },
                        {"type": "input_text", "text": "Call guidance now."},
                    ],
                }
            ],
        }
        if self._reasoning_effort is not None:
            response["reasoning"] = {"effort": self._reasoning_effort}
        return response

    async def _connect_locked(self) -> None:
        if self._closed:
            raise RealtimeGuidanceError("Realtime guidance session is closed")
        if self._connection is not None:
            return
        manager = self._client.realtime.connect(
            model=self._model,
            max_retries=0,
            max_queue_size=262_144,
        )
        enter = getattr(manager, "enter", None)
        if not callable(enter):
            raise RealtimeGuidanceError("Realtime connection manager is invalid")
        try:
            async with asyncio.timeout(self._connect_timeout_seconds):
                connection = await enter()
                while True:
                    event = await connection.recv()
                    if getattr(event, "type", None) == "session.created":
                        break
                    self._raise_server_error(event)
        except TimeoutError as error:
            await self._close_connection(connection if "connection" in locals() else None)
            raise RealtimeGuidanceTimeoutError(
                "Realtime guidance connection timed out"
            ) from error
        except Exception:
            await self._close_connection(connection if "connection" in locals() else None)
            raise
        self._connection_manager = manager
        self._connection = connection
        self._connect_count += 1

    @staticmethod
    def _raise_server_error(event: object) -> None:
        if getattr(event, "type", None) != "error":
            return
        error = getattr(event, "error", None)
        code = getattr(error, "code", None)
        suffix = f" ({code})" if isinstance(code, str) and code else ""
        raise RealtimeGuidanceError(f"Realtime guidance server error{suffix}")

    async def _request_text_locked(
        self,
        input_value: GuidanceInput,
        *,
        timeout_seconds: float,
        instructions: str | None = None,
    ) -> str:
        await self._connect_locked()
        connection = self._connection
        if connection is None:  # pragma: no cover - defensive invariant
            raise RealtimeGuidanceError("Realtime guidance connection is unavailable")
        request_id = uuid4().hex
        response_resource = getattr(connection, "response", None)
        create = getattr(response_resource, "create", None)
        if not callable(create):
            raise RealtimeGuidanceError("Realtime response resource is unavailable")
        self._request_count += 1
        output_text: str | None = None
        try:
            async with asyncio.timeout(timeout_seconds):
                await create(
                    response=self.request_for(
                        input_value,
                        request_id=request_id,
                        instructions=instructions,
                    )
                )
                while True:
                    event = await connection.recv()
                    self._raise_server_error(event)
                    event_type = getattr(event, "type", None)
                    if event_type == "response.output_text.done":
                        candidate = getattr(event, "text", None)
                        if isinstance(candidate, str):
                            output_text = candidate
                    if event_type == "response.function_call_arguments.done":
                        candidate = getattr(event, "arguments", None)
                        if isinstance(candidate, str):
                            output_text = candidate
                    if event_type != "response.done":
                        continue
                    response = getattr(event, "response", None)
                    metadata = getattr(response, "metadata", None)
                    if not isinstance(metadata, Mapping) or metadata.get(
                        "request_id"
                    ) != request_id:
                        continue
                    status = getattr(response, "status", None)
                    if status != "completed":
                        raise RealtimeGuidanceError(
                            f"Realtime guidance response ended with status={status}"
                        )
                    if output_text is None:
                        output_text = _response_output_text(response)
                    if not isinstance(output_text, str):
                        raise GuidanceContractError(
                            "Realtime guidance response did not contain text"
                        )
                    return output_text.strip()
        except TimeoutError as error:
            # Keep the already-warm socket. Closing it here makes every later
            # frame pay cold-connect latency and guarantees a timeout loop.
            # There is only one active response, so cancelling the current one
            # is unambiguous; its eventual cancelled response.done is ignored
            # by the next request's metadata fence.
            await self._cancel_current_response_locked()
            raise RealtimeGuidanceTimeoutError(
                "Realtime guidance response exceeded its deadline"
            ) from error
        except asyncio.CancelledError:
            await asyncio.shield(self._cancel_current_response_locked())
            raise
        except Exception:
            await self._reset_connection_locked()
            raise

    async def _cancel_current_response_locked(self) -> None:
        connection = self._connection
        response_resource = getattr(connection, "response", None)
        cancel = getattr(response_resource, "cancel", None)
        if not callable(cancel):
            await self._reset_connection_locked()
            return
        try:
            await cancel()
        except Exception:
            await self._reset_connection_locked()

    async def prewarm(self) -> None:
        """Open the socket and warm the image-response path before camera use."""

        async with self._lock:
            if self._closed:
                raise RealtimeGuidanceError("Realtime guidance session is closed")
            if self._warmed and self._connection is not None:
                return
            await self._connect_locked()
            if self._prewarm_enabled:
                warm_input = GuidanceInput(
                    frame=EncodedImage(_PREWARM_PNG, "image/png", 8, 8),
                    requested_shot=GuidanceShot.FRONT,
                )
                await self._request_text_locked(
                    warm_input,
                    timeout_seconds=self._prewarm_timeout_seconds,
                    instructions="Call guidance with READY",
                )
            self._warmed = True

    async def __call__(self, input_value: GuidanceInput) -> VisionDecision:
        validated = validate_guidance_input(input_value)
        async with self._lock:
            if not self._warmed or self._connection is None:
                await self._connect_locked()
                self._warmed = True
            text = await self._request_text_locked(
                validated,
                timeout_seconds=self._response_timeout_seconds,
            )
        code: object = text
        if text.startswith("{"):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as error:
                raise GuidanceContractError(
                    "Realtime guidance tool output must be valid JSON"
                ) from error
            if not isinstance(payload, Mapping) or set(payload) != {"code"}:
                raise GuidanceContractError(
                    "Realtime guidance tool output must contain only code"
                )
            code = payload["code"]
        return validate_vision_decision_for_shot(
            {"code": code, "confidence": self._confidence},
            validated.requested_shot,
        )

    async def _close_connection(self, connection: object | None) -> None:
        close = getattr(connection, "close", None)
        if callable(close):
            try:
                await close()
            except Exception:
                pass

    async def _reset_connection_locked(self) -> None:
        connection = self._connection
        self._connection = None
        self._connection_manager = None
        self._warmed = False
        await self._close_connection(connection)

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed and self._client_closed:
                return
            self._closed = True
            await self._reset_connection_locked()
            if not self._client_closed:
                self._client_closed = True
                await self._client.close()

    close = aclose


def _response_output_text(response: object) -> str | None:
    output = getattr(response, "output", None)
    if not isinstance(output, list):
        return None
    chunks: list[str] = []
    for item in output:
        if getattr(item, "type", None) != "message":
            continue
        content = getattr(item, "content", None)
        if not isinstance(content, list):
            continue
        for part in content:
            text = getattr(part, "text", None)
            if getattr(part, "type", None) == "output_text" and isinstance(text, str):
                chunks.append(text)
    return "".join(chunks) if chunks else None


__all__ = [
    "DEFAULT_REALTIME_CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_REALTIME_GUIDANCE_MODEL",
    "DEFAULT_REALTIME_IMAGE_MAX_EDGE",
    "DEFAULT_REALTIME_JPEG_QUALITY",
    "DEFAULT_REALTIME_MAX_OUTPUT_TOKENS",
    "DEFAULT_REALTIME_PREWARM_TIMEOUT_SECONDS",
    "DEFAULT_REALTIME_RESPONSE_TIMEOUT_SECONDS",
    "OpenAIRealtimeVisionGuidanceAnalyzer",
    "RealtimeClient",
    "RealtimeGuidanceError",
    "RealtimeGuidanceTimeoutError",
]
