import { describe, expect, it, vi } from "vitest";

import { supportsLocalAnalysis } from "./localAnalysisSupport";

describe("supportsLocalAnalysis", () => {
  it("requires both Worker and a Canvas 2D context", () => {
    const noWorkerCanvas = vi.fn(() => ({ getContext: vi.fn(() => ({})) }));
    expect(
      supportsLocalAnalysis({ hasWorker: false, createCanvas: noWorkerCanvas }),
    ).toBe(false);
    expect(noWorkerCanvas).not.toHaveBeenCalled();

    expect(
      supportsLocalAnalysis({
        hasWorker: true,
        createCanvas: () => ({ getContext: () => null }),
      }),
    ).toBe(false);

    expect(
      supportsLocalAnalysis({
        hasWorker: true,
        createCanvas: () => ({ getContext: () => ({}) }),
      }),
    ).toBe(true);
  });

  it("treats analyzer initialization errors as unsupported", () => {
    expect(
      supportsLocalAnalysis({
        hasWorker: true,
        createCanvas: () => {
          throw new Error("canvas init failed");
        },
      }),
    ).toBe(false);
  });
});
