import { act, type ComponentProps } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import type { CameraSession } from "./camera";
import { FixtureShotAssessor } from "./providers/fixtureShotAssessor";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

let roots: Root[] = [];
let objectUrlCounter = 0;
let originalCreateObjectURL: typeof URL.createObjectURL | undefined;
let originalRevokeObjectURL: typeof URL.revokeObjectURL | undefined;

beforeEach(() => {
  objectUrlCounter = 0;
  originalCreateObjectURL = URL.createObjectURL;
  originalRevokeObjectURL = URL.revokeObjectURL;
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn(() => `blob:test-${++objectUrlCounter}`),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: vi.fn(),
  });
});

afterEach(() => {
  act(() => {
    roots.forEach((root) => root.unmount());
  });
  roots = [];
  vi.restoreAllMocks();

  if (originalCreateObjectURL === undefined) {
    Reflect.deleteProperty(URL, "createObjectURL");
  } else {
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: originalCreateObjectURL,
    });
  }

  if (originalRevokeObjectURL === undefined) {
    Reflect.deleteProperty(URL, "revokeObjectURL");
  } else {
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: originalRevokeObjectURL,
    });
  }
});

function renderApp(props: ComponentProps<typeof App> = {}): HTMLElement {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  roots.push(root);
  act(() => {
    root.render(<App {...props} />);
  });
  return container;
}

function createCameraSession(): CameraSession {
  let running = false;

  return {
    currentStream: undefined,
    currentVideoTrack: undefined,
    get isRunning() {
      return running;
    },
    start: vi.fn(async () => {
      running = true;
    }),
    stop: vi.fn(() => {
      running = false;
    }),
  };
}

async function clickButton(
  container: HTMLElement,
  label: string,
): Promise<void> {
  const button = Array.from(container.querySelectorAll("button")).find(
    (candidate) => candidate.textContent?.includes(label),
  );
  if (button === undefined) {
    throw new Error(`button ${label} is not rendered`);
  }

  await act(async () => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await Promise.resolve();
    await Promise.resolve();
  });
}

function selectValue(
  container: HTMLElement,
  testId: string,
  value: string,
): void {
  const select = container.querySelector<HTMLSelectElement>(
    `[data-testid="${testId}"]`,
  );
  if (select === null) {
    throw new Error(`select ${testId} is not rendered`);
  }

  act(() => {
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

async function upload(container: HTMLElement, name: string): Promise<void> {
  const input = container.querySelector<HTMLInputElement>(
    '[data-testid="upload-input"]',
  );
  if (input === null) {
    throw new Error("upload input is not rendered");
  }

  const file = new File([name], name, { type: "image/png" });
  Object.defineProperty(input, "files", {
    configurable: true,
    value: [file],
  });

  await act(async () => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("FixtureShotAssessor", () => {
  it("keeps ok, retry, wrong-shot, and error as explicit provider outcomes", async () => {
    const blob = new Blob(["fixture"], { type: "image/png" });
    const input = { blob, requestedShot: "front" as const };

    await expect(
      new FixtureShotAssessor({ fixtureShot: "front", outcome: "ok" }).assess(input),
    ).resolves.toMatchObject({ shotType: "front", quality: "ok" });
    await expect(
      new FixtureShotAssessor({ fixtureShot: "front", outcome: "retry" }).assess(input),
    ).resolves.toMatchObject({ shotType: "front", quality: "retry" });
    await expect(
      new FixtureShotAssessor({ fixtureShot: "front", outcome: "wrong-shot" }).assess(input),
    ).resolves.toMatchObject({ quality: "retry", issues: ["WRONG_SHOT"] });
    await expect(
      new FixtureShotAssessor({ fixtureShot: "front", outcome: "error" }).assess(input),
    ).rejects.toMatchObject({
      provider: "shot-assessor",
      code: "UNAVAILABLE",
      retryable: true,
    });
  });
});

describe("capture upload vertical slice", () => {
  it("keeps the edit entry hidden until front, back, and tag are accepted", async () => {
    const container = renderApp();

    expect(container.querySelector('[data-testid="edit-entry"]')).toBeNull();
    expect(container.querySelector('[data-testid="accepted-count"]')?.textContent).toContain("0/3");
    expect(container.querySelector('[data-testid="current-step"]')?.textContent).toBe("front");

    await upload(container, "front.png");
    expect(container.querySelector('[data-testid="current-step"]')?.textContent).toBe("back");
    selectValue(container, "fixture-shot", "back");
    await upload(container, "back.png");
    expect(container.querySelector('[data-testid="current-step"]')?.textContent).toBe("tag");
    selectValue(container, "fixture-shot", "tag");
    await upload(container, "tag.png");

    expect(container.querySelector('[data-testid="edit-entry"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="progress-front"]')?.textContent).toContain("完了");
    expect(container.querySelector('[data-testid="progress-back"]')?.textContent).toContain("完了");
    expect(container.querySelector('[data-testid="progress-tag"]')?.textContent).toContain("完了");
  });

  it("preserves front when the back upload is rejected for retry", async () => {
    const container = renderApp();
    await upload(container, "front.png");
    const frontImage = container.querySelector<HTMLImageElement>(
      'img[alt="正面写真"]',
    );

    selectValue(container, "fixture-shot", "back");
    selectValue(container, "fixture-outcome", "retry");
    await upload(container, "back-blurry.png");

    expect(container.querySelector('[data-testid="current-step"]')?.textContent).toBe("back");
    expect(container.querySelector<HTMLImageElement>('img[alt="正面写真"]')).toBe(frontImage);
    expect(container.querySelector('[data-testid="assessment-feedback"]')?.textContent).toContain("撮り直してください");
    expect(container.querySelector('[data-testid="edit-entry"]')).toBeNull();
  });

  it("keeps the same step for a wrong-shot result", async () => {
    const container = renderApp();
    selectValue(container, "fixture-shot", "back");
    selectValue(container, "fixture-outcome", "wrong-shot");
    await upload(container, "wrong-shot.png");

    expect(container.querySelector('[data-testid="current-step"]')?.textContent).toBe("front");
    expect(container.querySelector('[data-testid="progress-front"]')?.textContent).toContain("次に撮る");
    expect(container.querySelector('[data-testid="assessment-feedback"]')?.textContent).toContain("指定された向きと違います");
    expect(container.querySelector('[data-testid="edit-entry"]')).toBeNull();
  });

  it("keeps progress unchanged when the provider errors", async () => {
    const container = renderApp();
    await upload(container, "front.png");
    selectValue(container, "fixture-shot", "back");
    selectValue(container, "fixture-outcome", "error");
    await upload(container, "back.png");

    expect(container.querySelector('[data-testid="current-step"]')?.textContent).toBe("back");
    expect(container.querySelector('[data-testid="progress-front"]')?.textContent).toContain("完了");
    expect(container.querySelector('[data-testid="progress-back"]')?.textContent).toContain("次に撮る");
    expect(container.querySelector('[data-testid="capture-status"]')?.textContent).toContain("再試行");
    expect(container.querySelector('[data-testid="provider-error"]')?.textContent).toContain("fixture の ShotAssessor");
    expect(container.querySelector('[data-testid="edit-entry"]')).toBeNull();
  });
});

describe("camera capture vertical slice", () => {
  it("sends a raw manual capture to the assessor and advances the fixed guide", async () => {
    const rawCameraBlob = new Blob(["distinctive-raw-camera-frame"], {
      type: "image/jpeg",
    });
    const session = createCameraSession();
    const captureFrame = vi.fn(async () => rawCameraBlob);
    const assessSpy = vi.spyOn(FixtureShotAssessor.prototype, "assess");
    const container = renderApp({
      captureCameraFrame: captureFrame,
      createCameraController: () => session,
    });

    expect(container.querySelector('[data-testid="current-step"]')?.textContent).toBe("front");
    expect(container.querySelector('[data-testid="camera-guide"]')?.className).toContain(
      "camera-guide--front",
    );
    expect(container.textContent).toContain("襟・袖・裾をガイド内に入れてください");
    expect(container.textContent).not.toContain("READY");

    await clickButton(container, "カメラを起動");
    const shutter = container.querySelector<HTMLButtonElement>(
      'button[aria-label="正面を撮影"]',
    );
    expect(shutter?.disabled).toBe(false);

    await act(async () => {
      shutter?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(captureFrame).toHaveBeenCalledTimes(1);
    expect(URL.createObjectURL).toHaveBeenCalledWith(rawCameraBlob);
    expect(assessSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        blob: rawCameraBlob,
        requestedShot: "front",
      }),
    );
    expect(container.querySelector('[data-testid="current-step"]')?.textContent).toBe("back");
    expect(container.querySelector('[data-testid="camera-guide"]')?.className).toContain(
      "camera-guide--back",
    );
    expect(container.textContent).toContain("裏返して背面を上にしてください");
    expect(container.querySelector<HTMLImageElement>('img[alt="正面写真"]')?.src).toContain(
      "blob:test-1",
    );
  });
});
