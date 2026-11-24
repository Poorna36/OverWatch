# SECURITY

The firewall is the MVP. These rules are non-negotiable; see `AGENTS.md`.

## 1. Threat model (what we defend against)

| Threat | Example | Defence |
|---|---|---|
| Agent does something harmful | `rm -rf /`, writes outside workspace | Policy tiers, deny patterns, path jail, read-only root FS, non-root |
| Agent reaches the internet freely | exfiltrate data to a random host | Internal network + egress allowlist proxy |
| Agent escapes the container | privilege escalation | Drop all caps, `no-new-privileges`, no Docker socket, no host mounts, pid/memory/cpu limits |
| Runaway cost or loops | endless retries | Budget, rate limits, loop breaker, TTL |
| Credential leakage | token printed to logs or seen by agent | Vault injection at call time, redaction, no secrets in container |
| Unauthorized phone access | stolen link | Bearer token, short-lived session-scoped view/WS tokens |
| Audit tampering | hide a blocked attempt | Hash chain + append-only triggers |
| Untrusted content (web, files, email) | page text tells the agent to act | Content treated as data; irreversible actions need approval. **No injection detection is claimed.** |

**Out of scope:** nation-state attackers, kernel 0-days, multi-tenant isolation guarantees. Say so openly in the demo.

## 2. Container hardening (exact flags)

```bash
docker run -d --name sess_$ID \
  --user 1000:1000 \
  --read-only --tmpfs /tmp:rw,size=256m \
  -v ws_$ID:/workspace:rw \
  --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 256 --memory 1g --cpus 1.0 \
  --network sess_net_$ID \
  -e HTTP_PROXY=http://egress:3128 -e HTTPS_PROXY=http://egress:3128 \
  <image>
```

Implement via Docker SDK with equivalent options. Additional rules:

- Never `--privileged`, never mount `/var/run/docker.sock`, never mount host paths other than the session volume.
- Pinned image tags (no `latest`); images built from `/images`.
- `sess_net_$ID` is created with `internal: true`; only the egress proxy is attached to both networks.
- Limits come from the profile `resources` but are clamped to hard maximums in code.
- The container holds no long-lived credentials; connector tokens are injected by the gateway per call.

## 3. Egress proxy

- Squid (or equivalent) with a per-session allowlist from the profile.
- Deny and log everything else; blocked requests surface as `blocked` events with reason `EGRESS_DENIED`.
- DNS: resolve only through the proxy; no direct DNS from the container.

## 4. Gateway rules

- Default deny; see pipeline in `ARCHITECTURE.md` section 4.
- Escalations can only tighten a decision.
- Argument validation uses JSON schemas per tool; reject unknown fields.
- Path jail: resolve to a real path, reject symlinks that leave `/workspace`, reject `..` after normalization.
- Terminal: non-root, per-call timeout, stdout/stderr caps, `deny_patterns` as a speed bump (the container is the real boundary).
- Output caps and truncation on every result.
- Approvals: out-of-band to the phone; the agent cannot approve its own actions. Timeouts deny.
- Pausing a session blocks all calls until resume.

## 5. Secrets handling

- Secrets live in `.env` (host) and the `vault` module, never in images, repos, logs or the DB in plaintext.
- `connections.secret_enc` is encrypted with a key from `.env` (`VAULT_KEY`).
- The agent, the phone and the audit log never see raw tokens.
- Redaction: strip keys matching `(token|secret|password|authorization|api[_-]?key)` from any logged args; truncate strings over 200 chars.
- `.env.example` has placeholders only. CI should fail on committed secrets.

## 6. Authentication

- App ↔ API: static bearer token (demo). Rotate by changing `.env`.
- WS and live-view: session-scoped tokens, 15-minute expiry, refreshable while the session is active.
- Reject any request for a session not owned by the caller.

## 7. Basic MCP-level defences (per server)

- Argument schemas and size limits.
- Path restrictions and allowed roots.
- Timeouts and output caps.
- No shell interpolation of unvalidated args (use argv arrays).
- Desktop MCP: rate-limit clicks/keystrokes per second.

## 8. Teardown guarantees

At `ENDED`: no container, volume or network for the session; per-session credentials deleted; session-scoped provider tokens revoked; audit receipt generated. Verified by `TESTING.md`.

## 9. Demo-connector rules (Gmail/Calendar)

- Test-user Google project only; state this in the demo.
- Profile grants compose (drafts) not send. `gmail.send` is only callable via the user-originated endpoint.
- New recipient on a send shows a warning on the approval card.
- Delete tools are `sensitive`.
- "Disconnect Google" revokes the grant and blocks connector tools immediately.

## 10. Honest claims for the demo

- "Every tool call goes through one gateway with an audit trail."
- "The container is the hard boundary; the policy is the guard rail."
- "Irreversible actions need a human tap."
- Do **not** claim: "the agent can't be tricked", "prompt injection is solved", "multi-tenant safe".
