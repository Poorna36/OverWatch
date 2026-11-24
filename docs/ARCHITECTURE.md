# ARCHITECTURE

## 1. Overview

```
┌────────────┐  HTTPS / WSS   ┌──────────────────────── Control Plane ────────────────────────┐
│ Mobile App │◄──────────────►│ API (FastAPI)                                                  │
│            │                │  ├─ Session Manager ──(Docker SDK)──► Execution Host           │
│            │                │  ├─ Agent (LLM, role profile)                                  │
│            │                │  ├─ MCP GATEWAY  ◄── policy.yaml, profiles/*.yaml              │
│            │                │  ├─ Vault (credentials, optional connectors)                   │
│            │                │  ├─ Audit log (hash-chained)                                   │
│            │                │  └─ Event bus (sequenced stream, replay)                       │
└────────────┘                └───────────────────────────┬────────────────────────────────────┘
                                                          │ MCP (stdio/HTTP), gateway only
                              ┌───────────────────────────▼────────────────────────────────────┐
                              │ Execution Host: one hardened Docker container per session      │
                              │  MCP servers: terminal | files | browser | desktop             │
                              │               (+ calendar | gmail as DEMO connectors)          │
                              │  Xvfb + KasmVNC (live view)                                    │
                              │  Egress via allowlist proxy only                               │
                              └────────────────────────────────────────────────────────────────┘
```

## 2. Components

### 2.1 Mobile app (`/app`)
Expo React Native. Screens in `API_AND_UI.md`. Responsibilities: create tasks, show plan/approval cards, render live view (WebView), render event feed and audit timeline, report network quality, push notifications. Holds **no secrets** beyond its own session token.

### 2.2 Control plane (`/control-plane`)
- **API**: REST for commands, WebSocket for the event stream.
- **Session manager**: creates/destroys containers from profiles; owns the session state machine.
- **Agent**: turns the user's request into a plan and tool calls using the active role profile. The agent is an LLM client; it only sees the tool list the gateway exposes for this session.
- **Gateway**: the firewall (section 4).
- **Vault**: stores credentials for optional connectors and injects them at call time.
- **Audit**: append-only, hash-chained log.
- **Event bus**: assigns sequence numbers to events per session; supports replay from `last_seq`.

### 2.3 Execution host (per-session container)
Runs MCP servers and the desktop. Receives calls only from the gateway. No inbound internet, no Docker socket, outbound only through the egress proxy.

### 2.4 MCP servers
| Server | Tools | Origin |
|---|---|---|
| files | `files.list`, `files.read`, `files.write`, `files.upload`, `files.delete` | existing filesystem MCP server, wrapped with path jail |
| terminal | `terminal.run` | in-house, constrained shell |
| browser | `browser.open`, `browser.read_page`, `browser.screenshot` | existing browser-automation MCP server |
| desktop | `desktop.screenshot`, `desktop.click`, `desktop.type`, `desktop.key` | in-house, `xdotool` against container display |
| calendar *(DEMO)* | `calendar.*` | existing MCP server |
| gmail *(DEMO)* | `gmail.*` | existing MCP server |

Verify each existing server's scopes and credential method before use. All tools are described in `CONTRACTS.md`.

## 3. Trust boundaries

| Boundary | Rule |
|---|---|
| Phone ↔ control plane | Authenticated, TLS. Phone can only act on its own sessions. |
| Agent ↔ gateway | Agent is untrusted: it can request anything; the gateway decides. |
| Gateway ↔ MCP servers | Only the gateway holds server connections and credentials. |
| Container ↔ outside | Only via the egress proxy allowlist. No host access. |
| Web/file/email content | Treated as untrusted data, never as instructions to the system. |

## 4. MCP Gateway (firewall)

Pipeline for every call (all steps logged; see `CONTRACTS.md` for types):

```
1. session active?                  no  → SESSION_INACTIVE
2. tool in session profile?         no  → NOT_IN_PROFILE
3. within approved plan? (STRETCH)  no  → escalate to ASK
4. validate args (schema, jail)     bad → ARG_INVALID / PATH_JAIL
5. policy tier → allow | ask | block
6. budget / rate / loop check       hit → ASK (or BUDGET_EXCEEDED)
7. dry-run? (STRETCH)               write → record only
8. ask → approval card, await       deny/timeout → USER_DENIED / APPROVAL_TIMEOUT
9. undo capture (STRETCH)
10. inject credentials, forward, enforce timeout + output cap
11. audit entry
12. return result (truncated, redacted)
```

Policy and profile formats: `CONTRACTS.md`. Hardening: `SECURITY.md`.

## 5. Session lifecycle

```
CREATED → PLANNING → (AWAITING_PLAN_APPROVAL) → STARTING → ACTIVE ⇄ PAUSED → ENDING → ENDED
                                                              └──────────► FAILED
```

| Transition | Trigger |
|---|---|
| CREATED → PLANNING | `POST /sessions` |
| PLANNING → STARTING | plan ready (MVP: auto-approved; STRETCH: user approves) |
| STARTING → ACTIVE | container healthy, MCP servers connected |
| ACTIVE ⇄ PAUSED | user pause/resume or takeover |
| ACTIVE → ENDING | task done, user ends, TTL, budget exhausted |
| ENDING → ENDED | teardown completed (section 7) |
| any → FAILED | fatal error (container death, repeated failure) → teardown still runs |

## 6. Environment profiles

A profile is a YAML file under `/profiles` defining role, image, resources, MCP servers, scopes, egress allowlist and budget. The agent can choose a profile; it cannot widen one. Unknown requests for extra servers, scopes or registries become an **ask**. MVP ships `coding.yaml` and `general.yaml`; `daily_routine.yaml` (Calendar + Gmail) is DEMO.

## 7. Teardown

1. Mark session `ENDING`; reject new calls.
2. Flush audit; produce receipt.
3. `docker rm -f` container; remove volumes and session network.
4. Delete per-session credentials from vault; revoke provider tokens if the connector was session-scoped.
5. Emit `session_ended` event and push notification.

## 8. Live view and bandwidth tiers (STRETCH polish; basic live view is MVP)

| Tier | Trigger (tunable) | Payload |
|---|---|---|
| video | > ~1.5 Mbps | KasmVNC stream |
| screenshots | ~150 kbps to 1.5 Mbps | JPEG q40, ~1 fps, diff-only |
| text | < ~150 kbps | structured step events only |

Client reports measured throughput via WS ping/pong; server picks the tier with hysteresis (down fast, up slow). Events carry `seq`; reconnecting clients send `last_seq` and receive replay. Tasks run server-side and continue if the phone drops.

## 9. Voice (STRETCH)
App-side: record → speech-to-text → translate to English (API or local model) → show English text for confirmation → send. Only English reaches the control plane. Approvals remain taps.

## 10. Network layout

```
phone ──internet──► control-plane (host)
control-plane ──docker net "mgmt"──► container MCP ports (gateway only)
container ──internal net "sess_<id>"──► egress-proxy ──► allowlisted internet
```
The session network is `internal: true`; the only route out is the proxy.

## 11. Own-PC connector (DEMO / "next")
A lightweight agent on the user's PC opens an outbound WSS tunnel to the control plane and exposes the same MCP tools. It goes through the same gateway with a stricter default (one user-chosen folder, no desktop unless enabled). Not part of MVP.

## 12. Non-goals
Prompt-injection detection, multi-agent crews, billing, marketplace, multi-user teams.
