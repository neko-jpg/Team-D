import { cleanupVideoStream, ensureVideoPlayback } from "./videoMedia";

/**
 * Camera lifecycle adapted from document-autocapture at commit
 * e24df25d17ddc4cf7d7944c653bd0fba55025452:
 * packages/runtime-web/src/session/defaults.ts and
 * packages/runtime-web/src/session.ts, plus the nested constraint merge from
 * packages/runtime-web/src/session/constraint-utils.ts.
 *
 * Document detection, workers, Canvas ingestion, and auto-capture were
 * intentionally omitted. Licensed under the MIT License; see
 * THIRD_PARTY_NOTICES.md.
 */

export const DEFAULT_VIDEO_CONSTRAINTS: MediaTrackConstraints = {
  facingMode: "environment",
  width: { ideal: 1_920 },
  height: { ideal: 1_080 },
};

export type CameraStartErrorCode =
  | "aborted"
  | "camera-not-found"
  | "camera-unavailable"
  | "insecure-context"
  | "permission-denied"
  | "playback-failed"
  | "unsupported"
  | "unknown";

export class CameraStartError extends Error {
  public readonly code: CameraStartErrorCode;

  public constructor(
    code: CameraStartErrorCode,
    message: string,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "CameraStartError";
    this.code = code;
  }
}

export interface CameraRuntime {
  readonly getUserMedia?: (
    constraints: MediaStreamConstraints,
  ) => Promise<MediaStream>;
  readonly hostname: string;
  readonly isSecureContext: boolean;
}

export interface CameraControllerOptions {
  readonly runtime?: CameraRuntime;
  readonly videoConstraints?: MediaTrackConstraints;
}

const LOCAL_CAMERA_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

function isConstraintRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function mergeConstraintValue<T>(
  base: T | undefined,
  override: T | undefined,
): T | undefined {
  if (override === undefined) {
    return base;
  }
  if (isConstraintRecord(base) && isConstraintRecord(override)) {
    return { ...base, ...override } as T;
  }
  return override;
}

function mergeVideoConstraints(
  base: MediaTrackConstraints,
  override: MediaTrackConstraints | undefined,
): MediaTrackConstraints {
  const safeOverride = override ?? {};
  return {
    ...base,
    ...safeOverride,
    width: mergeConstraintValue(base.width, safeOverride.width),
    height: mergeConstraintValue(base.height, safeOverride.height),
    frameRate: mergeConstraintValue(base.frameRate, safeOverride.frameRate),
    aspectRatio: mergeConstraintValue(
      base.aspectRatio,
      safeOverride.aspectRatio,
    ),
  };
}

function createBrowserRuntime(): CameraRuntime {
  const mediaDevices =
    typeof navigator === "undefined" ? undefined : navigator.mediaDevices;

  return {
    getUserMedia:
      typeof mediaDevices?.getUserMedia === "function"
        ? mediaDevices.getUserMedia.bind(mediaDevices)
        : undefined,
    hostname:
      typeof window === "undefined" ? "" : window.location.hostname,
    isSecureContext:
      typeof window !== "undefined" && window.isSecureContext === true,
  };
}

function errorName(error: unknown): string | undefined {
  if (typeof error !== "object" || error === null || !("name" in error)) {
    return undefined;
  }

  return typeof error.name === "string" ? error.name : undefined;
}

function mediaRequestError(error: unknown): CameraStartError {
  switch (errorName(error)) {
    case "NotAllowedError":
    case "PermissionDeniedError":
    case "SecurityError":
      return new CameraStartError(
        "permission-denied",
        "Camera permission was denied.",
        { cause: error },
      );
    case "NotFoundError":
    case "DevicesNotFoundError":
      return new CameraStartError(
        "camera-not-found",
        "No camera was found on this device.",
        { cause: error },
      );
    case "NotReadableError":
    case "TrackStartError":
      return new CameraStartError(
        "camera-unavailable",
        "The camera is already in use or could not be started.",
        { cause: error },
      );
    default:
      return new CameraStartError(
        "unknown",
        error instanceof Error ? error.message : "Camera start failed.",
        { cause: error },
      );
  }
}

function playbackError(error: unknown): CameraStartError {
  return new CameraStartError(
    "playback-failed",
    error instanceof Error
      ? error.message
      : "The camera stream could not be played.",
    { cause: error },
  );
}

export interface CameraSession {
  readonly currentStream: MediaStream | undefined;
  readonly currentVideoTrack: MediaStreamTrack | undefined;
  readonly isRunning: boolean;
  start(): Promise<void>;
  stop(): void;
}

export class CameraController implements CameraSession {
  private readonly runtime: CameraRuntime;
  private readonly videoConstraints: MediaTrackConstraints;
  private readonly video: HTMLVideoElement;

  private lifecycleToken = 0;
  private running = false;
  private startTask: Promise<void> | undefined;
  private startTaskToken: number | undefined;
  private stream: MediaStream | undefined;

  public constructor(
    video: HTMLVideoElement,
    options: CameraControllerOptions = {},
  ) {
    this.video = video;
    this.runtime = options.runtime ?? createBrowserRuntime();
    this.videoConstraints = mergeVideoConstraints(
      DEFAULT_VIDEO_CONSTRAINTS,
      options.videoConstraints,
    );
  }

  public get currentStream(): MediaStream | undefined {
    return this.stream;
  }

  public get currentVideoTrack(): MediaStreamTrack | undefined {
    return this.stream?.getVideoTracks()[0];
  }

  public get isRunning(): boolean {
    return this.running;
  }

  public start(): Promise<void> {
    if (this.running) {
      return Promise.resolve();
    }

    if (this.startTask !== undefined) {
      if (this.startTaskToken === this.lifecycleToken) {
        return this.startTask;
      }

      // A stop/pagehide invalidated the in-flight getUserMedia request. Wait
      // for its late stream to be released, then perform the requested restart
      // without overlapping two camera acquisitions.
      const staleTask = this.startTask;
      const restartToken = ++this.lifecycleToken;
      return this.trackStartTask(
        restartToken,
        staleTask
          .catch(() => undefined)
          .then(() => this.startInternal(restartToken)),
      );
    }

    const startToken = ++this.lifecycleToken;
    return this.trackStartTask(startToken, this.startInternal(startToken));
  }

  private trackStartTask(
    startToken: number,
    pendingTask: Promise<void>,
  ): Promise<void> {
    const task = pendingTask.finally(() => {
      if (this.startTaskToken === startToken) {
        this.startTask = undefined;
        this.startTaskToken = undefined;
      }
    });
    this.startTask = task;
    this.startTaskToken = startToken;
    return task;
  }

  public stop(): void {
    this.lifecycleToken += 1;
    this.running = false;

    const stream = this.stream;
    this.stream = undefined;
    cleanupVideoStream(this.video, stream);
  }

  private assertStartIsCurrent(startToken: number): void {
    if (startToken !== this.lifecycleToken) {
      throw new CameraStartError("aborted", "Camera start was cancelled.");
    }
  }

  private assertRuntimeAvailable(): (
    constraints: MediaStreamConstraints,
  ) => Promise<MediaStream> {
    if (
      !this.runtime.isSecureContext &&
      !LOCAL_CAMERA_HOSTS.has(this.runtime.hostname)
    ) {
      throw new CameraStartError(
        "insecure-context",
        "Camera access requires HTTPS or localhost.",
      );
    }

    if (this.runtime.getUserMedia === undefined) {
      throw new CameraStartError(
        "unsupported",
        "Camera access is unavailable in this browser.",
      );
    }

    return this.runtime.getUserMedia;
  }

  private async startInternal(startToken: number): Promise<void> {
    let acquiredStream: MediaStream | undefined;

    try {
      this.assertStartIsCurrent(startToken);
      const getUserMedia = this.assertRuntimeAvailable();

      this.video.muted = true;
      this.video.playsInline = true;
      this.video.autoplay = true;

      try {
        acquiredStream = await getUserMedia({
          video: { ...this.videoConstraints },
          audio: false,
        });
      } catch (error) {
        throw mediaRequestError(error);
      }

      this.assertStartIsCurrent(startToken);
      this.stream = acquiredStream;
      this.video.srcObject = acquiredStream;

      try {
        await ensureVideoPlayback(this.video);
      } catch (error) {
        throw playbackError(error);
      }

      this.assertStartIsCurrent(startToken);
      this.running = true;
    } catch (error) {
      if (acquiredStream !== undefined) {
        cleanupVideoStream(this.video, acquiredStream);
      }
      if (this.stream === acquiredStream) {
        this.stream = undefined;
      }
      this.running = false;

      if (error instanceof CameraStartError) {
        // stop() intentionally wins lifecycle races. Matching the source
        // implementation, cancellation cleans up and completes quietly.
        if (error.code === "aborted") {
          return;
        }
        throw error;
      }

      throw new CameraStartError(
        "unknown",
        error instanceof Error ? error.message : "Camera start failed.",
        { cause: error },
      );
    }
  }
}
