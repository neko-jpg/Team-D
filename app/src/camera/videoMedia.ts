/**
 * Adapted from document-autocapture at commit
 * e24df25d17ddc4cf7d7944c653bd0fba55025452:
 * packages/runtime-web/src/session/video-media.ts.
 *
 * Modified for this app's camera-only lifecycle. Licensed under the MIT
 * License; see THIRD_PARTY_NOTICES.md.
 */

export const VIDEO_PLAYBACK_RETRY_TIMEOUT_MS = 1_500;

function hasErrorName(error: unknown, name: string): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === name
  );
}

async function waitForVideoLoadedData(
  video: HTMLVideoElement,
  timeoutMs: number,
): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    let settled = false;
    let timeoutHandle: ReturnType<typeof globalThis.setTimeout> | undefined;

    const cleanup = (): void => {
      video.removeEventListener("loadeddata", onLoadedData);
      video.removeEventListener("error", onError);
      if (timeoutHandle !== undefined) {
        globalThis.clearTimeout(timeoutHandle);
      }
    };

    const finish = (callback: () => void): void => {
      if (settled) {
        return;
      }

      settled = true;
      cleanup();
      callback();
    };

    const onLoadedData = (): void => finish(resolve);
    const onError = (): void =>
      finish(() => reject(new Error("Video failed to load camera stream")));

    video.addEventListener("loadeddata", onLoadedData, { once: true });
    video.addEventListener("error", onError, { once: true });
    timeoutHandle = globalThis.setTimeout(
      () => finish(resolve),
      timeoutMs,
    );
  });
}

/**
 * Plays an attached camera stream. Mobile Safari can abort the first play()
 * while the video element is still attaching, so that one case waits for
 * loaded data and retries once.
 */
export async function ensureVideoPlayback(
  video: HTMLVideoElement,
): Promise<void> {
  try {
    await video.play();
    return;
  } catch (error) {
    if (!hasErrorName(error, "AbortError")) {
      throw error;
    }
  }

  await waitForVideoLoadedData(video, VIDEO_PLAYBACK_RETRY_TIMEOUT_MS);
  await video.play();
}

/** Stops every owned track and detaches the stream from its video element. */
export function cleanupVideoStream(
  video: HTMLVideoElement | undefined,
  stream: MediaStream | undefined,
): void {
  if (video !== undefined) {
    video.pause();
    video.srcObject = null;
  }

  if (stream !== undefined) {
    stream.getTracks().forEach((track) => track.stop());
  }
}
