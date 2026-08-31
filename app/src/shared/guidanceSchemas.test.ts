import { describe, expect, it } from "vitest";

import {
  ConnectionStateSchema,
  GuidanceCodeSchema,
  GuidanceEventSchema,
  isGuidanceEventFresh,
} from "./guidanceSchemas";
import type { GuidanceEvent } from "./guidanceTypes";

const validGuidanceEvent: GuidanceEvent = {
  sessionId: "session-123",
  sequence: 0,
  shot: "front",
  code: "MOVE_CLOSER",
  message: "Move the camera closer to the garment",
  confidence: 0.8,
  observedAt: 1_000,
  expiresAt: 2_000,
};

describe("GuidanceEventSchema", () => {
  it("accepts a documented guidance event", () => {
    expect(GuidanceEventSchema.parse(validGuidanceEvent)).toEqual(
      validGuidanceEvent,
    );
  });

  it("accepts the measurement shot slot", () => {
    const measurementEvent: GuidanceEvent = {
      ...validGuidanceEvent,
      shot: "measurement",
      code: "PLACE_MARKER",
    };
    expect(GuidanceEventSchema.parse(measurementEvent)).toEqual(
      measurementEvent,
    );
  });

  it("rejects unknown enum values", () => {
    expect(
      GuidanceEventSchema.safeParse({
        ...validGuidanceEvent,
        code: "DO_A_BARREL_ROLL",
      }).success,
    ).toBe(false);

    expect(
      GuidanceEventSchema.safeParse({
        ...validGuidanceEvent,
        shot: "side",
      }).success,
    ).toBe(false);
  });

  it("rejects missing required fields", () => {
    const { sessionId: _sessionId, ...withoutSessionId } = validGuidanceEvent;
    expect(GuidanceEventSchema.safeParse(withoutSessionId).success).toBe(
      false,
    );

    const { sequence: _sequence, ...withoutSequence } = validGuidanceEvent;
    expect(GuidanceEventSchema.safeParse(withoutSequence).success).toBe(
      false,
    );

    const { shot: _shot, ...withoutShot } = validGuidanceEvent;
    expect(GuidanceEventSchema.safeParse(withoutShot).success).toBe(false);

    const { code: _code, ...withoutCode } = validGuidanceEvent;
    expect(GuidanceEventSchema.safeParse(withoutCode).success).toBe(false);

    const { message: _message, ...withoutMessage } = validGuidanceEvent;
    expect(GuidanceEventSchema.safeParse(withoutMessage).success).toBe(false);

    const { confidence: _confidence, ...withoutConfidence } =
      validGuidanceEvent;
    expect(GuidanceEventSchema.safeParse(withoutConfidence).success).toBe(
      false,
    );

    const { observedAt: _observedAt, ...withoutObservedAt } =
      validGuidanceEvent;
    expect(GuidanceEventSchema.safeParse(withoutObservedAt).success).toBe(
      false,
    );

    const { expiresAt: _expiresAt, ...withoutExpiresAt } = validGuidanceEvent;
    expect(GuidanceEventSchema.safeParse(withoutExpiresAt).success).toBe(
      false,
    );
  });

  it("rejects a non-integer, non-finite, or negative sequence", () => {
    expect(
      GuidanceEventSchema.safeParse({
        ...validGuidanceEvent,
        sequence: Number.NaN,
      }).success,
    ).toBe(false);

    expect(
      GuidanceEventSchema.safeParse({
        ...validGuidanceEvent,
        sequence: Number.POSITIVE_INFINITY,
      }).success,
    ).toBe(false);

    expect(
      GuidanceEventSchema.safeParse({
        ...validGuidanceEvent,
        sequence: -1,
      }).success,
    ).toBe(false);

    expect(
      GuidanceEventSchema.safeParse({
        ...validGuidanceEvent,
        sequence: 1.5,
      }).success,
    ).toBe(false);
  });

  it("rejects an out-of-range or non-finite confidence", () => {
    expect(
      GuidanceEventSchema.safeParse({
        ...validGuidanceEvent,
        confidence: -0.1,
      }).success,
    ).toBe(false);

    expect(
      GuidanceEventSchema.safeParse({
        ...validGuidanceEvent,
        confidence: 1.1,
      }).success,
    ).toBe(false);

    expect(
      GuidanceEventSchema.safeParse({
        ...validGuidanceEvent,
        confidence: Number.NaN,
      }).success,
    ).toBe(false);
  });

  it("rejects non-finite timestamps", () => {
    expect(
      GuidanceEventSchema.safeParse({
        ...validGuidanceEvent,
        observedAt: Number.POSITIVE_INFINITY,
      }).success,
    ).toBe(false);

    expect(
      GuidanceEventSchema.safeParse({
        ...validGuidanceEvent,
        expiresAt: Number.NaN,
      }).success,
    ).toBe(false);
  });

  it("rejects an expiresAt that does not come strictly after observedAt", () => {
    expect(
      GuidanceEventSchema.safeParse({
        ...validGuidanceEvent,
        observedAt: 1_000,
        expiresAt: 1_000,
      }).success,
    ).toBe(false);

    expect(
      GuidanceEventSchema.safeParse({
        ...validGuidanceEvent,
        observedAt: 1_000,
        expiresAt: 500,
      }).success,
    ).toBe(false);
  });

  it("rejects empty or whitespace-only sessionId and message", () => {
    expect(
      GuidanceEventSchema.safeParse({
        ...validGuidanceEvent,
        sessionId: "",
      }).success,
    ).toBe(false);

    expect(
      GuidanceEventSchema.safeParse({
        ...validGuidanceEvent,
        sessionId: "   ",
      }).success,
    ).toBe(false);

    expect(
      GuidanceEventSchema.safeParse({
        ...validGuidanceEvent,
        message: "",
      }).success,
    ).toBe(false);

    expect(
      GuidanceEventSchema.safeParse({
        ...validGuidanceEvent,
        message: "   ",
      }).success,
    ).toBe(false);
  });

  it("rejects unknown top-level keys", () => {
    expect(
      GuidanceEventSchema.safeParse({
        ...validGuidanceEvent,
        modelCommentary: "untrusted extra value",
      }).success,
    ).toBe(false);
  });
});

describe("GuidanceCodeSchema", () => {
  it("accepts a documented guidance code and rejects unknown ones", () => {
    expect(GuidanceCodeSchema.parse("READY")).toBe("READY");
    expect(GuidanceCodeSchema.safeParse("NOT_A_CODE").success).toBe(false);
  });
});

describe("ConnectionStateSchema", () => {
  it("accepts a documented connection state and rejects unknown ones", () => {
    expect(ConnectionStateSchema.parse("connected")).toBe("connected");
    expect(ConnectionStateSchema.safeParse("idle").success).toBe(false);
  });
});

describe("isGuidanceEventFresh", () => {
  it("returns true while now is before expiresAt", () => {
    expect(isGuidanceEventFresh(validGuidanceEvent, 1_500)).toBe(true);
  });

  it("returns false once now has reached or passed expiresAt", () => {
    expect(isGuidanceEventFresh(validGuidanceEvent, 2_000)).toBe(false);
    expect(isGuidanceEventFresh(validGuidanceEvent, 2_500)).toBe(false);
  });
});
