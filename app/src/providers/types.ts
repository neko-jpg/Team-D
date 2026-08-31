import type {
  ProviderError,
  ShotAssessment,
  ShotType,
} from "../shared";

/** Capture slots accepted by the fixture and live assessor boundary. */
export type CaptureShotType = Extract<ShotType, "front" | "back" | "tag">;

export const FIXTURE_SHOT_TYPES = ["front", "back", "tag"] as const satisfies
  readonly CaptureShotType[];

export type FixtureOutcome = "ok" | "retry" | "wrong-shot" | "error";

/** Input shared by fixture and a future live ShotAssessor implementation. */
export interface ShotAssessorInput {
  blob: Blob;
  requestedShot?: CaptureShotType;
  /** Already accepted slots, used to make missingShots truthful in fixture mode. */
  acceptedShots?: readonly CaptureShotType[];
  /** Compatibility aliases for callers that name the requested slot directly. */
  slot?: CaptureShotType;
  shotType?: CaptureShotType;
}

/** Provider failures reject with the shared ProviderError contract. */
export interface ShotAssessor {
  assess(input: ShotAssessorInput): Promise<ShotAssessment>;
}

export interface FixtureShotAssessorOptions {
  fixtureShot?: CaptureShotType;
  /** Alias for fixtureShot when a caller models the selected image as shotType. */
  shotType?: CaptureShotType;
  outcome?: FixtureOutcome;
}

export function isProviderError(value: unknown): value is ProviderError {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as Partial<ProviderError>;
  return (
    typeof candidate.provider === "string" &&
    typeof candidate.code === "string" &&
    typeof candidate.message === "string" &&
    typeof candidate.retryable === "boolean"
  );
}
