import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CameraPreview,
  type CameraPreviewProps,
} from "./CameraPreview";
import { CAPTURE_GUIDES } from "./CaptureGuide";
import {
  CameraStartError,
  type CameraSession,
} from "./cameraController";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const roots: Root[] = [];

afterEach(() => {
  act(() => {
    roots.splice(0).forEach((root) => root.unmount());
  });
});

function createSession(
  start: () => Promise<void> = async () => undefined,
) {
  let running = false;
  const session = {
    currentStream: undefined,
    currentVideoTrack: undefined,
    get isRunning() {
      return running;
    },
    start: vi.fn(async () => {
      await start();
      running = true;
    }),
    stop: vi.fn(() => {
      running = false;
    }),
  } satisfies CameraSession;

  return session;
}

function renderPreview(
  session: CameraSession,
  props: Omit<CameraPreviewProps, "createController"> = {},
): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);

  act(() => {
    root.render(
      <CameraPreview {...props} createController={() => session} />,
    );
  });

  return container;
}

function manualShutter(container: HTMLElement): HTMLButtonElement {
  const button = container.querySelector<HTMLButtonElement>(
    '[data-testid="manual-shutter"]',
  );
  if (button === null) {
    throw new Error("manual shutter is not rendered");
  }
  return button;
}

async function clickStart(container: HTMLElement): Promise<void> {
  const button = Array.from(container.querySelectorAll("button")).find(
    (candidate) => candidate.textContent?.includes("カメラを起動"),
  );
  if (button === undefined) {
    throw new Error("camera start button is not rendered");
  }

  await act(async () => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await Promise.resolve();
  });
}

describe("CameraPreview", () => {
  it("shares one garment ROI for front/back and a separate tag rectangle", () => {
    expect(CAPTURE_GUIDES.front).toEqual(CAPTURE_GUIDES.back);
    expect(CAPTURE_GUIDES.tag).not.toEqual(CAPTURE_GUIDES.front);
    expect(CAPTURE_GUIDES.tag.width).toBeGreaterThan(0);
    expect(CAPTURE_GUIDES.tag.height).toBeGreaterThan(0);
  });

  it("starts on user action and releases the session on pagehide", async () => {
    const session = createSession();
    const container = renderPreview(session);
    const video = container.querySelector("video");

    expect(video).toMatchObject({
      autoplay: true,
      muted: true,
      playsInline: true,
    });
    expect(container.querySelector('[data-testid="camera-status"]')?.textContent)
      .toBe("停止中");

    await clickStart(container);

    expect(session.start).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-testid="camera-status"]')?.textContent)
      .toBe("映像表示中");

    act(() => {
      window.dispatchEvent(new Event("pagehide"));
    });

    expect(session.stop).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-testid="camera-status"]')?.textContent)
      .toBe("停止中");
  });

  it("shows a recoverable upload path when permission is denied", async () => {
    const requestUpload = vi.fn();
    const session = createSession(async () => {
      throw new CameraStartError(
        "permission-denied",
        "Camera permission was denied.",
      );
    });
    const container = renderPreview(session, {
      onRequestUpload: requestUpload,
    });

    await clickStart(container);

    expect(container.querySelector('[data-testid="camera-status"]')?.textContent)
      .toBe("権限が必要");
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      "画像を選ぶ",
    );
    expect(container.textContent).toContain("もう一度試す");

    const fallbackButton = container.querySelector<HTMLButtonElement>(
      '[data-testid="camera-upload-fallback"]',
    );
    expect(fallbackButton).not.toBeNull();
    act(() => {
      fallbackButton?.click();
    });
    expect(requestUpload).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["front", "garment"],
    ["back", "garment"],
    ["tag", "tag"],
  ] as const)("shows the fixed %s guide over a playing video", async (shot, kind) => {
    const container = renderPreview(createSession(), {
      checkLocalAnalysisSupport: () => true,
      currentShot: shot,
    });

    await clickStart(container);

    const video = container.querySelector("video");
    const guide = container.querySelector<HTMLElement>(
      '[data-testid="capture-guide"]',
    );
    expect(video).not.toBeNull();
    expect(guide?.dataset.guideKind).toBe(kind);
    expect(guide?.dataset.shot).toBe(shot);
    expect(guide?.parentElement).toBe(video?.parentElement);
    expect(guide?.querySelector("svg")?.getAttribute("preserveAspectRatio"))
      .toBe("none");
    const guideRect = CAPTURE_GUIDES[shot];
    expect(
      guide?.querySelector('[data-testid="capture-guide-geometry"]')
        ?.getAttribute("transform"),
    ).toBe(
      `translate(${Number((guideRect.x * 100).toFixed(4))} ${Number((guideRect.y * 100).toFixed(4))}) scale(${guideRect.width} ${guideRect.height})`,
    );
  });

  it("captures while a non-READY hint is visible", async () => {
    const blob = new Blob(["raw-video"], { type: "image/jpeg" });
    const captureFrame = vi.fn(async () => blob);
    const onCapture = vi.fn(() => true);
    const container = renderPreview(createSession(), {
      captureFrame,
      checkLocalAnalysisSupport: () => true,
      currentShot: "front",
      localHint: "TOO_DARK",
      onCapture,
    });

    await clickStart(container);
    const shutter = manualShutter(container);
    expect(shutter.disabled).toBe(false);
    expect(
      container.querySelector('[data-testid="camera-guidance"]')?.getAttribute(
        "data-quality-hint",
      ),
    ).toBe("TOO_DARK");

    await act(async () => {
      shutter.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    const video = container.querySelector("video");
    expect(captureFrame).toHaveBeenCalledWith(video);
    expect(onCapture).toHaveBeenCalledWith({ blob, shot: "front" });
  });

  it("keeps the guide and manual shutter when local analysis is unavailable", async () => {
    const blob = new Blob(["fallback-raw"], { type: "image/jpeg" });
    const onCapture = vi.fn(() => true);
    const session = createSession();
    const container = renderPreview(session, {
      captureFrame: async () => blob,
      checkLocalAnalysisSupport: () => false,
      currentShot: "back",
      onCapture,
    });

    await clickStart(container);

    expect(
      container.querySelector('[data-testid="camera-guidance"]')?.getAttribute(
        "data-quality-hint",
      ),
    ).toBe("ANALYZER_UNAVAILABLE");
    expect(container.querySelector('[data-testid="capture-guide"]')).not.toBeNull();
    expect(session.stop).not.toHaveBeenCalled();
    const shutter = manualShutter(container);
    expect(shutter.disabled).toBe(false);

    await act(async () => {
      shutter.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(onCapture).toHaveBeenCalledWith({ blob, shot: "back" });
    expect(session.stop).not.toHaveBeenCalled();
  });

  it("does not deliver a late raw capture after pagehide", async () => {
    let finishCapture: ((blob: Blob) => void) | undefined;
    const captureFrame = vi.fn(
      () => new Promise<Blob>((resolve) => {
        finishCapture = resolve;
      }),
    );
    const onCapture = vi.fn();
    const container = renderPreview(createSession(), {
      captureFrame,
      checkLocalAnalysisSupport: () => true,
      onCapture,
    });

    await clickStart(container);
    act(() => {
      manualShutter(container).click();
      window.dispatchEvent(new Event("pagehide"));
    });
    await act(async () => {
      finishCapture?.(new Blob(["late"]));
      await Promise.resolve();
    });

    expect(onCapture).not.toHaveBeenCalled();
  });

  it("coalesces rapid shutter presses and recovers from a stale submission", async () => {
    let finishCapture: ((blob: Blob) => void) | undefined;
    const captureFrame = vi.fn(
      () => new Promise<Blob>((resolve) => {
        finishCapture = resolve;
      }),
    );
    const onCapture = vi.fn(() => false);
    const container = renderPreview(createSession(), {
      captureFrame,
      checkLocalAnalysisSupport: () => true,
      onCapture,
    });

    await clickStart(container);
    act(() => {
      const shutter = manualShutter(container);
      shutter.click();
      shutter.click();
    });
    expect(captureFrame).toHaveBeenCalledTimes(1);

    await act(async () => {
      finishCapture?.(new Blob(["stale"]));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(onCapture).toHaveBeenCalledTimes(1);
    expect(manualShutter(container).disabled).toBe(false);
  });

  it("clears captured feedback when post-capture analysis returns to the same shot", async () => {
    const session = createSession();
    const captureFrame = vi.fn(async () => new Blob(["retry"]));
    const onCapture = vi.fn(() => true);
    const props = {
      captureFrame,
      checkLocalAnalysisSupport: () => true,
      createController: () => session,
      currentShot: "front" as const,
      onCapture,
    };
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    roots.push(root);
    act(() => {
      root.render(<CameraPreview {...props} captureBusy={false} />);
    });

    await clickStart(container);
    await act(async () => {
      manualShutter(container).click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="camera-guidance"]')?.textContent)
      .toContain("写真を確認しています");

    act(() => {
      root.render(<CameraPreview {...props} captureBusy />);
    });
    act(() => {
      root.render(<CameraPreview {...props} captureBusy={false} />);
    });

    expect(container.querySelector('[data-testid="camera-guidance"]')?.textContent)
      .toBe("ガイドに合わせて、いつでも撮影できます");
    expect(manualShutter(container).disabled).toBe(false);
  });

  it("normalizes capture adapter errors to app-owned copy", async () => {
    const container = renderPreview(createSession(), {
      captureFrame: async () => {
        throw new Error("internal adapter diagnostics");
      },
      checkLocalAnalysisSupport: () => true,
    });

    await clickStart(container);
    await act(async () => {
      manualShutter(container).click();
      await Promise.resolve();
      await Promise.resolve();
    });

    const error = container.querySelector('[data-testid="capture-error"]');
    expect(error?.textContent).toContain("カメラを確認してもう一度");
    expect(error?.textContent).not.toContain("internal adapter diagnostics");
  });

  it("stops the controller when the preview unmounts", async () => {
    const session = createSession();
    const container = renderPreview(session);

    await clickStart(container);
    act(() => {
      roots.pop()?.unmount();
    });

    expect(session.stop).toHaveBeenCalledTimes(1);
  });
});
