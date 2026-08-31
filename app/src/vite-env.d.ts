/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_LIVEKIT_URL?: string;
  readonly VITE_LIVEKIT_ROOM?: string;
  readonly VITE_LIVEKIT_BROWSER_IDENTITY?: string;
  readonly VITE_LIVEKIT_AGENT_IDENTITY?: string;
  readonly VITE_LIVEKIT_BROWSER_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
