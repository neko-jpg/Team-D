import { defineConfig } from "vitest/config";

export default defineConfig({
  root: "app",
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    setupFiles: ["src/test/setup.ts"]
  }
});
