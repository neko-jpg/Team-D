"""Prewarm the loopback rembg sidecar through the production mask contract.

This helper deliberately does not duplicate rembg request construction or mask
validation.  It delegates to :class:`GarmentMasker`, so a prewarm request uses
the exact production multipart fields and rejects the same invalid responses.
"""

from __future__ import annotations

from .providers.garment_masker import (
    GarmentMask,
    GarmentMaskHttpClient,
    GarmentMaskInput,
    GarmentMasker,
    HttpxGarmentMaskHttpClient,
)


async def prewarm_rembg(
    front_data: bytes,
    mime_type: str,
    *,
    client: GarmentMaskHttpClient | None = None,
) -> GarmentMask:
    """Send one unmodified front original to rembg and return its verified mask.

    ``client`` is injectable for integration tests.  When omitted, the helper
    uses the lazy httpx client, which raises the provider's explicit
    unavailable error if httpx is not installed.  Pillow availability and all
    image contract failures are likewise reported by ``GarmentMasker``.
    """

    masker = GarmentMasker(client if client is not None else HttpxGarmentMaskHttpClient())
    return await masker.mask(GarmentMaskInput(data=front_data, mime_type=mime_type))


__all__ = ["prewarm_rembg"]
