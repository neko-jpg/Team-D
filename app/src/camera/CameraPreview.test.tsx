import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CameraPreview } from "./CameraPreview";
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

function renderPreview(session: CameraSession): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);

  act(() => {
    root.render(<CameraPreview createController={() => session} />);
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
    const session = createSession(async () => {
      throw new CameraStartError(
        "permission-denied",
        "Camera permission was denied.",
      );
    });
    const container = renderPreview(session);

    await clickStart(container);

    expect(container.querySelector('[data-testid="camera-status"]')?.textContent)
      .toBe("権限が必要");
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      "画像を選ぶ",
    );
    expect(container.textContent).toContain("もう一度試す");
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
