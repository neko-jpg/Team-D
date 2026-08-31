/** A point in image space, normalized to 0..1 on both axes. */
export interface NormalizedPoint {
  x: number;
  y: number;
}

/** The four endpoints a measurement draft is built from. */
export const MEASUREMENT_ENDPOINT_KEYS = [
  "lengthStart",
  "lengthEnd",
  "widthStart",
  "widthEnd",
] as const;
export type MeasurementEndpointKey = (typeof MEASUREMENT_ENDPOINT_KEYS)[number];

/** The four endpoints, keyed by which measurement they belong to. */
export type MeasurementEndpoints = Record<MeasurementEndpointKey, NormalizedPoint>;

/** Outer side length of the dedicated printed marker, in centimetres. */
export const MARKER_KNOWN_SIDE_CM = 5 as const;

/** Detected reference marker used to derive the image's pixel-to-cm scale. */
export interface MeasurementMarker {
  knownSideCm: typeof MARKER_KNOWN_SIDE_CM;
  corners: readonly [
    NormalizedPoint,
    NormalizedPoint,
    NormalizedPoint,
    NormalizedPoint,
  ];
  pxPerCm: number;
}

/** A single measured line with its derived real-world length. */
export interface MeasurementLine {
  start: NormalizedPoint;
  end: NormalizedPoint;
  valueCm: number;
}

/** Where a measurement draft's line endpoints originated. */
export const MEASUREMENT_SOURCES = ["ai", "contour", "user"] as const;
export type MeasurementSource = (typeof MEASUREMENT_SOURCES)[number];

/** Review state of a measurement draft. */
export const MEASUREMENT_STATUSES = [
  "needs_review",
  "approved_cv",
  "approved_manual",
] as const;
export type MeasurementStatus = (typeof MEASUREMENT_STATUSES)[number];

/** The two statuses that unlock background editing. */
export const APPROVED_MEASUREMENT_STATUSES = [
  "approved_cv",
  "approved_manual",
] as const;
export type ApprovedMeasurementStatus =
  (typeof APPROVED_MEASUREMENT_STATUSES)[number];

/** Finite reasons the measurement photo or endpoint draft can be rejected. */
export const MEASUREMENT_FAILURE_CODES = [
  "MARKER_MISSING",
  "MARKER_MULTIPLE",
  "MARKER_TOO_SMALL",
  "MARKER_OCCLUDED",
  "GARMENT_OUT_OF_FRAME",
  "GARMENT_MARKER_OVERLAP",
  "SEGMENTATION_FAILED",
  "ENDPOINTS_INVALID",
] as const;
export type MeasurementFailureCode = (typeof MEASUREMENT_FAILURE_CODES)[number];

/** A working measurement, before or after user review. */
export interface MeasurementDraft {
  imageId: string;
  marker: MeasurementMarker | null;
  length: MeasurementLine;
  width: MeasurementLine;
  source: MeasurementSource;
  status: MeasurementStatus;
}

/** A draft the user has explicitly approved. */
export interface ApprovedMeasurement extends MeasurementDraft {
  status: ApprovedMeasurementStatus;
}

/** Warning-only plausibility ranges. Values outside these stay approvable. */
export const LENGTH_CM_RANGE = { min: 20, max: 100 } as const;
export const WIDTH_CM_RANGE = { min: 20, max: 80 } as const;
