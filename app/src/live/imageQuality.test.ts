import { describe, expect, it } from "vitest";
import {
  DEFAULT_BLUR_VARIANCE_MIN,
  DEFAULT_BRIGHTNESS_MAX,
  DEFAULT_BRIGHTNESS_MIN,
  assessRgbaImageQuality,
  blurCheck,
  brightnessCheck,
  laplacianVariance,
  rgbaToGrayscale,
} from "./imageQuality";

interface RgbaFixture {
  readonly width: number;
  readonly height: number;
  readonly rgba: Uint8ClampedArray;
}

function rgbaFixture(
  width: number,
  height: number,
  lumaAt: (x: number, y: number) => number,
): RgbaFixture {
  const rgba = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const value = lumaAt(x, y);
      const index = (y * width + x) * 4;
      rgba[index] = value;
      rgba[index + 1] = value;
      rgba[index + 2] = value;
      rgba[index + 3] = 255;
    }
  }
  return { width, height, rgba };
}

// Deterministic post-resize ROI fixtures. Production supplies the same shape
// after cropping the PixelRoi and scaling its longest edge to at most 320px.
const DARK_FIXTURE = rgbaFixture(32, 32, () => 32);
const BRIGHT_FIXTURE = rgbaFixture(32, 32, () => 232);
const BLURRED_FIXTURE = rgbaFixture(32, 32, (x) => 96 + Math.round((64 * x) / 31));
const SHARP_FIXTURE = rgbaFixture(32, 32, (x, y) => ((x + y) % 2 === 0 ? 48 : 208));

describe("rgbaToGrayscale", () => {
  it("uses the adopted weighted RGB formula, ignores alpha, and can reuse a buffer", () => {
    const rgba = new Uint8ClampedArray([
      255, 0, 0, 0,
      0, 255, 0, 100,
      0, 0, 255, 255,
    ]);
    const reuse = new Uint8ClampedArray(3);

    const result = rgbaToGrayscale(rgba, 3, 1, reuse);

    expect(result).toBe(reuse);
    expect([...result]).toEqual([76, 150, 29]);
  });

  it("rejects a buffer that does not match the declared dimensions", () => {
    expect(() => rgbaToGrayscale(new Uint8ClampedArray(4), 2, 1)).toThrow(
      /RGBA buffer length/,
    );
  });
});

describe("fixed ROI image quality fixtures", () => {
  it("classifies the dark fixture below average luma 45", () => {
    expect(
      assessRgbaImageQuality(
        DARK_FIXTURE.rgba,
        DARK_FIXTURE.width,
        DARK_FIXTURE.height,
      ),
    ).toMatchObject({
      issue: "TOO_DARK",
      averageLuma: 32,
      brightnessOk: false,
    });
  });

  it("classifies the bright fixture above average luma 215", () => {
    expect(
      assessRgbaImageQuality(
        BRIGHT_FIXTURE.rgba,
        BRIGHT_FIXTURE.width,
        BRIGHT_FIXTURE.height,
      ),
    ).toMatchObject({
      issue: "TOO_BRIGHT",
      averageLuma: 232,
      brightnessOk: false,
    });
  });

  it("classifies the smooth, in-range fixture below Laplacian variance 24", () => {
    const result = assessRgbaImageQuality(
      BLURRED_FIXTURE.rgba,
      BLURRED_FIXTURE.width,
      BLURRED_FIXTURE.height,
    );

    expect(result.issue).toBe("TOO_BLURRY");
    expect(result.brightnessOk).toBe(true);
    expect(result.laplacianVariance).toBeLessThan(24);
    expect(result.blurOk).toBe(false);
  });

  it("leaves an in-range, high-detail fixture issue-free for the stability tracker", () => {
    const result = assessRgbaImageQuality(
      SHARP_FIXTURE.rgba,
      SHARP_FIXTURE.width,
      SHARP_FIXTURE.height,
    );

    expect(result.issue).toBeNull();
    expect(result.averageLuma).toBe(128);
    expect(result.laplacianVariance).toBeGreaterThanOrEqual(24);
    expect(result.blurOk).toBe(true);
  });

  it("uses the exact inclusive 45..215 brightness boundaries", () => {
    expect(brightnessCheck(new Uint8ClampedArray([44]), 1, 1).issue).toBe("TOO_DARK");
    expect(brightnessCheck(new Uint8ClampedArray([45]), 1, 1).ok).toBe(true);
    expect(brightnessCheck(new Uint8ClampedArray([215]), 1, 1).ok).toBe(true);
    expect(brightnessCheck(new Uint8ClampedArray([216]), 1, 1).issue).toBe("TOO_BRIGHT");
  });

  it("keeps the OpenSpec default thresholds fixed at 45, 215, and 24", () => {
    expect(DEFAULT_BRIGHTNESS_MIN).toBe(45);
    expect(DEFAULT_BRIGHTNESS_MAX).toBe(215);
    expect(DEFAULT_BLUR_VARIANCE_MIN).toBe(24);
  });

  it("matches the adopted four-neighbor population-variance calculation", () => {
    const grayscale = new Uint8ClampedArray(16);
    grayscale[5] = 255;

    expect(laplacianVariance(grayscale, 4, 4)).toBe(276_356.25);
  });

  it("accepts a Laplacian variance exactly on the configured boundary", () => {
    const grayscale = rgbaToGrayscale(
      SHARP_FIXTURE.rgba,
      SHARP_FIXTURE.width,
      SHARP_FIXTURE.height,
    );
    const variance = laplacianVariance(
      grayscale,
      SHARP_FIXTURE.width,
      SHARP_FIXTURE.height,
    );

    expect(
      blurCheck(grayscale, SHARP_FIXTURE.width, SHARP_FIXTURE.height, {
        blurVarianceMin: variance,
      }).ok,
    ).toBe(true);
    expect(
      blurCheck(grayscale, SHARP_FIXTURE.width, SHARP_FIXTURE.height, {
        blurVarianceMin: variance + 1,
      }).ok,
    ).toBe(false);
  });

  it("returns zero variance when no interior Laplacian sample exists", () => {
    expect(laplacianVariance(new Uint8ClampedArray([128, 128]), 2, 1)).toBe(0);
  });
});
