"""Explicit ``PROVIDER_MODE`` selection for post-capture assessment.

The factory is the only place that selects a deterministic fixture provider.
In particular, the live adapter never catches an error and retries through the
fixture implementation: operators must restart with ``PROVIDER_MODE=fixture``
when they deliberately choose the demo fallback.
"""

from __future__ import annotations

import os

from ..config import BackendSettings
from .shot_assessor import (
    NextAction,
    RequestedShot,
    ResponsesClient,
    ResponsesShotAssessor,
    ShotAssessment,
    ShotAssessor,
    ShotAssessorInput,
)


class LiveShotAssessorUnavailable(RuntimeError):
    """The explicitly selected live provider cannot be constructed."""


class FixtureShotAssessor:
    """Deterministic post-capture result for the explicit fixture mode."""

    async def assess(self, input: ShotAssessorInput) -> ShotAssessment:
        missing_by_requested_shot = {
            RequestedShot.FRONT: (RequestedShot.BACK, RequestedShot.TAG),
            RequestedShot.BACK: (RequestedShot.TAG,),
            RequestedShot.TAG: (),
        }
        missing = missing_by_requested_shot[input.requested_shot]
        return ShotAssessment(
            shot_type=input.requested_shot.value,
            quality="ok",
            issues=(),
            missing_shots=missing,
            next_action=NextAction.COMPLETE if not missing else NextAction.REQUEST_NEXT,
        )


class _UnavailableLiveShotAssessor:
    async def assess(self, input: ShotAssessorInput) -> ShotAssessment:
        raise LiveShotAssessorUnavailable(
            "live ShotAssessor is unavailable; explicitly restart with PROVIDER_MODE=fixture "
            "to use deterministic fixture responses"
        )


def _create_responses_client() -> ResponsesClient:
    """Create the optional Responses client only for an explicit live mode."""

    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise LiveShotAssessorUnavailable("OPENAI_API_KEY is required for PROVIDER_MODE=live")
    try:
        from openai import AsyncOpenAI  # type: ignore[import-not-found]
    except ImportError as error:
        raise LiveShotAssessorUnavailable(
            "openai package is required for PROVIDER_MODE=live"
        ) from error
    return AsyncOpenAI().responses  # type: ignore[no-any-return]


def create_shot_assessor(
    settings: BackendSettings | None = None,
    *,
    live_assessor: ShotAssessor | None = None,
    live_client: ResponsesClient | None = None,
    live_model: str | None = None,
) -> ShotAssessor:
    """Return exactly the provider selected by ``PROVIDER_MODE``.

    ``live_assessor`` and ``live_client`` are injection seams for contract
    tests and deployments.  Neither is consulted in fixture mode.
    """

    resolved_settings = settings or BackendSettings.from_env()
    if resolved_settings.provider_mode == "fixture":
        return FixtureShotAssessor()

    if live_assessor is not None:
        return live_assessor
    try:
        client = live_client or _create_responses_client()
        model = live_model or os.environ.get(
            "SHOT_ASSESSOR_MODEL", "gpt-4.1-mini-2025-04-14"
        )
        return ResponsesShotAssessor(client, model)
    except LiveShotAssessorUnavailable:
        return _UnavailableLiveShotAssessor()


def get_configured_shot_assessor() -> ShotAssessor:
    """FastAPI dependency used by the app wiring; mode is read explicitly."""

    return create_shot_assessor()


__all__ = [
    "FixtureShotAssessor",
    "LiveShotAssessorUnavailable",
    "create_shot_assessor",
    "get_configured_shot_assessor",
]
