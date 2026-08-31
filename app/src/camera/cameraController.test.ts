import { describe, expect, it, vi } from "vitest";

import {
  CameraController,
  DEFAULT_VIDEO_CONSTRAINTS,
  type CameraRuntime,
} from "./cameraController";
import { cleanupVideoStream, ensureVideoPlayback } from "./videoMedia";

interface StreamHarness {
  readonly stream: MediaStream;
  readonly tracks: Array<{
    readonly stop: ReturnType<typeof vi.fn>;
  }>;
}

function createStream(trackCount = 2): StreamHarness {
  const tracks = Array.from({ length: trackCount }, () => ({
    stop: vi.fn(),
  }));
  const stream = {
    getTracks: () => tracks as unknown as MediaStreamTrack[],
    getVideoTracks: () => tracks.slice(0, 1) as unknown as MediaStreamTrack[],
  } as unknown as MediaStream;

  return { stream, tracks };
}

function createVideo(
  playImplementation: () => Promise<void> = async () => undefined,
): {
  readonly pause: ReturnType<typeof vi.fn>;
  readonly play: ReturnType<typeof vi.fn>;
  readonly video: HTMLVideoElement;
} {
  const video = document.createElement("video");
  const play = vi.fn(playImplementation);
  const pause = vi.fn();

  Object.defineProperties(video, {
    pause: { configurable: true, value: pause },
    play: { configurable: true, value: play },
    srcObject: { configurable: true, value: null, writable: true },
  });

  return { pause, play, video };
}

function createRuntime(
  getUserMedia: CameraRuntime["getUserMedia"],
  overrides: Partial<Pick<CameraRuntime, "hostname" | "isSecureContext">> = {},
): CameraRuntime {
  return {
    getUserMedia,
    hostname: overrides.hostname ?? "camera.example",
    isSecureContext: overrides.isSecureContext ?? true,
  };
}

describe("CameraController", () => {
  it("starts one rear-camera stream with the fixed ideal resolution", async () => {
    const { stream, tracks } = createStream();
    const { play, video } = createVideo();
    const getUserMedia = vi.fn(async () => stream);
    const controller = new CameraController(video, {
      runtime: createRuntime(getUserMedia),
    });

    await controller.start();

    expect(DEFAULT_VIDEO_CONSTRAINTS).toEqual({
      facingMode: "environment",
      width: { ideal: 1_920 },
      height: { ideal: 1_080 },
    });
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(getUserMedia).toHaveBeenCalledWith({
      video: DEFAULT_VIDEO_CONSTRAINTS,
      audio: false,
    });
    expect(video).toMatchObject({
      autoplay: true,
      muted: true,
      playsInline: true,
      srcObject: stream,
    });
    expect(play).toHaveBeenCalledTimes(1);
    expect(controller.isRunning).toBe(true);
    expect(controller.currentStream).toBe(stream);
    expect(controller.currentVideoTrack).toBe(tracks[0]);
  });

  it("shares an in-flight start instead of acquiring a second stream", async () => {
    const { stream } = createStream();
    const { video } = createVideo();
    let resolveStream!: (value: MediaStream) => void;
    const pendingStream = new Promise<MediaStream>((resolve) => {
      resolveStream = resolve;
    });
    const getUserMedia = vi.fn(() => pendingStream);
    const controller = new CameraController(video, {
      runtime: createRuntime(getUserMedia),
    });

    const firstStart = controller.start();
    const secondStart = controller.start();

    expect(secondStart).toBe(firstStart);
    expect(getUserMedia).toHaveBeenCalledTimes(1);

    resolveStream(stream);
    await firstStart;
    expect(controller.isRunning).toBe(true);
  });

  it("preserves nested defaults when video constraints are overridden", async () => {
    const { stream } = createStream();
    const { video } = createVideo();
    const getUserMedia = vi.fn(async () => stream);
    const controller = new CameraController(video, {
      runtime: createRuntime(getUserMedia),
      videoConstraints: {
        width: { max: 1_280 },
        frameRate: { ideal: 30 },
      },
    });

    await controller.start();

    expect(getUserMedia).toHaveBeenCalledWith({
      video: {
        facingMode: "environment",
        width: { ideal: 1_920, max: 1_280 },
        height: { ideal: 1_080 },
        frameRate: { ideal: 30 },
      },
      audio: false,
    });
  });

  it("normalizes permission denial without attaching a stream", async () => {
    const { video } = createVideo();
    const getUserMedia = vi.fn(async () => {
      throw new DOMException("denied", "NotAllowedError");
    });
    const controller = new CameraController(video, {
      runtime: createRuntime(getUserMedia),
    });

    await expect(controller.start()).rejects.toMatchObject({
      code: "permission-denied",
      name: "CameraStartError",
    });
    expect(video.srcObject).toBeNull();
    expect(controller.currentStream).toBeUndefined();
    expect(controller.isRunning).toBe(false);
  });

  it("rejects insecure non-local origins before asking for permission", async () => {
    const { video } = createVideo();
    const getUserMedia = vi.fn();
    const controller = new CameraController(video, {
      runtime: createRuntime(getUserMedia, {
        hostname: "192.0.2.10",
        isSecureContext: false,
      }),
    });

    await expect(controller.start()).rejects.toMatchObject({
      code: "insecure-context",
    });
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("releases a late stream when stop wins the start race", async () => {
    const { stream, tracks } = createStream();
    const { video } = createVideo();
    let resolveStream!: (value: MediaStream) => void;
    const pendingStream = new Promise<MediaStream>((resolve) => {
      resolveStream = resolve;
    });
    const controller = new CameraController(video, {
      runtime: createRuntime(() => pendingStream),
    });

    const startTask = controller.start();
    controller.stop();
    resolveStream(stream);

    await expect(startTask).resolves.toBeUndefined();
    tracks.forEach((track) => expect(track.stop).toHaveBeenCalled());
    expect(controller.currentStream).toBeUndefined();
    expect(controller.isRunning).toBe(false);
  });

  it("queues a fresh acquisition when start follows a pending stop", async () => {
    const first = createStream();
    const second = createStream();
    const { video } = createVideo();
    let resolveFirstStream!: (value: MediaStream) => void;
    const firstRequest = new Promise<MediaStream>((resolve) => {
      resolveFirstStream = resolve;
    });
    const getUserMedia = vi
      .fn<() => Promise<MediaStream>>()
      .mockReturnValueOnce(firstRequest)
      .mockResolvedValueOnce(second.stream);
    const controller = new CameraController(video, {
      runtime: createRuntime(getUserMedia),
    });

    const staleStart = controller.start();
    controller.stop();
    const restarted = controller.start();

    expect(restarted).not.toBe(staleStart);
    expect(getUserMedia).toHaveBeenCalledTimes(1);

    resolveFirstStream(first.stream);
    await Promise.all([staleStart, restarted]);

    expect(getUserMedia).toHaveBeenCalledTimes(2);
    first.tracks.forEach((track) =>
      expect(track.stop).toHaveBeenCalledTimes(1),
    );
    expect(controller.currentStream).toBe(second.stream);
    expect(video.srcObject).toBe(second.stream);
    expect(controller.isRunning).toBe(true);
  });

  it("stops every track and detaches the video after a successful start", async () => {
    const first = createStream(3);
    const second = createStream();
    const { pause, video } = createVideo();
    const getUserMedia = vi
      .fn<() => Promise<MediaStream>>()
      .mockResolvedValueOnce(first.stream)
      .mockResolvedValueOnce(second.stream);
    const controller = new CameraController(video, {
      runtime: createRuntime(getUserMedia),
    });

    await controller.start();
    controller.stop();

    expect(pause).toHaveBeenCalledTimes(1);
    expect(video.srcObject).toBeNull();
    first.tracks.forEach((track) =>
      expect(track.stop).toHaveBeenCalledTimes(1),
    );
    expect(controller.currentStream).toBeUndefined();
    expect(controller.currentVideoTrack).toBeUndefined();
    expect(controller.isRunning).toBe(false);

    await controller.start();

    expect(getUserMedia).toHaveBeenCalledTimes(2);
    expect(controller.currentStream).toBe(second.stream);
    expect(video.srcObject).toBe(second.stream);
    expect(controller.isRunning).toBe(true);
  });

  it("releases every track when playback fails", async () => {
    const { stream, tracks } = createStream(3);
    const { video } = createVideo(async () => {
      throw new DOMException("blocked", "NotAllowedError");
    });
    const controller = new CameraController(video, {
      runtime: createRuntime(async () => stream),
    });

    await expect(controller.start()).rejects.toMatchObject({
      code: "playback-failed",
    });
    tracks.forEach((track) => expect(track.stop).toHaveBeenCalledTimes(1));
    expect(video.srcObject).toBeNull();
  });
});

describe("camera video media helpers", () => {
  it("retries play after loadeddata when the first attempt is aborted", async () => {
    const { video } = createVideo();
    const play = vi
      .fn<() => Promise<void>>()
      .mockRejectedValueOnce(new DOMException("attaching", "AbortError"))
      .mockResolvedValueOnce(undefined);
    Object.defineProperty(video, "play", {
      configurable: true,
      value: play,
    });

    const playback = ensureVideoPlayback(video);
    await vi.waitFor(() => expect(play).toHaveBeenCalledTimes(1));
    video.dispatchEvent(new Event("loadeddata"));
    await playback;

    expect(play).toHaveBeenCalledTimes(2);
  });

  it("pauses, detaches, and stops all tracks during cleanup", () => {
    const { stream, tracks } = createStream(3);
    const { pause, video } = createVideo();
    video.srcObject = stream;

    cleanupVideoStream(video, stream);

    expect(pause).toHaveBeenCalledTimes(1);
    expect(video.srcObject).toBeNull();
    tracks.forEach((track) => expect(track.stop).toHaveBeenCalledTimes(1));
  });
});
