import { createServer, type IncomingMessage, type ServerResponse } from "node:http";

const jsonHeaders = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
};

function sendJson(
  response: ServerResponse,
  statusCode: number,
  body: Record<string, string>,
) {
  response.writeHead(statusCode, jsonHeaders);
  response.end(JSON.stringify(body));
}

export function handleApiRequest(
  request: IncomingMessage,
  response: ServerResponse,
) {
  const requestUrl = new URL(
    request.url ?? "/",
    `http://${request.headers.host ?? "127.0.0.1"}`,
  );

  if (request.method === "GET" && requestUrl.pathname === "/api/health") {
    sendJson(response, 200, { status: "ok" });
    return;
  }

  sendJson(response, 404, { error: "not_found" });
}

export function createApiServer() {
  return createServer(handleApiRequest);
}
