#!/usr/bin/env bash
# Verify the root FastAPI/Agent entrypoints without LiveKit credentials.
set -euo pipefail

task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"

python_bin=".venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
  echo "Missing $python_bin. Run: python3.11 -m venv .venv && .venv/bin/python -m pip install -r requirements-backend-dev.txt" >&2
  exit 1
fi

npm run build
"$python_bin" -c 'import backend.app, backend.live_agent; print("python import: ok")'

verify_port="${VERIFY_API_PORT:-3101}"
server_log="$(mktemp)"
fixture_agent_log="$(mktemp)"
live_agent_log="$(mktemp)"
server_pid=""
cleanup() {
  [[ -z "$server_pid" ]] || kill "$server_pid" 2>/dev/null || true
  rm -f "$server_log" "$fixture_agent_log" "$live_agent_log"
}
trap cleanup EXIT

PROVIDER_MODE=fixture API_PORT="$verify_port" "$python_bin" -m backend.server >"$server_log" 2>&1 &
server_pid=$!
health_ok=false
for _attempt in {1..20}; do
  if curl --fail --silent --show-error "http://127.0.0.1:${verify_port}/api/health" | grep -Fx '{"status":"ok"}'; then
    health_ok=true
    break
  fi
  sleep 0.1
done

if [[ "$health_ok" != true ]]; then
  cat "$server_log" >&2
  exit 1
fi

if ! grep -Fq "Uvicorn running on http://127.0.0.1:${verify_port}" "$server_log"; then
  cat "$server_log" >&2
  exit 1
fi
grep -Fq "backend_starting provider_mode=fixture url=http://127.0.0.1:${verify_port}" "$server_log"
echo "fixture server startup log: ok"

PROVIDER_MODE=fixture "$python_bin" -m backend.agent --help >"$fixture_agent_log" 2>&1
grep -Fq "agent_starting provider_mode=fixture provider_schema=VisionGuidanceProvider" "$fixture_agent_log"

# ``--help`` loads the exact root worker entrypoint but does not connect to a
# Room. This makes the mode selection testable without credentials. In
# particular, a live process must report live rather than silently switching
# to the fixture mode.
PROVIDER_MODE=live "$python_bin" -m backend.agent --help >"$live_agent_log" 2>&1
grep -Fq "agent_starting provider_mode=live provider_schema=VisionGuidanceProvider" "$live_agent_log"
if grep -Fq "provider_mode=fixture" "$live_agent_log"; then
  echo "live Agent unexpectedly selected fixture mode" >&2
  exit 1
fi
echo "fixture/live Agent startup logs: ok"
