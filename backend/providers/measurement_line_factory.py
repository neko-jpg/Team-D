"""Explicit provider-mode selection for measurement endpoint suggestions."""

from __future__ import annotations

import os

from ..settings import BackendSettings, ProviderMode
from .measurement_line import (
    MeasurementEndpoints,
    MeasurementLineContractError,
    MeasurementLineInput,
    MeasurementLineProvider,
    NormalizedPoint,
    ResponsesClient,
    ResponsesMeasurementLineProvider,
)


class LiveMeasurementLineProviderUnavailable(RuntimeError):
    """The explicitly selected live measurement provider cannot be used."""


class FixtureMeasurementLineProvider:
    """Deterministic endpoint proposal used only in explicit fixture mode."""

    async def suggest(self, input: MeasurementLineInput) -> MeasurementEndpoints:
        if not isinstance(input, MeasurementLineInput):
            raise MeasurementLineContractError("input must be a MeasurementLineInput")
        return MeasurementEndpoints(
            length_start=NormalizedPoint(0.50, 0.16),
            length_end=NormalizedPoint(0.50, 0.88),
            width_start=NormalizedPoint(0.24, 0.36),
            width_end=NormalizedPoint(0.76, 0.36),
        )


class _UnavailableLiveMeasurementLineProvider:
    async def suggest(self, input: MeasurementLineInput) -> MeasurementEndpoints:
        raise LiveMeasurementLineProviderUnavailable(
            "live MeasurementLineProvider is unavailable; explicitly restart with "
            "PROVIDER_MODE=fixture to use deterministic fixture responses"
        )


def _create_responses_client() -> ResponsesClient:
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise LiveMeasurementLineProviderUnavailable(
            "OPENAI_API_KEY is required for PROVIDER_MODE=live"
        )
    try:
        from openai import AsyncOpenAI  # type: ignore[import-not-found]
    except ImportError as error:
        raise LiveMeasurementLineProviderUnavailable(
            "openai package is required for PROVIDER_MODE=live"
        ) from error
    try:
        return AsyncOpenAI().responses  # type: ignore[no-any-return]
    except Exception as error:
        raise LiveMeasurementLineProviderUnavailable(
            "OpenAI Responses client could not be configured"
        ) from error


def create_measurement_line_provider(
    settings: BackendSettings | None = None,
    *,
    live_provider: MeasurementLineProvider | None = None,
    live_client: ResponsesClient | None = None,
    live_model: str | None = None,
) -> MeasurementLineProvider:
    """Return only the explicitly selected fixture or live provider."""

    resolved_settings = settings or BackendSettings.from_env()
    if resolved_settings.provider_mode is ProviderMode.FIXTURE:
        return FixtureMeasurementLineProvider()

    if live_provider is not None:
        return live_provider
    try:
        client = live_client or _create_responses_client()
        model = live_model or os.environ.get("MEASUREMENT_LINE_MODEL", "").strip()
        if not model:
            model = "gpt-5.6-luna"
        return ResponsesMeasurementLineProvider(client, model)
    except (LiveMeasurementLineProviderUnavailable, MeasurementLineContractError):
        return _UnavailableLiveMeasurementLineProvider()


__all__ = [
    "FixtureMeasurementLineProvider",
    "LiveMeasurementLineProviderUnavailable",
    "create_measurement_line_provider",
]
