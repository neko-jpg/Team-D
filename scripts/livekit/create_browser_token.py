from __future__ import annotations

import os
from datetime import timedelta

from dotenv import load_dotenv
from livekit import api


load_dotenv(".env.local")


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def main() -> None:
    api_key = required("LIVEKIT_API_KEY")
    api_secret = required("LIVEKIT_API_SECRET")
    room_name = required("LIVEKIT_ROOM")
    browser_identity = required("LIVEKIT_BROWSER_IDENTITY")
    agent_identity = required("LIVEKIT_AGENT_IDENTITY")

    if browser_identity == agent_identity:
        raise RuntimeError("Browser and Agent identities must be different")

    ttl_minutes = int(os.getenv("LIVEKIT_TOKEN_TTL_MINUTES", "10"))
    if not 1 <= ttl_minutes <= 60:
        raise RuntimeError("LIVEKIT_TOKEN_TTL_MINUTES must be between 1 and 60")

    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(browser_identity)
        .with_name("Team-D browser smoke participant")
        .with_ttl(timedelta(minutes=ttl_minutes))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )

    # Print the short-lived client token only, so command substitution is safe.
    print(token)


if __name__ == "__main__":
    main()
