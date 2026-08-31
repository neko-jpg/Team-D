/**
 * Raw video-to-Canvas capture pattern adapted from document-autocapture at
 * commit e24df25d17ddc4cf7d7944c653bd0fba55025452.
 *
 * The original project is MIT licensed, Copyright (c) 2026 Maaz Khan.
 * See THIRD_PARTY_NOTICES.md. Document detection, perspective correction,
 * auto-capture, and overlays are intentionally excluded.
 */
export const RAW_CAPTURE_MIME_TYPE = "image/jpeg";
export const RAW_CAPTURE_QUALITY = 0.92;

export type CaptureCanvasFactory = () => HTMLCanvasElement;
export interface StillImageCapture {
  takePhoto(): Promise<Blob>;
}
export type ImageCaptureFactory = (
  track: MediaStreamTrack,
) => StillImageCapture | undefined;
export type RawFrameCapture = (video: HTMLVideoElement) => Promise<Blob>;

export interface RawCaptureOptions {
  readonly createCanvas?: CaptureCanvasFactory;
  readonly createImageCapture?: ImageCaptureFactory;
  readonly mimeType?: string;
  readonly quality?: number;
}

function defaultCanvasFactory(): HTMLCanvasElement {
  return document.createElement("canvas");
}

function defaultImageCaptureFactory(
  track: MediaStreamTrack,
): StillImageCapture | undefined {
  type ImageCaptureConstructor = new (
    sourceTrack: MediaStreamTrack,
  ) => StillImageCapture;
  const constructor = (
    globalThis as typeof globalThis & {
      ImageCapture?: ImageCaptureConstructor;
    }
  ).ImageCapture;

  return constructor === undefined ? undefined : new constructor(track);
}

function currentVideoTrack(video: HTMLVideoElement): MediaStreamTrack | undefined {
  const source = video.srcObject as
    | { getVideoTracks?: () => MediaStreamTrack[] }
    | null;
  return source?.getVideoTracks?.()[0];
}

function captureWithCanvas(
  video: HTMLVideoElement,
  options: RawCaptureOptions,
): Promise<Blob> {
  const width = video.videoWidth;
  const height = video.videoHeight;
  if (
    !Number.isInteger(width) ||
    width <= 0 ||
    !Number.isInteger(height) ||
    height <= 0
  ) {
    return Promise.reject(
      new Error(
        "カメラ映像の準備が完了していません。少し待ってから撮影してください。",
      ),
    );
  }

  const canvas = (options.createCanvas ?? defaultCanvasFactory)();
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d", { alpha: false });
  if (context === null) {
    return Promise.reject(
      new Error("写真を作成できませんでした。もう一度撮影してください。"),
    );
  }

  context.drawImage(video, 0, 0, width, height);

  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob === null) {
          reject(
            new Error("写真を作成できませんでした。もう一度撮影してください。"),
          );
          return;
        }
        resolve(blob);
      },
      options.mimeType ?? RAW_CAPTURE_MIME_TYPE,
      options.quality ?? RAW_CAPTURE_QUALITY,
    );
  });
}

/**
 * Captures only the intrinsic video pixels into an off-DOM canvas. Overlay DOM
 * and controls are deliberately not accepted as inputs, so they cannot be
 * burned into the returned image.
 */
export async function captureRawVideoFrame(
  video: HTMLVideoElement,
  options: RawCaptureOptions = {},
): Promise<Blob> {
  let canvasError: unknown;
  try {
    return await captureWithCanvas(video, options);
  } catch (error) {
    canvasError = error;
  }

  const track = currentVideoTrack(video);
  if (track !== undefined) {
    try {
      const imageCapture = (
        options.createImageCapture ?? defaultImageCaptureFactory
      )(track);
      if (imageCapture !== undefined) {
        return await imageCapture.takePhoto();
      }
    } catch {
      // Preserve the first actionable Canvas error when both raw paths fail.
    }
  }

  throw canvasError;
}
