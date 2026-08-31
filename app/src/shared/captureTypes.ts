/** Required capture slots for a garment session. */
export const CAPTURE_SLOTS = ["front", "back", "tag"] as const;
export const SHOT_SLOTS = CAPTURE_SLOTS;

export type CaptureSlot = (typeof CAPTURE_SLOTS)[number];
export type ShotSlot = CaptureSlot;

/** Shot labels returned by the post-capture assessor. */
export const SHOT_TYPES = ["front", "back", "tag", "unknown"] as const;
export type ShotType = (typeof SHOT_TYPES)[number];

/** Hints produced by the local, pre-capture quality analyzer. */
export const LIVE_HINTS = [
  "TOO_DARK",
  "TOO_BRIGHT",
  "TOO_BLURRY",
  "HOLD_STEADY",
  "READY",
  "ANALYZER_UNAVAILABLE",
] as const;
export type LiveHint = (typeof LIVE_HINTS)[number];

/** Finite issue codes exposed to the capture UI. */
export const SHOT_ISSUE_CODES = [
  "TOO_DARK",
  "TOO_BRIGHT",
  "TOO_BLURRY",
  "BLURRY",
  "GARMENT_CROPPED",
  "TAG_UNREADABLE",
  "WRONG_SHOT",
] as const;
export type ShotIssueCode = (typeof SHOT_ISSUE_CODES)[number];

export const SHOT_QUALITIES = ["ok", "retry"] as const;
export type ShotQuality = (typeof SHOT_QUALITIES)[number];

export const NEXT_ACTIONS = ["RETAKE", "REQUEST_NEXT", "COMPLETE"] as const;
export type NextAction = (typeof NEXT_ACTIONS)[number];

/** Strict result returned by post-capture image assessment. */
export interface ShotAssessment {
  shotType: ShotType;
  quality: ShotQuality;
  issues: ShotIssueCode[];
  missingShots: CaptureSlot[];
  nextAction: NextAction;
}

/**
 * Local metrics attached to a live capture hint.
 * Null metrics are used when the analyzer is unavailable.
 */
export interface LiveCaptureAssessment {
  hint: LiveHint;
  brightness: number | null;
  blurScore: number | null;
  frameDifference: number | null;
  stableForMs: number;
}

/** Accepted data stored for one capture slot. */
export interface CaptureSlotState {
  blob: Blob;
  objectUrl: string;
  assessment: ShotAssessment;
}

/** A slot state together with the slot it belongs to. */
export interface CaptureSlotRecord extends CaptureSlotState {
  slot: CaptureSlot;
}

/** Providers that cross the Node-side provider boundary. */
export const PROVIDER_NAMES = [
  "shot-assessor",
  "background-generator",
  "garment-masker",
] as const;
export type ProviderName = (typeof PROVIDER_NAMES)[number];

export const PROVIDER_ERROR_CODES = [
  "TIMEOUT",
  "UNAVAILABLE",
  "INVALID_RESPONSE",
  "INVALID_INPUT",
  "UNKNOWN",
] as const;
export type ProviderErrorCode = (typeof PROVIDER_ERROR_CODES)[number];

/** Safe, serializable error contract shared by providers and the UI. */
export interface ProviderError {
  provider: ProviderName;
  code: ProviderErrorCode;
  message: string;
  retryable: boolean;
}
