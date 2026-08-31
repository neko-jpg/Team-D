import type { Server } from "node:http";

import { afterEach, describe, expect, it } from "vitest";

import { createApiServer } from "./app";

let server: Server | undefined;

afterEach(async () => {
  if (server?.listening) {
    await new Promise<void>((resolve, reject) => {
      server?.close((error) => (error ? reject(error) : resolve()));
    });
  }

  server = undefined;
});

describe("GET /api/health", () => {
  it("returns an ok response", async () => {
    server = createApiServer();

    await new Promise<void>((resolve, reject) => {
      server?.once("error", reject);
      server?.listen(0, "127.0.0.1", resolve);
    });

    const address = server.address();

    if (!address || typeof address === "string") {
      throw new Error("API server did not expose a TCP address");
    }

    const response = await fetch(
      `http://127.0.0.1:${address.port}/api/health`,
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("application/json");
    await expect(response.json()).resolves.toEqual({ status: "ok" });
  });
});
