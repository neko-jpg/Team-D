"""External provider contracts used by the backend and Agent."""

from .runtime import (
    FixtureVisionGuidanceProvider,
    LiveAnalyzer,
    LiveVisionGuidanceProvider,
    ProviderInference,
    ProviderUnavailableError,
    create_provider_inference,
    create_vision_guidance_provider,
    guidance_input_from_frame,
)
from .vision_guidance import (
    EncodedImage,
    GuidanceCode,
    GuidanceContractError,
    GuidanceInput,
    GuidanceShot,
    VisionDecision,
    VisionGuidanceProvider,
)
from .shot_assessor import (
    AssessmentImage,
    ResponsesShotAssessor,
    ShotAssessment,
    ShotAssessmentContractError,
    ShotAssessor,
    ShotAssessorInput,
    validate_shot_assessment,
)
from .shot_assessor_factory import FixtureShotAssessor, create_shot_assessor


__all__ = [
    "AssessmentImage",
    "EncodedImage",
    "FixtureShotAssessor",
    "FixtureVisionGuidanceProvider",
    "GuidanceCode",
    "GuidanceContractError",
    "GuidanceInput",
    "GuidanceShot",
    "LiveAnalyzer",
    "LiveVisionGuidanceProvider",
    "ProviderInference",
    "ProviderUnavailableError",
    "ResponsesShotAssessor",
    "ShotAssessment",
    "ShotAssessmentContractError",
    "ShotAssessor",
    "ShotAssessorInput",
    "VisionDecision",
    "VisionGuidanceProvider",
    "create_provider_inference",
    "create_shot_assessor",
    "create_vision_guidance_provider",
    "guidance_input_from_frame",
    "validate_shot_assessment",
]
