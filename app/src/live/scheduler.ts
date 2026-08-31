export const FRAME_RATE_HZ = 4;
export const DEFAULT_FRAME_INTERVAL_MS = 1000 / FRAME_RATE_HZ;
export const DEFAULT_INTERVAL_MS = DEFAULT_FRAME_INTERVAL_MS;

export type FrameSchedulerSource = "rvfc" | "raf" | "timer" | "manual";
export type TimerHandle = unknown;
export type AnimationFrameHandle = unknown;

export type FrameProcessor<Frame> = (
  frame: Frame,
  tick: FrameSchedulerTick<Frame>,
) => void | PromiseLike<void>;

export type FrameReader<Frame> = (
  tick: Omit<FrameSchedulerTick<Frame>, "frame">,
) => Frame | undefined;

export interface FrameSchedulerTick<Frame> {
  readonly source: FrameSchedulerSource;
  readonly timestamp: number;
  readonly metadata?: unknown;
  readonly frame?: Frame;
}

export type VideoFrameCallback = (timestamp: number, metadata: unknown) => void;

export interface VideoFrameCallbackSource {
  requestVideoFrameCallback?: (callback: VideoFrameCallback) => TimerHandle;
  cancelVideoFrameCallback?: (handle: TimerHandle) => void;
}

export interface FrameSchedulerPlatform {
  now: () => number;
  setTimeout: (callback: () => void, delayMs: number) => TimerHandle;
  clearTimeout: (handle: TimerHandle) => void;
  requestVideoFrameCallback?: (callback: VideoFrameCallback) => TimerHandle;
  cancelVideoFrameCallback?: (handle: TimerHandle) => void;
  requestAnimationFrame?: (callback: (timestamp: number) => void) => AnimationFrameHandle;
  cancelAnimationFrame?: (handle: AnimationFrameHandle) => void;
}

export type FrameSchedulerClock = Pick<
  FrameSchedulerPlatform,
  "now" | "setTimeout" | "clearTimeout"
>;

export interface FrameSchedulerOptions<Frame> {
  /** The single in-flight analysis operation. */
  onFrame?: FrameProcessor<Frame>;
  /** Alias for onFrame, useful when the processor is named after its work. */
  processFrame?: FrameProcessor<Frame>;
  /** Reads the current video frame when a browser callback fires. */
  readFrame?: FrameReader<Frame>;
  /** Alias for readFrame. */
  frameProvider?: FrameReader<Frame>;
  /** A video-like object exposing requestVideoFrameCallback, if available. */
  video?: VideoFrameCallbackSource;
  /** Alias for video, useful for callers that model it as a frame source. */
  frameSource?: VideoFrameCallbackSource;
  /** Browser APIs and the clock. Supplying these makes the scheduler deterministic. */
  platform?: Partial<FrameSchedulerPlatform>;
  /** Clock-only shorthand for tests that do not expose browser callbacks. */
  clock?: FrameSchedulerClock;
  /** Four hertz by default. Values below zero are rejected. */
  intervalMs?: number;
  /** Manual mode does not register browser callbacks; callers drive it with tick(). */
  mode?: "auto" | "manual";
  /** Analysis and callback failures are reported without stopping the schedule. */
  onError?: (error: unknown, tick?: FrameSchedulerTick<Frame>) => void;
  /** Optional cleanup for a frame discarded while another frame is being analyzed. */
  disposeFrame?: (frame: Frame) => void;
}

interface PendingFrame<Frame> {
  readonly frame: Frame;
  readonly tick: FrameSchedulerTick<Frame>;
}

function defaultPlatform(): FrameSchedulerPlatform {
  const globalScope = globalThis as typeof globalThis & {
    requestAnimationFrame?: (callback: (timestamp: number) => void) => number;
    cancelAnimationFrame?: (handle: number) => void;
  };

  return {
    now: () =>
      typeof globalScope.performance?.now === "function"
        ? globalScope.performance.now()
        : Date.now(),
    setTimeout: (callback, delayMs) => globalScope.setTimeout(callback, delayMs),
    clearTimeout: (handle) => globalScope.clearTimeout(handle as number),
    requestAnimationFrame:
      typeof globalScope.requestAnimationFrame === "function"
        ? globalScope.requestAnimationFrame.bind(globalScope)
        : undefined,
    cancelAnimationFrame:
      typeof globalScope.cancelAnimationFrame === "function"
        ? (handle) => globalScope.cancelAnimationFrame?.(handle as number)
        : undefined,
  };
}

function assertFiniteTimestamp(timestamp: number): number {
  if (!Number.isFinite(timestamp)) {
    throw new TypeError("Frame timestamp must be finite");
  }

  return timestamp;
}

/**
 * Schedules local frame analysis at most four times per second.
 *
 * The scheduler deliberately stores one pending frame, never a queue. A browser
 * callback can continue arriving while onFrame is awaiting a worker or canvas
 * operation; each new frame replaces the previous pending frame. The caller can
 * therefore safely pass resource-backed frames and use disposeFrame to release a
 * frame that was superseded before analysis began.
 */
export class FrameScheduler<Frame> {
  private readonly processFrame: FrameProcessor<Frame>;
  private readonly readFrame?: FrameReader<Frame>;
  private readonly platform: FrameSchedulerPlatform;
  private readonly video?: VideoFrameCallbackSource;
  private readonly intervalMs: number;
  private readonly mode: "auto" | "manual";
  private readonly onError?: (error: unknown, tick?: FrameSchedulerTick<Frame>) => void;
  private readonly disposeFrame?: (frame: Frame) => void;

  private running = false;
  private processing = false;
  private pending?: PendingFrame<Frame>;
  private activeFrame?: Frame;
  private source: FrameSchedulerSource = "manual";
  private sourceScheduled = false;
  private rvfcHandle?: TimerHandle;
  private rafHandle?: AnimationFrameHandle;
  private timerHandle?: TimerHandle;
  private wakeHandle?: TimerHandle;
  private wakeAt?: number;
  private nextProcessAt = 0;

  public constructor(options: FrameSchedulerOptions<Frame>);
  public constructor(
    processFrame: FrameProcessor<Frame>,
    options?: Omit<FrameSchedulerOptions<Frame>, "onFrame" | "processFrame">,
  );
  public constructor(
    optionsOrProcessor: FrameSchedulerOptions<Frame> | FrameProcessor<Frame>,
    shorthandOptions: Omit<FrameSchedulerOptions<Frame>, "onFrame" | "processFrame"> = {},
  ) {
    const options: FrameSchedulerOptions<Frame> =
      typeof optionsOrProcessor === "function"
        ? { ...shorthandOptions, onFrame: optionsOrProcessor }
        : optionsOrProcessor;
    const processFrame = options.onFrame ?? options.processFrame;

    if (!processFrame) {
      throw new TypeError("FrameScheduler requires an onFrame processor");
    }

    const intervalMs = options.intervalMs ?? DEFAULT_FRAME_INTERVAL_MS;
    if (!Number.isFinite(intervalMs) || intervalMs <= 0) {
      throw new RangeError("FrameScheduler intervalMs must be greater than zero");
    }

    this.processFrame = processFrame;
    this.readFrame = options.readFrame ?? options.frameProvider;
    const defaultApis = defaultPlatform();
    this.platform = options.platform || options.clock
      ? {
          now: options.platform?.now ?? options.clock?.now ?? defaultApis.now,
          setTimeout: options.platform?.setTimeout ?? options.clock?.setTimeout ?? defaultApis.setTimeout,
          clearTimeout: options.platform?.clearTimeout ?? options.clock?.clearTimeout ?? defaultApis.clearTimeout,
          requestVideoFrameCallback: options.platform?.requestVideoFrameCallback,
          cancelVideoFrameCallback: options.platform?.cancelVideoFrameCallback,
          requestAnimationFrame: options.platform?.requestAnimationFrame,
          cancelAnimationFrame: options.platform?.cancelAnimationFrame,
        }
      : defaultApis;
    this.video = options.video ?? options.frameSource;
    this.intervalMs = intervalMs;
    this.mode = options.mode ?? "auto";
    this.onError = options.onError;
    this.disposeFrame = options.disposeFrame;
  }

  public get isRunning(): boolean {
    return this.running;
  }

  public get isProcessing(): boolean {
    return this.processing;
  }

  /** Number of frames waiting behind the current analysis (always 0 or 1). */
  public get pendingFrameCount(): 0 | 1 {
    return this.pending ? 1 : 0;
  }

  public get interval(): number {
    return this.intervalMs;
  }

  public get selectedSource(): FrameSchedulerSource {
    return this.source;
  }

  public start(): void {
    if (this.running) {
      this.scheduleNextFrame();
      return;
    }

    this.running = true;
    this.source = this.mode === "manual" ? "manual" : this.selectSource();
    this.nextProcessAt = this.platform.now();
    this.clearWakeTimer();

    if (this.source !== "manual") {
      this.scheduleNextFrame();
    }
  }

  public stop(): void {
    this.running = false;
    this.cancelScheduledSource();
    this.clearWakeTimer();
    this.dropPendingFrame();
  }

  /**
   * Registers the next rVFC/rAF/timer callback. It is public so an integration
   * can explicitly re-arm the callback after replacing its video source.
   */
  public scheduleNextFrame(): void {
    if (!this.running || this.source === "manual" || this.sourceScheduled) {
      return;
    }

    if (this.source === "rvfc") {
      this.scheduleVideoFrameCallback();
      return;
    }

    if (this.source === "raf") {
      this.scheduleAnimationFrameCallback();
      return;
    }

    this.scheduleTimerCallback();
  }

  /**
   * Supplies a frame without depending on a browser callback. In manual mode it
   * also starts the scheduler lazily, which keeps pure-core tests compact.
   */
  public tick(frame: Frame, timestamp = this.platform.now(), metadata?: unknown): void {
    if (!this.running) {
      if (this.mode !== "manual") {
        return;
      }

      this.running = true;
      this.source = "manual";
      this.nextProcessAt = timestamp;
    }

    this.acceptTick({
      source: "manual",
      timestamp: assertFiniteTimestamp(timestamp),
      metadata,
      frame,
    });
  }

  public manualTick(frame: Frame, timestamp = this.platform.now(), metadata?: unknown): void {
    this.tick(frame, timestamp, metadata);
  }

  private selectSource(): FrameSchedulerSource {
    if (this.getVideoFrameCallback()) {
      return "rvfc";
    }

    if (this.platform.requestAnimationFrame) {
      return "raf";
    }

    return "timer";
  }

  private getVideoFrameCallback():
    | ((callback: VideoFrameCallback) => TimerHandle)
    | undefined {
    return this.video?.requestVideoFrameCallback ?? this.platform.requestVideoFrameCallback;
  }

  private getCancelVideoFrameCallback():
    | ((handle: TimerHandle) => void)
    | undefined {
    return this.video?.cancelVideoFrameCallback ?? this.platform.cancelVideoFrameCallback;
  }

  private scheduleVideoFrameCallback(): void {
    const request = this.getVideoFrameCallback();
    if (!request) {
      this.source = this.platform.requestAnimationFrame ? "raf" : "timer";
      this.scheduleNextFrame();
      return;
    }

    this.sourceScheduled = true;
    try {
      const handle = this.video
        ? request.call(this.video, (timestamp, metadata) => {
            this.sourceScheduled = false;
            this.rvfcHandle = undefined;
            try {
              this.acceptBrowserTick("rvfc", timestamp, metadata);
            } finally {
              this.scheduleNextFrame();
            }
          })
        : request((timestamp, metadata) => {
            this.sourceScheduled = false;
            this.rvfcHandle = undefined;
            try {
              this.acceptBrowserTick("rvfc", timestamp, metadata);
            } finally {
              this.scheduleNextFrame();
            }
          });
      this.rvfcHandle = handle;
      if (!this.running) {
        this.getCancelVideoFrameCallback()?.(handle);
      }
    } catch (error) {
      this.sourceScheduled = false;
      this.rvfcHandle = undefined;
      this.reportError(error);
      this.source = this.platform.requestAnimationFrame ? "raf" : "timer";
      this.scheduleNextFrame();
    }
  }

  private scheduleAnimationFrameCallback(): void {
    const request = this.platform.requestAnimationFrame;
    if (!request) {
      this.source = "timer";
      this.scheduleNextFrame();
      return;
    }

    this.sourceScheduled = true;
    try {
      const handle = request((timestamp) => {
        this.sourceScheduled = false;
        this.rafHandle = undefined;
        try {
          this.acceptBrowserTick("raf", timestamp);
        } finally {
          this.scheduleNextFrame();
        }
      });
      this.rafHandle = handle;
      if (!this.running) {
        this.platform.cancelAnimationFrame?.(handle);
      }
    } catch (error) {
      this.sourceScheduled = false;
      this.rafHandle = undefined;
      this.reportError(error);
      this.source = "timer";
      this.scheduleNextFrame();
    }
  }

  private scheduleTimerCallback(): void {
    this.sourceScheduled = true;
    try {
      const handle = this.platform.setTimeout(() => {
        this.sourceScheduled = false;
        this.timerHandle = undefined;
        if (!this.running) {
          return;
        }

        try {
          this.acceptBrowserTick("timer", this.platform.now());
        } finally {
          this.scheduleNextFrame();
        }
      }, this.intervalMs);
      this.timerHandle = handle;
      if (!this.running) {
        this.platform.clearTimeout(handle);
      }
    } catch (error) {
      this.sourceScheduled = false;
      this.timerHandle = undefined;
      this.reportError(error);
      this.running = false;
    }
  }

  private acceptBrowserTick(
    source: Exclude<FrameSchedulerSource, "manual">,
    timestamp: number,
    metadata?: unknown,
  ): void {
    if (!this.running) {
      return;
    }

    const baseTick: Omit<FrameSchedulerTick<Frame>, "frame"> = {
      source,
      timestamp: assertFiniteTimestamp(timestamp),
      metadata,
    };

    let frame: Frame | undefined;
    try {
      frame = this.readFrame?.(baseTick);
    } catch (error) {
      this.reportError(error, baseTick);
      return;
    }

    if (frame === undefined) {
      return;
    }

    this.acceptTick({ ...baseTick, frame });
  }

  private acceptTick(tick: FrameSchedulerTick<Frame>): void {
    if (!this.running) {
      return;
    }

    this.replacePendingFrame({ frame: tick.frame as Frame, tick });
    const currentTime = Math.max(this.platform.now(), tick.timestamp);
    this.drainIfDue(currentTime);

    if (this.pending && !this.processing) {
      this.scheduleWakeTimer();
    }
  }

  private replacePendingFrame(next: PendingFrame<Frame>): void {
    const previous = this.pending;
    if (previous && previous.frame !== next.frame) {
      this.disposeDroppedFrame(previous.frame);
    }

    this.pending = next;
  }

  private dropPendingFrame(): void {
    const pending = this.pending;
    this.pending = undefined;
    if (pending) {
      this.disposeDroppedFrame(pending.frame);
    }
  }

  private disposeDroppedFrame(frame: Frame): void {
    if (!this.disposeFrame || frame === this.activeFrame) {
      return;
    }

    try {
      this.disposeFrame(frame);
    } catch (error) {
      this.reportError(error);
    }
  }

  private drainIfDue(now: number): void {
    if (!this.running || this.processing || !this.pending) {
      return;
    }

    if (now < this.nextProcessAt) {
      this.scheduleWakeTimer();
      return;
    }

    const pending = this.pending;
    this.pending = undefined;
    this.clearWakeTimer();
    this.processing = true;
    this.activeFrame = pending.frame;
    this.nextProcessAt = now + this.intervalMs;

    Promise.resolve()
      .then(() => this.processFrame(pending.frame, pending.tick))
      .catch((error: unknown) => {
        this.reportError(error, pending.tick);
      })
      .finally(() => {
        this.processing = false;
        this.activeFrame = undefined;

        if (!this.running || !this.pending) {
          return;
        }

        this.drainIfDue(this.platform.now());
        if (this.pending && !this.processing) {
          this.scheduleWakeTimer();
        }
      });
  }

  private scheduleWakeTimer(): void {
    if (!this.running || this.processing || !this.pending || this.wakeHandle !== undefined) {
      return;
    }

    const now = this.platform.now();
    const delay = Math.max(0, this.nextProcessAt - now);
    this.wakeAt = this.nextProcessAt;
    try {
      this.wakeHandle = this.platform.setTimeout(() => {
        this.wakeHandle = undefined;
        this.wakeAt = undefined;
        if (this.running) {
          this.drainIfDue(this.platform.now());
        }
      }, delay);
    } catch (error) {
      this.wakeHandle = undefined;
      this.wakeAt = undefined;
      this.reportError(error);
    }
  }

  private clearWakeTimer(): void {
    if (this.wakeHandle !== undefined) {
      this.platform.clearTimeout(this.wakeHandle);
      this.wakeHandle = undefined;
    }
    this.wakeAt = undefined;
  }

  private cancelScheduledSource(): void {
    if (this.rvfcHandle !== undefined) {
      this.getCancelVideoFrameCallback()?.(this.rvfcHandle);
      this.rvfcHandle = undefined;
    }
    if (this.rafHandle !== undefined) {
      this.platform.cancelAnimationFrame?.(this.rafHandle);
      this.rafHandle = undefined;
    }
    if (this.timerHandle !== undefined) {
      this.platform.clearTimeout(this.timerHandle);
      this.timerHandle = undefined;
    }
    this.sourceScheduled = false;
  }

  private reportError(error: unknown, tick?: FrameSchedulerTick<Frame> | Omit<FrameSchedulerTick<Frame>, "frame">): void {
    try {
      this.onError?.(error, tick as FrameSchedulerTick<Frame> | undefined);
    } catch {
      // Error reporting must not break scheduling or turn a dropped frame into a queue.
    }
  }
}

export function createFrameScheduler<Frame>(
  options: FrameSchedulerOptions<Frame>,
): FrameScheduler<Frame>;
export function createFrameScheduler<Frame>(
  processFrame: FrameProcessor<Frame>,
  options?: Omit<FrameSchedulerOptions<Frame>, "onFrame" | "processFrame">,
): FrameScheduler<Frame>;
export function createFrameScheduler<Frame>(
  optionsOrProcessor: FrameSchedulerOptions<Frame> | FrameProcessor<Frame>,
  shorthandOptions: Omit<FrameSchedulerOptions<Frame>, "onFrame" | "processFrame"> = {},
): FrameScheduler<Frame> {
  return typeof optionsOrProcessor === "function"
    ? new FrameScheduler(optionsOrProcessor, shorthandOptions)
    : new FrameScheduler(optionsOrProcessor);
}
