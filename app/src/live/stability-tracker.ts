export const DEFAULT_FRAME_DIFFERENCE_THRESHOLD = 0.02;
export const DEFAULT_STABLE_DURATION_MS = 600;
export const GRAYSCALE_MAX_VALUE = 255;

export interface RoiGeometry {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface RoiFrame {
  readonly width: number;
  readonly height: number;
  /** Grayscale values, normally 0..255. `pixels` is accepted as an alias. */
  readonly data?: ArrayLike<number>;
  readonly pixels?: ArrayLike<number>;
  /** Changes to any geometry identity reset the stability history. */
  readonly geometryKey?: string | number;
  readonly sourceSize?: { readonly width: number; readonly height: number };
  readonly roi?: RoiGeometry;
}

export type GrayRoiFrame = RoiFrame;

export type StabilityResetReason = "initial" | "geometry-change" | "movement" | undefined;

export interface StabilityResult {
  readonly isStable: boolean;
  /** Alias for isStable for callers that use the shorter state name. */
  readonly stable: boolean;
  /** Null when there is no comparable previous frame. */
  readonly normalizedDifference: number | null;
  /** Alias for normalizedDifference. */
  readonly delta: number | null;
  readonly stableSince: number | null;
  readonly stableForMs: number;
  readonly reset: boolean;
  readonly resetReason: StabilityResetReason;
}

export interface StabilityTrackerOptions {
  threshold?: number;
  stableDurationMs?: number;
  /** Alias for threshold. */
  movementThreshold?: number;
  /** Alias for stableDurationMs. */
  durationMs?: number;
}

interface StoredFrame {
  readonly width: number;
  readonly height: number;
  readonly values: Float64Array;
  readonly geometrySignature: string;
}

function geometrySignature(frame: RoiFrame): string {
  const source = frame.sourceSize
    ? `${frame.sourceSize.width}x${frame.sourceSize.height}`
    : "-";
  const roi = frame.roi
    ? `${frame.roi.x},${frame.roi.y},${frame.roi.width},${frame.roi.height}`
    : "-";
  const key = frame.geometryKey === undefined ? "-" : String(frame.geometryKey);
  return `${key}|${source}|${roi}`;
}

function frameValues(frame: RoiFrame): Float64Array {
  if (!Number.isInteger(frame.width) || frame.width <= 0) {
    throw new RangeError("ROI width must be a positive integer");
  }
  if (!Number.isInteger(frame.height) || frame.height <= 0) {
    throw new RangeError("ROI height must be a positive integer");
  }

  const source = frame.data ?? frame.pixels;
  if (!source) {
    throw new TypeError("ROI frame requires data or pixels");
  }

  const expectedLength = frame.width * frame.height;
  if (source.length !== expectedLength) {
    throw new RangeError(
      `ROI pixel count ${source.length} does not match ${frame.width}x${frame.height}`,
    );
  }

  const values = new Float64Array(expectedLength);
  for (let index = 0; index < expectedLength; index += 1) {
    const value = Number(source[index]);
    if (!Number.isFinite(value)) {
      throw new TypeError("ROI grayscale values must be finite");
    }
    values[index] = value;
  }

  return values;
}

export function normalizedFrameDifference(
  previous: ArrayLike<number>,
  current: ArrayLike<number>,
  maxValue = GRAYSCALE_MAX_VALUE,
): number {
  if (previous.length !== current.length) {
    throw new RangeError("Frame difference requires equal pixel counts");
  }
  if (previous.length === 0) {
    throw new RangeError("Frame difference requires at least one pixel");
  }
  if (!Number.isFinite(maxValue) || maxValue <= 0) {
    throw new RangeError("Frame difference maxValue must be greater than zero");
  }

  let absoluteDifference = 0;
  for (let index = 0; index < previous.length; index += 1) {
    absoluteDifference += Math.abs(Number(current[index]) - Number(previous[index]));
  }

  return absoluteDifference / (previous.length * maxValue);
}

function result(
  isStable: boolean,
  normalizedDifference: number | null,
  stableSince: number | null,
  nowMs: number,
  reset: boolean,
  resetReason: StabilityResetReason,
): StabilityResult {
  const stableForMs = stableSince === null ? 0 : Math.max(0, nowMs - stableSince);
  return {
    isStable,
    stable: isStable,
    normalizedDifference,
    delta: normalizedDifference,
    stableSince,
    stableForMs,
    reset,
    resetReason,
  };
}

/**
 * Tracks stability for one continuous grayscale ROI.
 *
 * Only the immediately previous ROI is retained. The first frame establishes a
 * baseline but is not stable; a second frame with a sub-threshold difference
 * starts the 600ms stability window. Movement replaces that baseline and resets
 * the window. A dimension or geometry identity change is also a hard reset.
 */
export class StabilityTracker {
  private readonly threshold: number;
  private readonly stableDurationMs: number;
  private previous?: StoredFrame;
  private stableSinceMs: number | null = null;
  private lastResult = result(false, null, null, 0, false, undefined);

  public constructor(options?: StabilityTrackerOptions);
  public constructor(threshold?: number, stableDurationMs?: number);
  public constructor(
    optionsOrThreshold: StabilityTrackerOptions | number = {},
    shorthandDurationMs = DEFAULT_STABLE_DURATION_MS,
  ) {
    const options: StabilityTrackerOptions =
      typeof optionsOrThreshold === "number"
        ? { threshold: optionsOrThreshold, stableDurationMs: shorthandDurationMs }
        : optionsOrThreshold;
    const threshold = options.threshold ?? options.movementThreshold ?? DEFAULT_FRAME_DIFFERENCE_THRESHOLD;
    const duration = options.stableDurationMs ?? options.durationMs ?? DEFAULT_STABLE_DURATION_MS;

    if (!Number.isFinite(threshold) || threshold < 0) {
      throw new RangeError("Stability threshold must be zero or greater");
    }
    if (!Number.isFinite(duration) || duration < 0) {
      throw new RangeError("Stable duration must be zero or greater");
    }

    this.threshold = threshold;
    this.stableDurationMs = duration;
  }

  public get movementThreshold(): number {
    return this.threshold;
  }

  public get durationMs(): number {
    return this.stableDurationMs;
  }

  public get previousFrameSize(): { readonly width: number; readonly height: number } | null {
    return this.previous
      ? { width: this.previous.width, height: this.previous.height }
      : null;
  }

  public getSnapshot(): StabilityResult {
    return this.lastResult;
  }

  public reset(): void {
    this.previous = undefined;
    this.stableSinceMs = null;
    this.lastResult = result(false, null, null, 0, true, "initial");
  }

  public update(frame: RoiFrame, nowMs: number): StabilityResult {
    if (!Number.isFinite(nowMs)) {
      throw new TypeError("Stability timestamp must be finite");
    }

    const values = frameValues(frame);
    const signature = geometrySignature(frame);
    const next: StoredFrame = {
      width: frame.width,
      height: frame.height,
      values,
      geometrySignature: signature,
    };

    if (!this.previous) {
      this.previous = next;
      this.stableSinceMs = null;
      this.lastResult = result(false, null, null, nowMs, true, "initial");
      return this.lastResult;
    }

    if (
      this.previous.width !== next.width ||
      this.previous.height !== next.height ||
      this.previous.geometrySignature !== next.geometrySignature
    ) {
      this.previous = next;
      // A geometry change establishes a new baseline. The stability window
      // starts only after the next comparable frame, never on the reset frame.
      this.stableSinceMs = null;
      this.lastResult = result(false, null, null, nowMs, true, "geometry-change");
      return this.lastResult;
    }

    const difference = normalizedFrameDifference(this.previous.values, next.values);
    this.previous = next;

    if (difference >= this.threshold) {
      this.stableSinceMs = nowMs;
      this.lastResult = result(false, difference, this.stableSinceMs, nowMs, true, "movement");
      return this.lastResult;
    }

    if (this.stableSinceMs === null) {
      this.stableSinceMs = nowMs;
    }

    const stableForMs = Math.max(0, nowMs - this.stableSinceMs);
    const isStable = stableForMs >= this.stableDurationMs;
    this.lastResult = result(
      isStable,
      difference,
      this.stableSinceMs,
      nowMs,
      false,
      undefined,
    );
    return this.lastResult;
  }

  public process(frame: RoiFrame, nowMs: number): StabilityResult {
    return this.update(frame, nowMs);
  }
}

export function createStabilityTracker(options?: StabilityTrackerOptions): StabilityTracker {
  return new StabilityTracker(options);
}
