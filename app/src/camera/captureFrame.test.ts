import { afterEach, describe, expect, it, vi } from "vitest";

import { captureVideoFrame } from "./captureFrame";

interface CanvasHarness {
  readonly canvas: HTMLCanvasElement;
  readonly drawImage: ReturnType<typeof vi.fn>;
  readonly toBlob: ReturnType<typeof vi.fn>;
}

function createVideo(width = 1_920, height = 1_080): HTMLVideoElement {
  const video = document.createElement("video");
  Object.defineProperties(video, {
    videoHeight: { configurable: true, value: height },
    videoWidth: { configurable: true, value: width },
  });
  return video;
}

function installCanvas(blob: Blob | null): CanvasHarness {
  const drawImage = vi.fn();
  const toBlob = vi.fn(
    (callback: BlobCallback) => callback(blob),
  );
  const canvas = {
    height: 0,
    width: 0,
    getContext: vi.fn(() => ({ drawImage })),
    toBlob,
  } as unknown as HTMLCanvasElement;

  vi.spyOn(document, "createElement").mockReturnValue(canvas);

  return { canvas, drawImage, toBlob };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("captureVideoFrame", () => {
  it("draws only the video at its intrinsic dimensions and returns the encoded blob", async () => {
    const video = createVideo();
    const overlay = document.createElement("div");
    const blob = new Blob(["frame"], { type: "image/jpeg" });
    const { canvas, drawImage, toBlob } = installCanvas(blob);

    await expect(captureVideoFrame(video)).resolves.toBe(blob);

    expect(canvas.width).toBe(1_920);
    expect(canvas.height).toBe(1_080);
    expect(drawImage).toHaveBeenCalledOnce();
    expect(drawImage).toHaveBeenCalledWith(video, 0, 0, 1_920, 1_080);
    expect(drawImage).not.toHaveBeenCalledWith(overlay, expect.anything());
    expect(toBlob).toHaveBeenCalledWith(
      expect.any(Function),
      "image/jpeg",
      undefined,
    );
  });

  it("passes a requested image type and quality to canvas encoding", async () => {
    const video = createVideo(640, 480);
    const blob = new Blob(["frame"], { type: "image/webp" });
    const { toBlob } = installCanvas(blob);

    await expect(captureVideoFrame(video, {
      imageType: "image/webp",
      quality: 0.8,
    })).resolves.toBe(blob);

    expect(toBlob).toHaveBeenCalledWith(
      expect.any(Function),
      "image/webp",
      0.8,
    );
  });

  it.each([
    [0, 1_080, "videoWidth"],
    [1_920, 0, "videoHeight"],
  ])("rejects unusable intrinsic dimensions", async (width, height, field) => {
    await expect(captureVideoFrame(createVideo(width, height))).rejects.toThrow(field);
  });

  it("rejects when a 2D canvas context cannot be created", async () => {
    const video = createVideo();
    const canvas = {
      height: 0,
      width: 0,
      getContext: vi.fn(() => null),
    } as unknown as HTMLCanvasElement;
    vi.spyOn(document, "createElement").mockReturnValue(canvas);

    await expect(captureVideoFrame(video)).rejects.toThrow(/2D canvas context/);
  });

  it("rejects when canvas encoding does not produce a blob", async () => {
    const video = createVideo();
    installCanvas(null);

    await expect(captureVideoFrame(video)).rejects.toThrow(/encoding failed/);
  });
});
