import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  root: "app",
  envDir: "..",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:3001"
    }
  },
  build: {
    outDir: "../dist",
    emptyOutDir: true
  }
});
