import { useEffect, useState } from "react";

type ApiStatus = "checking" | "ready" | "unavailable";

type HealthResponse = {
  status: "ok";
};

export function App() {
  const [apiStatus, setApiStatus] = useState<ApiStatus>("checking");

  useEffect(() => {
    const controller = new AbortController();

    async function checkApi() {
      try {
        const response = await fetch("/api/health", {
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(`Health check failed: ${response.status}`);
        }

        const body = (await response.json()) as HealthResponse;
        setApiStatus(body.status === "ok" ? "ready" : "unavailable");
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setApiStatus("unavailable");
        }
      }
    }

    void checkApi();

    return () => controller.abort();
  }, []);

  const statusLabel = {
    checking: "APIを確認中",
    ready: "API接続済み",
    unavailable: "APIに接続できません",
  }[apiStatus];

  return (
    <main className="app-shell">
      <section className="hero" aria-labelledby="page-title">
        <p className="eyebrow">Mercari AI Agent Hackathon</p>
        <h1 id="page-title">出品写真アシスタント</h1>
        <p className="lead">
          正面・背面・タグの撮影から、背景を整えた出品用画像の確認まで伴走します。
        </p>
        <div className={`api-status api-status--${apiStatus}`} role="status">
          <span aria-hidden="true" className="status-dot" />
          {statusLabel}
        </div>
      </section>
    </main>
  );
}
