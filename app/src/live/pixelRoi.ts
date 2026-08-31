export interface NormalizedGuideRect {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface VideoRoiInput {
  /** Fixed guide edges as 0..1 ratios of the display rectangle. */
  readonly guide: NormalizedGuideRect;
  /** Rendered preview dimensions in CSS pixels. */
  readonly display: { readonly width: number; readonly height: number };
  /** Intrinsic video dimensions in source pixels. */
  readonly video: { readonly width: number; readonly height: number };
  readonly objectFit: "cover" | "contain";
}

/** A possibly fractional rectangle in the intrinsic video-pixel coordinate space. */
export interface PixelRoi {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

interface Rect {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

interface ObjectFitTransform {
  readonly scale: number;
  readonly offsetX: number;
  readonly offsetY: number;
  readonly renderedWidth: number;
  readonly renderedHeight: number;
}

function assertPositiveFinite(value: number, name: string): void {
  if (!Number.isFinite(value) || value <= 0) {
    throw new RangeError(`${name} must be a finite number greater than zero`);
  }
}

function assertFinite(value: number, name: string): void {
  if (!Number.isFinite(value)) {
    throw new TypeError(`${name} must be finite`);
  }
}

function validateInput(input: VideoRoiInput): void {
  assertPositiveFinite(input.video.width, "video.width");
  assertPositiveFinite(input.video.height, "video.height");
  if (!Number.isInteger(input.video.width) || !Number.isInteger(input.video.height)) {
    throw new RangeError("video dimensions must be integers");
  }

  assertPositiveFinite(input.display.width, "display.width");
  assertPositiveFinite(input.display.height, "display.height");
  assertFinite(input.guide.x, "guide.x");
  assertFinite(input.guide.y, "guide.y");
  assertPositiveFinite(input.guide.width, "guide.width");
  assertPositiveFinite(input.guide.height, "guide.height");
  assertFinite(input.guide.x + input.guide.width, "guide right edge");
  assertFinite(input.guide.y + input.guide.height, "guide bottom edge");

  if (input.objectFit !== "cover" && input.objectFit !== "contain") {
    throw new TypeError('objectFit must be either "cover" or "contain"');
  }
}

function intersect(left: Rect, right: Rect): Rect | null {
  const x = Math.max(left.x, right.x);
  const y = Math.max(left.y, right.y);
  const maxX = Math.min(left.x + left.width, right.x + right.width);
  const maxY = Math.min(left.y + left.height, right.y + right.height);

  if (maxX <= x || maxY <= y) {
    return null;
  }

  return { x, y, width: maxX - x, height: maxY - y };
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function normalizeMinimumPixelEdge(
  start: number,
  length: number,
  bound: number,
): { readonly start: number; readonly length: number } | null {
  const tolerance = Number.EPSILON * Math.max(1, bound) * 32;
  if (length < 1 - tolerance) {
    return null;
  }
  if (length >= 1) {
    return { start, length };
  }

  // The mathematical result is one pixel, but the aspect-ratio division lost
  // a few ULPs. Snap only this machine-precision boundary case back to 1px.
  return {
    start: Math.max(0, Math.min(start, bound - 1)),
    length: 1,
  };
}

function computeObjectFitTransform(input: VideoRoiInput): ObjectFitTransform {
  const widthScale = input.display.width / input.video.width;
  const heightScale = input.display.height / input.video.height;
  const scale = input.objectFit === "cover"
    ? Math.max(widthScale, heightScale)
    : Math.min(widthScale, heightScale);
  const renderedWidth = input.video.width * scale;
  const renderedHeight = input.video.height * scale;

  return {
    scale,
    offsetX: (input.display.width - renderedWidth) / 2,
    offsetY: (input.display.height - renderedHeight) / 2,
    renderedWidth,
    renderedHeight,
  };
}

function normalizedGuideToDisplayRect(input: VideoRoiInput): Rect | null {
  // Clamp edges independently so an offscreen guide preserves only its actual
  // overlap (for example x=-0.2,width=0.3 becomes 0..0.1, not 0..0.3).
  const left = clamp(input.guide.x, 0, 1);
  const top = clamp(input.guide.y, 0, 1);
  const right = clamp(input.guide.x + input.guide.width, 0, 1);
  const bottom = clamp(input.guide.y + input.guide.height, 0, 1);

  if (right <= left || bottom <= top) {
    return null;
  }

  return {
    x: left * input.display.width,
    y: top * input.display.height,
    width: (right - left) * input.display.width,
    height: (bottom - top) * input.display.height,
  };
}

/**
 * Converts a normalized fixed guide into the intrinsic PixelRoi represented by
 * a centered CSS `object-fit` preview. The result stays fractional for direct
 * use with Canvas drawImage and is clipped to the visible video bounds.
 */
export function toPixelRoi(input: VideoRoiInput): PixelRoi | null {
  validateInput(input);
  const displayGuide = normalizedGuideToDisplayRect(input);
  if (!displayGuide) {
    return null;
  }

  const transform = computeObjectFitTransform(input);
  const renderedVideo: Rect = {
    x: transform.offsetX,
    y: transform.offsetY,
    width: transform.renderedWidth,
    height: transform.renderedHeight,
  };
  const visibleGuide = intersect(displayGuide, renderedVideo);
  if (!visibleGuide) {
    return null;
  }

  const x = clamp(
    (visibleGuide.x - transform.offsetX) / transform.scale,
    0,
    input.video.width,
  );
  const y = clamp(
    (visibleGuide.y - transform.offsetY) / transform.scale,
    0,
    input.video.height,
  );
  const maxX = clamp(
    (visibleGuide.x + visibleGuide.width - transform.offsetX) / transform.scale,
    0,
    input.video.width,
  );
  const maxY = clamp(
    (visibleGuide.y + visibleGuide.height - transform.offsetY) / transform.scale,
    0,
    input.video.height,
  );
  const width = maxX - x;
  const height = maxY - y;
  const horizontal = normalizeMinimumPixelEdge(x, width, input.video.width);
  const vertical = normalizeMinimumPixelEdge(y, height, input.video.height);

  if (!horizontal || !vertical) {
    return null;
  }

  return {
    x: horizontal.start,
    y: vertical.start,
    width: horizontal.length,
    height: vertical.length,
  };
}
