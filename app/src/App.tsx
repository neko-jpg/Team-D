import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type ChangeEvent,
  type ComponentProps,
  type ReactElement,
} from "react";

import type {
  ProviderError,
  ShotAssessment,
} from "./shared";
import { ProviderErrorSchema, ShotAssessmentSchema } from "./shared";
import {
  captureActions,
  captureReducer,
  canEnterEdit,
  createCaptureRequestId,
  initialCaptureState,
} from "./capture/captureReducer";
import type {
  CaptureAction,
  CaptureState,
} from "./capture/captureReducer";
import {
  createFixtureShotAssessor,
  type CaptureShotType,
  type FixtureOutcome,
  type ShotAssessor,
} from "./providers";
import { CameraPreview } from "./camera";

const SHOT_TYPES = ["front", "back", "tag"] as const satisfies
  readonly CaptureShotType[];

const SHOT_LABELS: Record<CaptureShotType, string> = {
  front: "正面",
  back: "背面",
  tag: "タグ",
};

const OUTCOME_LABELS: Record<FixtureOutcome, string> = {
  ok: "成功（quality: ok）",
  retry: "retry（quality: retry）",
  "wrong-shot": "wrong-shot（quality: retry）",
  error: "error（provider error）",
};

const ISSUE_LABELS: Partial<Record<ShotAssessment["issues"][number], string>> = {
  BLURRY: "少しぼけています",
  TOO_DARK: "暗すぎます",
  TOO_BRIGHT: "明るすぎます",
  GARMENT_CROPPED: "衣類全体を写してください",
  TAG_UNREADABLE: "タグが読めません",
  WRONG_SHOT: "指定された向きと違います",
};

function isCaptureShot(value: CaptureState["currentStep"]): value is CaptureShotType {
  return value === "front" || value === "back" || value === "tag";
}

function createCaptureObjectUrl(blob: Blob): string {
  if (typeof URL !== "undefined" && typeof URL.createObjectURL === "function") {
    return URL.createObjectURL(blob);
  }

  // jsdom does not provide createObjectURL. The browser path above is always
  // used in the app; this non-empty fallback keeps reducer tests deterministic.
  const fileName = blob instanceof File ? blob.name : "camera";
  const lastModified = blob instanceof File ? blob.lastModified : 0;
  return `blob:capture-${fileName}-${blob.size}-${lastModified}`;
}

function revokeUploadObjectUrl(url: string): void {
  if (typeof URL !== "undefined" && typeof URL.revokeObjectURL === "function") {
    URL.revokeObjectURL(url);
  }
}

function normalizeProviderError(value: unknown): ProviderError {
  const parsed = ProviderErrorSchema.safeParse(value);
  if (parsed.success) {
    return parsed.data;
  }

  return {
    provider: "shot-assessor",
    code: "UNKNOWN",
    message:
      value instanceof Error
        ? value.message
        : "画像判定に失敗しました。もう一度試してください。",
    retryable: true,
  };
}

function statusLabel(state: CaptureState): string {
  switch (state.status) {
    case "analyzing":
      return "画像を確認しています…";
    case "error":
      return "判定を再試行できます";
    case "ready_to_edit":
      return "3枚の写真が揃いました";
    case "capturing":
      return "撮影または画像を選んでください";
  }
}

function assessmentMessage(
  assessment: ShotAssessment | null,
  currentStep: CaptureState["currentStep"],
): string | null {
  if (assessment === null || assessment.quality !== "retry") {
    return null;
  }

  const issue = assessment.issues[0];
  if (issue !== undefined && ISSUE_LABELS[issue] !== undefined) {
    return ISSUE_LABELS[issue];
  }

  if (isCaptureShot(currentStep) && assessment.shotType !== currentStep) {
    return `${SHOT_LABELS[currentStep]}の写真を選んでください`;
  }

  return "この写真は受け付けられません。撮り直してください。";
}

type CameraPreviewDependencies = Pick<
  ComponentProps<typeof CameraPreview>,
  "captureFrame" | "checkLocalAnalysisSupport" | "createController"
>;

export interface AppProps {
  readonly captureCameraFrame?: CameraPreviewDependencies["captureFrame"];
  readonly checkLocalAnalysisSupport?: CameraPreviewDependencies["checkLocalAnalysisSupport"];
  readonly createCameraController?: CameraPreviewDependencies["createController"];
}

export function App({
  captureCameraFrame,
  checkLocalAnalysisSupport,
  createCameraController,
}: AppProps = {}): ReactElement {
  const [state, reducerDispatch] = useReducer(
    captureReducer,
    initialCaptureState,
  );
  const [fixtureShot, setFixtureShot] = useState<CaptureShotType>("front");
  const [fixtureOutcome, setFixtureOutcome] = useState<FixtureOutcome>("ok");
  const [editStarted, setEditStarted] = useState(false);
  const objectUrls = useRef(new Set<string>());
  const stateRef = useRef(state);
  const submissionInFlightRef = useRef(false);
  const uploadInputRef = useRef<HTMLInputElement>(null);
  stateRef.current = state;

  const dispatch = useCallback(
    (action: CaptureAction) => reducerDispatch(action),
    [],
  );

  const assessor = useMemo<ShotAssessor>(
    () => createFixtureShotAssessor({ fixtureShot, outcome: fixtureOutcome }),
    [fixtureOutcome, fixtureShot],
  );

  useEffect(() => {
    if (isCaptureShot(state.currentStep)) {
      setFixtureShot(state.currentStep);
    }
  }, [state.currentStep]);

  useEffect(() => {
    if (state.status === "capturing") {
      submissionInFlightRef.current = false;
    }
  }, [state.currentStep, state.status]);

  useEffect(() => {
    const urls = objectUrls.current;
    return () => {
      urls.forEach(revokeUploadObjectUrl);
      urls.clear();
    };
  }, []);

  const runAssessment = useCallback(
    async (
      provider: ShotAssessor,
      slot: CaptureShotType,
      blob: Blob,
      requestId: string,
      acceptedShots: readonly CaptureShotType[],
    ): Promise<void> => {
      try {
        const result = await provider.assess({
          blob,
          requestedShot: slot,
          acceptedShots,
        });
        const assessment = ShotAssessmentSchema.parse(result);
        dispatch(captureActions.shotAssessed(slot, assessment, requestId));
      } catch (error: unknown) {
        dispatch(
          captureActions.providerError(
            slot,
            normalizeProviderError(error),
            requestId,
          ),
        );
      }
    },
    [dispatch],
  );

  const submitImage = useCallback(
    async (blob: Blob): Promise<void> => {
      const currentState = stateRef.current;
      if (
        currentState.status !== "capturing" ||
        !isCaptureShot(currentState.currentStep) ||
        submissionInFlightRef.current
      ) {
        return;
      }

      submissionInFlightRef.current = true;
      const slot = currentState.currentStep;
      const objectUrl = createCaptureObjectUrl(blob);
      objectUrls.current.add(objectUrl);
      const requestId = createCaptureRequestId();
      const acceptedShots = SHOT_TYPES.filter(
        (acceptedSlot) => currentState.slots[acceptedSlot] !== null,
      );

      dispatch(captureActions.submitCapture(slot, blob, objectUrl, requestId));
      await runAssessment(assessor, slot, blob, requestId, acceptedShots);
    },
    [assessor, dispatch, runAssessment],
  );

  const handleUpload = (event: ChangeEvent<HTMLInputElement>): void => {
    const file = event.target.files?.[0];
    event.target.value = "";

    if (file === undefined) {
      return;
    }

    void submitImage(file);
  };

  const handleRetryAnalysis = (): void => {
    const pendingCapture = state.pendingCapture;
    if (pendingCapture === null || state.providerError === null) {
      return;
    }

    const requestId = createCaptureRequestId();
    const acceptedShots = SHOT_TYPES.filter(
      (acceptedSlot) => state.slots[acceptedSlot] !== null,
    );
    dispatch(captureActions.retryAnalysis(requestId));
    void runAssessment(
      assessor,
      pendingCapture.slot,
      pendingCapture.blob,
      requestId,
      acceptedShots,
    );
  };

  const editReady =
    canEnterEdit(state) &&
    state.currentStep === "edit" &&
    state.status === "ready_to_edit";
  const currentCaptureSlot = isCaptureShot(state.currentStep)
    ? state.currentStep
    : null;
  const acceptedCount = SHOT_TYPES.filter((slot) => state.slots[slot] !== null)
    .length;
  const feedback = assessmentMessage(state.lastAssessment, state.currentStep);

  return (
    <main className="capture-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">CAPTURE CORE / FIXTURE</p>
          <h1>衣類の写真をそろえる</h1>
        </div>
        <span className="mode-pill">fixture</span>
      </header>

      <section className="progress-card" aria-labelledby="progress-title">
        <div className="section-heading">
          <div>
            <p className="section-kicker">進捗</p>
            <h2 data-testid="accepted-count" id="progress-title">必須写真 {acceptedCount}/3</h2>
          </div>
          <span className="status-text" data-testid="capture-status">
            {statusLabel(state)}
          </span>
        </div>
        <ol className="progress-list" aria-label="撮影の進捗">
          {SHOT_TYPES.map((slot) => {
            const accepted = state.slots[slot] !== null;
            const active = state.currentStep === slot;
            return (
              <li
                className={`${accepted ? "is-complete" : ""} ${active ? "is-active" : ""}`}
                data-testid={`progress-${slot}`}
                key={slot}
              >
                <span className="progress-index">{accepted ? "✓" : SHOT_TYPES.indexOf(slot) + 1}</span>
                <span>
                  <strong>{SHOT_LABELS[slot]}</strong>
                  <small>{accepted ? "完了" : active ? "次に撮る" : "未撮影"}</small>
                </span>
              </li>
            );
          })}
        </ol>
      </section>

      {currentCaptureSlot !== null ? (
        <>
          <section className="capture-card" aria-labelledby="capture-title">
            <div className="capture-card-heading">
              <div>
                <p className="section-kicker">次の1枚</p>
                <h2 id="capture-title">{SHOT_LABELS[currentCaptureSlot]}を追加</h2>
              </div>
              <span className="step-badge" data-testid="current-step">
                {currentCaptureSlot}
              </span>
            </div>

            <CameraPreview
              captureBusy={state.status !== "capturing"}
              captureFrame={captureCameraFrame}
              checkLocalAnalysisSupport={checkLocalAnalysisSupport}
              createController={createCameraController}
              onCapture={submitImage}
              onRequestUpload={() => uploadInputRef.current?.click()}
              shot={currentCaptureSlot}
            />

            <div className="upload-panel">
              <div className="upload-icon" aria-hidden="true">＋</div>
              <strong>写真をアップロード</strong>
              <span>端末の画像を選ぶだけで自動確認します</span>
              <label
                aria-disabled={state.status !== "capturing"}
                className={`upload-button ${state.status !== "capturing" ? "is-disabled" : ""}`}
              >
                <span>画像を選ぶ</span>
                <input
                  accept="image/*"
                  aria-label={`${SHOT_LABELS[currentCaptureSlot]}画像をアップロード`}
                  data-testid="upload-input"
                  disabled={state.status !== "capturing"}
                  onChange={handleUpload}
                  ref={uploadInputRef}
                  type="file"
                />
              </label>
            </div>

            <div className="fixture-controls" aria-label="fixture provider 設定">
              <div className="control-row">
                <label htmlFor="fixture-shot">fixture画像</label>
                <select
                  aria-label="fixture画像"
                  data-testid="fixture-shot"
                  id="fixture-shot"
                  onChange={(event) =>
                    setFixtureShot(event.target.value as CaptureShotType)
                  }
                  value={fixtureShot}
                >
                  {SHOT_TYPES.map((shot) => (
                    <option key={shot} value={shot}>
                      {shot}
                    </option>
                  ))}
                </select>
              </div>
              <div className="control-row">
                <label htmlFor="fixture-outcome">判定結果</label>
                <select
                  aria-label="fixture判定結果"
                  data-testid="fixture-outcome"
                  id="fixture-outcome"
                  onChange={(event) =>
                    setFixtureOutcome(event.target.value as FixtureOutcome)
                  }
                  value={fixtureOutcome}
                >
                  {(Object.keys(OUTCOME_LABELS) as FixtureOutcome[]).map(
                    (outcome) => (
                      <option key={outcome} value={outcome}>
                        {OUTCOME_LABELS[outcome]}
                      </option>
                    ),
                  )}
                </select>
              </div>
            </div>

            <p className="helper-text">
              ガイドの状態にかかわらず手動撮影できます。カメラを使えない場合は画像を選んでください。
            </p>

            {state.status === "analyzing" ? (
              <p className="inline-status" role="status">
                {statusLabel(state)}
              </p>
            ) : null}

            {feedback !== null ? (
              <div className="feedback feedback-warning" data-testid="assessment-feedback" role="alert">
                <strong>撮り直してください</strong>
                <span>{feedback}</span>
              </div>
            ) : null}

            {state.providerError !== null ? (
              <div className="feedback feedback-error" data-testid="provider-error" role="alert">
                <strong>判定サービスに接続できません</strong>
                <span>{state.providerError.error.message}</span>
                <div className="feedback-actions">
                  {state.providerError.error.retryable ? (
                    <button onClick={handleRetryAnalysis} type="button">
                      同じ画像で再試行
                    </button>
                  ) : null}
                  <button
                    className="secondary-button"
                    onClick={() => dispatch({ type: "RETAKE", slot: currentCaptureSlot })}
                    type="button"
                  >
                    別の画像を選ぶ
                  </button>
                </div>
              </div>
            ) : null}
          </section>

          <section className="accepted-card" aria-labelledby="accepted-title">
            <div className="section-heading compact-heading">
              <div>
                <p className="section-kicker">保存済み</p>
                <h2 id="accepted-title">受け入れた写真</h2>
              </div>
              <span className="count-label">{acceptedCount}/3</span>
            </div>
            <div className="thumbnail-grid">
              {SHOT_TYPES.map((slot) => {
                const accepted = state.slots[slot];
                return (
                  <div className={`thumbnail ${accepted ? "has-image" : ""}`} key={slot}>
                    {accepted ? (
                      <img alt={`${SHOT_LABELS[slot]}写真`} src={accepted.objectUrl} />
                    ) : (
                      <span aria-hidden="true">—</span>
                    )}
                    <strong>{SHOT_LABELS[slot]}</strong>
                    {accepted ? (
                      <button
                        className="text-button"
                        onClick={() => dispatch({ type: "RETAKE", slot })}
                        type="button"
                      >
                        撮り直す
                      </button>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </section>
        </>
      ) : null}

      {editReady ? (
        <section className="edit-entry" data-testid="edit-entry" aria-labelledby="edit-title">
          <div>
            <p className="section-kicker">撮影完了</p>
            <h2 id="edit-title">3枚そろいました</h2>
            <p>正面画像の背景編集へ進めます。</p>
          </div>
          <button
            className="primary-button"
            onClick={() => {
              dispatch({ type: "EDIT_REQUESTED" });
              setEditStarted(true);
            }}
            type="button"
          >
            編集を開始
          </button>
          {editStarted ? (
            <p className="edit-confirmation" data-testid="edit-surface" role="status">
              編集画面を準備しています。
            </p>
          ) : null}
        </section>
      ) : null}
    </main>
  );
}

export default App;
