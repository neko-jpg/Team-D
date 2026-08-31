import { createApiServer } from "./app";

const host = "127.0.0.1";
const port = Number.parseInt(process.env.API_PORT ?? "3001", 10);

if (!Number.isInteger(port) || port < 1 || port > 65_535) {
  throw new Error("API_PORT must be an integer between 1 and 65535");
}

const server = createApiServer();

server.listen(port, host, () => {
  console.log(`API listening on http://${host}:${port}`);
});

function shutdown() {
  server.close((error) => {
    if (error) {
      console.error(error);
      process.exitCode = 1;
    }
  });
}

process.once("SIGINT", shutdown);
process.once("SIGTERM", shutdown);
