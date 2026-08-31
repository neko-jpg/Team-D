import { describe, expect, it } from "vitest";
import type { ProviderError, ShotAssessment } from "../shared";
import {
  CAPTURE_SLOTS,
  captureActions,
  captureReducer,
  canEnterEdit,
  createInitialCaptureState,
  nextCaptureStep,
} from "./captureReducer";

const makeBlob = (name: string): Blob =>
  new Blob([name], { type: "image/jpeg" });

const makeAssessment = (
  shotType: ShotAssessment["shotType"],
  quality: ShotAssessment["quality"] = "ok",
  nextAction: ShotAssessment["nextAction"] =
    quality === "ok" ? "REQUEST_NEXT" : "RETAKE",
): ShotAssessment => ({
  shotType,
  quality,
  issues: quality === "ok" ? [] : ["BLURRY"],
  missingShots: [...CAPTURE_SLOTS],
  nextAction,
});

const makeProviderError = (): ProviderError =>
  ({
    provider: "shot-assessor",
    code: "TIMEOUT",
    message: "shot assessment timed out",
    retryable: true,
  }) as unknown as ProviderError;

function submitAndAssess(
  state: ReturnType<typeof createInitialCaptureState>,
  slot: "front" | "back" | "tag",
  blob = makeBlob(slot),
  assessment = makeAssessment(slot),
) {
  const analyzing = captureReducer(
    state,
    captureActions.submitCapture(slot, blob, `${slot}-url`),
  );

  const requestId = analyzing.pendingCapture?.requestId;
  if (requestId === undefined) {
    throw new Error("capture submission did not create a request id");
  }

  return captureReducer(
    analyzing,
    captureActions.shotAssessed(slot, assessment, requestId),
  );
}

describe("captureReducer", () => {
  it("exports edit-gate and next-step helpers for UI integration", () => {
    const state = createInitialCaptureState();

    expect(nextCaptureStep(state)).toBe("front");
    expect(canEnterEdit(state)).toBe(false);
  });

  it("does not allow a retake to jump over an earlier missing slot", () => {
    const state = createInitialCaptureState();

    expect(captureReducer(state, captureActions.retake("tag"))).toBe(state);
  });

  it("advances front → back → tag → edit only after all three slots are accepted", () => {
    let state = createInitialCaptureState();

    state = submitAndAssess(
      state,
      "front",
      makeBlob("front"),
      // The provider's nextAction cannot skip the application state machine.
      makeAssessment("front", "ok", "COMPLETE"),
    );
    expect(state.currentStep).toBe("back");
    expect(state.status).toBe("capturing");

    state = submitAndAssess(state, "back");
    expect(state.currentStep).toBe("tag");

    state = submitAndAssess(state, "tag");
    expect(state.currentStep).toBe("edit");
    expect(state.status).toBe("ready_to_edit");
    expect(state.slots.front).not.toBeNull();
    expect(state.slots.back).not.toBeNull();
    expect(state.slots.tag).not.toBeNull();
  });

  it("keeps edit blocked while any required slot is missing", () => {
    let state = createInitialCaptureState();
    state = submitAndAssess(state, "front");
    state = submitAndAssess(state, "back");

    const beforeRequest = state;
    const afterRequest = captureReducer(
      state,
      captureActions.requestEdit(),
    );

    expect(afterRequest).toBe(beforeRequest);
    expect(afterRequest.currentStep).toBe("tag");
    expect(afterRequest.slots.tag).toBeNull();
  });

  it("preserves the other slots during a retake and replaces only the retaken slot", () => {
    let state = createInitialCaptureState();
    state = submitAndAssess(state, "front", makeBlob("front-original"));
    state = submitAndAssess(state, "back", makeBlob("back-original"));
    state = submitAndAssess(state, "tag", makeBlob("tag-original"));

    const frontBeforeRetake = state.slots.front;
    const backBeforeRetake = state.slots.back;
    const tagBeforeRetake = state.slots.tag;

    state = captureReducer(state, captureActions.retake("back"));
    expect(state.currentStep).toBe("back");
    expect(state.slots.front).toBe(frontBeforeRetake);
    expect(state.slots.back).toBe(backBeforeRetake);
    expect(state.slots.tag).toBe(tagBeforeRetake);

    // A rejected retake does not erase the previously accepted back image.
    state = submitAndAssess(
      state,
      "back",
      makeBlob("back-rejected"),
      makeAssessment("back", "retry"),
    );
    expect(state.currentStep).toBe("back");
    expect(state.slots.front).toBe(frontBeforeRetake);
    expect(state.slots.back).toBe(backBeforeRetake);
    expect(state.slots.tag).toBe(tagBeforeRetake);

    state = submitAndAssess(state, "back", makeBlob("back-replacement"));
    expect(state.currentStep).toBe("edit");
    expect(state.slots.front).toBe(frontBeforeRetake);
    expect(state.slots.tag).toBe(tagBeforeRetake);
    expect(state.slots.back).not.toBe(backBeforeRetake);
    expect(state.slots.back?.blob).toBeInstanceOf(Blob);
  });

  it("reports provider errors without changing progress and keeps the image retryable", () => {
    let state = createInitialCaptureState();
    state = submitAndAssess(state, "front");
    state = captureReducer(
      state,
      captureActions.submitCapture("back", makeBlob("back"), "back-url"),
    );

    const stepBeforeError = state.currentStep;
    const slotsBeforeError = state.slots;
    const pendingBeforeError = state.pendingCapture;
    const providerError = makeProviderError();

    state = captureReducer(
      state,
      captureActions.providerError(
        "back",
        providerError,
        pendingBeforeError?.requestId ?? "missing-request-id",
      ),
    );

    expect(state.currentStep).toBe(stepBeforeError);
    expect(state.slots).toBe(slotsBeforeError);
    expect(state.pendingCapture).toBe(pendingBeforeError);
    expect(state.status).toBe("error");
    expect(state.providerError?.error).toBe(providerError);

    state = captureReducer(state, captureActions.retryAnalysis("retry-request"));
    expect(state.currentStep).toBe("back");
    expect(state.slots).toBe(slotsBeforeError);
    expect(state.pendingCapture?.blob).toBe(pendingBeforeError?.blob);
    expect(state.pendingCapture?.objectUrl).toBe(pendingBeforeError?.objectUrl);
    expect(state.pendingCapture?.requestId).toBe("retry-request");
    expect(state.status).toBe("analyzing");
    expect(state.providerError).toBeNull();
  });

  it("does not mutate the previous state when a capture starts", () => {
    const state = createInitialCaptureState();
    const nextState = captureReducer(
      state,
      captureActions.submitCapture(
        "front",
        makeBlob("front"),
        "front-url",
      ),
    );

    expect(state.currentStep).toBe("front");
    expect(state.status).toBe("capturing");
    expect(state.pendingCapture).toBeNull();
    expect(nextState).not.toBe(state);
    expect(nextState.status).toBe("analyzing");
  });

  it("ignores a stale assessment after a new capture request for the same slot", () => {
    let state = createInitialCaptureState();
    const first = captureReducer(
      state,
      captureActions.submitCapture("front", makeBlob("front-old"), "old-url", "request-old"),
    );

    state = captureReducer(
      first,
      captureActions.retake("front"),
    );
    const second = captureReducer(
      state,
      captureActions.submitCapture("front", makeBlob("front-new"), "new-url", "request-new"),
    );

    const afterStaleResult = captureReducer(
      second,
      captureActions.shotAssessed(
        "front",
        makeAssessment("front"),
        "request-old",
      ),
    );
    expect(afterStaleResult).toBe(second);
    expect(afterStaleResult.status).toBe("analyzing");
    expect(afterStaleResult.pendingCapture?.requestId).toBe("request-new");
  });
});
