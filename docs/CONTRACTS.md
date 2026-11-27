# CONTRACTS

Literal schemas. Python (pydantic) and TypeScript types must use these names and fields exactly. Types below are TypeScript-style for language neutrality. Optional fields end with `?`.

## 1. Primitives

```ts
type Id = string;                       // prefixed ULID: s_ session, p_ plan, a_ approval, u_ undo, e_ event
type Iso = string;                      // UTC ISO 8601
type Decision = "allow" | "ask" | "block";
type Tier = "read" | "reversible" | "sensitive" | "forbidden";
type SessionState =
  "CREATED" | "PLANNING" | "AWAITING_PLAN_APPROVAL" | "STARTING" |
  "ACTIVE" | "PAUSED" | "ENDING" | "ENDED" | "FAILED";

interface SessionSummary {
  session_id: Id;
  state: SessionState;
  profile: string;
  created_at: Iso;
}
```

## 2. Error codes

| Code | Meaning | Retry? |
|---|---|---|
| `SESSION_INACTIVE` | session not ACTIVE (paused, ending, ended) | no |
| `NOT_IN_PROFILE` | tool not allowed for this session's profile | no |
| `POLICY_BLOCK` | policy says block | no |
| `ARG_INVALID` | schema or size validation failed | agent may fix args |
| `PATH_JAIL` | path outside allowed root | no |
| `DENY_PATTERN` | terminal command matched a deny pattern | no |
| `USER_DENIED` | user denied approval | no |
| `APPROVAL_TIMEOUT` | no answer in time | no |
| `BUDGET_EXCEEDED` | budget or rate limit hit | after user raises |
| `LOOP_DETECTED` | same call repeated without progress | no |
| `TIMEOUT` | tool exceeded timeout | transient, bounded |
| `UPSTREAM_ERROR` | MCP server error | transient, bounded |
| `AUTH_ERROR` | credential missing/expired (connectors) | after reconnect |
| `FATAL` | container died or unrecoverable | no |

```ts
interface ErrorBody { code: string; message: string; details?: Record<string, unknown>; }
```

## 3. Tool call contract (agent ↔ gateway)

```ts
interface ToolCall {
  session_id: Id;
  call_id: string;                      // unique per call
  tool: string;                         // "files.write"
  args: Record<string, unknown>;
}

interface ToolResult {
  call_id: string;
  ok: boolean;
  decision: Decision | "asked_approved" | "asked_denied" | "dry_run";
  result?: unknown;                     // truncated to output cap
  truncated?: boolean;
  error?: ErrorBody;
  audit_seq: number;
  undo_id?: Id;
}
```

## 4. Tool catalog (MVP)

All paths are relative to `/workspace` unless stated. Every tool has a timeout and output cap defined in policy.

| Tool | Args | Returns | Default tier |
|---|---|---|---|
| `files.list` | `{path: string}` | `{entries: {name, type, size}[]}` | read |
| `files.read` | `{path: string, max_bytes?: number}` | `{content: string}` | read |
| `files.write` | `{path: string, content: string}` | `{bytes: number}` | reversible |
| `files.delete` | `{path: string}` | `{deleted: boolean}` | sensitive |
| `files.upload` | `{path: string, destination: string}` | `{url?: string}` | sensitive |
| `terminal.run` | `{command: string, cwd?: string, timeout_s?: number}` | `{exit_code, stdout, stderr}` | reversible |
| `browser.open` | `{url: string}` | `{title: string, status: number}` | read |
| `browser.read_page` | `{max_chars?: number}` | `{text: string}` | read |
| `browser.screenshot` | `{}` | `{image_b64: string}` | read |
| `desktop.screenshot` | `{}` | `{image_b64: string, width, height}` | read |
| `desktop.click` | `{x: number, y: number, button?: "left"\|"right"}` | `{ok: true}` | reversible |
| `desktop.type` | `{text: string}` | `{ok: true}` | reversible |
| `desktop.key` | `{combo: string}` | `{ok: true}` | reversible |

DEMO connectors: `calendar.list_events` (read), `calendar.create_event` / `calendar.update_event` (reversible), `calendar.delete_event` (sensitive), `gmail.search` / `gmail.read_message` (read), `gmail.create_draft` (reversible), `gmail.send` (sensitive; user-originated only).

## 5. Policy file (`policy.yaml`)

```yaml
version: 1
defaults:
  unknown_tool: block
  approval_timeout_s: 120
  output_cap_bytes: 200000
  tool_timeout_s: 30

tiers:
  read:        { decision: allow, log: true }
  reversible:  { decision: allow, log: true, undo: capture }   # undo is STRETCH; ignored in MVP
  sensitive:   { decision: ask }
  forbidden:   { decision: block }

tools:
  files.list:   { tier: read,       path_jail: /workspace }
  files.read:   { tier: read,       path_jail: /workspace }
  files.write:  { tier: reversible, path_jail: /workspace, max_arg_bytes: 1000000 }
  files.delete: { tier: sensitive,  path_jail: /workspace }
  files.upload: { tier: sensitive }
  terminal.run:
    tier: reversible
    timeout_s: 30
    deny_patterns: ["rm -rf /", ":(){", "mkfs", "dd if=", "> /dev/sd"]
  browser.open:        { tier: read }
  browser.read_page:   { tier: read }
  browser.screenshot:  { tier: read }
  desktop.screenshot:  { tier: read }
  desktop.click:       { tier: reversible }
  desktop.type:        { tier: reversible }
  desktop.key:         { tier: reversible }

escalations:                       # evaluated after the tool tier; can only make a decision stricter
  - when: { tool: "terminal.run", matches: "(curl|wget|ssh|scp)\\b" }
    decision: ask
    reason: "network tool in terminal"
  - when: { tool: "*", output_bytes_gt: 200000 }
    action: truncate
```

Rules: a tool missing from `tools` is blocked. Escalations never relax a decision.

## 6. Profile file (`profiles/*.yaml`)

```yaml
name: coding                       # unique, matches filename
role: coder                        # assistant | coder | routine
system_prompt_file: prompts/coder.md
image: pocket/py-node:1.0          # pinned tag
resources: { cpus: 2.0, memory: 2g, pids: 512, ttl_minutes: 60 }
budget: { max_tool_calls: 200, max_usd: 1.00 }
mcp_servers:
  terminal: {}
  files: { root: /workspace }
  desktop: {}
egress_allowlist: [pypi.org, files.pythonhosted.org, registry.npmjs.org, github.com]
tool_overrides:                    # optional; may only tighten
  terminal.run: { tier: sensitive }
```

DEMO profile `daily_routine.yaml` adds `calendar` and `gmail` servers with `scopes` per server (compose, not send).

## 7. Plan (STRETCH, F-11)

```ts
interface Plan {
  plan_id: Id;
  session_id: Id;
  summary: string;
  profile: string;
  steps: { tool: string; tier: Tier; note?: string }[];
  scopes: string[];
  est_budget_usd?: number;
  plan_hash: string;                // sha256 of canonical JSON
}
```

## 8. Approval card

```ts
interface ApprovalRequest {
  approval_id: Id;
  session_id: Id;
  call_id: string;
  tool: string;
  tier: Tier;
  args_redacted: Record<string, unknown>;
  reason: string;                   // why ask
  irreversible: boolean;            // show "cannot be undone"
  warnings?: string[];
  expires_at: Iso;
}
interface ApprovalResponse { approval_id: Id; approve: boolean; }
```

## 9. Events (WebSocket, server → phone)

```ts
interface EventEnvelope<T = unknown> { seq: number; session_id: Id; ts: Iso; type: EventType; data: T; }

type EventType =
  | "session_state"      // { state: SessionState, reason?: string }
  | "tool_call"          // { call_id, tool, decision, args_redacted }
  | "tool_result"        // { call_id, ok, error?: ErrorBody, duration_ms }
  | "blocked"            // { call_id, tool, code, reason }
  | "approval_request"   // ApprovalRequest
  | "approval_resolved"  // { approval_id, approved: boolean, by: "user"|"timeout" }
  | "plan"               // Plan                                  (STRETCH)
  | "dry_run_diff"       // { changes: {tool, summary}[] }        (STRETCH)
  | "problem_report"     // ProblemReport                         (STRETCH)
  | "tier_change"        // { tier: "video"|"screenshots"|"text" } (STRETCH)
  | "budget"             // { calls_used, calls_max, usd_used, usd_max }
  | "agent_message"      // { text: string }
  | "session_ended"      // { receipt_url: string };
```

Client → server WS messages: `{type:"hello", last_seq?: number}`, `{type:"throughput", kbps: number}`, `{type:"ping"}`.

## 10. Problem report (STRETCH, F-14)

```ts
interface ProblemReport {
  what_failed: string;
  why: string;
  tried: string[];
  fixes: { label: string; action: "retry"|"skip_step"|"reconnect"|"end_session"|"edit_plan" }[];
}
```

## 11. Audit entry

```ts
interface AuditEntry {
  seq: number;
  session_id: Id;
  ts: Iso;
  tool: string;
  args_redacted: Record<string, unknown>;
  decision:
    | "allowed"
    | "asked"
    | "asked_approved"
    | "asked_denied"
    | "blocked"
    | "dry_run";
  reason: string;                   // error code or short text
  tier: Tier | null;
  undo_id?: Id;
  duration_ms?: number;
  prev_hash: string;
  hash: string;                     // sha256(prev_hash + canonical_json(entry without hash))
}
```

- `"asked"`: gateway preflight decision awaiting human approval resolution.
- `"asked_approved"` / `"asked_denied"`: resolved approval outcomes recorded after human decision.

Redaction rules: never log secrets, tokens or full file/email bodies; truncate long strings to 200 chars.

## 12. Undo record (STRETCH, F-13)

```ts
interface UndoRecord {
  undo_id: Id; session_id: Id; audit_seq: number;
  tool: string;
  inverse_tool: string; inverse_args: Record<string, unknown>;
  state: "available" | "used" | "expired";
}
```

| Tool | Capture before call | Inverse |
|---|---|---|
| `files.write` | previous content or "absent" | restore / delete |
| `calendar.create_event` | none | `calendar.delete_event` |
| `calendar.update_event` | previous event | update back |
| `gmail.create_draft` | none | delete draft |

## 13. Connector token modes (DEMO)

`token_mode: "session" | "linked"`. `session`: revoked at teardown. `linked`: refresh token kept until "Disconnect"; access token dropped at teardown.
