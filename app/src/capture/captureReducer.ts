import type {
  CaptureSlot,
  CaptureSlotState,
  ProviderError,
  ShotAssessment,
} from "../shared";

/** The required capture order for a garment session. */
export const CAPTURE_SLOTS = ["front", "back", "tag"] as const satisfies
  readonly CaptureSlot[];
export type CaptureStep = CaptureSlot | "edit";
export type CaptureStatus =
  | "capturing"
  | "analyzing"
  | "error"
  | "ready_to_edit";

export type CapturedSlot = CaptureSlotState;

export type CaptureSlots = {
  [slot in CaptureSlot]: CapturedSlot | null;
};

export interface PendingCapture {
  slot: CaptureSlot;
  blob: Blob;
  objectUrl: string;
  /** Identifies the exact provider request so stale async results are ignored. */
  requestId: string;
}

export interface CaptureProviderError {
  slot: CaptureSlot;
  error: ProviderError;
}

export interface CaptureState {
  /** The slot currently shown by the capture UI, or edit once all slots are accepted. */
  currentStep: CaptureStep;
  status: CaptureStatus;
  slots: CaptureSlots;
  /** The most recent image waiting for a provider result. */
  pendingCapture: PendingCapture | null;
  /** The most recent assessment, including a rejected assessment for UI feedback. */
  lastAssessment: ShotAssessment | null;
  /** Provider failures do not alter currentStep or slots and keep pendingCapture retryable. */
  providerError: CaptureProviderError | null;
}

export type CaptureAction =
  | {
      type: "CAPTURE_SUBMITTED";
      slot: CaptureSlot;
      blob: Blob;
      objectUrl: string;
      requestId: string;
    }
  | {
      type: "SHOT_ASSESSED";
      slot: CaptureSlot;
      assessment: ShotAssessment;
      requestId: string;
    }
  | {
      type: "PROVIDER_ERROR";
      slot: CaptureSlot;
      error: ProviderError;
      requestId: string;
    }
  | { type: "RETRY_ANALYSIS"; requestId: string }
  | { type: "RETAKE"; slot: CaptureSlot }
  | { type: "EDIT_REQUESTED" }
  | { type: "RESET" };

export type CaptureReducer = (
  state: CaptureState,
  action: CaptureAction,
) => CaptureState;

function createEmptySlots(): CaptureSlots {
  return {
    front: null,
    back: null,
    tag: null,
  };
}

export function createInitialCaptureState(): CaptureState {
  return {
    currentStep: "front",
    status: "capturing",
    slots: createEmptySlots(),
    pendingCapture: null,
    lastAssessment: null,
    providerError: null,
  };
}

/** A fresh state factory is preferred when a component can be mounted more than once. */
export const initialCaptureState = createInitialCaptureState();

let requestSequence = 0;

/** Creates a local-only request token for correlating async provider results. */
export function createCaptureRequestId(): string {
  requestSequence += 1;
  return `capture-request-${requestSequence}`;
}

export function canEnterEdit(input: CaptureState | CaptureSlots): boolean {
  const slots = "slots" in input ? input.slots : input;
  return CAPTURE_SLOTS.every((slot) => {
    const captured = slots[slot];
    return (
      captured !== null &&
      captured.assessment.quality === "ok" &&
      captured.assessment.shotType === slot
    );
  });
}

export function nextCaptureStep(input: CaptureState | CaptureSlots): CaptureStep {
  const slots = "slots" in input ? input.slots : input;
  return CAPTURE_SLOTS.find((slot) => slots[slot] === null) ?? "edit";
}

/** Backward-compatible names for callers that prefer the longer helpers. */
export const hasAllRequiredSlots = canEnterEdit;
export const getNextCaptureStep = nextCaptureStep;

function pendingFromSubmission(
  action: Extract<CaptureAction, { type: "CAPTURE_SUBMITTED" }>,
): PendingCapture {
  return {
    slot: action.slot,
    blob: action.blob,
    objectUrl: action.objectUrl,
    requestId: action.requestId,
  };
}

function capturedSlotFromAssessment(
  pendingCapture: PendingCapture,
  assessment: ShotAssessment,
): CapturedSlot {
  return {
    blob: pendingCapture.blob,
    objectUrl: pendingCapture.objectUrl,
    assessment,
  };
}

/**
 * Pure capture-session state machine for React's useReducer.
 *
 * The reducer intentionally ignores ShotAssessment.nextAction when deciding the
 * next step. Accepted slots and the current step are the source of truth, so an
 * AI response cannot skip a required photo or enter edit early.
 */
export const captureReducer: CaptureReducer = (state, action) => {
  switch (action.type) {
    case "CAPTURE_SUBMITTED": {
      // A stale UI event or a submission after the session is ready for editing
      // must not start an analysis for a different slot.
      if (state.status !== "capturing" || state.currentStep !== action.slot) {
        return state;
      }

      return {
        ...state,
        status: "analyzing",
        pendingCapture: pendingFromSubmission(action),
        lastAssessment: null,
        providerError: null,
      };
    }

    case "SHOT_ASSESSED": {
      const pendingCapture = state.pendingCapture;

      // Async responses are associated with the submitted slot. Responses from
      // an old request cannot overwrite a later retake or advance the flow.
      if (
        state.status !== "analyzing" ||
        pendingCapture === null ||
        pendingCapture.slot !== action.slot ||
        pendingCapture.requestId !== action.requestId ||
        state.currentStep !== action.slot
      ) {
        return state;
      }

      const accepted =
        action.assessment.quality === "ok" &&
        action.assessment.shotType === action.slot;

      if (!accepted) {
        return {
          ...state,
          status: "capturing",
          pendingCapture: null,
          lastAssessment: action.assessment,
          providerError: null,
        };
      }

      const slots: CaptureSlots = {
        ...state.slots,
        [action.slot]: capturedSlotFromAssessment(
          pendingCapture,
          action.assessment,
        ),
      };
      const nextStep = nextCaptureStep(slots);

      return {
        ...state,
        currentStep: nextStep,
        status: nextStep === "edit" ? "ready_to_edit" : "capturing",
        slots,
        pendingCapture: null,
        lastAssessment: action.assessment,
        providerError: null,
      };
    }

    case "PROVIDER_ERROR": {
      const pendingCapture = state.pendingCapture;

      if (
        state.status !== "analyzing" ||
        pendingCapture === null ||
        pendingCapture.slot !== action.slot ||
        pendingCapture.requestId !== action.requestId ||
        state.currentStep !== action.slot
      ) {
        return state;
      }

      // Keep the pending image so the UI can retry the same provider request.
      // Neither the current step nor any accepted slot is changed here.
      return {
        ...state,
        status: "error",
        providerError: {
          slot: action.slot,
          error: action.error,
        },
      };
    }

    case "RETRY_ANALYSIS": {
      if (
        state.status !== "error" ||
        state.pendingCapture === null ||
        state.providerError === null
      ) {
        return state;
      }

      return {
        ...state,
        status: "analyzing",
        pendingCapture: {
          ...state.pendingCapture,
          requestId: action.requestId,
        },
        providerError: null,
      };
    }

    case "RETAKE": {
      // A retake may target an accepted slot, or the slot already requested by
      // the normal flow. It cannot jump over an earlier missing slot.
      if (state.slots[action.slot] === null && state.currentStep !== action.slot) {
        return state;
      }

      // Retaking a slot changes only the active step. Existing accepted images,
      // including the slot being replaced until a new image is accepted, stay
      // available to the UI.
      return {
        ...state,
        currentStep: action.slot,
        status: "capturing",
        pendingCapture: null,
        lastAssessment: null,
        providerError: null,
      };
    }

    case "EDIT_REQUESTED": {
      // This guard is intentionally independent of the AI's nextAction value.
      if (
        state.status === "analyzing" ||
        state.pendingCapture !== null ||
        !canEnterEdit(state.slots)
      ) {
        return state;
      }

      return {
        ...state,
        currentStep: "edit",
        status: "ready_to_edit",
        providerError: null,
      };
    }

    case "RESET":
      return createInitialCaptureState();

    default:
      return state;
  }
};

/** Small action creators keep UI dispatches type-safe without coupling the reducer to React. */
export const captureActions = {
  submitCapture: (
    slot: CaptureSlot,
    blob: Blob,
    objectUrl: string,
    requestId = createCaptureRequestId(),
  ): CaptureAction => ({
    type: "CAPTURE_SUBMITTED",
    slot,
    blob,
    objectUrl,
    requestId,
  }),
  shotAssessed: (
    slot: CaptureSlot,
    assessment: ShotAssessment,
    requestId: string,
  ): CaptureAction => ({
    type: "SHOT_ASSESSED",
    slot,
    assessment,
    requestId,
  }),
  providerError: (
    slot: CaptureSlot,
    error: ProviderError,
    requestId: string,
  ): CaptureAction => ({
    type: "PROVIDER_ERROR",
    slot,
    error,
    requestId,
  }),
  retryAnalysis: (requestId: string): CaptureAction => ({
    type: "RETRY_ANALYSIS",
    requestId,
  }),
  retake: (slot: CaptureSlot): CaptureAction => ({ type: "RETAKE", slot }),
  requestEdit: (): CaptureAction => ({ type: "EDIT_REQUESTED" }),
  reset: (): CaptureAction => ({ type: "RESET" }),
};
