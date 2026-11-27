# TESTING

Tests define "done". Layout: `tests/unit`, `tests/integration`, `tests/bypass`. Use `pytest`. The audited policy/path-jail regression suite in `tests/bypass` runs without Docker; network and container boundary checks in `tests/integration` require Docker and the documented images.

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

Run the local gateway regressions with `pytest -q tests/bypass`. These invoke the
preflight, audit, and executor boundary together. They cover B3–B6, B8–B15, and
the F-18 connector safety invariants, asserting that blocked attempts fail closed,
are persisted as `blocked` in the hash chain, and never execute on the underlying runtime.
Docker-only network and container boundary checks (B1–B2, B7, B16) remain in `tests/integration`
and require a Docker-enabled host with provisioned images.

| Cases | Verification |
|---|---|
| B3–B6 (path jail, deny patterns) | `tests/bypass/test_gateway_bypass.py` |
| B8–B9 (profile boundary, unknown tool deny) | `tests/bypass/test_gateway_bypass.py` |
| B10 (output cap truncation) | `tests/bypass/test_gateway_bypass.py` |
| B11 (tool timeout fail-closed) | `tests/bypass/test_gateway_bypass.py` |
| B12 (inactive session call rejection) | `tests/bypass/test_gateway_bypass.py` |
| B13 (cross-session stream/view token isolation) | `tests/bypass/test_gateway_bypass.py` |
| B14 (sensitive tool approval & denial) | `tests/bypass/test_gateway_bypass.py` |
| B15 (audit hash-chain tamper detection) | `tests/bypass/test_gateway_bypass.py` & `tests/unit/test_verify_audit.py` |
| DEMO F-18 (agent gmail.send forbidden) | `tests/bypass/test_gateway_bypass.py` |
| B1–B2 (egress proxy allowlist), teardown | `tests/integration/test_session_egress.py` (Docker required) |
| B7 (pids limit fork bomb) | `tests/integration/test_container_hardening.py` (Docker required) |
| End-to-end gateway, approval, audit and teardown flow | `tests/integration/test_backend_e2e_docker.py` (Docker required) |

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
| B13 | access another session's WS/view URL, or open the view WS from a foreign/missing Origin | rejected |
| B14 | `files.upload` | `ask`; deny → `USER_DENIED`; no answer → `APPROVAL_TIMEOUT` |
| B15 | tamper with an audit row | `verify_audit` reports broken chain |
| B16 | attempt direct MCP server connection from the agent process | not reachable (only the gateway holds connections) |

DEMO (F-18): `gmail.send` from the agent is rejected; only the user-originated endpoint can send; after Disconnect Google, connector tools fail with `AUTH_ERROR`/block.

## 4. Functional / integration

- Create session → ACTIVE → run scripted task (write file, run script, read output) → END; verify audit entries and receipt.
- Teardown (F-08): after `ENDED`, assert no container, volume, network, or vault credentials for the session.
- Live view: load the trusted KasmVNC assets through the view URL, exchange its token for the scoped cookie, open the authenticated WebSocket from the configured Origin, and verify an RFB update/frame. Reject missing/foreign Origin, invalid tokens, and cross-session access; confirm bridge cleanup on disconnect.
- Reconnect: drop WS mid-task; reconnect with `last_seq`; all missed events delivered.
- STRETCH: dry-run changes nothing and Apply matches the diff; undo restores a previous file; plan escalation for off-plan calls; problem report after repeated failure.

## 5. Network and bandwidth tests (F-15)

- Throttle with `tc netem` (or Chrome DevTools) at ~3 Mbps, ~500 kbps, ~100 kbps.
- Record per tier: bytes/min, perceived latency, tier switch time.
- Verify the task keeps running when the phone disconnects for 60 s and the replay is complete.

## 6. Metrics to capture for the demo/pitch

| Metric | Target / Empirical Measurement | How Measured / Status |
|---|---|---|
| Gateway Preflight Pipeline Latency | **p50 = 0.20 ms**, **p95 = 0.22 ms**, Mean = 0.20 ms | Measured via `test_gateway_pipeline_latency_benchmark` over 500 iterations with high-resolution monotonic clocks (`time.perf_counter_ns`). |
| Full Audited Coordinator Latency | **p50 = 3.04 ms**, **p95 = 3.85 ms**, Mean = 3.07 ms | Measured via `test_gateway_coordinator_latency_benchmark` (full preflight + SQLite persistent hash-chain write over 500 iterations). |
| Cold Container Start (`POST /sessions` to `ACTIVE`) | ~2.5–4.0s (target estimate) | **Blocked on current environment:** Requires live Docker daemon (Docker daemon unavailable). Design estimate; empirical timings remain blocked until measured on a Docker-enabled host. |
| Pre-warmed Container Startup | < 1.0s (target estimate) | **Blocked on current environment:** Requires live Docker daemon. Image caching eliminates layer extraction and build latency; empirical startup timing remains blocked until measured on a Docker-enabled host. |
| kbps per bandwidth tier | High ~3 Mbps, Med ~500 kbps, Low ~100 kbps (target estimates) | Target thresholds verified via bandwidth tier selection logic (`test_bandwidth_tiering.py`); empirical network shaping and measurement via `tc netem` requires a Docker/network environment. |
| Block count in a standard run | Exact count from audit table | Query `SELECT COUNT(*) FROM audit_entries WHERE decision = 'blocked'`. |

### Pre-warmed Container Evaluation

1. **Estimated Cold Start Latency Breakdown (Design Estimates)**:
   - Image resolution & layer extraction: 10–60s if cold, 0s if pre-cached locally.
   - Resource allocation: volume `ws_$ID`, network `sess_net_$ID`, proxy attachment: ~500ms–1.5s.
   - Container process initialization (Xvfb, Openbox WM, KasmVNC websocket bridge): ~1.5s–3s.
2. **Security Invariant Trade-offs & Policy Limits**:
   - `docs/SECURITY.md §8` mandates strict single-use isolation per session (`ws_<session_id>`, `sess_net_<session_id>`, and complete teardown at `ENDED`). Reusing an already-running container across different sessions introduces cross-session taint, shared filesystem residue, or credential leakage risks.
   - **Permitted Pre-warming (Image Caching Only)**: In accordance with `docs/SECURITY.md`, pre-warming is strictly limited to pre-building and caching base images (`images/base`) on the host. This ensures `POST /sessions` never triggers an on-demand Docker build or registry pull, keeping fresh container creation within ~3–4s while preserving full single-use isolation guarantees.
   - **No Standby Container Pools**: Standby container pools or pre-spawned shared containers are not permitted under the security policy, as each session must be bound to its own ephemeral internal network and isolated volume created at session start and destroyed at teardown.

## 7. CI

- Lint (`ruff`, `black --check`), type check (`mypy` optional), unit tests on every PR.
- Integration and bypass tests on a Docker-enabled runner (nightly or manual if unavailable).
- Secret scanning on commits.
