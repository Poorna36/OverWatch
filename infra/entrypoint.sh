#!/bin/bash
set -e

# Egress proxy entrypoint and configuration watcher.
# Automatically detects dynamic ACL file changes, parses the configuration,
# and reloads Squid. If parsing or reload fails, the proxy terminates immediately
# to fail closed and prevent stale allow rules from remaining active.

ACL_FILE="${SQUID_ACL_FILE:-/etc/squid/conf.d/sessions.conf}"
CONFIG_FILE="${SQUID_CONFIG_FILE:-/etc/squid/squid.conf}"

echo "[egress-proxy] Starting Squid egress proxy watcher..."
echo "[egress-proxy] Monitored ACL file: ${ACL_FILE}"
echo "[egress-proxy] Main configuration: ${CONFIG_FILE}"

# Wait for the control plane to populate the initial ACL file if not yet present
while [ ! -f "${ACL_FILE}" ]; do
    echo "[egress-proxy] Waiting for initial ACL file ${ACL_FILE}..."
    sleep 0.5
done

# Validate initial configuration before starting daemon
echo "[egress-proxy] Validating initial Squid configuration..."
if ! /usr/sbin/squid -k parse -f "${CONFIG_FILE}"; then
    echo "[egress-proxy] FATAL: Initial Squid configuration parse failed! Terminating." >&2
    exit 1
fi

# Re-create self-signed cert if missing (standard ubuntu/squid container setup)
if [ ! -f /etc/ssl/private/ssl-cert-snakeoil.key ]; then
    /usr/sbin/make-ssl-cert generate-default-snakeoil --force-overwrite > /dev/null 2>&1 || true
fi

# Initialize cache directories if needed
/usr/sbin/squid -Nz -f "${CONFIG_FILE}" 2>/dev/null || true

# Start Squid daemon in background
/usr/sbin/squid -f "${CONFIG_FILE}" -NYC &
SQUID_PID=$!
echo "[egress-proxy] Squid running with PID ${SQUID_PID}."

# Fail-closed shutdown function
stop_proxy_fail_closed() {
    local reason="${1:-Unknown error}"
    echo "[egress-proxy] FATAL: Stopping proxy (${reason}) to fail closed!" >&2
    if kill -0 "${SQUID_PID}" 2>/dev/null; then
        kill -TERM "${SQUID_PID}" 2>/dev/null || true
        wait "${SQUID_PID}" 2>/dev/null || true
    fi
    exit 1
}

# Clean shutdown function for SIGTERM/SIGINT
clean_shutdown() {
    echo "[egress-proxy] Received termination signal. Stopping Squid cleanly..."
    if kill -0 "${SQUID_PID}" 2>/dev/null; then
        kill -TERM "${SQUID_PID}" 2>/dev/null || true
        wait "${SQUID_PID}" 2>/dev/null || true
    fi
    exit 0
}

trap clean_shutdown SIGTERM SIGINT

# Record initial ACL file checksum
LAST_HASH=$(sha256sum "${ACL_FILE}" 2>/dev/null | awk '{print $1}')

# Monitoring loop: check for file changes and verify process health
while true; do
    if ! kill -0 "${SQUID_PID}" 2>/dev/null; then
        echo "[egress-proxy] FATAL: Squid process ${SQUID_PID} exited unexpectedly!" >&2
        exit 1
    fi

    if [ ! -f "${ACL_FILE}" ]; then
        stop_proxy_fail_closed "Monitored ACL file ${ACL_FILE} was removed"
    fi

    CURRENT_HASH=$(sha256sum "${ACL_FILE}" 2>/dev/null | awk '{print $1}')
    if [ "${CURRENT_HASH}" != "${LAST_HASH}" ]; then
        echo "[egress-proxy] ACL file changed. Validating updated configuration..."

        # Parse new configuration
        if ! /usr/sbin/squid -k parse -f "${CONFIG_FILE}"; then
            stop_proxy_fail_closed "Updated configuration failed parsing"
        fi

        echo "[egress-proxy] Configuration valid. Reloading Squid..."
        # Reconfigure running Squid process
        if ! /usr/sbin/squid -k reconfigure -f "${CONFIG_FILE}"; then
            stop_proxy_fail_closed "Squid reload (reconfigure) failed"
        fi

        echo "[egress-proxy] Squid reloaded successfully with updated session policies."
        LAST_HASH="${CURRENT_HASH}"
    fi

    sleep 1
done
