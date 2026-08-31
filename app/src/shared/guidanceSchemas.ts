import { z } from "zod";

import { SessionSlotSchema } from "./captureSchemas";
import {
  CONNECTION_STATES,
  GUIDANCE_CODES,
  type GuidanceEvent,
} from "./guidanceTypes";

export const GuidanceCodeSchema = z.enum(GUIDANCE_CODES);
export const ConnectionStateSchema = z.enum(CONNECTION_STATES);

const finiteNonNegativeInteger = z.number().int().finite().nonnegative();

export const GuidanceEventSchema: z.ZodType<GuidanceEvent> = z
  .object({
    sessionId: z.string().trim().min(1),
    sequence: finiteNonNegativeInteger,
    shot: SessionSlotSchema,
    code: GuidanceCodeSchema,
    message: z.string().trim().min(1),
    confidence: z.number().finite().min(0).max(1),
    observedAt: finiteNonNegativeInteger,
    expiresAt: finiteNonNegativeInteger,
  })
  .strict()
  .superRefine((value, context) => {
    if (value.expiresAt <= value.observedAt) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["expiresAt"],
        message: "expiresAt must be strictly after observedAt",
      });
    }
  });

/** True while a guidance event has not yet expired at the given instant. */
export function isGuidanceEventFresh(
  event: GuidanceEvent,
  now: number,
): boolean {
  return now < event.expiresAt;
}
