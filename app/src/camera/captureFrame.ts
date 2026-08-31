export type CaptureFrameReader = (video: HTMLVideoElement) => Promise<Blob>;

export interface CaptureVideoFrameOptions {
  readonly imageType?: string;
  readonly quality?: number;
}

const DEFAULT_IMAGE_TYPE = "image/jpeg";

function assertPositiveDimension(value: number, name: string): void {
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`Cannot capture a video frame: ${name} must be greater than zero.`);
  }
}

function assertOptions(options: CaptureVideoFrameOptions): void {
  if (options.imageType !== undefined && options.imageType.trim() === "") {
    throw new Error("Cannot capture a video frame: imageType must not be empty.");
  }

  if (
    options.quality !== undefined
    && (!Number.isFinite(options.quality) || options.quality < 0 || options.quality > 1)
  ) {
    throw new Error("Cannot capture a video frame: quality must be between 0 and 1.");
  }
}

/**
 * Copies the current intrinsic video frame into an unattached canvas.
 *
 * Only the video element is drawn, so guide and feedback DOM layered over the
 * preview never becomes part of the captured image.
 */
export async function captureVideoFrame(
  video: HTMLVideoElement,
  options: CaptureVideoFrameOptions = {},
): Promise<Blob> {
  assertOptions(options);

  const width = video.videoWidth;
  const height = video.videoHeight;
  assertPositiveDimension(width, "videoWidth");
  assertPositiveDimension(height, "videoHeight");

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;

  const context = canvas.getContext("2d");
  if (context === null) {
    throw new Error("Cannot capture a video frame: 2D canvas context is unavailable.");
  }

  context.drawImage(video, 0, 0, width, height);

  const imageType = options.imageType ?? DEFAULT_IMAGE_TYPE;
  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob === null) {
          reject(new Error("Cannot capture a video frame: canvas encoding failed."));
          return;
        }

        resolve(blob);
      },
      imageType,
      options.quality,
    );
  });
}
