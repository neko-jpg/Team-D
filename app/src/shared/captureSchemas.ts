import { z } from "zod";

import {
  CAPTURE_SLOTS,
  LIVE_HINTS,
  NEXT_ACTIONS,
  PROVIDER_ERROR_CODES,
  PROVIDER_NAMES,
  SESSION_SLOTS,
  SHOT_ISSUE_CODES,
  SHOT_QUALITIES,
  SHOT_SLOTS,
  SHOT_TYPES,
  type CaptureSlotRecord,
  type CaptureSlotState,
  type LiveCaptureAssessment,
  type ProviderError,
  type ShotAssessment,
} from "./captureTypes";

export const CaptureSlotSchema = z.enum(CAPTURE_SLOTS);
export const ShotSlotSchema = z.enum(SHOT_SLOTS);

/** Accepts the four required session photos, including `measurement`. */
export const SessionSlotSchema = z.enum(SESSION_SLOTS);
export const ShotTypeSchema = z.enum(SHOT_TYPES);
export const LiveHintSchema = z.enum(LIVE_HINTS);
export const ShotIssueCodeSchema = z.enum(SHOT_ISSUE_CODES);
export const ShotQualitySchema = z.enum(SHOT_QUALITIES);
export const NextActionSchema = z.enum(NEXT_ACTIONS);

export const ShotAssessmentSchema: z.ZodType<ShotAssessment> = z
  .object({
    shotType: ShotTypeSchema,
    quality: ShotQualitySchema,
    issues: z.array(ShotIssueCodeSchema),
    missingShots: z.array(ShotSlotSchema),
    nextAction: NextActionSchema,
  })
  .strict();

const finiteNonNegativeNumber = z.number().finite().nonnegative();

export const LiveCaptureAssessmentSchema: z.ZodType<LiveCaptureAssessment> = z
  .object({
    hint: LiveHintSchema,
    brightness: z.number().finite().min(0).max(255).nullable(),
    blurScore: finiteNonNegativeNumber.nullable(),
    frameDifference: z.number().finite().min(0).max(1).nullable(),
    stableForMs: finiteNonNegativeNumber,
  })
  .strict();

const blobSchema = z.custom<Blob>(
  (value): value is Blob =>
    typeof Blob !== "undefined" && value instanceof Blob,
  { message: "Expected a Blob" },
);

export const CaptureSlotStateSchema: z.ZodType<CaptureSlotState> = z
  .object({
    blob: blobSchema,
    objectUrl: z.string().trim().min(1),
    assessment: ShotAssessmentSchema,
  })
  .strict();

export const CaptureSlotRecordSchema: z.ZodType<CaptureSlotRecord> = z
  .object({
    slot: CaptureSlotSchema,
    blob: blobSchema,
    objectUrl: z.string().trim().min(1),
    assessment: ShotAssessmentSchema,
  })
  .strict()
  .superRefine((value, context) => {
    if (value.assessment.quality !== "ok") {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["assessment", "quality"],
        message: "A captured slot must contain an accepted assessment",
      });
    }

    if (value.assessment.shotType !== value.slot) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["assessment", "shotType"],
        message: "The assessment shotType must match the capture slot",
      });
    }
  });

export const ProviderNameSchema = z.enum(PROVIDER_NAMES);
export const ProviderErrorCodeSchema = z.enum(PROVIDER_ERROR_CODES);

export const ProviderErrorSchema: z.ZodType<ProviderError> = z
  .object({
    provider: ProviderNameSchema,
    code: ProviderErrorCodeSchema,
    message: z.string().trim().min(1),
    retryable: z.boolean(),
  })
  .strict();
