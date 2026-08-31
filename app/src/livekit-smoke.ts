import { Room, RoomEvent } from "livekit-client";

type LogDetails = Record<string, unknown>;

function requiredElement<T extends HTMLElement>(selector: string): T {
  const element = document.querySelector<T>(selector);
  if (!element) {
    throw new Error(`LiveKit smoke test element is missing: ${selector}`);
  }
  return element;
}

const connectButton = requiredElement<HTMLButtonElement>("#connect");
const logElement = requiredElement<HTMLPreElement>("#log");
const summaryElement = requiredElement<HTMLParagraphElement>("#summary");

const config = {
  serverUrl: import.meta.env.VITE_LIVEKIT_URL?.trim() ?? "",
  roomName: import.meta.env.VITE_LIVEKIT_ROOM?.trim() ?? "",
  browserIdentity:
    import.meta.env.VITE_LIVEKIT_BROWSER_IDENTITY?.trim() ?? "",
  agentIdentity: import.meta.env.VITE_LIVEKIT_AGENT_IDENTITY?.trim() ?? "",
  token: import.meta.env.VITE_LIVEKIT_BROWSER_TOKEN?.trim() ?? "",
};

let activeRoom: Room | undefined;

function writeLog(event: string, details: LogDetails = {}): void {
  const line = `[browser] ${event} ${JSON.stringify(details)}`;
  console.info(line);
  logElement.textContent += `${line}\n`;
  logElement.scrollTop = logElement.scrollHeight;
}

function requireConfig(): void {
  const missing = Object.entries(config)
    .filter(([, value]) => value.length === 0)
    .map(([key]) => key);

  if (missing.length > 0) {
    throw new Error(`Missing LiveKit browser config: ${missing.join(", ")}`);
  }

  if (config.browserIdentity === config.agentIdentity) {
    throw new Error("Browser and Agent identities must be different");
  }
}

function participantSnapshot(room: Room): LogDetails[] {
  return [
    {
      role: "local",
      identity: room.localParticipant.identity,
      sid: room.localParticipant.sid,
    },
    ...Array.from(room.remoteParticipants.values(), (participant) => ({
      role: "remote",
      identity: participant.identity,
      sid: participant.sid,
    })),
  ];
}

function reportIdentityCheck(room: Room): void {
  const agent = room.remoteParticipants.get(config.agentIdentity);

  if (!agent) {
    writeLog("identity_check_waiting", {
      expectedRemoteIdentity: config.agentIdentity,
    });
    return;
  }

  writeLog("identity_check", {
    status: "ok",
    room: room.name,
    browserIdentity: room.localParticipant.identity,
    agentIdentity: agent.identity,
    unique: room.localParticipant.identity !== agent.identity,
  });
  summaryElement.textContent = `確認済み: ${room.localParticipant.identity} / ${agent.identity}`;
}

async function connect(): Promise<void> {
  connectButton.disabled = true;
  logElement.textContent = "";
  summaryElement.textContent = "接続中…";

  try {
    requireConfig();
    writeLog("config", {
      serverUrl: config.serverUrl,
      room: config.roomName,
      browserIdentity: config.browserIdentity,
      agentIdentity: config.agentIdentity,
      tokenPresent: true,
    });

    if (activeRoom) {
      await activeRoom.disconnect();
    }

    const room = new Room();
    activeRoom = room;

    room.on(RoomEvent.ParticipantConnected, (participant) => {
      writeLog("participant_connected", {
        room: room.name,
        identity: participant.identity,
        sid: participant.sid,
      });
      writeLog("participant_snapshot", {
        room: room.name,
        participants: participantSnapshot(room),
      });
      reportIdentityCheck(room);
    });

    room.on(RoomEvent.ParticipantDisconnected, (participant) => {
      writeLog("participant_disconnected", {
        room: room.name,
        identity: participant.identity,
        sid: participant.sid,
      });
    });

    room.on(RoomEvent.Disconnected, (reason) => {
      writeLog("disconnected", { room: room.name, reason });
      summaryElement.textContent = "切断済み";
    });

    await room.connect(config.serverUrl, config.token);

    if (room.name !== config.roomName) {
      await room.disconnect();
      throw new Error(
        `Connected to unexpected Room: expected=${config.roomName} actual=${room.name}`,
      );
    }

    if (room.localParticipant.identity !== config.browserIdentity) {
      await room.disconnect();
      throw new Error(
        `Unexpected browser identity: expected=${config.browserIdentity} actual=${room.localParticipant.identity}`,
      );
    }

    writeLog("connected", {
      room: room.name,
      identity: room.localParticipant.identity,
      sid: room.localParticipant.sid,
    });
    writeLog("participant_snapshot", {
      room: room.name,
      participants: participantSnapshot(room),
    });
    summaryElement.textContent = `接続済み: ${room.name} / ${room.localParticipant.identity}`;
    reportIdentityCheck(room);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    writeLog("connection_error", { message });
    summaryElement.textContent = `接続失敗: ${message}`;
  } finally {
    connectButton.disabled = false;
  }
}

connectButton.addEventListener("click", () => {
  void connect();
});

window.addEventListener("pagehide", () => {
  if (activeRoom) {
    void activeRoom.disconnect();
  }
});
