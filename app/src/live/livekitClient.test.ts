import { describe, expect, it } from "vitest";
import { Room, RoomEvent, protocolVersion, version } from "livekit-client";

/**
 * The LiveKit JS SDK version pinned in package.json and package-lock.json.
 * Keep this in sync with THIRD_PARTY_NOTICES.md when the pin moves.
 */
const PINNED_LIVEKIT_CLIENT_VERSION = "2.22.1";

describe("livekit-client", () => {
  it("imports the pinned SDK version", () => {
    expect(version).toBe(PINNED_LIVEKIT_CLIENT_VERSION);
    expect(protocolVersion).toBeGreaterThan(0);
  });

  it("constructs a Room and exposes the APIs the live capture path depends on", () => {
    expect(typeof Room).toBe("function");

    const room = new Room();
    expect(typeof room.connect).toBe("function");
    expect(typeof room.disconnect).toBe("function");
    expect(typeof room.localParticipant.publishTrack).toBe("function");
    expect(typeof room.localParticipant.publishData).toBe("function");
  });

  it("exposes the connection and data events the guidance transport subscribes to", () => {
    expect(RoomEvent.ConnectionStateChanged).toBeTruthy();
    expect(RoomEvent.DataReceived).toBeTruthy();
    expect(RoomEvent.Disconnected).toBeTruthy();
    expect(RoomEvent.Reconnecting).toBeTruthy();
  });
});
