import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CameraPreview,
  type CameraPreviewProps,
} from "./CameraPreview";
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
  props: Omit<CameraPreviewProps, "createController" | "shot"> & {
    readonly shot?: CameraPreviewProps["shot"];
  } = {},
): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);

  act(() => {
    root.render(
      <CameraPreview
        {...props}
        createController={() => session}
        shot={props.shot ?? "front"}
      />,
    );
  });

  return container;
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

function shutter(container: HTMLElement): HTMLButtonElement {
  const button = container.querySelector<HTMLButtonElement>(
    'button[aria-label$="を撮影"]',
  );
  if (button === null) {
    throw new Error("camera shutter is not rendered");
  }

  return button;
}

describe("CameraPreview", () => {
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

  it("stops the controller when the preview unmounts", async () => {
    const session = createSession();
    const container = renderPreview(session);

    await clickStart(container);
    act(() => {
      roots.pop()?.unmount();
    });

    expect(session.stop).toHaveBeenCalledTimes(1);
  });

  it("switches between the garment and tag guides for the requested shot", () => {
    const session = createSession();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    roots.push(root);

    act(() => {
      root.render(
        <CameraPreview createController={() => session} shot="front" />,
      );
    });

    const frontGuide = container.querySelector('[data-testid="camera-guide"]');
    expect(frontGuide?.classList.contains("camera-guide--front")).toBe(true);
    expect(frontGuide?.getAttribute("aria-hidden")).toBe("true");
    expect(frontGuide?.querySelector(".camera-garment-guide")).not.toBeNull();
    expect(container.textContent).toContain(
      "襟・袖・裾をガイド内に入れてください",
    );

    act(() => {
      root.render(
        <CameraPreview createController={() => session} shot="back" />,
      );
    });

    expect(
      container
        .querySelector('[data-testid="camera-guide"]')
        ?.classList.contains("camera-guide--back"),
    ).toBe(true);
    expect(container.textContent).toContain("裏返して背面を上にしてください");

    act(() => {
      root.render(
        <CameraPreview createController={() => session} shot="tag" />,
      );
    });

    const tagGuide = container.querySelector('[data-testid="camera-guide"]');
    expect(tagGuide?.classList.contains("camera-guide--tag")).toBe(true);
    expect(tagGuide?.querySelector(".camera-tag-guide")).not.toBeNull();
    expect(container.textContent).toContain(
      "タグに近づき、枠に合わせてください",
    );
  });

  it("captures manually outside READY and passes the raw Blob unchanged", async () => {
    const session = createSession();
    const rawBlob = new Blob(["raw frame"], { type: "image/jpeg" });
    const captureFrame = vi.fn(async () => rawBlob);
    const onCapture = vi.fn();
    const container = renderPreview(session, { captureFrame, onCapture });

    await clickStart(container);

    const video = container.querySelector("video");
    const captureButton = shutter(container);
    expect(container.textContent).not.toContain("READY");
    expect(captureButton.disabled).toBe(false);

    await act(async () => {
      captureButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(captureFrame).toHaveBeenCalledTimes(1);
    expect(captureFrame).toHaveBeenCalledWith(video);
    expect(onCapture).toHaveBeenCalledTimes(1);
    expect(onCapture).toHaveBeenCalledWith(rawBlob);
    expect(shutter(container).disabled).toBe(false);
  });

  it("keeps the fixed guide, shutter, and upload fallback when local analysis is unavailable", async () => {
    const session = createSession();
    const rawBlob = new Blob(["fallback raw frame"], { type: "image/jpeg" });
    const captureFrame = vi.fn(async () => rawBlob);
    const onCapture = vi.fn();
    const requestUpload = vi.fn();
    const container = renderPreview(session, {
      captureFrame,
      checkLocalAnalysisSupport: () => false,
      onCapture,
      onRequestUpload: requestUpload,
      shot: "back",
    });

    await clickStart(container);

    expect(
      container.querySelector('[data-testid="camera-guidance"]')?.getAttribute(
        "data-quality-hint",
      ),
    ).toBe("ANALYZER_UNAVAILABLE");
    expect(container.querySelector('[data-testid="camera-guide"]')).not.toBeNull();
    expect(session.stop).not.toHaveBeenCalled();
    expect(shutter(container).disabled).toBe(false);

    act(() => {
      container
        .querySelector<HTMLButtonElement>('[data-testid="camera-upload-fallback"]')
        ?.click();
    });
    expect(requestUpload).toHaveBeenCalledTimes(1);

    await act(async () => {
      shutter(container).click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(onCapture).toHaveBeenCalledWith(rawBlob);
    expect(session.stop).not.toHaveBeenCalled();
  });

  it("keeps manual capture available while a non-READY local hint is visible", async () => {
    const rawBlob = new Blob(["dark raw frame"], { type: "image/jpeg" });
    const captureFrame = vi.fn(async () => rawBlob);
    const onCapture = vi.fn();
    const container = renderPreview(createSession(), {
      captureFrame,
      checkLocalAnalysisSupport: () => true,
      localHint: "TOO_DARK",
      onCapture,
    });

    await clickStart(container);

    expect(
      container.querySelector('[data-testid="camera-guidance"]')?.getAttribute(
        "data-quality-hint",
      ),
    ).toBe("TOO_DARK");
    expect(shutter(container).disabled).toBe(false);

    await act(async () => {
      shutter(container).click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(onCapture).toHaveBeenCalledWith(rawBlob);
  });

  it("does not deliver a late raw capture after pagehide", async () => {
    let finishCapture: ((blob: Blob) => void) | undefined;
    const captureFrame = vi.fn(
      () =>
        new Promise<Blob>((resolve) => {
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
      shutter(container).click();
      window.dispatchEvent(new Event("pagehide"));
    });
    await act(async () => {
      finishCapture?.(new Blob(["late"]));
      await Promise.resolve();
    });

    expect(onCapture).not.toHaveBeenCalled();
  });

  it("blocks repeated shutter presses while one capture is in flight", async () => {
    const session = createSession();
    const rawBlob = new Blob(["raw frame"], { type: "image/jpeg" });
    let resolveCapture: ((blob: Blob) => void) | undefined;
    const captureFrame = vi.fn(
      () =>
        new Promise<Blob>((resolve) => {
          resolveCapture = resolve;
        }),
    );
    const onCapture = vi.fn();
    const container = renderPreview(session, { captureFrame, onCapture });

    await clickStart(container);

    const captureButton = shutter(container);
    act(() => {
      captureButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      captureButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(captureFrame).toHaveBeenCalledTimes(1);
    expect(shutter(container).disabled).toBe(true);

    await act(async () => {
      resolveCapture?.(rawBlob);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(onCapture).toHaveBeenCalledTimes(1);
    expect(shutter(container).disabled).toBe(false);
  });

  it("keeps the shutter blocked until post-capture validation finishes", async () => {
    const session = createSession();
    const rawBlob = new Blob(["raw frame"], { type: "image/jpeg" });
    const captureFrame = vi.fn(async () => rawBlob);
    let finishValidation: (() => void) | undefined;
    const onCapture = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finishValidation = resolve;
        }),
    );
    const container = renderPreview(session, { captureFrame, onCapture });

    await clickStart(container);

    await act(async () => {
      shutter(container).dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await Promise.resolve();
    });

    expect(shutter(container).disabled).toBe(true);
    act(() => {
      shutter(container).dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    expect(captureFrame).toHaveBeenCalledTimes(1);

    await act(async () => {
      finishValidation?.();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(onCapture).toHaveBeenCalledWith(rawBlob);
    expect(shutter(container).disabled).toBe(false);
  });

  it("keeps the camera usable after a frame capture error", async () => {
    const session = createSession();
    const captureFrame = vi.fn(async () => {
      throw new Error("canvas unavailable");
    });
    const container = renderPreview(session, { captureFrame });

    await clickStart(container);

    await act(async () => {
      shutter(container).dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      "もう一度撮影してください",
    );
    expect(container.querySelector('[data-testid="camera-status"]')?.textContent)
      .toBe("映像表示中");
    expect(shutter(container).disabled).toBe(false);
  });
});
