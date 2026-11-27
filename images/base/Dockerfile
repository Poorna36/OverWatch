# syntax=docker/dockerfile:1
# Pinned Linux base image (Debian 12 Bookworm snapshot)
FROM debian:bookworm-20240904-slim

LABEL org.opencontainers.image.title="OverWatch Pocket Sandbox Base" \
      org.opencontainers.image.version="1.0" \
      org.opencontainers.image.authors="OverWatch Team"

# Prevent interactive prompts during apt operations
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies, desktop components, runtimes, and tools:
# - Xvfb (virtual X11 framebuffer)
# - openbox (lightweight window manager)
# - xdotool (X11 automation / mouse / keyboard injection)
# - scrot (lightweight X11 PNG screenshot capture)
# - Python 3 & pip & venv
# - Node.js & npm
# - KasmVNC server v1.3.3 (official release deb)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    procps \
    xvfb \
    openbox \
    xdotool \
    scrot \
    xauth \
    x11-xserver-utils \
    python3 \
    python3-pip \
    python3-venv \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Install pinned KasmVNC server v1.3.3 from official project release
RUN curl -fsSL -o /tmp/kasmvnc.deb \
    https://github.com/kasmtech/KasmVNC/releases/download/v1.3.3/kasmvncserver_bookworm_1.3.3_amd64.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends /tmp/kasmvnc.deb \
    && rm -f /tmp/kasmvnc.deb \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user with UID 1000 and GID 1000
RUN groupadd -g 1000 pocket && \
    useradd -u 1000 -g 1000 -m -s /bin/bash pocket

# Create workspace directory for volume mounts
RUN mkdir -p /workspace && \
    chown -R 1000:1000 /workspace && \
    chmod 755 /workspace

# Compatibility for --read-only root filesystem with --tmpfs /tmp:rw,size=256m:
# Point XDG and cache directories to /tmp so processes can write under read-only root.
ENV HOME=/home/pocket \
    XDG_CONFIG_HOME=/tmp/.config \
    XDG_CACHE_HOME=/tmp/.cache \
    XDG_DATA_HOME=/tmp/.local/share \
    XDG_RUNTIME_DIR=/tmp/runtime \
    KASMVNC_USER_DIR=/tmp/.kasmvnc

# Symlink standard user dotdirs in home to /tmp for tools that hardcode paths
RUN ln -s /tmp/.config /home/pocket/.config && \
    ln -s /tmp/.cache /home/pocket/.cache && \
    ln -s /tmp/.local /home/pocket/.local && \
    ln -s /tmp/.Xauthority /home/pocket/.Xauthority && \
    ln -s /tmp/.kasmvnc /home/pocket/.kasmvnc

WORKDIR /workspace

# Install isolated Python runtime for in-container MCP servers (no credentials or
# unrelated control-plane server/DB code). Keep mcp/__init__.py absent so imports of
# the official MCP SDK (`mcp.server.mcpserver`) resolve cleanly.
ENV OVERWATCH_RUNTIME_DIR=/opt/overwatch \
    OVERWATCH_VENV=/opt/overwatch/.venv \
    PLAYWRIGHT_BROWSERS_PATH=/opt/overwatch/ms-playwright \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends libatomic1 && \
    python3 -m venv "${OVERWATCH_VENV}" && \
    "${OVERWATCH_VENV}/bin/pip" install --no-cache-dir --upgrade pip && \
    "${OVERWATCH_VENV}/bin/pip" install --no-cache-dir "mcp>=1.0.0" "pydantic>=2.6.0" "playwright>=1.49.0" && \
    "${OVERWATCH_VENV}/bin/playwright" install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/*

COPY control-plane/__init__.py /opt/overwatch/lib/control_plane/__init__.py
COPY control-plane/contracts.py /opt/overwatch/lib/control_plane/contracts.py
COPY control-plane/gateway/__init__.py /opt/overwatch/lib/control_plane/gateway/__init__.py
COPY control-plane/gateway/path_jail.py /opt/overwatch/lib/control_plane/gateway/path_jail.py
COPY control-plane/gateway/argument_validation.py /opt/overwatch/lib/control_plane/gateway/argument_validation.py

COPY mcp/terminal/__init__.py /opt/overwatch/mcp/terminal/__init__.py
COPY mcp/terminal/server.py /opt/overwatch/mcp/terminal/server.py
COPY mcp/files/__init__.py /opt/overwatch/mcp/files/__init__.py
COPY mcp/files/server.py /opt/overwatch/mcp/files/server.py
COPY mcp/desktop/__init__.py /opt/overwatch/mcp/desktop/__init__.py
COPY mcp/desktop/server.py /opt/overwatch/mcp/desktop/server.py
COPY mcp/browser/__init__.py /opt/overwatch/mcp/browser/__init__.py
COPY mcp/browser/server.py /opt/overwatch/mcp/browser/server.py

RUN SITE_PACKAGES="$("${OVERWATCH_VENV}/bin/python" -c "import sysconfig; print(sysconfig.get_path('purelib'))")" && \
    printf "/opt/overwatch/lib\n" > "${SITE_PACKAGES}/overwatch_control_plane.pth" && \
    "${OVERWATCH_VENV}/bin/python" -m compileall -q /opt/overwatch/lib /opt/overwatch/mcp && \
    chmod -R a+rX /opt/overwatch

USER 1000:1000

# Keep container running in detached session mode; session manager executes tools/servers
CMD ["sleep", "infinity"]
