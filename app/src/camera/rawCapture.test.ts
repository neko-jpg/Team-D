import { describe, expect, it, vi } from "vitest";

import {
  captureRawVideoFrame,
  RAW_CAPTURE_MIME_TYPE,
  RAW_CAPTURE_QUALITY,
} from "./rawCapture";

function readyVideo(width = 1080, height = 1920): HTMLVideoElement {
  const video = document.createElement("video");
  Object.defineProperties(video, {
    videoWidth: { configurable: true, value: width },
    videoHeight: { configurable: true, value: height },
  });
  return video;
}

describe("captureRawVideoFrame", () => {
  it("draws only intrinsic video pixels and returns the canvas Blob", async () => {
    const video = readyVideo();
    const overlay = document.createElement("div");
    const drawImage = vi.fn();
    const expected = new Blob(["video-pixels"], { type: "image/jpeg" });
    const canvas = {
      width: 0,
      height: 0,
      getContext: vi.fn(() => ({ drawImage })),
      toBlob: vi.fn((callback: BlobCallback) => callback(expected)),
    } as unknown as HTMLCanvasElement;

    const result = await captureRawVideoFrame(video, {
      createCanvas: () => canvas,
      createImageCapture: () => undefined,
    });

    expect(result).toBe(expected);
    expect(canvas.width).toBe(1080);
    expect(canvas.height).toBe(1920);
    expect(drawImage).toHaveBeenCalledTimes(1);
    expect(drawImage).toHaveBeenCalledWith(video, 0, 0, 1080, 1920);
    expect(drawImage).not.toHaveBeenCalledWith(overlay, expect.anything());
    expect(canvas.toBlob).toHaveBeenCalledWith(
      expect.any(Function),
      RAW_CAPTURE_MIME_TYPE,
      RAW_CAPTURE_QUALITY,
    );
  });

  it("rejects before drawing when intrinsic video dimensions are unavailable", async () => {
    const createCanvas = vi.fn();

    await expect(
      captureRawVideoFrame(readyVideo(0, 0), {
        createCanvas,
        createImageCapture: () => undefined,
      }),
    ).rejects.toThrow("準備が完了していません");
    expect(createCanvas).not.toHaveBeenCalled();
  });

  it("rejects when the capture canvas cannot create a Blob", async () => {
    const canvas = {
      width: 0,
      height: 0,
      getContext: vi.fn(() => ({ drawImage: vi.fn() })),
      toBlob: vi.fn((callback: BlobCallback) => callback(null)),
    } as unknown as HTMLCanvasElement;

    await expect(
      captureRawVideoFrame(readyVideo(), {
        createCanvas: () => canvas,
        createImageCapture: () => undefined,
      }),
    ).rejects.toThrow("写真を作成できませんでした");
  });

  it("rejects when a 2D capture context is unavailable", async () => {
    const canvas = {
      width: 0,
      height: 0,
      getContext: vi.fn(() => null),
      toBlob: vi.fn(),
    } as unknown as HTMLCanvasElement;

    await expect(
      captureRawVideoFrame(readyVideo(), {
        createCanvas: () => canvas,
        createImageCapture: () => undefined,
      }),
    ).rejects.toThrow("写真を作成できませんでした");
    expect(canvas.toBlob).not.toHaveBeenCalled();
  });

  it("falls back to track-based ImageCapture when Canvas capture is unavailable", async () => {
    const video = readyVideo();
    const track = {} as MediaStreamTrack;
    Object.defineProperty(video, "srcObject", {
      configurable: true,
      value: { getVideoTracks: () => [track] },
    });
    const canvas = {
      width: 0,
      height: 0,
      getContext: vi.fn(() => null),
      toBlob: vi.fn(),
    } as unknown as HTMLCanvasElement;
    const expected = new Blob(["sensor-photo"], { type: "image/jpeg" });
    const takePhoto = vi.fn(async () => expected);
    const createImageCapture = vi.fn(() => ({ takePhoto }));

    const result = await captureRawVideoFrame(video, {
      createCanvas: () => canvas,
      createImageCapture,
    });

    expect(result).toBe(expected);
    expect(createImageCapture).toHaveBeenCalledWith(track);
    expect(takePhoto).toHaveBeenCalledTimes(1);
  });
});
