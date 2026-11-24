# FEATURES

Priority: **MVP** (must ship), **STRETCH** (after MVP works end-to-end), **DEMO** (only to complete the demo story). Each feature has an ID; reference IDs in commits and PRs.

## MVP

### F-01 Sandboxed session workspace
Create a hardened Docker container per session from a profile.
- Starts from a pinned image; runs as non-root with the flags in `SECURITY.md`.
- Only MCP servers listed in the profile are started.
- Start time is reported to the phone; a pre-warmed container option exists for the demo.
- **Accept:** `POST /sessions` returns an ACTIVE session with a stream URL; the container passes the hardening checks in `TESTING.md`.

### F-02 MCP gateway and policy engine
Every tool call is checked against `policy.yaml` and the session profile.
- Decisions: allow / ask / block, with reason codes.
- Default deny for unknown tools.
- Argument validation, path jail, timeouts, output caps.
- **Accept:** each call in the bypass suite is blocked/asked as specified; allowed calls return results with measurable added latency.

### F-03 Core MCP servers
terminal, files, browser, desktop (tools in `CONTRACTS.md`).
- **Accept:** each tool works through the gateway in a live session; terminal is non-root with timeout; files are jailed to `/workspace`.

### F-04 Remote desktop live view
Phone shows the session's desktop.
- KasmVNC/noVNC in a WebView with a session-scoped, token-authenticated URL.
- **Accept:** user sees the agent's GUI actions on the phone within ~2 s on normal Wi-Fi.

### F-05 Phone control
Start a task, see the tool-call feed, pause, resume, stop, take over.
- **Accept:** pausing rejects agent calls with `SESSION_INACTIVE`/paused reason; resume continues; stop triggers teardown.

### F-06 Approvals
`ask` decisions create an approval card on the phone.
- Card shows tool, redacted args, risk tier and (where relevant) "cannot be undone".
- Timeout → deny. Push notification when the app is backgrounded.
- **Accept:** approve → call proceeds; deny/timeout → call returns `USER_DENIED` / `APPROVAL_TIMEOUT`; both audited.

### F-07 Audit log
Append-only, hash-chained entries for every decision.
- Timeline view on the phone, filterable by decision.
- Export a JSON receipt per session.
- **Accept:** chain verification script passes; tampering a row fails verification.

### F-08 Session teardown
Container, volumes, network and per-session credentials are removed at the end.
- **Accept:** after `ENDED`, no container/volume/network remains and no credential is retrievable.

### F-09 Environment profiles
YAML profiles define what a session gets (`coding`, `general`).
- Agent can select a profile but not widen it; widening requests become `ask`.
- **Accept:** a tool outside the profile returns `NOT_IN_PROFILE`.

### F-10 Event stream
Sequenced events over WebSocket with replay.
- **Accept:** reconnecting with `last_seq` delivers every missed event exactly once, in order.

## STRETCH

### F-11 Plan preview
Before running, the agent shows a plan (tools, scopes, tiers, budget) for approval. Calls outside the plan escalate to `ask`.
- **Accept:** plan card renders; unapproved off-plan calls are escalated.

### F-12 Dry-run
Reads run for real; writes are recorded, not executed; a diff list is shown. Apply replays through the normal pipeline and re-checks state.
- **Accept:** dry-run changes nothing; Apply result matches the diff, or flags drifted steps.

### F-13 Undo
Reversible writes capture state before the call and support one-tap undo, per step or whole session.
- **Accept:** undo restores previous state for `files.write`; irreversible tools show "cannot be undone".

### F-14 Failure handling
Error classes, bounded retries, loop breaker, problem report cards with tappable fixes.
- **Accept:** repeating the same failing call 3 times pauses the session and emits a problem report.

### F-15 Bandwidth mode
Tiered live view with measured throughput, hysteresis, event replay.
- **Accept:** under throttling the tier degrades and the task continues; kbps per tier recorded.

### F-16 Voice to English
In-app speech-to-text and translation; English confirmation before send.
- **Accept:** a regional-language phrase becomes an English task after user confirmation.

### F-17 Budget meter
Per-session limits on tool calls and LLM cost with a live meter; exceeding pauses and asks.
- **Accept:** hitting the limit triggers `BUDGET_EXCEEDED` ask flow.

## DEMO (optional connectors)

### F-18 Calendar + Gmail connectors
Existing MCP servers behind the same gateway; `daily_routine.yaml` profile.
- Drafts by default: profile grants compose, not send. Send is a user-originated action from the phone.
- Session teardown revokes session-scoped tokens; "Disconnect Google" revokes the grant.
- **Accept:** agent can read the calendar and draft a reply, cannot send; send works only via the user tap.

### F-19 Own-PC connector (mention only)
Documented in `ARCHITECTURE.md` section 11. Do not build for MVP.

## Out of scope
Prompt-injection shield, multi-agent crew, billing and pricing, marketplace, multi-user teams.

## Role profiles (single agent, different capabilities)

| Role | Profile | Tools | Notes |
|---|---|---|---|
| assistant | `general` | files, browser, desktop | default |
| coder | `coding` | terminal, files, desktop | egress to package registries only |
| routine *(DEMO)* | `daily_routine` | calendar, gmail | drafts only |

One agent, one conversation. The role only changes the system prompt, tool list, policy overrides and budget.
