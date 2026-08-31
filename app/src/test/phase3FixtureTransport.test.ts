import { describe, expect, it } from "vitest";

import {
  PHASE3_TRANSPORT_SCENARIOS,
  assertPhase3SnapshotPreserved,
  canEnterEditFromFixtureSnapshot,
  createEmptySlotBlobHashes,
  type Phase3FixtureTransportSnapshot,
} from "./phase3FixtureTransport";

function fixtureSnapshot(
  overrides: Partial<Phase3FixtureTransportSnapshot> = {},
): Phase3FixtureTransportSnapshot {
  return {
    slotBlobHashes: createEmptySlotBlobHashes(),
    currentStep: "front",
    measurementStatus: null,
    ...overrides,
  };
}

describe("phase 3 fixture transport regression support", () => {
  it("names the required deterministic transport scenarios in execution order", () => {
    expect(PHASE3_TRANSPORT_SCENARIOS).toEqual([
      "normal",
      "retake",
      "assessment-timeout",
      "measurement-unapproved",
      "stale-event",
      "resize-orientation-change",
      "disconnect-reconnect",
      "analyzer-unavailable",
      "camera-permission-denied",
    ]);
  });

  it("keeps edit locked until all four raw photos and an explicit approval exist", () => {
    const hashes = createEmptySlotBlobHashes();
    hashes.front = "front-hash";
    hashes.back = "back-hash";
    hashes.tag = "tag-hash";
    hashes.measurement = "measurement-hash";

    expect(
      canEnterEditFromFixtureSnapshot(
        fixtureSnapshot({ slotBlobHashes: hashes, measurementStatus: "needs_review" }),
      ),
    ).toBe(false);
    expect(
      canEnterEditFromFixtureSnapshot(
        fixtureSnapshot({ slotBlobHashes: hashes, measurementStatus: "approved_cv" }),
      ),
    ).toBe(true);
    expect(
      canEnterEditFromFixtureSnapshot(
        fixtureSnapshot({
          slotBlobHashes: hashes,
          measurementStatus: "approved_manual",
        }),
      ),
    ).toBe(true);
    expect(
      canEnterEditFromFixtureSnapshot(
        fixtureSnapshot({
          slotBlobHashes: { ...hashes, measurement: null },
          measurementStatus: "approved_manual",
        }),
      ),
    ).toBe(false);
  });

  it("detects unwanted slot, step, and measurement changes", () => {
    const hashes = createEmptySlotBlobHashes();
    hashes.front = "front-hash";
    const before = fixtureSnapshot({
      slotBlobHashes: hashes,
      currentStep: "back",
      measurementStatus: "needs_review",
    });
    const unchanged = fixtureSnapshot({
      slotBlobHashes: { ...hashes },
      currentStep: "back",
      measurementStatus: "needs_review",
    });

    expect(() => assertPhase3SnapshotPreserved(before, unchanged)).not.toThrow();
    expect(() =>
      assertPhase3SnapshotPreserved(before, {
        ...unchanged,
        currentStep: "tag",
      }),
    ).toThrow("current step changed unexpectedly");
    expect(() =>
      assertPhase3SnapshotPreserved(before, {
        ...unchanged,
        slotBlobHashes: { ...hashes, front: "replaced-hash" },
      }),
    ).toThrow("accepted front Blob hash changed unexpectedly");
    expect(() =>
      assertPhase3SnapshotPreserved(before, {
        ...unchanged,
        measurementStatus: "approved_manual",
      }),
    ).toThrow("measurement state changed unexpectedly");
  });
});
