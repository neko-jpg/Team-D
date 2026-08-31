import {
  APPROVED_MEASUREMENT_STATUSES,
  SESSION_SLOTS,
  type MeasurementStatus,
  type SessionSlot,
} from "../shared";

/**
 * Serializable observation used by the phase-3 fixture transport regression.
 * The integration test captures this before every failure/transport event so it
 * can prove that accepted image bytes and progress did not roll back.
 */
export interface Phase3FixtureTransportSnapshot {
  readonly slotBlobHashes: Readonly<Record<SessionSlot, string | null>>;
  readonly currentStep: string;
  readonly measurementStatus: MeasurementStatus | null;
}

export const PHASE3_TRANSPORT_SCENARIOS = [
  "normal",
  "retake",
  "assessment-timeout",
  "measurement-unapproved",
  "stale-event",
  "resize-orientation-change",
  "disconnect-reconnect",
  "analyzer-unavailable",
  "camera-permission-denied",
] as const;

export type Phase3TransportScenario =
  (typeof PHASE3_TRANSPORT_SCENARIOS)[number];

/** Produces an empty, fixed-order hash record for a new fixture session. */
export function createEmptySlotBlobHashes(): Record<SessionSlot, string | null> {
  return Object.fromEntries(
    SESSION_SLOTS.map((slot) => [slot, null]),
  ) as Record<SessionSlot, string | null>;
}

/** Returns true only when four photos and an explicit measurement approval exist. */
export function canEnterEditFromFixtureSnapshot(
  snapshot: Phase3FixtureTransportSnapshot,
): boolean {
  return (
    SESSION_SLOTS.every((slot) => snapshot.slotBlobHashes[slot] !== null) &&
    (snapshot.measurementStatus === APPROVED_MEASUREMENT_STATUSES[0] ||
      snapshot.measurementStatus === APPROVED_MEASUREMENT_STATUSES[1])
  );
}

/**
 * Checks the invariant required after a failure or stale transport event.
 * A caller may opt into a new current step after a successful reconnect, but
 * accepted raw image hashes and measurement status must remain untouched.
 */
export function assertPhase3SnapshotPreserved(
  before: Phase3FixtureTransportSnapshot,
  after: Phase3FixtureTransportSnapshot,
  options: { readonly allowCurrentStepChange?: boolean } = {},
): void {
  for (const slot of SESSION_SLOTS) {
    if (before.slotBlobHashes[slot] !== after.slotBlobHashes[slot]) {
      throw new Error(`accepted ${slot} Blob hash changed unexpectedly`);
    }
  }

  if (before.measurementStatus !== after.measurementStatus) {
    throw new Error("measurement state changed unexpectedly");
  }

  if (!options.allowCurrentStepChange && before.currentStep !== after.currentStep) {
    throw new Error("current step changed unexpectedly");
  }
}
