import { z } from "zod";

import {
  APPROVED_MEASUREMENT_STATUSES,
  LENGTH_CM_RANGE,
  MARKER_KNOWN_SIDE_CM,
  MEASUREMENT_ENDPOINT_KEYS,
  MEASUREMENT_FAILURE_CODES,
  MEASUREMENT_SOURCES,
  MEASUREMENT_STATUSES,
  WIDTH_CM_RANGE,
  type ApprovedMeasurement,
  type MeasurementDraft,
  type MeasurementEndpoints,
  type MeasurementLine,
  type MeasurementMarker,
  type NormalizedPoint,
} from "./measurementTypes";

export const NormalizedPointSchema: z.ZodType<NormalizedPoint> = z
  .object({
    x: z.number().finite().min(0).max(1),
    y: z.number().finite().min(0).max(1),
  })
  .strict();

export const MeasurementEndpointKeySchema = z.enum(MEASUREMENT_ENDPOINT_KEYS);
export const MeasurementSourceSchema = z.enum(MEASUREMENT_SOURCES);
export const MeasurementStatusSchema = z.enum(MEASUREMENT_STATUSES);
export const ApprovedMeasurementStatusSchema = z.enum(
  APPROVED_MEASUREMENT_STATUSES,
);
export const MeasurementFailureCodeSchema = z.enum(MEASUREMENT_FAILURE_CODES);

export const MeasurementEndpointsSchema: z.ZodType<MeasurementEndpoints> = z
  .object({
    lengthStart: NormalizedPointSchema,
    lengthEnd: NormalizedPointSchema,
    widthStart: NormalizedPointSchema,
    widthEnd: NormalizedPointSchema,
  })
  .strict();

export const MeasurementMarkerSchema: z.ZodType<MeasurementMarker> = z
  .object({
    knownSideCm: z.literal(MARKER_KNOWN_SIDE_CM),
    corners: z.tuple([
      NormalizedPointSchema,
      NormalizedPointSchema,
      NormalizedPointSchema,
      NormalizedPointSchema,
    ]),
    pxPerCm: z.number().finite().positive(),
  })
  .strict();

export const MeasurementLineSchema: z.ZodType<MeasurementLine> = z
  .object({
    start: NormalizedPointSchema,
    end: NormalizedPointSchema,
    valueCm: z.number().finite().positive(),
  })
  .strict();

export const MeasurementDraftSchema: z.ZodType<MeasurementDraft> = z
  .object({
    imageId: z.string().trim().min(1),
    marker: MeasurementMarkerSchema.nullable(),
    length: MeasurementLineSchema,
    width: MeasurementLineSchema,
    source: MeasurementSourceSchema,
    status: MeasurementStatusSchema,
  })
  .strict();

/** Same shape as `MeasurementDraftSchema`, but rejects `needs_review`. */
export const ApprovedMeasurementSchema: z.ZodType<ApprovedMeasurement> = z
  .object({
    imageId: z.string().trim().min(1),
    marker: MeasurementMarkerSchema.nullable(),
    length: MeasurementLineSchema,
    width: MeasurementLineSchema,
    source: MeasurementSourceSchema,
    status: ApprovedMeasurementStatusSchema,
  })
  .strict();

/**
 * True when both measured lengths fall inside their expected plausibility
 * ranges. Out-of-range values remain valid and approvable; this is a
 * warning-only signal for the review UI.
 */
export function isMeasurementWithinExpectedRange(
  draft: MeasurementDraft,
): boolean {
  return (
    draft.length.valueCm >= LENGTH_CM_RANGE.min &&
    draft.length.valueCm <= LENGTH_CM_RANGE.max &&
    draft.width.valueCm >= WIDTH_CM_RANGE.min &&
    draft.width.valueCm <= WIDTH_CM_RANGE.max
  );
}
