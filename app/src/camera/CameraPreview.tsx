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
import {
  captureVideoFrame,
  type CaptureFrameReader,
} from "./captureFrame";
import "./camera.css";

export type CameraControllerFactory = (
  video: HTMLVideoElement,
) => CameraSession;

export interface CameraPreviewProps {
  readonly captureFrame?: CaptureFrameReader;
  readonly createController?: CameraControllerFactory;
  readonly onCapture?: (blob: Blob) => Promise<void> | void;
  readonly shot: "back" | "front" | "tag";
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

const shotContent = {
  front: {
    action: "襟・袖・裾をガイド内に入れてください",
    label: "正面",
  },
  back: {
    action: "裏返して背面を上にしてください",
    label: "背面",
  },
  tag: {
    action: "タグに近づき、枠に合わせてください",
    label: "タグ",
  },
} as const;

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

/** Camera lifecycle, fixed guides and manual raw-frame capture surface. */
export function CameraPreview({
  captureFrame = captureVideoFrame,
  createController = defaultControllerFactory,
  onCapture,
  shot,
}: CameraPreviewProps): ReactElement {
  const [view, setView] = useState<CameraViewState>({ type: "idle" });
  const [isCapturing, setIsCapturing] = useState(false);
  const [captureError, setCaptureError] = useState<string | undefined>();
  const videoRef = useRef<HTMLVideoElement>(null);
  const controllerRef = useRef<CameraSession | undefined>(undefined);
  const mountedRef = useRef(false);
  const attemptRef = useRef(0);
  const captureActiveRef = useRef(false);

  const stopCamera = useCallback((updateView: boolean): void => {
    attemptRef.current += 1;
    captureActiveRef.current = false;
    controllerRef.current?.stop();
    if (updateView && mountedRef.current) {
      setView({ type: "idle" });
      setIsCapturing(false);
      setCaptureError(undefined);
    }
  }, []);

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
    setCaptureError(undefined);
    setView({ type: "requesting" });

    try {
      await controller.start();
      if (mountedRef.current && attempt === attemptRef.current) {
        setView(controller.isRunning ? { type: "playing" } : { type: "idle" });
      }
    } catch (error) {
      if (mountedRef.current && attempt === attemptRef.current) {
        setView(viewError(error));
      }
    }
  };

  const isPlaying = view.type === "playing";
  const isRequesting = view.type === "requesting";
  const currentShot = shotContent[shot];

  const captureCurrentFrame = async (): Promise<void> => {
    const video = videoRef.current;
    if (video === null || !isPlaying || captureActiveRef.current) {
      return;
    }

    captureActiveRef.current = true;
    const attempt = attemptRef.current;
    setCaptureError(undefined);
    setIsCapturing(true);

    try {
      const blob = await captureFrame(video);
      if (!mountedRef.current || attempt !== attemptRef.current) {
        return;
      }

      await onCapture?.(blob);
    } catch {
      if (mountedRef.current && attempt === attemptRef.current) {
        setCaptureError(
          "写真を保存できませんでした。カメラはそのままです。もう一度撮影してください。",
        );
      }
    } finally {
      if (mountedRef.current && attempt === attemptRef.current) {
        captureActiveRef.current = false;
        setIsCapturing(false);
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
          <p className="camera-preview-kicker">{currentShot.label}を撮影</p>
          <h3 id="camera-preview-title">カメラで撮影</h3>
        </div>
        <span className="camera-preview-status" data-testid="camera-status">
          {isCapturing ? "撮影中" : statusLabel(view)}
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
        <div
          aria-hidden="true"
          className={`camera-guide camera-guide--${shot}`}
          data-testid="camera-guide"
        >
          {shot === "tag" ? (
            <span className="camera-tag-guide" />
          ) : (
            <span className="camera-garment-guide" />
          )}
        </div>
        <p className="camera-guidance" aria-live="polite">
          {currentShot.action}
        </p>
        {!isPlaying ? (
          <div className="camera-placeholder" aria-hidden="true">
            <span>カメラを起動すると映像が表示されます</span>
          </div>
        ) : null}
      </div>

      <div className="camera-preview-actions">
        {isPlaying ? (
          <div className="camera-playing-controls">
            <button
              className="camera-secondary-button"
              onClick={() => stopCamera(true)}
              type="button"
            >
              停止
            </button>
            <button
              aria-label={`${currentShot.label}を撮影`}
              className="camera-shutter"
              disabled={isCapturing}
              onClick={() => void captureCurrentFrame()}
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
        <p aria-live="polite">
          カメラが使えない場合は、下の画像選択から続けられます。
        </p>
      </div>

      {captureError !== undefined ? (
        <p className="camera-error" role="alert">
          {captureError}
        </p>
      ) : "message" in view ? (
        <p className="camera-error" role="alert">
          {view.message}
        </p>
      ) : null}
    </section>
  );
}
