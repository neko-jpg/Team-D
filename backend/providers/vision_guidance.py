"""Runtime-validated contract for live vision guidance providers.

The provider receives exactly one already-selected, downscaled frame and the
current capture shot. It may return only a finite guidance code and a
confidence. UI copy, ordering, expiry, deduplication, and transport belong to
``GuidanceStateMachine`` rather than to a model provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from types import MappingProxyType
from typing import Mapping, Protocol, runtime_checkable


class GuidanceContractError(ValueError):
    """Raised when a value violates the provider contract."""


class GuidanceCode(str, Enum):
    MOVE_CLOSER = "MOVE_CLOSER"
    MOVE_FARTHER = "MOVE_FARTHER"
    CENTER_GARMENT = "CENTER_GARMENT"
    SHOW_FULL_GARMENT = "SHOW_FULL_GARMENT"
    WRONG_SIDE = "WRONG_SIDE"
    MOVE_TO_TAG = "MOVE_TO_TAG"
    PLACE_MARKER = "PLACE_MARKER"
    MARKER_NOT_VISIBLE = "MARKER_NOT_VISIBLE"
    FLATTEN_GARMENT = "FLATTEN_GARMENT"
    CAMERA_OVERHEAD = "CAMERA_OVERHEAD"
    HOLD_STEADY = "HOLD_STEADY"
    READY = "READY"
    AGENT_UNAVAILABLE = "AGENT_UNAVAILABLE"


class GuidanceShot(str, Enum):
    FRONT = "front"
    BACK = "back"
    TAG = "tag"
    MEASUREMENT = "measurement"


GUIDANCE_CODES = tuple(code.value for code in GuidanceCode)
GUIDANCE_SHOTS = tuple(shot.value for shot in GuidanceShot)

_COMMON_GARMENT_CODES = frozenset(
    {
        GuidanceCode.MOVE_CLOSER,
        GuidanceCode.MOVE_FARTHER,
        GuidanceCode.CENTER_GARMENT,
        GuidanceCode.SHOW_FULL_GARMENT,
        GuidanceCode.HOLD_STEADY,
        GuidanceCode.READY,
        GuidanceCode.AGENT_UNAVAILABLE,
    }
)

# A strict schema can constrain the enum, but only this application-owned
# mapping can prevent a structurally valid measurement hint from being shown
# during front capture (or vice versa).
GUIDANCE_CODES_BY_SHOT: Mapping[GuidanceShot, frozenset[GuidanceCode]] = MappingProxyType(
    {
        GuidanceShot.FRONT: _COMMON_GARMENT_CODES
        | {GuidanceCode.WRONG_SIDE, GuidanceCode.FLATTEN_GARMENT},
        GuidanceShot.BACK: _COMMON_GARMENT_CODES
        | {GuidanceCode.WRONG_SIDE, GuidanceCode.FLATTEN_GARMENT},
        GuidanceShot.TAG: frozenset(
            {
                GuidanceCode.MOVE_CLOSER,
                GuidanceCode.MOVE_FARTHER,
                GuidanceCode.CENTER_GARMENT,
                GuidanceCode.MOVE_TO_TAG,
                GuidanceCode.HOLD_STEADY,
                GuidanceCode.READY,
                GuidanceCode.AGENT_UNAVAILABLE,
            }
        ),
        GuidanceShot.MEASUREMENT: _COMMON_GARMENT_CODES
        | {
            GuidanceCode.WRONG_SIDE,
            GuidanceCode.PLACE_MARKER,
            GuidanceCode.MARKER_NOT_VISIBLE,
            GuidanceCode.FLATTEN_GARMENT,
            GuidanceCode.CAMERA_OVERHEAD,
        },
    }
)

# ``HOLD_STEADY`` is derived from temporal frame quality and
# ``AGENT_UNAVAILABLE`` is emitted by the backend failure path.  They remain
# valid application events, but a single-image model must never manufacture
# either state.
MODEL_GUIDANCE_CODES_BY_SHOT: Mapping[GuidanceShot, frozenset[GuidanceCode]] = (
    MappingProxyType(
        {
            shot: frozenset(
                code
                for code in codes
                if code
                not in {GuidanceCode.HOLD_STEADY, GuidanceCode.AGENT_UNAVAILABLE}
                and not (
                    shot is GuidanceShot.MEASUREMENT
                    and code is GuidanceCode.WRONG_SIDE
                )
            )
            for shot, codes in GUIDANCE_CODES_BY_SHOT.items()
        }
    )
)


def _enum_value(enum_type: type[Enum], value: object, field: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise GuidanceContractError(f"{field} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_type)
        raise GuidanceContractError(f"{field} must be one of: {allowed}") from exc


def validate_guidance_code(value: object) -> GuidanceCode:
    return _enum_value(GuidanceCode, value, "code")  # type: ignore[return-value]


def validate_guidance_shot(value: object) -> GuidanceShot:
    return _enum_value(GuidanceShot, value, "requestedShot")  # type: ignore[return-value]


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GuidanceContractError("confidence must be a finite number")
    converted = float(value)
    if not isfinite(converted) or not 0.0 <= converted <= 1.0:
        raise GuidanceContractError("confidence must be between 0 and 1")
    return converted


@dataclass(frozen=True, slots=True)
class EncodedImage:
    """One encoded, downscaled frame selected by the Agent processor."""

    data: bytes
    mime_type: str = "image/jpeg"
    width: int | None = None
    height: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes) or not self.data:
            raise GuidanceContractError("frame.data must be non-empty bytes")
        if not isinstance(self.mime_type, str) or not self.mime_type.startswith("image/"):
            raise GuidanceContractError("frame.mime_type must be an image MIME type")
        for field, value in (("width", self.width), ("height", self.height)):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise GuidanceContractError(f"frame.{field} must be a positive integer")


@dataclass(frozen=True, slots=True)
class GuidanceInput:
    frame: EncodedImage
    requested_shot: GuidanceShot
    previous_code: GuidanceCode | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.frame, EncodedImage):
            raise GuidanceContractError("frame must be an EncodedImage")
        object.__setattr__(self, "requested_shot", validate_guidance_shot(self.requested_shot))
        if self.previous_code is not None:
            object.__setattr__(self, "previous_code", validate_guidance_code(self.previous_code))

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "GuidanceInput":
        if not isinstance(value, Mapping):
            raise GuidanceContractError("guidance input must be an object")
        allowed = {"frame", "requestedShot", "previousCode"}
        if set(value) - allowed or not {"frame", "requestedShot"} <= set(value):
            raise GuidanceContractError(
                "guidance input requires only frame, requestedShot, and optional previousCode"
            )
        raw_frame = value["frame"]
        if isinstance(raw_frame, Mapping):
            frame_allowed = {"data", "mimeType", "width", "height"}
            if set(raw_frame) - frame_allowed or "data" not in raw_frame:
                raise GuidanceContractError("frame contains unknown or missing fields")
            frame = EncodedImage(
                data=raw_frame["data"],  # type: ignore[arg-type]
                mime_type=raw_frame.get("mimeType", "image/jpeg"),  # type: ignore[arg-type]
                width=raw_frame.get("width"),  # type: ignore[arg-type]
                height=raw_frame.get("height"),  # type: ignore[arg-type]
            )
        elif isinstance(raw_frame, bytes):
            frame = EncodedImage(raw_frame)
        else:
            raise GuidanceContractError("frame must be encoded bytes or an image object")
        return cls(
            frame=frame,
            requested_shot=validate_guidance_shot(value["requestedShot"]),
            previous_code=(
                None
                if value.get("previousCode") is None
                else validate_guidance_code(value["previousCode"])
            ),
        )

    @property
    def requestedShot(self) -> str:
        return self.requested_shot.value

    @property
    def previousCode(self) -> str | None:
        return None if self.previous_code is None else self.previous_code.value


@dataclass(frozen=True, slots=True)
class VisionDecision:
    code: GuidanceCode
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", validate_guidance_code(self.code))
        object.__setattr__(self, "confidence", _confidence(self.confidence))

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "VisionDecision":
        if not isinstance(value, Mapping) or set(value) != {"code", "confidence"}:
            raise GuidanceContractError("vision decision requires only code and confidence")
        return cls(
            code=validate_guidance_code(value["code"]),
            confidence=_confidence(value["confidence"]),
        )


def validate_guidance_input(value: object) -> GuidanceInput:
    if isinstance(value, GuidanceInput):
        return value
    if isinstance(value, Mapping):
        return GuidanceInput.from_mapping(value)
    raise GuidanceContractError("guidance input must be GuidanceInput or an object")


def validate_vision_decision(value: object) -> VisionDecision:
    if isinstance(value, VisionDecision):
        return value
    if isinstance(value, Mapping):
        return VisionDecision.from_mapping(value)
    raise GuidanceContractError("vision decision must be VisionDecision or an object")


def validate_vision_decision_for_shot(value: object, shot: object) -> VisionDecision:
    """Validate both the finite result shape and its current-step meaning."""

    shot_value = validate_guidance_shot(shot)
    decision = validate_vision_decision(value)
    if decision.code not in GUIDANCE_CODES_BY_SHOT[shot_value]:
        raise GuidanceContractError(
            f"code {decision.code.value} is not valid for requestedShot={shot_value.value}"
        )
    return decision


def validate_model_vision_decision_for_shot(
    value: object, shot: object
) -> VisionDecision:
    """Reject application-owned states from a single-image model result."""

    shot_value = validate_guidance_shot(shot)
    decision = validate_vision_decision(value)
    if decision.code not in MODEL_GUIDANCE_CODES_BY_SHOT[shot_value]:
        raise GuidanceContractError(
            f"code {decision.code.value} is not valid model guidance for "
            f"requestedShot={shot_value.value}"
        )
    return decision


@runtime_checkable
class VisionGuidanceProvider(Protocol):
    async def analyze(self, input: GuidanceInput) -> VisionDecision | Mapping[str, object]:
        """Analyze one selected frame and return a finite decision."""


__all__ = [
    "EncodedImage",
    "GUIDANCE_CODES",
    "GUIDANCE_CODES_BY_SHOT",
    "GUIDANCE_SHOTS",
    "MODEL_GUIDANCE_CODES_BY_SHOT",
    "GuidanceCode",
    "GuidanceContractError",
    "GuidanceInput",
    "GuidanceShot",
    "VisionDecision",
    "VisionGuidanceProvider",
    "validate_guidance_code",
    "validate_guidance_input",
    "validate_guidance_shot",
    "validate_model_vision_decision_for_shot",
    "validate_vision_decision",
    "validate_vision_decision_for_shot",
]
