# AGENTS.md: Instructions for AI Coding Agents

You are building **MCP Pocket Computer**. Read this file first, then the docs in the order below. These docs are the source of truth. If code and docs disagree, fix the code, or propose a doc change in your PR description. Do not silently diverge.

## Read order

1. `README.md`: what and why
2. `docs/ARCHITECTURE.md`: components and boundaries
3. `docs/FEATURES.md`: what to build, with IDs and acceptance criteria
4. `docs/CONTRACTS.md`: exact schemas; implement these literally
5. `docs/DB.md`, `docs/API_AND_UI.md`: storage, endpoints, screens
6. `docs/SECURITY.md`: non-negotiable rules
7. `docs/BUILD_PLAN.md`: do work in this order
8. `docs/TESTING.md`: what "done" means

## Product in one paragraph

A phone app controls a sandboxed Docker workspace. An agent calls tools through MCP. All calls go through the **MCP gateway**, which enforces a YAML policy (allow / ask / block), then forwards to MCP servers running inside the workspace. The phone shows a live desktop view, a tool-call feed and approval cards. Sessions are logged and then destroyed.

## Fixed technical decisions (do not re-litigate)

| Area | Decision |
|---|---|
| Control plane | Python 3.11+, FastAPI, WebSocket, `uvicorn` |
| MCP | Official MCP Python SDK; gateway is an MCP client to servers and presents a tool interface to the agent |
| Sandbox | Docker, one container per session, via Docker SDK for Python |
| Live view | Xvfb + lightweight WM + KasmVNC (fallback: noVNC, then screenshot polling) |
| Egress | Internal Docker network + allowlist proxy (Squid or equivalent) |
| DB | SQLite via SQLAlchemy (schema in `docs/DB.md`) |
| Mobile | Expo (React Native, TypeScript) |
| Config | YAML for policy and profiles, validated at load with pydantic |
| Agent LLM | Any tool-calling LLM through a thin adapter in `control-plane/agent/llm.py` |

## Priority labels

Features are tagged in `docs/FEATURES.md`: **MVP**, **STRETCH**, **DEMO**. Finish all MVP before starting STRETCH. Never build anything listed under "Out of scope".

## Hard rules

1. **The agent never calls an MCP server directly.** Only the gateway does.
2. **Default deny.** Unknown tool, unknown session or unknown profile means block.
3. **Every gateway decision is written to the audit log**, including blocks.
4. **Secrets never reach the agent, the phone, logs or the container image.** See `docs/SECURITY.md`.
5. **Containers are hardened exactly as specified** in `docs/SECURITY.md`. No `--privileged`, no Docker socket, no host mounts.
6. **Do not add a prompt-injection "shield".** Basic MCP-level defences only (path jail, arg validation, timeouts, output caps).
7. **Do not invent schemas.** Use `docs/CONTRACTS.md`. If something is missing, add it to that file first, then implement.
8. Keep MVP features working end-to-end before polishing.

## Conventions

- Python: type hints everywhere, `ruff` + `black`, `pytest`. Pydantic models mirror `docs/CONTRACTS.md` names exactly.
- TypeScript: strict mode; shared types live in `app/src/contracts.ts` and mirror the contracts doc.
- Errors use the codes in `docs/CONTRACTS.md` section "Error codes". No ad-hoc strings.
- Timestamps: UTC ISO 8601. IDs: prefixed ULIDs (`s_`, `p_`, `a_`, `u_`, `e_`).
- Config in `.env` and YAML; never hard-code keys or URLs.
- Small PRs. One phase task per PR, with tests.

## Definition of done (per task)

- Matches the contract and acceptance criteria in `docs/FEATURES.md`.
- Unit tests plus any relevant bypass test from `docs/TESTING.md` pass.
- Audit entry written for any gateway-path change.
- Docs updated if behavior or schema changed.
- No secrets in code or logs.

## Repo layout

```
/app                       Expo mobile app
/control-plane
  /api                     FastAPI routes + websocket
  /agent                   planner, role profiles, llm adapter
  /gateway                 pipeline, policy loader, approvals, undo, failure
  /sessions                container lifecycle (Docker SDK), env builder
  /vault                   credential storage + injection
  /audit                   hash-chained audit log
  /events                  sequenced event bus + replay
/mcp
  /desktop                 xdotool-based MCP server (written in-house)
  /terminal                constrained shell MCP server (in-house)
/profiles                  *.yaml environment profiles
/policy.yaml               firewall policy
/images                    Dockerfiles (base, py-node)
/infra                     docker-compose, egress proxy config
/tests                     unit, integration, bypass
/docs                      the docs listed above
```

## When blocked or unsure

- Prefer the simplest implementation that satisfies the acceptance criteria.
- If a requirement is ambiguous, pick the safer option (more restrictive) and note it in the PR.
- Leave `TODO(owner): question` comments rather than guessing at security-sensitive behavior.
