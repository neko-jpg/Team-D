"""Compatibility imports for the canonical backend settings module.

``backend.settings`` owns the only settings implementation.  This module
keeps the phase-3 ``backend.config`` import path stable for existing callers.
"""

from .settings import BackendSettings, ProviderMode, SettingsError


ConfigurationError = SettingsError


__all__ = [
    "BackendSettings",
    "ConfigurationError",
    "ProviderMode",
    "SettingsError",
]
