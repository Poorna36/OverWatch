# API AND UI (placeholders)

Placeholder level: enough for an AI agent to scaffold routes and screens. Payload types come from `CONTRACTS.md`. Everything here is a starting point; keep the names.

## 1. Auth (hackathon level)

- Single static bearer token per demo user: `Authorization: Bearer <APP_TOKEN>` from `.env`.
- WebSocket and live-view URLs use short-lived, session-scoped tokens issued by the API.
- A phone may only access sessions it created.

## 2. REST API

Base path: `/api/v1`. JSON in/out. Errors: `{ "error": ErrorBody }`.

### Sessions

| Method | Path | Body | Returns | Feature |
|---|---|---|---|---|
| POST | `/sessions` | `{request_text: string, profile?: string, dry_run?: boolean}` | `{session_id, state, plan?}` | F-01 |
| GET | `/sessions` | | `{sessions: SessionSummary[]}` (ordered newest first) | F-05 |
| GET | `/sessions/{id}` | | `{session_id, state, profile, created_at, stream_url?, view_url?}` | F-05 |
| POST | `/sessions/{id}/pause` | | `{state}` | F-05 |
| POST | `/sessions/{id}/resume` | | `{state}` | F-05 |
| POST | `/sessions/{id}/takeover` | `{enabled: boolean}` | `{state}` | F-05 |
| POST | `/sessions/{id}/end` | | `{state}` | F-08 |
| POST | `/sessions/{id}/message` | `{text: string}` | `{ok: true}` | F-05 |

### Approvals

| Method | Path | Body | Returns | Feature |
|---|---|---|---|---|
| GET | `/sessions/{id}/approvals?pending=true` | | `{approvals: ApprovalRequest[]}` | F-06 |
| POST | `/sessions/{id}/approvals/{aid}` | `ApprovalResponse` | `{ok: true}` | F-06 |

### Audit

| Method | Path | Returns | Feature |
|---|---|---|---|
| GET | `/sessions/{id}/audit?decision=&after_seq=` | `{entries: AuditEntry[]}` | F-07 |
| GET | `/sessions/{id}/receipt` | JSON receipt (entries + chain head hash) | F-07 |
| GET | `/audit/verify/{session_id}` | `{valid: boolean, broken_at?: number}` | F-07 |

### Stretch

| Method | Path | Body | Returns | Feature |
|---|---|---|---|---|
| POST | `/sessions/{id}/plan/approve` | `{plan_id}` | `{state}` | F-11 |
| POST | `/sessions/{id}/plan/edit` | `{plan_id, remove_steps: number[]}` | `{plan}` | F-11 |
| POST | `/sessions/{id}/dry_run/apply` | | `{applied: number, flagged: number}` | F-12 |
| POST | `/sessions/{id}/undo/{undo_id}` | | `{ok: boolean}` | F-13 |
| POST | `/sessions/{id}/undo_all` | | `{undone: number}` | F-13 |
| POST | `/sessions/{id}/problem/{action}` | `{action: "retry"\|"skip_step"\|...}` | `{state}` | F-14 |
| GET | `/budget/{session_id}` | | budget event payload | F-17 |

### Meta

| Method | Path | Returns |
|---|---|---|
| GET | `/profiles` | `{profiles: {name, role, tools[]}[]}` |
| GET | `/health` | `{status: "ok"}` |
| POST | `/devices/push-token` | `{token}` → `{ok: true}` |

### DEMO connectors

| Method | Path | Notes |
|---|---|---|
| GET | `/connections` | list connected providers |
| POST | `/connections/google/start` | begin OAuth (test users only) |
| DELETE | `/connections/google` | revoke grant |
| POST | `/sessions/{id}/drafts/{draft_id}/send` | user-originated send (F-18) |

## 3. WebSocket

`GET /api/v1/sessions/{id}/stream?token=...` upgrades to WS. `GET /api/v1/sessions/{id}` builds `stream_url` from the configured `PUBLIC_BASE_URL` (converting `http`/`https` to `ws`/`wss`) with a short-lived stream token while the session is active.

- Client first sends `{"type":"hello","last_seq":N}`; server replays events with `seq > N`, then streams live.
- Server frames are `EventEnvelope` (see `CONTRACTS.md` section 9).
- Client sends `{"type":"throughput","kbps":N}` every ~5 s (F-15) and `{"type":"ping"}` every ~15 s.

## 4. Live view

- `view_url` from `GET /sessions/{id}`: KasmVNC/noVNC endpoint proxied by the control plane, token-authenticated and session-scoped.
- Fallback mode (`screenshots`): client polls `GET /sessions/{id}/screenshot` for JPEG frames.

## 5. Mobile UI (placeholder screens)

Navigation: Home → New Task → (Plan Preview) → Session → Audit. Plain components first; styling later.

### S1 Home
```
┌──────────────────────────┐
│ MCP Pocket Computer      │
│ [+ New task]             │
│ Recent sessions          │
│  • Build Python script ▸ │
│  • Research report     ▸ │
└──────────────────────────┘
```
Calls: `GET /sessions`.

### S2 New Task
```
┌──────────────────────────┐
│ What should I do?        │
│ [ text input.........  ] │
│ [🎤 voice]  (STRETCH)    │
│ Profile: [coding ▾]      │
│ [ ] Dry-run (STRETCH)    │
│ [ Start ]                │
└──────────────────────────┘
```
Calls: `POST /sessions`.

### S3 Plan Preview (STRETCH)
```
┌──────────────────────────┐
│ Plan                     │
│ 1 files.write   reversible│
│ 2 terminal.run  reversible│
│ Budget ~$0.12            │
│ [Approve][Dry-run][Edit] │
└──────────────────────────┘
```

### S4 Session (main screen)
```
┌──────────────────────────┐
│ ● ACTIVE   tier: video   │
│ ┌──────────────────────┐ │
│ │   live desktop view  │ │
│ └──────────────────────┘ │
│ Tool-call feed           │
│  ✓ files.write allowed   │
│  ⚠ files.upload ask      │
│  ✕ terminal.run blocked  │
│ Budget ▓▓▓░░ 38/100      │
│ [Pause][Take over][End]  │
└──────────────────────────┘
```
Data: WS events. Collapsible live view; the feed is the primary element in `text` tier.

### S5 Approval modal
```
┌──────────────────────────┐
│ Approve action?          │
│ files.upload             │
│ report.pdf → drive       │
│ Risk: sensitive          │
│ ⚠ Cannot be undone       │
│ Expires in 1:52          │
│ [Deny]        [Approve]  │
└──────────────────────────┘
```
Also delivered as a push notification.

### S6 Audit timeline
```
┌──────────────────────────┐
│ Filter: [All ▾]          │
│ 10:02 files.write ✓      │
│ 10:03 terminal.run ✓     │
│ 10:04 curl… ✕ blocked    │
│       reason: POLICY     │
│ [Export receipt] [Undo]  │
└──────────────────────────┘
```
Calls: `GET /sessions/{id}/audit`, `GET /sessions/{id}/receipt`. Undo is STRETCH.

### S7 Problem report (STRETCH)
Card with what failed, why, what was tried, and fix buttons from `ProblemReport.fixes`.

### S8 Settings
Server URL, token, push permissions. DEMO: Connections (Google connect/disconnect).

## 6. App structure

```
app/src/
  contracts.ts        # mirrors CONTRACTS.md
  api/client.ts       # REST helpers
  api/stream.ts       # WS with replay + throughput reporting
  screens/{Home,NewTask,PlanPreview,Session,Approval,Audit,Problem,Settings}.tsx
  components/{LiveView,ToolFeed,ApprovalCard,BudgetBar,TierBadge}.tsx
  state/session.ts    # reducer fed by events
```

## 7. Mocking for early UI work

Provide `control-plane/dev/mock_stream.py` that emits scripted `EventEnvelope`s (allowed call, ask, block, done) so the app can be built before the gateway exists.
