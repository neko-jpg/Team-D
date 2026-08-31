import { describe, expect, it } from "vitest";

import {
  ApprovedMeasurementSchema,
  isMeasurementWithinExpectedRange,
  MeasurementDraftSchema,
  MeasurementEndpointKeySchema,
  MeasurementEndpointsSchema,
  MeasurementFailureCodeSchema,
  MeasurementLineSchema,
  MeasurementMarkerSchema,
  MeasurementSourceSchema,
  MeasurementStatusSchema,
  NormalizedPointSchema,
} from "./measurementSchemas";
import type {
  ApprovedMeasurement,
  MeasurementDraft,
  MeasurementEndpoints,
  MeasurementLine,
  MeasurementMarker,
  NormalizedPoint,
} from "./measurementTypes";

const validPoint: NormalizedPoint = { x: 0.25, y: 0.75 };

const validEndpoints: MeasurementEndpoints = {
  lengthStart: { x: 0.1, y: 0.1 },
  lengthEnd: { x: 0.1, y: 0.9 },
  widthStart: { x: 0.2, y: 0.5 },
  widthEnd: { x: 0.8, y: 0.5 },
};

const validMarker: MeasurementMarker = {
  knownSideCm: 5,
  corners: [
    { x: 0.01, y: 0.01 },
    { x: 0.1, y: 0.01 },
    { x: 0.1, y: 0.1 },
    { x: 0.01, y: 0.1 },
  ],
  pxPerCm: 12.5,
};

const validLength: MeasurementLine = {
  start: { x: 0.1, y: 0.1 },
  end: { x: 0.1, y: 0.9 },
  valueCm: 60,
};

const validWidth: MeasurementLine = {
  start: { x: 0.2, y: 0.5 },
  end: { x: 0.8, y: 0.5 },
  valueCm: 45,
};

const validDraft: MeasurementDraft = {
  imageId: "img-001",
  marker: validMarker,
  length: validLength,
  width: validWidth,
  source: "ai",
  status: "needs_review",
};

const validApprovedMeasurement: ApprovedMeasurement = {
  ...validDraft,
  status: "approved_cv",
};

describe("NormalizedPointSchema", () => {
  it("accepts a point normalized to 0..1", () => {
    expect(NormalizedPointSchema.parse(validPoint)).toEqual(validPoint);
  });

  it("rejects out-of-range, non-finite, and missing coordinates", () => {
    expect(
      NormalizedPointSchema.safeParse({ ...validPoint, x: -0.1 }).success,
    ).toBe(false);
    expect(
      NormalizedPointSchema.safeParse({ ...validPoint, x: 1.1 }).success,
    ).toBe(false);
    expect(
      NormalizedPointSchema.safeParse({ ...validPoint, y: Number.NaN })
        .success,
    ).toBe(false);
    expect(
      NormalizedPointSchema.safeParse({
        ...validPoint,
        y: Number.POSITIVE_INFINITY,
      }).success,
    ).toBe(false);

    const { x: _x, ...withoutX } = validPoint;
    expect(NormalizedPointSchema.safeParse(withoutX).success).toBe(false);
  });

  it("rejects an unknown extra key", () => {
    expect(
      NormalizedPointSchema.safeParse({ ...validPoint, z: 0.5 }).success,
    ).toBe(false);
  });
});

describe("MeasurementEndpointKeySchema", () => {
  it("accepts the four documented endpoint keys", () => {
    expect(MeasurementEndpointKeySchema.parse("lengthStart")).toBe(
      "lengthStart",
    );
    expect(MeasurementEndpointKeySchema.parse("lengthEnd")).toBe("lengthEnd");
    expect(MeasurementEndpointKeySchema.parse("widthStart")).toBe(
      "widthStart",
    );
    expect(MeasurementEndpointKeySchema.parse("widthEnd")).toBe("widthEnd");
  });

  it("rejects an unknown endpoint key", () => {
    expect(MeasurementEndpointKeySchema.safeParse("heightStart").success).toBe(
      false,
    );
  });
});

describe("MeasurementEndpointsSchema", () => {
  it("accepts the four named endpoints", () => {
    expect(MeasurementEndpointsSchema.parse(validEndpoints)).toEqual(
      validEndpoints,
    );
  });

  it("rejects a missing endpoint and an unknown extra key", () => {
    const { lengthStart: _lengthStart, ...withoutLengthStart } =
      validEndpoints;
    expect(
      MeasurementEndpointsSchema.safeParse(withoutLengthStart).success,
    ).toBe(false);

    expect(
      MeasurementEndpointsSchema.safeParse({
        ...validEndpoints,
        heightStart: validPoint,
      }).success,
    ).toBe(false);
  });
});

describe("MeasurementMarkerSchema", () => {
  it("accepts a detected marker with four corners", () => {
    expect(MeasurementMarkerSchema.parse(validMarker)).toEqual(validMarker);
  });

  it("rejects a wrong knownSideCm literal", () => {
    expect(
      MeasurementMarkerSchema.safeParse({ ...validMarker, knownSideCm: 10 })
        .success,
    ).toBe(false);
  });

  it("rejects corners with three or five points", () => {
    expect(
      MeasurementMarkerSchema.safeParse({
        ...validMarker,
        corners: validMarker.corners.slice(0, 3),
      }).success,
    ).toBe(false);

    expect(
      MeasurementMarkerSchema.safeParse({
        ...validMarker,
        corners: [...validMarker.corners, { x: 0.5, y: 0.5 }],
      }).success,
    ).toBe(false);
  });

  it("rejects a non-positive or non-finite pxPerCm", () => {
    expect(
      MeasurementMarkerSchema.safeParse({ ...validMarker, pxPerCm: 0 })
        .success,
    ).toBe(false);
    expect(
      MeasurementMarkerSchema.safeParse({ ...validMarker, pxPerCm: -1 })
        .success,
    ).toBe(false);
    expect(
      MeasurementMarkerSchema.safeParse({
        ...validMarker,
        pxPerCm: Number.NaN,
      }).success,
    ).toBe(false);
  });

  it("rejects every required field missing", () => {
    const { knownSideCm: _knownSideCm, ...withoutKnownSideCm } = validMarker;
    expect(
      MeasurementMarkerSchema.safeParse(withoutKnownSideCm).success,
    ).toBe(false);

    const { corners: _corners, ...withoutCorners } = validMarker;
    expect(MeasurementMarkerSchema.safeParse(withoutCorners).success).toBe(
      false,
    );

    const { pxPerCm: _pxPerCm, ...withoutPxPerCm } = validMarker;
    expect(MeasurementMarkerSchema.safeParse(withoutPxPerCm).success).toBe(
      false,
    );
  });

  it("rejects an unknown extra key", () => {
    expect(
      MeasurementMarkerSchema.safeParse({
        ...validMarker,
        confidence: 0.9,
      }).success,
    ).toBe(false);
  });
});

describe("MeasurementLineSchema", () => {
  it("accepts a measured line", () => {
    expect(MeasurementLineSchema.parse(validLength)).toEqual(validLength);
  });

  it("rejects a non-positive or non-finite valueCm", () => {
    expect(
      MeasurementLineSchema.safeParse({ ...validLength, valueCm: 0 }).success,
    ).toBe(false);
    expect(
      MeasurementLineSchema.safeParse({ ...validLength, valueCm: -5 })
        .success,
    ).toBe(false);
    expect(
      MeasurementLineSchema.safeParse({
        ...validLength,
        valueCm: Number.NaN,
      }).success,
    ).toBe(false);
  });

  it("accepts an out-of-range cm value; range checks are warning-only", () => {
    expect(
      MeasurementLineSchema.safeParse({ ...validLength, valueCm: 150 })
        .success,
    ).toBe(true);
  });

  it("rejects every required field missing", () => {
    const { start: _start, ...withoutStart } = validLength;
    expect(MeasurementLineSchema.safeParse(withoutStart).success).toBe(false);

    const { end: _end, ...withoutEnd } = validLength;
    expect(MeasurementLineSchema.safeParse(withoutEnd).success).toBe(false);

    const { valueCm: _valueCm, ...withoutValueCm } = validLength;
    expect(MeasurementLineSchema.safeParse(withoutValueCm).success).toBe(
      false,
    );
  });

  it("rejects an unknown extra key", () => {
    expect(
      MeasurementLineSchema.safeParse({
        ...validLength,
        unit: "cm",
      }).success,
    ).toBe(false);
  });
});

describe("MeasurementSourceSchema", () => {
  it("accepts the documented sources", () => {
    expect(MeasurementSourceSchema.parse("ai")).toBe("ai");
    expect(MeasurementSourceSchema.parse("contour")).toBe("contour");
    expect(MeasurementSourceSchema.parse("user")).toBe("user");
  });

  it("rejects an unknown source", () => {
    expect(MeasurementSourceSchema.safeParse("manual-override").success).toBe(
      false,
    );
  });
});

describe("MeasurementStatusSchema", () => {
  it("accepts the documented statuses", () => {
    expect(MeasurementStatusSchema.parse("needs_review")).toBe(
      "needs_review",
    );
    expect(MeasurementStatusSchema.parse("approved_cv")).toBe("approved_cv");
    expect(MeasurementStatusSchema.parse("approved_manual")).toBe(
      "approved_manual",
    );
  });

  it("rejects an unknown status", () => {
    expect(MeasurementStatusSchema.safeParse("rejected").success).toBe(false);
  });
});

describe("MeasurementFailureCodeSchema", () => {
  it("accepts the documented failure codes", () => {
    expect(MeasurementFailureCodeSchema.parse("MARKER_MISSING")).toBe(
      "MARKER_MISSING",
    );
    expect(MeasurementFailureCodeSchema.parse("ENDPOINTS_INVALID")).toBe(
      "ENDPOINTS_INVALID",
    );
  });

  it("rejects an unknown failure code", () => {
    expect(
      MeasurementFailureCodeSchema.safeParse("MARKER_UPSIDE_DOWN").success,
    ).toBe(false);
  });
});

describe("MeasurementDraftSchema", () => {
  it("accepts a full measurement draft", () => {
    expect(MeasurementDraftSchema.parse(validDraft)).toEqual(validDraft);
  });

  it("accepts a null marker for the marker-failure path", () => {
    const draftWithoutMarker: MeasurementDraft = {
      ...validDraft,
      marker: null,
      status: "needs_review",
    };
    expect(MeasurementDraftSchema.parse(draftWithoutMarker)).toEqual(
      draftWithoutMarker,
    );
  });

  it("accepts an out-of-range cm value; range checks are warning-only", () => {
    const outOfRangeDraft: MeasurementDraft = {
      ...validDraft,
      length: { ...validLength, valueCm: 150 },
    };
    expect(MeasurementDraftSchema.safeParse(outOfRangeDraft).success).toBe(
      true,
    );
  });

  it("rejects an unknown source or status", () => {
    expect(
      MeasurementDraftSchema.safeParse({
        ...validDraft,
        source: "camera",
      }).success,
    ).toBe(false);

    expect(
      MeasurementDraftSchema.safeParse({
        ...validDraft,
        status: "rejected",
      }).success,
    ).toBe(false);
  });

  it("rejects every required field missing", () => {
    const { imageId: _imageId, ...withoutImageId } = validDraft;
    expect(MeasurementDraftSchema.safeParse(withoutImageId).success).toBe(
      false,
    );

    const { marker: _marker, ...withoutMarker } = validDraft;
    expect(MeasurementDraftSchema.safeParse(withoutMarker).success).toBe(
      false,
    );

    const { length: _length, ...withoutLength } = validDraft;
    expect(MeasurementDraftSchema.safeParse(withoutLength).success).toBe(
      false,
    );

    const { width: _width, ...withoutWidth } = validDraft;
    expect(MeasurementDraftSchema.safeParse(withoutWidth).success).toBe(
      false,
    );

    const { source: _source, ...withoutSource } = validDraft;
    expect(MeasurementDraftSchema.safeParse(withoutSource).success).toBe(
      false,
    );

    const { status: _status, ...withoutStatus } = validDraft;
    expect(MeasurementDraftSchema.safeParse(withoutStatus).success).toBe(
      false,
    );
  });

  it("rejects an unknown extra key", () => {
    expect(
      MeasurementDraftSchema.safeParse({
        ...validDraft,
        debug: "must not cross the boundary",
      }).success,
    ).toBe(false);
  });
});

describe("ApprovedMeasurementSchema", () => {
  it("accepts an approved_cv and an approved_manual measurement", () => {
    expect(
      ApprovedMeasurementSchema.parse(validApprovedMeasurement),
    ).toEqual(validApprovedMeasurement);

    const approvedManual: ApprovedMeasurement = {
      ...validDraft,
      status: "approved_manual",
    };
    expect(ApprovedMeasurementSchema.parse(approvedManual)).toEqual(
      approvedManual,
    );
  });

  it("rejects status: needs_review", () => {
    expect(
      ApprovedMeasurementSchema.safeParse({
        ...validDraft,
        status: "needs_review",
      }).success,
    ).toBe(false);
  });

  it("rejects an unknown extra key", () => {
    expect(
      ApprovedMeasurementSchema.safeParse({
        ...validApprovedMeasurement,
        approvedBy: "user",
      }).success,
    ).toBe(false);
  });
});

describe("isMeasurementWithinExpectedRange", () => {
  it("returns true when both length and width are within range", () => {
    expect(isMeasurementWithinExpectedRange(validDraft)).toBe(true);
  });

  it("returns false when the length is outside the expected range", () => {
    const outOfRangeDraft: MeasurementDraft = {
      ...validDraft,
      length: { ...validLength, valueCm: 150 },
    };
    expect(isMeasurementWithinExpectedRange(outOfRangeDraft)).toBe(false);
  });

  it("returns false when the width is outside the expected range", () => {
    const outOfRangeDraft: MeasurementDraft = {
      ...validDraft,
      width: { ...validWidth, valueCm: 5 },
    };
    expect(isMeasurementWithinExpectedRange(outOfRangeDraft)).toBe(false);
  });
});
