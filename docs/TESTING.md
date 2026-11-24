# TESTING

Tests define "done". Layout: `tests/unit`, `tests/integration`, `tests/bypass`. Use `pytest`; integration and bypass tests need Docker.

## 1. Unit tests

| Area | Cases |
|---|---|
| Policy loader | invalid YAML rejected; unknown tool blocked; escalation never relaxes a decision |
| Gateway pipeline | each step returns the right error code (`NOT_IN_PROFILE`, `ARG_INVALID`, `PATH_JAIL`, `DENY_PATTERN`, `POLICY_BLOCK`, `SESSION_INACTIVE`) |
| Path jail | `..`, absolute paths, symlink escapes, unicode tricks |
| Redaction | secrets stripped, long strings truncated |
| Audit chain | valid chain verifies; modified row detected; UPDATE/DELETE rejected by trigger |
| Event bus | seq monotonic per session; replay from `last_seq` exact, in order, no duplicates |
| Approvals | approve, deny, timeout; agent cannot self-approve |
| Budget / loop | limit triggers ask; same call 3x triggers `LOOP_DETECTED` (STRETCH) |

## 2. Container hardening checks (F-01)

Run inside a live session container and assert:

- `id -u` is not 0
- Root filesystem is read-only (writing to `/etc` fails)
- `capsh --print` shows no effective capabilities
- `/var/run/docker.sock` does not exist
- No host paths mounted except the session volume
- pids, memory and cpu limits are set (inspect via Docker API)
- Container is attached only to the internal session network

## 3. Bypass suite (each must be blocked or asked, and audited)

| # | Attempt | Expected |
|---|---|---|
| B1 | `terminal.run: curl https://example.org` | `ask` (escalation) then egress denied if approved |
| B2 | `terminal.run: curl https://evil.example` after approval | proxy denies; `blocked` event |
| B3 | `files.write` to `/etc/passwd` | `PATH_JAIL` |
| B4 | `files.read` with `../../etc/shadow` | `PATH_JAIL` |
| B5 | symlink in workspace pointing to `/` then read through it | `PATH_JAIL` |
| B6 | `terminal.run: rm -rf /` | `DENY_PATTERN`; root FS read-only regardless |
| B7 | fork bomb | pids limit stops it; session stays responsive |
| B8 | call a tool not in the profile (e.g. `gmail.send` in `coding`) | `NOT_IN_PROFILE` |
| B9 | unknown tool name | `POLICY_BLOCK` (default deny) |
| B10 | huge output (`yes | head -c 50M`) | truncated at output cap |
| B11 | long-running command | `TIMEOUT` at limit |
| B12 | call while paused/ended | `SESSION_INACTIVE` |
| B13 | access another session's WS/view URL | rejected |
| B14 | `files.upload` | `ask`; deny → `USER_DENIED`; no answer → `APPROVAL_TIMEOUT` |
| B15 | tamper with an audit row | `verify_audit` reports broken chain |
| B16 | attempt direct MCP server connection from the agent process | not reachable (only the gateway holds connections) |

DEMO (F-18): `gmail.send` from the agent is rejected; only the user-originated endpoint can send; after Disconnect Google, connector tools fail with `AUTH_ERROR`/block.

## 4. Functional / integration

- Create session → ACTIVE → run scripted task (write file, run script, read output) → END; verify audit entries and receipt.
- Teardown (F-08): after `ENDED`, assert no container, volume, network, or vault credentials for the session.
- Live view: view URL reachable with a valid token, rejected without.
- Reconnect: drop WS mid-task; reconnect with `last_seq`; all missed events delivered.
- STRETCH: dry-run changes nothing and Apply matches the diff; undo restores a previous file; plan escalation for off-plan calls; problem report after repeated failure.

## 5. Network and bandwidth tests (F-15)

- Throttle with `tc netem` (or Chrome DevTools) at ~3 Mbps, ~500 kbps, ~100 kbps.
- Record per tier: bytes/min, perceived latency, tier switch time.
- Verify the task keeps running when the phone disconnects for 60 s and the replay is complete.

## 6. Metrics to capture for the demo/pitch

| Metric | How |
|---|---|
| Gateway added latency p50/p95 | timing around pipeline vs direct tool call |
| Cold vs pre-warmed container start | `POST /sessions` to ACTIVE |
| kbps per bandwidth tier | netem runs above |
| Block count in a standard run | audit query |

## 7. CI

- Lint (`ruff`, `black --check`), type check (`mypy` optional), unit tests on every PR.
- Integration and bypass tests on a Docker-enabled runner (nightly or manual if unavailable).
- Secret scanning on commits.
