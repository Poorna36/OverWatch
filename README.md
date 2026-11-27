# MCP Pocket Computer

> Your phone is the remote control. The cloud VM is the computer.

A secure remote workspace you control from your phone. An MCP-enabled agent does the work inside a sandboxed VM/container, and **every tool call passes through a firewall (MCP gateway)** that decides allow, ask or block. No need to carry a computer around.

Built for the **Make MCP Matter** hackathon track. This is a technical demo, not a product launch.

## What it does (MVP)

- Start a task from your phone (text; voice optional).
- A sandboxed workspace (Docker) spins up with only the MCP servers the task needs.
- Watch the agent work through a live remote-desktop view and a tool-call feed.
- Approve, deny, pause or stop from the phone.
- Every decision is logged; the workspace is destroyed when you end the session.

## Core idea in one diagram

```
Phone app ──► Control plane ──► MCP Gateway (policy) ──► MCP servers ──► Sandboxed VM/container
    ▲                                  │                                      │
    └──────── events, approvals, live view, audit ◄───────────────────────────┘
```

## Documentation map (read in this order)

| File | Purpose |
|---|---|
| [AGENTS.md](AGENTS.md) | Rules and conventions for AI coding agents working in this repo. **Start here.** |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, trust boundaries, session lifecycle, tech stack, repo layout |
| [docs/FEATURES.md](docs/FEATURES.md) | Feature list with IDs, priority (MVP / stretch / demo-only) and acceptance criteria |
| [docs/CONTRACTS.md](docs/CONTRACTS.md) | Schemas: policy, profiles, tool calls, events, approvals, errors |
| [docs/API_AND_UI.md](docs/API_AND_UI.md) | REST/WebSocket endpoints and placeholder UI screens |
| [docs/DB.md](docs/DB.md) | Database schema |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model, hardening, secrets handling |
| [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) | Ordered build phases with checklists |
| [docs/TESTING.md](docs/TESTING.md) | Bypass tests, functional tests, metrics |
| [docs/DEMO.md](docs/DEMO.md) | Demo script and fallbacks |

## Scope at a glance

| Tier | What |
|---|---|
| **MVP** | Sandboxed workspace, MCP gateway + policy, terminal/files/browser/desktop MCP servers, live view, phone control and approvals, audit log, teardown |
| **Stretch** | Plan preview, dry-run, undo, bandwidth tiers, failure handling polish, voice-to-English |
| **Demo-only** | Gmail / Calendar connectors, own-PC connector |
| **Out of scope** | Prompt-injection shield, multi-agent crew, billing, marketplace |

## Quick start (target state)

```bash
cp .env.example .env            # add LLM API key
docker compose up --build       # control plane + egress proxy + base image build
cd app && npm install && npx expo start
```

When running the control plane directly outside Docker, keep Uvicorn access logs disabled so query-string stream/view tokens (`?token=...`) are never recorded:

```bash
uvicorn control_plane.api.main:app --host 0.0.0.0 --port 8000 --no-access-log
```

### Docker Engine connection

The control plane connects to a Docker daemon to create and destroy session containers. The host Docker socket (`/var/run/docker.sock`) is **never** mounted into the `control-plane` container or any session container.

| Scenario | Configuration |
|---|---|
| **Host-local development** (`uvicorn` on the Docker host) | Leave all `DOCKER_*` variables unset. The Docker SDK uses the host's normal local socket configuration automatically. |
| **Containerised Compose / remote deployment** | `docker-compose.yml` pins `DOCKER_TLS_VERIFY=1`, mounts the host certificate directory read-only at `/etc/overwatch/docker-tls`, and sets `DOCKER_CERT_PATH=/etc/overwatch/docker-tls`. Plain `tcp://` endpoints and incomplete certificate sets fail closed. |

#### Remote Docker TLS setup for Compose

1. **Certificate placement and permissions (on the host, outside the repository)**

   Provision `ca.pem`, `cert.pem`, and `key.pem` for the remote Docker Engine's TLS endpoint (`:2376`). Never commit real certificates or private keys to git. Place them in a restricted host directory (for example `/etc/overwatch/docker-tls`):

   ```bash
   sudo mkdir -p /etc/overwatch/docker-tls
   sudo chmod 0700 /etc/overwatch/docker-tls
   sudo chmod 0444 /etc/overwatch/docker-tls/ca.pem /etc/overwatch/docker-tls/cert.pem
   sudo chmod 0400 /etc/overwatch/docker-tls/key.pem
   ```

   `docker-compose.yml` mounts `${DOCKER_TLS_CERT_DIR:-/etc/overwatch/docker-tls}` read-only (`:ro`) into `/etc/overwatch/docker-tls` inside `control-plane`, matching `DOCKER_CERT_PATH=/etc/overwatch/docker-tls`.

2. **Environment configuration**

   Copy `.env.example` to `.env` and set your secrets plus the Docker TLS settings (use placeholders until replaced on the target host):

   ```bash
   # .env — remote TLS endpoint for Compose (replace placeholders; never commit certs)
   DOCKER_HOST=tcp://replace-with-docker-host:2376
   DOCKER_TLS_CERT_DIR=/path/to/docker/tls/certs
   ```

   (`DOCKER_TLS_VERIFY=1` and `DOCKER_CERT_PATH=/etc/overwatch/docker-tls` are already enforced by `docker-compose.yml`. If you run `uvicorn` directly on a host against a remote TLS daemon instead of Compose, also set `DOCKER_TLS_VERIFY=1` and `DOCKER_CERT_PATH=/path/to/docker/tls/certs`.)

3. **Validate and start the service**

   ```bash
   docker compose config          # verify rendered Compose configuration
   docker compose up -d --build   # start control-plane + egress-proxy
   ```

> **Security note on TLS scope:** Mutual TLS authenticates the client and daemon and encrypts traffic in transit, **but it does not restrict Docker API permissions**. Any client holding a valid certificate can exercise whatever Docker Engine API capabilities the remote daemon exposes. Restrict access to the remote Docker daemon (e.g. dedicated host/network isolation or an external authorization layer) to trusted control-plane use only.

See `.env.example` for the full list of supported environment variables.

Details and commands are finalized during Phase 1 of [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md).

## Status

See the checklist in [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md).

## License

TBD.
