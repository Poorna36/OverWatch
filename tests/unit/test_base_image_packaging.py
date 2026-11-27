"""Static and packaging unit checks for images/base/Dockerfile and images/README.md."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE_PATH = REPO_ROOT / "images" / "base" / "Dockerfile"
IMAGES_README_PATH = REPO_ROOT / "images" / "README.md"


def test_base_dockerfile_hardening_and_minimal_mcp_packaging() -> None:
    """Dockerfile copies only required MCP/gateway files and stays non-root/ro-safe."""
    text = DOCKERFILE_PATH.read_text(encoding="utf-8")

    # Preserves non-root UID:GID, workspace workdir, and sleep infinity entrypoint
    assert "USER 1000:1000" in text
    assert "WORKDIR /workspace" in text
    assert 'CMD ["sleep", "infinity"]' in text
    assert "PYTHONDONTWRITEBYTECODE=1" in text
    assert (
        '"/opt/overwatch/.venv"' in text
        or "OVERWATCH_VENV=/opt/overwatch/.venv" in text
    )
    assert "PLAYWRIGHT_BROWSERS_PATH=/opt/overwatch/ms-playwright" in text
    assert "playwright" in text
    assert re.search(r'playwright"?\s+install\s+--with-deps\s+chromium', text)

    # No forbidden host/secret/privileged constructs
    lower = text.lower()
    assert "docker.sock" not in lower
    assert "--privileged" not in lower
    assert "app_token" not in lower
    assert "vault_key" not in lower
    assert "llm_api_key" not in lower

    # Every COPY source exists in repo root and includes browser MCP while omitting
    # top-level mcp/__init__.py
    copy_sources = re.findall(r"^COPY\s+(\S+)\s+(\S+)$", text, flags=re.MULTILINE)
    assert len(copy_sources) >= 13

    copied_srcs = [src for src, _dst in copy_sources]
    assert "mcp/__init__.py" not in copied_srcs
    assert not (REPO_ROOT / "mcp" / "__init__.py").exists()
    assert "mcp/browser/__init__.py" in copied_srcs
    assert "mcp/browser/server.py" in copied_srcs
    assert all(
        all(
            excluded not in src
            for excluded in (
                "control-plane/api",
                "control-plane/db",
                "control-plane/audit",
                "control-plane/events",
                "control-plane/sessions",
                "control-plane/vault",
                "control-plane/agent",
            )
        )
        for src in copied_srcs
    )
    for src in copied_srcs:
        assert (REPO_ROOT / src).is_file(), f"Missing COPY source {src}"


def test_images_readme_documents_root_context_and_mcp_commands() -> None:
    """README uses repo root build context and documents Playwright + all 4 servers."""
    readme = IMAGES_README_PATH.read_text(encoding="utf-8")

    assert "docker build -t pocket/py-node:1.0 -f images/base/Dockerfile ." in readme
    assert "-f images/base/Dockerfile images/base" not in readme

    for server in ("terminal", "files", "desktop", "browser"):
        expected_cmd = (
            f"/opt/overwatch/.venv/bin/python /opt/overwatch/mcp/{server}/server.py"
        )
        assert expected_cmd in readme

    assert "playwright" in readme.lower()
    assert "chromium" in readme.lower()
