export interface LocalAnalysisSupportRuntime {
  readonly hasWorker: boolean;
  readonly createCanvas: () => {
    getContext(contextId: "2d"): unknown | null;
  };
}

export type LocalAnalysisSupportCheck = () => boolean;

function browserRuntime(): LocalAnalysisSupportRuntime {
  const worker = (globalThis as typeof globalThis & { Worker?: unknown }).Worker;
  return {
    hasWorker: typeof worker === "function",
    createCanvas: () => document.createElement("canvas"),
  };
}

/** Checks only analyzer prerequisites; camera playback and raw capture are separate. */
export function supportsLocalAnalysis(
  runtime: LocalAnalysisSupportRuntime = browserRuntime(),
): boolean {
  if (!runtime.hasWorker) {
    return false;
  }

  try {
    return runtime.createCanvas().getContext("2d") !== null;
  } catch {
    return false;
  }
}
