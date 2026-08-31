import { describe, expect, it } from "vitest";

import {
  CaptureSlotRecordSchema,
  CaptureSlotSchema,
  CaptureSlotStateSchema,
  LiveCaptureAssessmentSchema,
  ProviderErrorSchema,
  SessionSlotSchema,
  ShotAssessmentSchema,
  type CaptureSlotState,
  type LiveCaptureAssessment,
  type ProviderError,
  type ShotAssessment,
} from "./index";

const validShotAssessment: ShotAssessment = {
  shotType: "front",
  quality: "ok",
  issues: [],
  missingShots: ["back", "tag"],
  nextAction: "REQUEST_NEXT",
};

const validLiveAssessment: LiveCaptureAssessment = {
  hint: "READY",
  brightness: 120,
  blurScore: 30,
  frameDifference: 0.01,
  stableForMs: 600,
};

describe("ShotAssessmentSchema", () => {
  it("accepts the documented post-capture assessment", () => {
    expect(ShotAssessmentSchema.parse(validShotAssessment)).toEqual(
      validShotAssessment,
    );
  });

  it("rejects unknown enum values and missing fields", () => {
    expect(
      ShotAssessmentSchema.safeParse({
        ...validShotAssessment,
        shotType: "side",
      }).success,
    ).toBe(false);

    const { issues: _issues, ...withoutIssues } = validShotAssessment;
    expect(ShotAssessmentSchema.safeParse(withoutIssues).success).toBe(false);

    expect(
      ShotAssessmentSchema.safeParse({
        ...validShotAssessment,
        nextAction: "KEEP_GOING",
      }).success,
    ).toBe(false);
  });

  it("rejects invalid array items and unknown object keys", () => {
    expect(
      ShotAssessmentSchema.safeParse({
        ...validShotAssessment,
        issues: ["TOO_DARK", "NOT_A_KNOWN_ISSUE"],
      }).success,
    ).toBe(false);

    expect(
      ShotAssessmentSchema.safeParse({
        ...validShotAssessment,
        missingShots: ["back", "camera"],
      }).success,
    ).toBe(false);

    expect(
      ShotAssessmentSchema.safeParse({
        ...validShotAssessment,
        modelCommentary: "untrusted extra value",
      }).success,
    ).toBe(false);
  });
});

describe("LiveCaptureAssessmentSchema", () => {
  it("accepts local quality metrics", () => {
    expect(LiveCaptureAssessmentSchema.parse(validLiveAssessment)).toEqual(
      validLiveAssessment,
    );
  });

  it("accepts analyzer-unavailable assessments with null metrics", () => {
    expect(
      LiveCaptureAssessmentSchema.parse({
        hint: "ANALYZER_UNAVAILABLE",
        brightness: null,
        blurScore: null,
        frameDifference: null,
        stableForMs: 0,
      }),
    ).toMatchObject({ hint: "ANALYZER_UNAVAILABLE" });
  });

  it("rejects unknown values, missing metrics, and out-of-range metrics", () => {
    expect(
      LiveCaptureAssessmentSchema.safeParse({
        ...validLiveAssessment,
        hint: "MAYBE_READY",
      }).success,
    ).toBe(false);

    const { stableForMs: _stableForMs, ...withoutStableDuration } =
      validLiveAssessment;
    expect(
      LiveCaptureAssessmentSchema.safeParse(withoutStableDuration).success,
    ).toBe(false);

    expect(
      LiveCaptureAssessmentSchema.safeParse({
        ...validLiveAssessment,
        frameDifference: 1.1,
      }).success,
    ).toBe(false);
  });
});

describe("capture slot schemas", () => {
  const validSlotState: CaptureSlotState = {
    blob: new Blob(["raw image"]),
    objectUrl: "blob:http://localhost/capture-front",
    assessment: validShotAssessment,
  };

  it("accepts only the required front, back, and tag slot IDs", () => {
    expect(CaptureSlotSchema.parse("front")).toBe("front");
    expect(CaptureSlotSchema.safeParse("edit").success).toBe(false);
  });

  it("validates a slot's raw blob, object URL, and assessment", () => {
    expect(CaptureSlotStateSchema.parse(validSlotState)).toEqual(
      validSlotState,
    );
    expect(
      CaptureSlotRecordSchema.parse({ slot: "front", ...validSlotState }),
    ).toMatchObject({ slot: "front", objectUrl: validSlotState.objectUrl });

    expect(
      CaptureSlotStateSchema.safeParse({
        ...validSlotState,
        blob: "not-a-blob",
      }).success,
    ).toBe(false);

    const { assessment: _assessment, ...withoutAssessment } = validSlotState;
    expect(CaptureSlotStateSchema.safeParse(withoutAssessment).success).toBe(
      false,
    );

    expect(
      CaptureSlotRecordSchema.safeParse({
        slot: "back",
        ...validSlotState,
      }).success,
    ).toBe(false);

    expect(
      CaptureSlotRecordSchema.safeParse({
        slot: "front",
        ...validSlotState,
        assessment: { ...validShotAssessment, quality: "retry" },
      }).success,
    ).toBe(false);
  });
});

describe("ProviderErrorSchema", () => {
  const validProviderError: ProviderError = {
    provider: "shot-assessor",
    code: "TIMEOUT",
    message: "Shot assessment timed out",
    retryable: true,
  };

  it("accepts a serializable provider error", () => {
    expect(ProviderErrorSchema.parse(validProviderError)).toEqual(
      validProviderError,
    );
  });

  it("rejects unknown values, missing fields, and unknown keys", () => {
    expect(
      ProviderErrorSchema.safeParse({
        ...validProviderError,
        provider: "unknown-provider",
      }).success,
    ).toBe(false);

    const { retryable: _retryable, ...withoutRetryable } = validProviderError;
    expect(ProviderErrorSchema.safeParse(withoutRetryable).success).toBe(false);

    expect(
      ProviderErrorSchema.safeParse({
        ...validProviderError,
        debug: { secret: "must not cross the boundary" },
      }).success,
    ).toBe(false);
  });
});

describe("session slot compatibility", () => {
  it("accepts all four required session photos", () => {
    expect(SessionSlotSchema.parse("front")).toBe("front");
    expect(SessionSlotSchema.parse("back")).toBe("back");
    expect(SessionSlotSchema.parse("tag")).toBe("tag");
    expect(SessionSlotSchema.parse("measurement")).toBe("measurement");
  });

  it("keeps the assessed shot slots at front, back, and tag", () => {
    expect(CaptureSlotSchema.safeParse("measurement").success).toBe(false);
    expect(SessionSlotSchema.safeParse("edit").success).toBe(false);
  });
});

describe("ShotAssessmentSchema measurement isolation", () => {
  it("rejects measurement as an assessed shot type", () => {
    expect(
      ShotAssessmentSchema.safeParse({
        ...validShotAssessment,
        shotType: "measurement",
      }).success,
    ).toBe(false);
  });

  it("rejects measurement inside missingShots", () => {
    expect(
      ShotAssessmentSchema.safeParse({
        ...validShotAssessment,
        missingShots: ["measurement"],
      }).success,
    ).toBe(false);

    expect(
      ShotAssessmentSchema.safeParse({
        ...validShotAssessment,
        missingShots: ["back", "tag", "measurement"],
      }).success,
    ).toBe(false);
  });

  it("rejects measurement geometry smuggled in as extra fields", () => {
    expect(
      ShotAssessmentSchema.safeParse({
        ...validShotAssessment,
        measurement: { lengthCm: 68, widthCm: 52 },
      }).success,
    ).toBe(false);

    expect(
      ShotAssessmentSchema.safeParse({ ...validShotAssessment, lengthCm: 68 })
        .success,
    ).toBe(false);

    expect(
      ShotAssessmentSchema.safeParse({
        ...validShotAssessment,
        pxPerCm: 12.5,
      }).success,
    ).toBe(false);

    expect(
      ShotAssessmentSchema.safeParse({
        ...validShotAssessment,
        endpoints: {
          lengthStart: { x: 0.5, y: 0.1 },
          lengthEnd: { x: 0.5, y: 0.9 },
          widthStart: { x: 0.2, y: 0.4 },
          widthEnd: { x: 0.8, y: 0.4 },
        },
      }).success,
    ).toBe(false);
  });
});

describe("provider boundary coverage", () => {
  it("accepts the live guidance and measurement providers", () => {
    expect(
      ProviderErrorSchema.parse({
        provider: "vision-guidance",
        code: "UNAVAILABLE",
        message: "Agent is not connected",
        retryable: true,
      }).provider,
    ).toBe("vision-guidance");

    expect(
      ProviderErrorSchema.parse({
        provider: "measurement-line",
        code: "TIMEOUT",
        message: "Endpoint suggestion timed out",
        retryable: true,
      }).provider,
    ).toBe("measurement-line");
  });
});
