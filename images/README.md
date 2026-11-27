# OverWatch Sandbox Images

This directory contains container definitions for the hardened session workspaces in OverWatch (Phase 1, F-01).

---

## 1. Image Specification

- **Target Image Tag**: `pocket/py-node:1.0`
- **Dockerfile Path**: `images/base/Dockerfile`
- **Base Image**: `debian:bookworm-20240904-slim` (pinned, non-latest Debian 12 snapshot)

### Included Components & Runtimes
| Component | Package / Source | Version / Purpose |
|---|---|---|
| OS Base | Debian 12 Bookworm | `debian:bookworm-20240904-slim` |
| Virtual Display | `xvfb` | Headless X11 virtual framebuffer |
| Window Manager | `openbox` | Minimal, lightweight X11 window manager |
| Remote Desktop VNC | `kasmvncserver` | Official GitHub release v1.3.3 (`kasmvncserver_bookworm_1.3.3_amd64.deb`) |
| Desktop Automation | `xdotool` | Synthetic keyboard, mouse, and window control |
| Screenshot Capture | `scrot` | Lightweight X11 PNG screenshot utility |
| Python Runtime | `python3`, `python3-pip`, `python3-venv` | Python 3.11 execution environment |
| Node.js Runtime | `nodejs`, `npm` | Node.js 18 LTS runtime environment |
| Browser Automation | `playwright`, Chromium | Headless Playwright Chromium + system libraries in `/opt/overwatch/ms-playwright` |
| MCP Server Runtime | `/opt/overwatch/.venv` + `/opt/overwatch/mcp/{terminal,files,desktop,browser}` | Official `mcp` SDK, `pydantic`, `playwright`, in-house stdio MCP servers, and minimal `control_plane` path-jail/contract modules |
| Non-Root User | `pocket` | UID `1000`, GID `1000` |

---

## 2. Build Instructions

Build the image locally using the repository root as the Docker build context:

```bash
docker build -t pocket/py-node:1.0 -f images/base/Dockerfile .
```

### In-Container MCP Server Commands (`stdio`)

After the session container starts in detached `sleep infinity` mode, each implemented MCP server is launched inside the container (e.g., via `docker exec -i sess_$ID ...`) using the isolated runtime venv:

- **Terminal MCP (`terminal.run`)**:
  ```bash
  /opt/overwatch/.venv/bin/python /opt/overwatch/mcp/terminal/server.py
  ```
- **Files MCP (`files.list`, `files.read`, `files.write`, `files.delete`)**:
  ```bash
  /opt/overwatch/.venv/bin/python /opt/overwatch/mcp/files/server.py
  ```
- **Desktop MCP (`desktop.screenshot`, `desktop.click`, `desktop.type`, `desktop.key`)**:
  ```bash
  /opt/overwatch/.venv/bin/python /opt/overwatch/mcp/desktop/server.py
  ```
- **Browser MCP (`browser.open`, `browser.read_page`, `browser.screenshot`)**:
  ```bash
  /opt/overwatch/.venv/bin/python /opt/overwatch/mcp/browser/server.py
  ```

Note: `/opt/overwatch/mcp` intentionally omits `__init__.py` so imports of the official `mcp` SDK resolve cleanly, and gateway-to-container transport wiring remains out of scope for image packaging.

---

## 3. Expected Runtime Mounts & Filesystem Architecture

The image is designed to run securely with a read-only root filesystem:

1. **Read-Only Root Filesystem (`--read-only`)**:
   - Operating system binaries and libraries are immutable at runtime.
2. **Temporary Memory Filesystem (`--tmpfs /tmp:rw,size=256m`)**:
   - Provides writable scratch space for X11 sockets (`/tmp/.X11-unix`), runtime locks, and process caches.
   - `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME`, `XDG_RUNTIME_DIR`, and `KASMVNC_USER_DIR` are directed to `/tmp`. Common home dotfiles in `/home/pocket` are symlinked to `/tmp`.
3. **Dedicated Session Workspace Volume (`-v ws_$ID:/workspace:rw`)**:
   - Read-write Docker named volume mounted at `/workspace` (UID:GID `1000:1000`), wiped upon session teardown (F-08).

---

## 4. Hardening Parameters Supplied by the Session Manager

The session manager (Phase 1) must launch the container using the exact hardening flags defined in `docs/SECURITY.md` Section 2:

```bash
docker run -d --name sess_$ID \
  --user 1000:1000 \
  --read-only --tmpfs /tmp:rw,size=256m \
  -v ws_$ID:/workspace:rw \
  --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 256 --memory 1g --cpus 1.0 \
  --network sess_net_$ID \
  -e HTTP_PROXY=http://egress:3128 -e HTTPS_PROXY=http://egress:3128 \
  pocket/py-node:1.0
```

### Security Invariants (docs/SECURITY.md)
- **Non-Root Execution**: Runs strictly as `1000:1000`; no `sudo` or setuid binaries.
- **Capabilities**: All Linux capabilities dropped (`--cap-drop ALL`), `no-new-privileges` enabled.
- **Zero Host Mounts**: Never mount host filesystem paths; only the isolated session volume `ws_$ID`.
- **No Docker Socket**: `/var/run/docker.sock` is never mounted or accessible.
- **No Privileged Mode**: Never run with `--privileged`.
- **Egress Proxy Only**: Container is attached only to the internal session network `sess_net_$ID` (`internal: true`). All internet traffic must route through the egress allowlist proxy.
- **Zero Stored Secrets**: No API keys, credentials, or private tokens are embedded in the image or volume. Per-call tokens are injected dynamically by the gateway.
