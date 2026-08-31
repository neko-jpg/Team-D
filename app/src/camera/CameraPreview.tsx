import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactElement,
} from "react";

import {
  CameraController,
  CameraStartError,
  type CameraSession,
} from "./cameraController";
import type { CaptureSlot, LiveHint } from "../shared";
import { CaptureGuide } from "./CaptureGuide";
import {
  supportsLocalAnalysis,
  type LocalAnalysisSupportCheck,
} from "./localAnalysisSupport";
import {
  captureRawVideoFrame,
  type RawFrameCapture,
} from "./rawCapture";
import "./camera.css";

export type CameraControllerFactory = (
  video: HTMLVideoElement,
) => CameraSession;

export interface CameraPreviewProps {
  readonly captureBusy?: boolean;
  readonly captureFrame?: RawFrameCapture;
  readonly checkLocalAnalysisSupport?: LocalAnalysisSupportCheck;
  readonly createController?: CameraControllerFactory;
  readonly currentShot?: CaptureSlot;
  readonly localHint?: LiveHint;
  readonly onCapture?: (
    capture: { readonly blob: Blob; readonly shot: CaptureSlot },
  ) => boolean | void | Promise<boolean | void>;
  readonly onRequestUpload?: () => void;
}

type CameraViewState =
  | { readonly type: "idle" }
  | { readonly type: "requesting" }
  | { readonly type: "playing" }
  | {
      readonly message: string;
      readonly type: "denied" | "error" | "unavailable";
    };

const defaultControllerFactory: CameraControllerFactory = (video) =>
  new CameraController(video);

function viewError(error: unknown): CameraViewState {
  if (error instanceof CameraStartError) {
    switch (error.code) {
      case "permission-denied":
        return {
          type: "denied",
          message:
            "カメラの使用が許可されていません。端末の設定で許可するか、下の「画像を選ぶ」から続けてください。",
        };
      case "camera-not-found":
      case "camera-unavailable":
      case "unsupported":
        return {
          type: "unavailable",
          message:
            "この端末ではカメラを利用できません。下の「画像を選ぶ」から続けてください。",
        };
      case "insecure-context":
        return {
          type: "unavailable",
          message:
            "カメラの起動にはHTTPS接続が必要です。下の「画像を選ぶ」から続けてください。",
        };
      case "playback-failed":
        return {
          type: "error",
          message:
            "映像を開始できませんでした。「もう一度試す」を押してください。",
        };
      case "aborted":
        return { type: "idle" };
      case "unknown":
        break;
    }
  }

  return {
    type: "error",
    message: "カメラを起動できませんでした。もう一度お試しください。",
  };
}

function statusLabel(state: CameraViewState): string {
  switch (state.type) {
    case "idle":
      return "停止中";
    case "requesting":
      return "準備中";
    case "playing":
      return "映像表示中";
    case "denied":
      return "権限が必要";
    case "unavailable":
      return "利用できません";
    case "error":
      return "再試行できます";
  }
}

const LOCAL_HINT_LABELS: Record<LiveHint, string> = {
  TOO_DARK: "もう少し明るい場所へ移動してください",
  TOO_BRIGHT: "光が強すぎます。反射を避けてください",
  TOO_BLURRY: "カメラをゆっくり固定してください",
  HOLD_STEADY: "そのまま少し止めてください",
  READY: "撮影できます",
  ANALYZER_UNAVAILABLE:
    "端末内の画質サポートを利用できません。ガイドを見ながら撮影できます",
};

/**
 * Camera lifecycle, fixed guide, and manual capture stay independent from the
 * optional local analyzer so a quality hint never gates the shutter.
 */
export function CameraPreview({
  captureBusy = false,
  captureFrame = captureRawVideoFrame,
  checkLocalAnalysisSupport = supportsLocalAnalysis,
  createController = defaultControllerFactory,
  currentShot = "front",
  localHint,
  onCapture,
  onRequestUpload,
}: CameraPreviewProps): ReactElement {
  const [view, setView] = useState<CameraViewState>({ type: "idle" });
  const [analysisAvailable, setAnalysisAvailable] = useState<
    "unknown" | "available" | "unavailable"
  >("unknown");
  const [capturePhase, setCapturePhase] = useState<
    "idle" | "capturing" | "captured"
  >("idle");
  const [captureError, setCaptureError] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const controllerRef = useRef<CameraSession | undefined>(undefined);
  const mountedRef = useRef(false);
  const attemptRef = useRef(0);
  const captureAttemptRef = useRef(0);
  const captureInFlightRef = useRef(false);
  const previousCaptureBusyRef = useRef(captureBusy);

  const stopCamera = useCallback((updateView: boolean): void => {
    attemptRef.current += 1;
    captureAttemptRef.current += 1;
    captureInFlightRef.current = false;
    controllerRef.current?.stop();
    if (updateView && mountedRef.current) {
      setView({ type: "idle" });
      setAnalysisAvailable("unknown");
      setCapturePhase("idle");
      setCaptureError(null);
    }
  }, []);

  useEffect(() => {
    setCapturePhase("idle");
    setCaptureError(null);
  }, [currentShot]);

  useEffect(() => {
    if (previousCaptureBusyRef.current && !captureBusy) {
      setCapturePhase("idle");
      setCaptureError(null);
    }
    previousCaptureBusyRef.current = captureBusy;
  }, [captureBusy]);

  useEffect(() => {
    mountedRef.current = true;

    const handlePageHide = (): void => stopCamera(true);
    const handleVisibilityChange = (): void => {
      if (document.visibilityState === "hidden") {
        stopCamera(true);
      }
    };

    window.addEventListener("pagehide", handlePageHide);
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      mountedRef.current = false;
      window.removeEventListener("pagehide", handlePageHide);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      stopCamera(false);
    };
  }, [stopCamera]);

  const startCamera = async (): Promise<void> => {
    const video = videoRef.current;
    if (video === null) {
      return;
    }

    const attempt = ++attemptRef.current;
    const controller = controllerRef.current ?? createController(video);
    controllerRef.current = controller;
    setView({ type: "requesting" });
    setCaptureError(null);

    try {
      await controller.start();
      if (mountedRef.current && attempt === attemptRef.current) {
        setView(controller.isRunning ? { type: "playing" } : { type: "idle" });
        if (controller.isRunning) {
          let supported = false;
          try {
            supported = checkLocalAnalysisSupport();
          } catch {
            supported = false;
          }
          setAnalysisAvailable(supported ? "available" : "unavailable");
        }
      }
    } catch (error) {
      if (mountedRef.current && attempt === attemptRef.current) {
        setView(viewError(error));
      }
    }
  };

  const isPlaying = view.type === "playing";
  const isRequesting = view.type === "requesting";
  const isCapturing = capturePhase === "capturing";
  const analyzerUnavailable = analysisAvailable === "unavailable";
  const visibleHint: LiveHint | undefined = analyzerUnavailable
    ? "ANALYZER_UNAVAILABLE"
    : localHint;
  const uploadFallback = view.type === "denied" || view.type === "unavailable";

  const captureManually = async (): Promise<void> => {
    const video = videoRef.current;
    if (
      video === null ||
      !isPlaying ||
      captureInFlightRef.current ||
      captureBusy
    ) {
      return;
    }

    const captureAttempt = ++captureAttemptRef.current;
    captureInFlightRef.current = true;
    setCapturePhase("capturing");
    setCaptureError(null);

    try {
      const blob = await captureFrame(video);
      if (!mountedRef.current || captureAttempt !== captureAttemptRef.current) {
        return;
      }

      const accepted = await onCapture?.({ blob, shot: currentShot });
      if (
        mountedRef.current &&
        captureAttempt === captureAttemptRef.current &&
        accepted === false
      ) {
        setCapturePhase("idle");
        return;
      }
      if (
        mountedRef.current &&
        captureAttempt === captureAttemptRef.current &&
        accepted !== false
      ) {
        setCapturePhase("captured");
      }
    } catch {
      if (mountedRef.current && captureAttempt === captureAttemptRef.current) {
        setCapturePhase("idle");
        setCaptureError("撮影できませんでした。カメラを確認してもう一度お試しください。");
      }
    } finally {
      if (captureAttempt === captureAttemptRef.current) {
        captureInFlightRef.current = false;
      }
    }
  };

  return (
    <section
      className="camera-preview"
      data-camera-state={view.type}
      data-testid="camera-preview"
      aria-labelledby="camera-preview-title"
    >
      <div className="camera-preview-heading">
        <div>
          <p className="camera-preview-kicker">背面カメラ</p>
          <h3 id="camera-preview-title">カメラプレビュー</h3>
        </div>
        <span className="camera-preview-status" data-testid="camera-status">
          {statusLabel(view)}
        </span>
      </div>

      <div className="camera-viewport">
        <video
          aria-label="背面カメラのプレビュー"
          autoPlay
          className="camera-video"
          muted
          playsInline
          ref={videoRef}
        />
        {isPlaying ? <CaptureGuide shot={currentShot} /> : null}
        {!isPlaying ? (
          <div className="camera-placeholder" aria-hidden="true">
            <span>カメラを起動すると映像が表示されます</span>
          </div>
        ) : null}
      </div>

      <div className="camera-preview-actions">
        {isPlaying ? (
          <div className="camera-playing-actions">
            <button
              className="camera-secondary-button"
              onClick={() => stopCamera(true)}
              type="button"
            >
              カメラを停止
            </button>
            <button
              aria-label={`${currentShot === "front" ? "正面" : currentShot === "back" ? "背面" : "タグ"}を撮影`}
              className="camera-shutter-button"
              data-testid="manual-shutter"
              disabled={isCapturing || captureBusy}
              onClick={() => void captureManually()}
              type="button"
            >
              <span aria-hidden="true" />
            </button>
          </div>
        ) : (
          <button
            className="camera-primary-button"
            disabled={isRequesting}
            onClick={() => void startCamera()}
            type="button"
          >
            {isRequesting
              ? "カメラを準備中…"
              : view.type === "idle"
                ? "カメラを起動"
                : "もう一度試す"}
          </button>
        )}
        <p
          aria-live="polite"
          data-quality-hint={visibleHint}
          data-testid="camera-guidance"
        >
          {isCapturing
            ? "撮影しています…"
            : capturePhase === "captured"
              ? "撮影しました。写真を確認しています"
              : visibleHint === undefined
                ? "ガイドに合わせて、いつでも撮影できます"
                : LOCAL_HINT_LABELS[visibleHint]}
        </p>
      </div>

      {"message" in view ? (
        <div className="camera-error">
          <p role="alert">{view.message}</p>
          {uploadFallback && onRequestUpload !== undefined ? (
            <button
              className="camera-upload-fallback-button"
              data-testid="camera-upload-fallback"
              onClick={onRequestUpload}
              type="button"
            >
              画像を選んで続ける
            </button>
          ) : null}
        </div>
      ) : null}

      {captureError !== null ? (
        <p className="camera-error" data-testid="capture-error" role="alert">
          {captureError}
        </p>
      ) : null}
    </section>
  );
}
