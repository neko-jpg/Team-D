/**
 * Limited adaptation from document-autocapture at commit
 * e24df25d17ddc4cf7d7944c653bd0fba55025452:
 * - packages/core-engine/src/pipeline/pixels.ts
 * - packages/core-engine/src/pipeline/quality.ts
 *
 * The original project is MIT licensed, Copyright (c) 2026 Maaz Khan.
 * See THIRD_PARTY_NOTICES.md. Document Quad, glare, area, and auto-capture
 * behavior are intentionally excluded; this module analyzes an already-cropped
 * fixed garment-guide ROI only.
 */

export const DEFAULT_BRIGHTNESS_MIN = 45;
export const DEFAULT_BRIGHTNESS_MAX = 215;
export const DEFAULT_BLUR_VARIANCE_MIN = 24;

export type ImageQualityIssue =
  | "TOO_DARK"
  | "TOO_BRIGHT"
  | "TOO_BLURRY";

export interface ImageQualityThresholds {
  readonly brightnessMin?: number;
  readonly brightnessMax?: number;
  readonly blurVarianceMin?: number;
}

export interface BrightnessCheckResult {
  readonly averageLuma: number;
  readonly ok: boolean;
  readonly issue: "TOO_DARK" | "TOO_BRIGHT" | null;
}

export interface BlurCheckResult {
  readonly laplacianVariance: number;
  readonly ok: boolean;
}

export interface ImageQualityResult {
  /** Null means quality passed; StabilityTracker alone decides HOLD_STEADY/READY. */
  readonly issue: ImageQualityIssue | null;
  readonly averageLuma: number;
  readonly laplacianVariance: number;
  readonly brightnessOk: boolean;
  readonly blurOk: boolean;
}

interface ResolvedThresholds {
  readonly brightnessMin: number;
  readonly brightnessMax: number;
  readonly blurVarianceMin: number;
}

function assertDimensions(width: number, height: number): number {
  if (!Number.isInteger(width) || width <= 0) {
    throw new RangeError("width must be a positive integer");
  }
  if (!Number.isInteger(height) || height <= 0) {
    throw new RangeError("height must be a positive integer");
  }

  return width * height;
}

function assertPixelLength(
  pixels: ArrayLike<number>,
  expectedLength: number,
  name: string,
): void {
  if (pixels.length !== expectedLength) {
    throw new RangeError(
      `${name} length ${pixels.length} does not match expected length ${expectedLength}`,
    );
  }
}

function resolveThresholds(
  thresholds: ImageQualityThresholds = {},
): ResolvedThresholds {
  const resolved = {
    brightnessMin: thresholds.brightnessMin ?? DEFAULT_BRIGHTNESS_MIN,
    brightnessMax: thresholds.brightnessMax ?? DEFAULT_BRIGHTNESS_MAX,
    blurVarianceMin: thresholds.blurVarianceMin ?? DEFAULT_BLUR_VARIANCE_MIN,
  };

  if (
    !Number.isFinite(resolved.brightnessMin) ||
    resolved.brightnessMin < 0 ||
    resolved.brightnessMin > 255
  ) {
    throw new RangeError("brightnessMin must be between 0 and 255");
  }
  if (
    !Number.isFinite(resolved.brightnessMax) ||
    resolved.brightnessMax < 0 ||
    resolved.brightnessMax > 255
  ) {
    throw new RangeError("brightnessMax must be between 0 and 255");
  }
  if (resolved.brightnessMin > resolved.brightnessMax) {
    throw new RangeError("brightnessMin must not exceed brightnessMax");
  }
  if (!Number.isFinite(resolved.blurVarianceMin) || resolved.blurVarianceMin < 0) {
    throw new RangeError("blurVarianceMin must be zero or greater");
  }

  return resolved;
}

/** Converts RGBA pixels to BT.601-style luma while deliberately ignoring alpha. */
export function rgbaToGrayscale(
  rgba: Uint8ClampedArray,
  width: number,
  height: number,
  reuse?: Uint8ClampedArray,
): Uint8ClampedArray {
  const length = assertDimensions(width, height);
  assertPixelLength(rgba, length * 4, "RGBA buffer");

  const output = reuse && reuse.length === length
    ? reuse
    : new Uint8ClampedArray(length);
  for (let index = 0; index < length; index += 1) {
    const rgbaIndex = index * 4;
    const red = rgba[rgbaIndex];
    const green = rgba[rgbaIndex + 1];
    const blue = rgba[rgbaIndex + 2];
    output[index] = Math.round(0.299 * red + 0.587 * green + 0.114 * blue);
  }

  return output;
}

/** Checks the average luma of the already-cropped fixed guide ROI. */
export function brightnessCheck(
  grayscale: Uint8ClampedArray,
  width: number,
  height: number,
  thresholds: ImageQualityThresholds = {},
): BrightnessCheckResult {
  const length = assertDimensions(width, height);
  assertPixelLength(grayscale, length, "Grayscale buffer");
  const resolved = resolveThresholds(thresholds);

  let sum = 0;
  for (let index = 0; index < length; index += 1) {
    sum += grayscale[index];
  }
  const averageLuma = sum / length;
  const issue = averageLuma < resolved.brightnessMin
    ? "TOO_DARK"
    : averageLuma > resolved.brightnessMax
      ? "TOO_BRIGHT"
      : null;

  return {
    averageLuma,
    ok: issue === null,
    issue,
  };
}

/**
 * Returns the population variance of the four-neighbor Laplacian over interior
 * pixels. Images smaller than 3x3 have no valid interior sample and return 0.
 */
export function laplacianVariance(
  grayscale: Uint8ClampedArray,
  width: number,
  height: number,
): number {
  const length = assertDimensions(width, height);
  assertPixelLength(grayscale, length, "Grayscale buffer");

  let sum = 0;
  let squaredSum = 0;
  let samples = 0;

  for (let y = 1; y < height - 1; y += 1) {
    for (let x = 1; x < width - 1; x += 1) {
      const index = y * width + x;
      const laplacian =
        grayscale[index] * 4 -
        grayscale[(y - 1) * width + x] -
        grayscale[(y + 1) * width + x] -
        grayscale[y * width + (x - 1)] -
        grayscale[y * width + (x + 1)];
      sum += laplacian;
      squaredSum += laplacian * laplacian;
      samples += 1;
    }
  }

  if (samples === 0) {
    return 0;
  }

  const mean = sum / samples;
  return squaredSum / samples - mean * mean;
}

/** Checks whether the fixed guide ROI has enough local edge detail. */
export function blurCheck(
  grayscale: Uint8ClampedArray,
  width: number,
  height: number,
  thresholds: ImageQualityThresholds = {},
): BlurCheckResult {
  const resolved = resolveThresholds(thresholds);
  const variance = laplacianVariance(grayscale, width, height);

  return {
    laplacianVariance: variance,
    ok: variance >= resolved.blurVarianceMin,
  };
}

/** Applies brightness before blur, leaving HOLD_STEADY/READY to StabilityTracker. */
export function assessGrayscaleImageQuality(
  grayscale: Uint8ClampedArray,
  width: number,
  height: number,
  thresholds: ImageQualityThresholds = {},
): ImageQualityResult {
  const brightness = brightnessCheck(grayscale, width, height, thresholds);
  const blur = blurCheck(grayscale, width, height, thresholds);
  const issue: ImageQualityIssue | null = brightness.issue ?? (blur.ok ? null : "TOO_BLURRY");

  return {
    issue,
    averageLuma: brightness.averageLuma,
    laplacianVariance: blur.laplacianVariance,
    brightnessOk: brightness.ok,
    blurOk: blur.ok,
  };
}

export function assessRgbaImageQuality(
  rgba: Uint8ClampedArray,
  width: number,
  height: number,
  thresholds: ImageQualityThresholds = {},
  grayscaleReuse?: Uint8ClampedArray,
): ImageQualityResult {
  const grayscale = rgbaToGrayscale(rgba, width, height, grayscaleReuse);
  return assessGrayscaleImageQuality(grayscale, width, height, thresholds);
}
