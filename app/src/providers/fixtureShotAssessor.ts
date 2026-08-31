import {
  ShotAssessmentSchema,
  type ProviderError,
  type ShotAssessment,
} from "../shared";
import {
  FIXTURE_SHOT_TYPES,
  type CaptureShotType,
  type FixtureOutcome,
  type FixtureShotAssessorOptions,
  type ShotAssessor,
  type ShotAssessorInput,
} from "./types";

const DEFAULT_FIXTURE_SHOT: CaptureShotType = "front";
const DEFAULT_OUTCOME: FixtureOutcome = "ok";

function providerError(message: string): ProviderError {
  return {
    provider: "shot-assessor",
    code: "UNAVAILABLE",
    message,
    retryable: true,
  };
}

function inputError(message: string): ProviderError {
  return {
    provider: "shot-assessor",
    code: "INVALID_INPUT",
    message,
    retryable: false,
  };
}

function requestedShotFrom(input: ShotAssessorInput): CaptureShotType {
  if (
    typeof Blob !== "undefined" &&
    !(input.blob instanceof Blob)
  ) {
    throw inputError("画像データがありません。");
  }

  const requestedShot = input.requestedShot ?? input.slot ?? input.shotType;

  if (
    requestedShot === undefined ||
    !FIXTURE_SHOT_TYPES.includes(requestedShot as CaptureShotType)
  ) {
    throw inputError("要求中の撮影種別がありません。");
  }

  return requestedShot as CaptureShotType;
}

function missingShots(
  input: ShotAssessorInput,
  requestedShot: CaptureShotType,
  acceptedByApp: boolean,
): CaptureShotType[] {
  const accepted = new Set(input.acceptedShots ?? []);
  if (acceptedByApp) {
    accepted.add(requestedShot);
  }

  return FIXTURE_SHOT_TYPES.filter((shot) => !accepted.has(shot));
}

function nextShot(slot: CaptureShotType): CaptureShotType {
  const index = FIXTURE_SHOT_TYPES.indexOf(slot);
  return FIXTURE_SHOT_TYPES[(index + 1) % FIXTURE_SHOT_TYPES.length];
}

/**
 * Deterministic post-capture provider for the upload vertical slice.
 *
 * The provider never changes capture state. It returns an assessment for the
 * requested Blob or rejects with ProviderError; the caller owns reducer
 * dispatch and therefore cannot turn a live/provider failure into success.
 */
export class FixtureShotAssessor implements ShotAssessor {
  readonly fixtureShot: CaptureShotType;
  readonly outcome: FixtureOutcome;

  constructor(options: FixtureShotAssessorOptions = {}) {
    this.fixtureShot = options.fixtureShot ?? options.shotType ?? DEFAULT_FIXTURE_SHOT;
    this.outcome = options.outcome ?? DEFAULT_OUTCOME;
  }

  async assess(input: ShotAssessorInput): Promise<ShotAssessment> {
    const requestedShot = requestedShotFrom(input);

    if (this.outcome === "error") {
      throw providerError("fixture の ShotAssessor は利用できません。再試行してください。");
    }

    if (this.outcome === "retry") {
      return ShotAssessmentSchema.parse({
        shotType: this.fixtureShot,
        quality: "retry",
        issues: ["BLURRY"],
        missingShots: missingShots(input, requestedShot, false),
        nextAction: "RETAKE",
      });
    }

    if (this.outcome === "wrong-shot") {
      return ShotAssessmentSchema.parse({
        shotType:
          this.fixtureShot === requestedShot
            ? nextShot(requestedShot)
            : this.fixtureShot,
        quality: "retry",
        issues: ["WRONG_SHOT"],
        missingShots: missingShots(input, requestedShot, false),
        nextAction: "RETAKE",
      });
    }

    const matchesRequestedShot = this.fixtureShot === requestedShot;
    return ShotAssessmentSchema.parse({
      shotType: this.fixtureShot,
      quality: "ok",
      issues: matchesRequestedShot ? [] : ["WRONG_SHOT"],
      missingShots: missingShots(input, requestedShot, matchesRequestedShot),
      nextAction:
        matchesRequestedShot && requestedShot === "tag"
          ? "COMPLETE"
          : matchesRequestedShot
            ? "REQUEST_NEXT"
            : "RETAKE",
    });
  }
}

export function createFixtureShotAssessor(
  options: FixtureShotAssessorOptions = {},
): FixtureShotAssessor {
  return new FixtureShotAssessor(options);
}

export default FixtureShotAssessor;
