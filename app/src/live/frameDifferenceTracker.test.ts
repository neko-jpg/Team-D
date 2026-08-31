import { describe, expect, it } from "vitest";
import {
  StabilityTracker,
  normalizedFrameDifference,
  type RoiFrame,
} from "./frameDifferenceTracker";

function roi(
  value: number,
  width = 2,
  height = 1,
  geometryKey?: string,
): RoiFrame {
  return {
    width,
    height,
    data: new Uint8Array(width * height).fill(value),
    geometryKey,
  };
}

describe("normalizedFrameDifference", () => {
  it("returns mean absolute difference normalized to the grayscale range", () => {
    expect(
      normalizedFrameDifference(new Uint8Array([0, 255]), new Uint8Array([0, 0])),
    ).toBe(0.5);
  });
});

describe("StabilityTracker", () => {
  it("requires 600ms of sub-threshold differences after the baseline", () => {
    const tracker = new StabilityTracker();

    expect(tracker.update(roi(100), 0)).toMatchObject({
      isStable: false,
      normalizedDifference: null,
      resetReason: "initial",
    });
    expect(tracker.update(roi(100), 100)).toMatchObject({
      isStable: false,
      normalizedDifference: 0,
      stableSince: 100,
      stableForMs: 0,
    });
    expect(tracker.update(roi(100), 699).isStable).toBe(false);
    expect(tracker.update(roi(100), 700)).toMatchObject({
      isStable: true,
      stableForMs: 600,
      stableSince: 100,
    });
  });

  it("resets the stability window when movement crosses the threshold", () => {
    const tracker = new StabilityTracker();
    tracker.update(roi(100), 0);
    tracker.update(roi(100), 100);
    tracker.update(roi(100), 699);

    expect(tracker.update(roi(120), 700)).toMatchObject({
      isStable: false,
      reset: true,
      resetReason: "movement",
      normalizedDifference: 20 / 255,
      stableSince: 700,
      stableForMs: 0,
    });
    expect(tracker.update(roi(120), 1299).isStable).toBe(false);
    expect(tracker.update(roi(120), 1300).isStable).toBe(true);
  });

  it("resets when ROI size or geometry identity changes", () => {
    const tracker = new StabilityTracker();
    tracker.update(roi(100, 2, 1, "guide-a"), 0);
    tracker.update(roi(100, 2, 1, "guide-a"), 100);
    expect(tracker.update(roi(100, 2, 1, "guide-a"), 700).isStable).toBe(true);

    expect(tracker.update(roi(100, 3, 1, "guide-a"), 701)).toMatchObject({
      isStable: false,
      normalizedDifference: null,
      reset: true,
      resetReason: "geometry-change",
      stableSince: null,
    });
    expect(tracker.update(roi(100, 3, 1, "guide-a"), 1300).isStable).toBe(false);
    expect(tracker.update(roi(100, 3, 1, "guide-a"), 1900).isStable).toBe(true);

    expect(tracker.update(roi(100, 3, 1, "guide-b"), 1400)).toMatchObject({
      isStable: false,
      resetReason: "geometry-change",
    });
  });
});
