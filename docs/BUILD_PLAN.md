# BUILD PLAN

Do phases in order. Each task should be one small PR with tests. Never cut Phases 1 to 4. Check boxes as you go.

## Phase 0: Scaffold
- [x] Repo layout from `AGENTS.md`; `pyproject.toml`, `ruff`, `black`, `pytest`
- [x] `docker-compose.yml` (control plane, egress proxy), `.env.example`
- [x] Pydantic models mirroring `CONTRACTS.md` in `control-plane/contracts.py`
- [x] Expo app skeleton with `contracts.ts`
- [x] SQLite + Alembic initial migration from `DB.md`
- **Done when:** `docker compose up` runs a health endpoint; `pytest` runs with a trivial test.

## Phase 1: Sandbox (F-01, F-08, F-09)
- [ ] `images/base/Dockerfile` (non-root user, Xvfb, WM, KasmVNC, xdotool, MCP servers installed)
- [ ] Session manager: create container + internal network + volume with exact hardening flags
- [ ] Egress proxy config with per-session allowlist
- [ ] Profile loader (`profiles/*.yaml`, pydantic-validated); `coding.yaml`, `general.yaml`
- [ ] Teardown routine (container, volume, network, credentials)
- **Done when:** hardening checks and teardown checks in `TESTING.md` pass.

## Phase 2: Gateway core (F-02, F-07)
- [ ] Policy loader + validator for `policy.yaml`
- [ ] Pipeline steps 1, 2, 4, 5, 10, 11, 12 (see `ARCHITECTURE.md`)
- [ ] Path jail, arg schemas, timeouts, output caps, deny patterns
- [ ] Hash-chained audit writer + `verify_audit.py` + append-only triggers
- [ ] Stub MCP server for tests
- **Done when:** allow/block unit tests and audit chain tests pass.

## Phase 3: MCP servers (F-03)
- [ ] files server wrapped with path jail
- [ ] terminal MCP (in-house, argv-based, non-root, timeout)
- [ ] browser MCP (existing server wired through the gateway)
- [ ] desktop MCP (in-house, xdotool): screenshot, click, type, key
- [ ] Gateway connects to servers inside the container over the mgmt network
- **Done when:** each tool in `CONTRACTS.md` section 4 works end-to-end through the gateway.

## Phase 4: Control plane, events, approvals (F-05, F-06, F-10)
- [ ] REST routes: sessions, pause/resume/end, approvals, audit
- [ ] Event bus with seq + replay; WebSocket route
- [ ] Approval flow (step 8): create card, await, timeout → deny
- [ ] Agent loop with LLM adapter, tool list from the session profile, role system prompts
- [ ] Push notifications for approvals and completion
- **Done when:** a scripted task runs, an `ask` produces an approval, deny and approve both behave, replay works.

## Phase 5: Mobile app MVP (F-04, F-05, F-06, F-07)
- [ ] Home, New Task, Session, Approval modal, Audit screens
- [ ] WS client with replay, tool-call feed, state reducer
- [ ] Live view WebView (KasmVNC/noVNC), fallback to screenshot polling
- [ ] Pause / resume / take over / end controls
- **Done when:** the MVP demo path in `DEMO.md` works on a real phone.

## Phase 6: Stretch (in this order)
1. [ ] **Plan preview** (F-11): plan generation, card, `plan_hash`, off-plan escalation
2. [ ] **Failure handling** (F-14): error classes, bounded retry, loop breaker, problem report card
3. [ ] **Undo** (F-13): capture + inverse for `files.write`; UI buttons
4. [ ] **Dry-run** (F-12): intercept writes, diff list, apply with re-check
5. [ ] **Bandwidth tiers** (F-15): throughput reporting, tier switch, kbps meter, measurements
6. [ ] **Budget meter** (F-17)
7. [ ] **Voice to English** (F-16): STT + translate + confirm screen
- **Done when:** each feature's acceptance criteria in `FEATURES.md` pass.

## Phase 7: Demo connectors (F-18) (only after Phase 5 is solid)
- [ ] Vault + encrypted `connections`
- [ ] Google test-user OAuth, Calendar + Gmail MCP servers behind the gateway
- [ ] `daily_routine.yaml` profile with draft-only scopes
- [ ] User-originated send endpoint, Disconnect Google revoke
- [ ] Teardown revokes session-scoped tokens

## Phase 8: Polish and hardening
- [x] Bypass suite fully green; record gateway latency (p50/p95) and container start times
- [ ] Pre-warmed container for the demo
- [ ] Recorded backup demo video
- [x] README quick start verified from a clean clone

## Cut line

If time runs short, drop in this order: Phase 7, voice, bandwidth polish, dry-run, undo, budget meter. Plan preview and failure handling are cheap and valuable; keep them if possible.

## Risks

| Risk | Mitigation |
|---|---|
| Live view integration slips | noVNC, then screenshot polling |
| Container cold start | pre-warmed container |
| Existing MCP servers need different creds/config | wrap in the gateway; replace with a thin in-house server if needed |
| Google OAuth restrictions | test users only; DEMO scope |
| Demo network fails | recorded video; run stack locally |
