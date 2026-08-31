import { describe, expect, it } from "vitest";
import {
  toPixelRoi,
  type PixelRoi,
  type VideoRoiInput,
} from "./pixelRoi";

const SAME_ASPECT_FIXTURE: VideoRoiInput = {
  video: { width: 1920, height: 1080 },
  display: { width: 960, height: 540 },
  guide: { x: 0.25, y: 0.25, width: 0.5, height: 0.5 },
  objectFit: "cover",
};

const LANDSCAPE_TO_PORTRAIT_COVER_FIXTURE: VideoRoiInput = {
  video: { width: 1920, height: 1080 },
  display: { width: 360, height: 640 },
  guide: { x: 0, y: 0, width: 1, height: 1 },
  objectFit: "cover",
};

const PORTRAIT_TO_LANDSCAPE_COVER_FIXTURE: VideoRoiInput = {
  video: { width: 1080, height: 1920 },
  display: { width: 640, height: 360 },
  guide: { x: 0, y: 0, width: 1, height: 1 },
  objectFit: "cover",
};

function expectPixelRoiCloseTo(actual: PixelRoi | null, expected: PixelRoi): void {
  expect(actual).not.toBeNull();
  expect(actual?.x).toBeCloseTo(expected.x, 8);
  expect(actual?.y).toBeCloseTo(expected.y, 8);
  expect(actual?.width).toBeCloseTo(expected.width, 8);
  expect(actual?.height).toBeCloseTo(expected.height, 8);
}

describe("toPixelRoi", () => {
  it("maps a normalized same-aspect guide into intrinsic video pixels", () => {
    expect(toPixelRoi(SAME_ASPECT_FIXTURE)).toEqual({
      x: 480,
      y: 270,
      width: 960,
      height: 540,
    });
  });

  it("accounts for horizontal cropping when landscape video covers a portrait display", () => {
    expectPixelRoiCloseTo(
      toPixelRoi(LANDSCAPE_TO_PORTRAIT_COVER_FIXTURE),
      { x: 656.25, y: 0, width: 607.5, height: 1080 },
    );
  });

  it("accounts for vertical cropping when portrait video covers a landscape display", () => {
    expectPixelRoiCloseTo(
      toPixelRoi(PORTRAIT_TO_LANDSCAPE_COVER_FIXTURE),
      { x: 0, y: 656.25, width: 1080, height: 607.5 },
    );
  });

  it("clips a contain guide to the rendered video and maps only the overlap", () => {
    expectPixelRoiCloseTo(
      toPixelRoi({
        video: { width: 1920, height: 1080 },
        display: { width: 360, height: 640 },
        guide: { x: 0, y: 200 / 640, width: 1, height: 100 / 640 },
        objectFit: "contain",
      }),
      { x: 0, y: 0, width: 1920, height: 433.3333333333333 },
    );
  });

  it("returns null when a contain guide is wholly in letterboxing", () => {
    expect(
      toPixelRoi({
        video: { width: 1920, height: 1080 },
        display: { width: 360, height: 640 },
        guide: { x: 20 / 360, y: 20 / 640, width: 320 / 360, height: 100 / 640 },
        objectFit: "contain",
      }),
    ).toBeNull();
  });

  it("clamps normalized guide edges before converting them to display pixels", () => {
    expect(toPixelRoi({
      video: { width: 800, height: 800 },
      display: { width: 400, height: 400 },
      guide: { x: -0.25, y: 0.25, width: 0.75, height: 1 },
      objectFit: "cover",
    })).toEqual({
      x: 0,
      y: 200,
      width: 400,
      height: 600,
    });
  });

  it("rejects a visible source overlap smaller than one intrinsic pixel", () => {
    const input: VideoRoiInput = {
      video: { width: 100, height: 100 },
      display: { width: 100, height: 100 },
      guide: { x: 0, y: 0, width: 0.009, height: 0.02 },
      objectFit: "cover",
    };

    expect(toPixelRoi(input)).toBeNull();
    expect(toPixelRoi({
      ...input,
      guide: { x: 0, y: 0, width: 0.01, height: 0.01 },
    })).toEqual({ x: 0, y: 0, width: 1, height: 1 });
  });

  it("keeps a mathematical one-pixel ROI across a non-matching aspect ratio", () => {
    const result = toPixelRoi({
      video: { width: 100, height: 100 },
      display: { width: 100, height: 146 },
      guide: { x: 0.4927, y: 0, width: 0.0146, height: 1 },
      objectFit: "cover",
    });

    expect(result).not.toBeNull();
    expect(result?.width).toBe(1);
    expect(result?.height).toBeCloseTo(100, 10);
  });

  it("rejects invalid dimensions and object-fit values", () => {
    expect(() =>
      toPixelRoi({
        video: { width: 0, height: 1080 },
        display: { width: 360, height: 640 },
        guide: { x: 0, y: 0, width: 1, height: 1 },
        objectFit: "cover",
      }),
    ).toThrow(/video\.width/);

    expect(() =>
      toPixelRoi({
        video: { width: 1920, height: 1080 },
        display: { width: 360, height: 640 },
        guide: { x: 0, y: 0, width: Number.NaN, height: 1 },
        objectFit: "cover",
      }),
    ).toThrow(/guide\.width/);

    expect(() =>
      toPixelRoi({
        video: { width: 1920, height: 1080 },
        display: { width: 360, height: 640 },
        guide: { x: 0, y: 0, width: 1, height: 1 },
        objectFit: "fill" as VideoRoiInput["objectFit"],
      }),
    ).toThrow(/objectFit/);
  });
});
