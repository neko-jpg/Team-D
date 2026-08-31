#!/usr/bin/env bash
# Phase 3 completion-gate runner. It deliberately fails until the 4-slot
# transport integration exists, rather than reporting a misleading green run
# over the legacy 3-slot vertical slice.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

# This support suite must always run: it exercises the expected fixture matrix
# and invariant helpers even while the app integration prerequisites are absent.
npx --no-install vitest run --config app/vitest.config.ts \
  src/test/phase3FixtureTransport.test.ts

missing=()

if ! rg -q '"measurement"' app/src/capture/captureReducer.ts; then
  missing+=("3.5: CaptureReducer has not adopted the measurement slot/state")
fi

if ! rg -q 'GuidanceEvent|DataReceived' app/src/capture app/src/App.tsx; then
  missing+=("3.11: fixture transport does not consume and reject stale guidance events")
fi

if ! rg -q 'connecting|connected|reconnecting|disconnected' app/src/capture app/src/App.tsx; then
  missing+=("3.8/3.14: capture UI has no independent LiveKit connection state")
fi

if ! rg -q 'measurement' app/src/App.tsx; then
  missing+=("3.6: app integration has no fourth measurement capture/approval flow")
fi

if ! rg -q 'ANALYZER_UNAVAILABLE' app/src/capture app/src/App.tsx; then
  missing+=("3.15: four-slot flow does not expose the analyzer-unavailable fallback")
fi

if ! rg -q 'permission-denied' app/src/capture app/src/App.tsx; then
  missing+=("3.15: four-slot flow does not integrate the camera-permission upload fallback")
fi

if [[ ${#missing[@]} -gt 0 ]]; then
  printf '%s\n' 'Phase 3 regression gate is blocked; required implementation is missing:' >&2
  printf ' - %s\n' "${missing[@]}" >&2
  printf '%s\n' 'The runner intentionally exits non-zero until these prerequisites and the fixture transport integration test exist.' >&2
  exit 2
fi

npx --no-install vitest run --config app/vitest.config.ts \
  src/test/phase3FixtureTransport.test.ts \
  src/test/phase3FixtureTransport.integration.test.ts
