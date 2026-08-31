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
import "./camera.css";

export type CameraControllerFactory = (
  video: HTMLVideoElement,
) => CameraSession;

export interface CameraPreviewProps {
  readonly createController?: CameraControllerFactory;
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

/**
 * Thin lifecycle verification surface. The final camera overlay and shutter
 * remain separate from this task so the upload fallback stays intact.
 */
export function CameraPreview({
  createController = defaultControllerFactory,
}: CameraPreviewProps): ReactElement {
  const [view, setView] = useState<CameraViewState>({ type: "idle" });
  const videoRef = useRef<HTMLVideoElement>(null);
  const controllerRef = useRef<CameraSession | undefined>(undefined);
  const mountedRef = useRef(false);
  const attemptRef = useRef(0);

  const stopCamera = useCallback((updateView: boolean): void => {
    attemptRef.current += 1;
    controllerRef.current?.stop();
    if (updateView && mountedRef.current) {
      setView({ type: "idle" });
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
        {!isPlaying ? (
          <div className="camera-placeholder" aria-hidden="true">
            <span>カメラを起動すると映像が表示されます</span>
          </div>
        ) : null}
      </div>

      <div className="camera-preview-actions">
        {isPlaying ? (
          <button
            className="camera-secondary-button"
            onClick={() => stopCamera(true)}
            type="button"
          >
            カメラを停止
          </button>
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
          背面カメラを確認できます。写真は下の画像選択から追加できます。
        </p>
      </div>

      {"message" in view ? (
        <p className="camera-error" role="alert">
          {view.message}
        </p>
      ) : null}
    </section>
  );
}
