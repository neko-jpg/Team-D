from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import AgentServer, JobContext, cli


load_dotenv(".env.local")

logger = logging.getLogger("team-d-livekit-smoke")
server = AgentServer()


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def participant_snapshot(room: rtc.Room) -> str:
    participants = [f"local:{room.local_participant.identity}:{room.local_participant.sid}"]
    participants.extend(
        f"remote:{participant.identity}:{participant.sid}"
        for participant in room.remote_participants.values()
    )
    return ",".join(participants)


@server.rtc_session(agent_name="team-d-livekit-smoke")
async def entrypoint(ctx: JobContext) -> None:
    expected_room = required("LIVEKIT_ROOM")
    expected_agent_identity = required("LIVEKIT_AGENT_IDENTITY")
    expected_browser_identity = required("LIVEKIT_BROWSER_IDENTITY")

    if expected_agent_identity == expected_browser_identity:
        raise RuntimeError("Browser and Agent identities must be different")

    room = ctx.room
    disconnected = asyncio.Event()

    @room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant) -> None:
        logger.info(
            "participant_connected room=%s identity=%s sid=%s",
            room.name,
            participant.identity,
            participant.sid,
        )
        logger.info(
            "participant_snapshot room=%s participants=%s",
            room.name,
            participant_snapshot(room),
        )
        if participant.identity == expected_browser_identity:
            logger.info(
                "identity_check status=ok room=%s browser_identity=%s agent_identity=%s unique=true",
                room.name,
                participant.identity,
                room.local_participant.identity,
            )

    @room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
        logger.info(
            "participant_disconnected room=%s identity=%s sid=%s",
            room.name,
            participant.identity,
            participant.sid,
        )

    @room.on("disconnected")
    def on_disconnected(*_: object) -> None:
        disconnected.set()

    await ctx.connect()

    actual_agent_identity = room.local_participant.identity
    if room.name != expected_room:
        raise RuntimeError(
            f"Unexpected Room: expected={expected_room} actual={room.name}"
        )
    if actual_agent_identity != expected_agent_identity:
        raise RuntimeError(
            "Unexpected Agent identity: "
            f"expected={expected_agent_identity} actual={actual_agent_identity}"
        )

    logger.info(
        "agent_connected room=%s identity=%s sid=%s",
        room.name,
        actual_agent_identity,
        room.local_participant.sid,
    )
    logger.info(
        "participant_snapshot room=%s participants=%s",
        room.name,
        participant_snapshot(room),
    )

    browser = room.remote_participants.get(expected_browser_identity)
    if browser:
        logger.info(
            "identity_check status=ok room=%s browser_identity=%s agent_identity=%s unique=true",
            room.name,
            browser.identity,
            actual_agent_identity,
        )
    else:
        logger.info(
            "identity_check_waiting room=%s expected_browser_identity=%s",
            room.name,
            expected_browser_identity,
        )

    await disconnected.wait()


if __name__ == "__main__":
    cli.run_app(server)
