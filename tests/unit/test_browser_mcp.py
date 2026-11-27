"""Unit tests for the Phase 3 Playwright browser MCP server (mcp/browser/server.py)."""

from __future__ import annotations

import base64
import importlib.util
import json
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest
from control_plane.contracts import ErrorCode
from mcp.server.mcpserver.exceptions import ToolError

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = REPO_ROOT / "mcp" / "browser" / "server.py"


def _load_browser_server_module():
    """Load mcp/browser/server.py without shadowing the installed 'mcp' SDK."""
    spec = importlib.util.spec_from_file_location(
        "overwatch_browser_server", SERVER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["overwatch_browser_server"] = module
    spec.loader.exec_module(module)
    return module


browser_mod = _load_browser_server_module()
BrowserOpenResult = browser_mod.BrowserOpenResult
BrowserReadPageResult = browser_mod.BrowserReadPageResult
BrowserScreenshotResult = browser_mod.BrowserScreenshotResult
BrowserServerConfig = browser_mod.BrowserServerConfig
BrowserServerError = browser_mod.BrowserServerError
BrowserSession = browser_mod.BrowserSession
create_browser_server = browser_mod.create_browser_server
resolve_proxy_url = browser_mod.resolve_proxy_url


class FakePlaywrightTimeoutError(Exception):
    """Simulates Playwright's async TimeoutError without requiring chromium binary."""


class _FakePlaywrightHarness:
    """Async mocked Playwright -> Chromium -> Context -> Page harness."""

    def __init__(self) -> None:
        self.launch_calls: list[dict] = []
        self.new_context_calls: list[dict] = []
        self.goto_calls: list[tuple[str, dict]] = []
        self.screenshot_calls: list[dict] = []
        self.inner_text_calls: list[tuple[str, dict]] = []
        self.context_closed = False
        self.browser_closed = False
        self.pw_exited = False
        self.page_closed = False
        self.route_handler = None

        self.page = MagicMock()
        self.page.url = "about:blank"
        self.page.set_default_timeout = MagicMock()
        self.page.set_default_navigation_timeout = MagicMock()
        self.page.route = AsyncMock(side_effect=self._register_route)
        self.page.goto = AsyncMock(side_effect=self._goto)
        self.page.title = AsyncMock(return_value="Example Domain")
        self.page.inner_text = AsyncMock(side_effect=self._inner_text)
        self.page.screenshot = AsyncMock(side_effect=self._screenshot)
        self.page.close = AsyncMock(side_effect=self._close_page)

        self.context = MagicMock()
        self.context.set_default_timeout = MagicMock()
        self.context.new_page = AsyncMock(return_value=self.page)
        self.context.close = AsyncMock(side_effect=self._close_context)

        self.browser = MagicMock()
        self.browser.new_context = AsyncMock(side_effect=self._new_context)
        self.browser.close = AsyncMock(side_effect=self._close_browser)

        self.playwright = MagicMock()
        self.playwright.chromium = MagicMock()
        self.playwright.chromium.launch = AsyncMock(side_effect=self._launch)

        self.next_response = SimpleNamespace(
            status=200,
            url="https://example.com/",
            request=None,
        )
        self.next_text = "Visible page text"
        self.next_png = b"\x89PNG\r\n\x1a\nFAKE_PNG_DATA"
        self.next_jpeg = b"FAKE_JPEG_DATA"

    async def _register_route(self, pattern: str, handler) -> None:
        self.route_handler = handler

    async def _launch(self, **kwargs):
        self.launch_calls.append(kwargs)
        return self.browser

    async def _new_context(self, **kwargs):
        self.new_context_calls.append(kwargs)
        return self.context

    async def _goto(self, url: str, **kwargs):
        self.goto_calls.append((url, kwargs))
        if isinstance(self.next_response, Exception):
            raise self.next_response
        self.page.url = getattr(self.next_response, "url", url)
        return self.next_response

    async def _inner_text(self, selector: str, **kwargs):
        self.inner_text_calls.append((selector, kwargs))
        if isinstance(self.next_text, Exception):
            raise self.next_text
        return self.next_text

    async def _screenshot(self, **kwargs):
        self.screenshot_calls.append(kwargs)
        if kwargs.get("type") == "jpeg":
            if isinstance(self.next_jpeg, Exception):
                raise self.next_jpeg
            return self.next_jpeg
        if isinstance(self.next_png, Exception):
            raise self.next_png
        return self.next_png

    async def _close_page(self) -> None:
        self.page_closed = True

    async def _close_context(self) -> None:
        self.context_closed = True

    async def _close_browser(self) -> None:
        self.browser_closed = True

    def factory(self):
        harness = self

        @asynccontextmanager
        async def _cm():
            try:
                yield harness.playwright
            finally:
                harness.pw_exited = True

        return _cm()


def test_mcp_sdk_tool_schemas_and_stateful_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCPServer exposes contract tools and preserves page state across calls."""
    monkeypatch.setenv("HTTP_PROXY", "http://egress-proxy:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://egress-proxy:3128")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")

    harness = _FakePlaywrightHarness()
    cfg = BrowserServerConfig(timeout_s=15.0, output_cap_bytes=200_000)
    server = create_browser_server(config=cfg, playwright_factory=harness.factory)

    async def _exercise() -> None:
        tools = await server.list_tools()
        by_name = {t.name: t for t in tools}
        assert set(by_name.keys()) == {
            "browser.open",
            "browser.read_page",
            "browser.screenshot",
        }
        assert by_name["browser.open"].input_schema["required"] == ["url"]
        assert set(by_name["browser.read_page"].input_schema["properties"].keys()) == {
            "max_chars"
        }
        assert (
            set(by_name["browser.screenshot"].input_schema["properties"].keys())
            == set()
        )

        # Calling read_page / screenshot before browser.open fails safely
        with pytest.raises(ToolError) as exc_unopened:
            await server.call_tool("browser.read_page", {})
        assert ErrorCode.UPSTREAM_ERROR in str(exc_unopened.value)

        open_res = await server.call_tool(
            "browser.open", {"url": "https://example.com/"}
        )
        assert open_res.is_error is False
        assert open_res.structured_content == {"title": "Example Domain", "status": 200}

        with pytest.raises(BrowserServerError) as err_extra:
            browser_mod._validate_args("browser.screenshot", {"full_page": True})
        assert err_extra.value.code == ErrorCode.ARG_INVALID

        with pytest.raises(ToolError) as err_chars:
            await server.call_tool("browser.read_page", {"max_chars": 0})
        assert ErrorCode.ARG_INVALID in str(err_chars.value)

        read_res = await server.call_tool("browser.read_page", {"max_chars": 7})
        assert read_res.structured_content == {"text": "Visible"}

        shot_res = await server.call_tool("browser.screenshot", {})
        assert shot_res.structured_content == {
            "image_b64": base64.b64encode(harness.next_png).decode("ascii")
        }

        # State persists in non-persistent context: launch + new_context called once
        assert len(harness.launch_calls) == 1
        assert len(harness.new_context_calls) == 1
        assert not hasattr(
            harness.playwright.chromium, "launch_persistent_context"
        ) or (harness.playwright.chromium.launch_persistent_context.call_count == 0)

        # Verify proxy, no bypass list, TLS verification intact, and NO_PROXY stripped
        launch_kw = harness.launch_calls[0]
        assert launch_kw["headless"] is True
        assert launch_kw["proxy"] == {"server": "http://egress-proxy:3128"}
        assert "bypass" not in launch_kw["proxy"]
        assert "--no-sandbox" in launch_kw["args"]
        assert "--disable-dev-shm-usage" in launch_kw["args"]
        assert "NO_PROXY" not in launch_kw["env"]
        assert "no_proxy" not in launch_kw["env"]

        ctx_kw = harness.new_context_calls[0]
        assert ctx_kw["proxy"] == {"server": "http://egress-proxy:3128"}
        assert "bypass" not in ctx_kw["proxy"]
        assert ctx_kw["ignore_https_errors"] is False

    anyio.run(_exercise)


@pytest.mark.parametrize(
    "bad_url",
    [
        "",
        "   ",
        " https://example.com",
        "https://example.com ",
        "file:///etc/passwd",
        "ftp://example.com/archive.tar.gz",
        "javascript:alert(1)",
        "data:text/html,<h1>hi</h1>",
        "http://user:pass@example.com/",
        "https://token@example.com/",
        "https://example.com/a\nb",
        "https://example.com/\x00evil",
        "https://example.com/\r\nInjected: 1",
        "https://example.com/with space",
        "not-a-url",
        "http://:8080/path",
        "https://example.com:99999/",
    ],
)
def test_browser_open_rejects_invalid_and_unsafe_urls(
    monkeypatch: pytest.MonkeyPatch, bad_url: str
) -> None:
    """Only valid http/https URLs without credentials or control chars are accepted."""
    monkeypatch.setenv("HTTP_PROXY", "http://egress-proxy:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://egress-proxy:3128")

    harness = _FakePlaywrightHarness()
    session = BrowserSession(playwright_factory=harness.factory)

    async def _run() -> None:
        with pytest.raises(BrowserServerError) as exc_info:
            await session.open_url(bad_url)
        assert exc_info.value.code == ErrorCode.ARG_INVALID

    anyio.run(_run)
    assert harness.launch_calls == []


def test_proxy_missing_or_invalid_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing, empty, malformed, credentialed, or conflicting proxies fail closed."""
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)

    harness = _FakePlaywrightHarness()
    session = BrowserSession(
        config=BrowserServerConfig(),
        playwright_factory=harness.factory,
    )

    async def _run_missing() -> None:
        with pytest.raises(BrowserServerError) as exc_info:
            await session.open_url("https://pypi.org/")
        assert exc_info.value.code == ErrorCode.UPSTREAM_ERROR

    anyio.run(_run_missing)
    assert harness.launch_calls == []

    for bad_cfg in [
        BrowserServerConfig(http_proxy="", https_proxy=""),
        BrowserServerConfig(http_proxy="not-a-proxy", https_proxy="not-a-proxy"),
        BrowserServerConfig(http_proxy="socks5://proxy:1080"),
        BrowserServerConfig(https_proxy="http://user:pw@proxy:3128"),
        BrowserServerConfig(http_proxy="http://proxy:3128/path?x=1"),
        BrowserServerConfig(http_proxy="http://proxy:99999"),
        BrowserServerConfig(http_proxy="http://p1:3128", https_proxy="http://p2:3128"),
    ]:
        with pytest.raises(BrowserServerError) as exc_bad:
            resolve_proxy_url(bad_cfg)
        assert exc_bad.value.code == ErrorCode.UPSTREAM_ERROR


def test_valid_and_blocked_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid http/https redirects succeed; redirects to unsafe targets fail closed."""
    monkeypatch.setenv("HTTP_PROXY", "http://egress-proxy:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://egress-proxy:3128")

    harness = _FakePlaywrightHarness()
    first_hop = SimpleNamespace(url="http://example.com/", redirected_from=None)
    final_hop = SimpleNamespace(
        url="https://www.example.com/landing",
        redirected_from=first_hop,
    )
    harness.next_response = SimpleNamespace(
        status=200,
        url="https://www.example.com/landing",
        request=final_hop,
    )
    harness.page.title = AsyncMock(return_value="Landing")

    session = BrowserSession(
        config=BrowserServerConfig(),
        playwright_factory=harness.factory,
    )

    async def _run() -> None:
        res = await session.open_url("http://example.com/")
        assert res == BrowserOpenResult(title="Landing", status=200)

        # Navigation guard aborts unsafe intermediate route requests
        assert harness.route_handler is not None
        unsafe_route = SimpleNamespace(
            request=SimpleNamespace(
                is_navigation_request=lambda: True,
                url="file:///etc/passwd",
            ),
            abort=AsyncMock(),
            continue_=AsyncMock(),
        )
        await harness.route_handler(unsafe_route)
        unsafe_route.abort.assert_awaited_once_with("blockedbyclient")
        unsafe_route.continue_.assert_not_awaited()

        # Unsafe redirect chain target caught and tainted page cleared
        bad_hop = SimpleNamespace(
            url="https://user:secret@evil.example/",
            redirected_from=first_hop,
        )
        harness.next_response = SimpleNamespace(
            status=200,
            url="https://user:secret@evil.example/",
            request=bad_hop,
        )
        with pytest.raises(BrowserServerError) as exc_redir:
            await session.open_url("http://example.com/")
        assert exc_redir.value.code == ErrorCode.ARG_INVALID
        assert harness.page_closed is True

        # Follow-up read fails safely because tainted page was cleared
        with pytest.raises(BrowserServerError) as exc_no_page:
            await session.read_page()
        assert exc_no_page.value.code == ErrorCode.UPSTREAM_ERROR

    anyio.run(_run)


def test_timeout_and_output_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Timeout and output caps apply across open, read_page, and screenshot."""
    monkeypatch.setenv("HTTPS_PROXY", "http://egress-proxy:3128")

    harness = _FakePlaywrightHarness()
    cfg = BrowserServerConfig(timeout_s=2.5, output_cap_bytes=100)
    session = BrowserSession(config=cfg, playwright_factory=harness.factory)

    async def _run() -> None:
        # Huge title capped within output_cap_bytes
        harness.page.title = AsyncMock(return_value="A" * 500)
        opened = await session.open_url("https://example.com/")
        assert len(json.dumps(opened.model_dump()).encode("utf-8")) <= 100
        assert harness.goto_calls[0][1]["timeout"] == 2500

        # Heavily escaped body text stays under output_cap_bytes
        harness.next_text = "\n" * 500
        read_res = await session.read_page()
        assert len(json.dumps(read_res.model_dump()).encode("utf-8")) <= 100

        # Oversized PNG falls back to JPEG compression within cap
        harness.next_png = b"X" * 200
        harness.next_jpeg = b"Y" * 40
        shot_res = await session.screenshot()
        assert shot_res == BrowserScreenshotResult(
            image_b64=base64.b64encode(b"Y" * 40).decode("ascii")
        )
        assert [c["type"] for c in harness.screenshot_calls] == ["png", "jpeg"]

        # If JPEG also exceeds cap, fail safely
        harness.next_jpeg = b"Z" * 200
        with pytest.raises(BrowserServerError) as exc_cap:
            await session.screenshot()
        assert exc_cap.value.code == ErrorCode.UPSTREAM_ERROR
        assert "output cap" in exc_cap.value.message

        # Playwright timeout maps cleanly to ErrorCode.TIMEOUT
        harness.next_text = FakePlaywrightTimeoutError("Timed out")
        with pytest.raises(BrowserServerError) as exc_to:
            await session.read_page()
        assert exc_to.value.code == ErrorCode.TIMEOUT

        harness.next_response = FakePlaywrightTimeoutError("Navigation timeout")
        with pytest.raises(BrowserServerError) as exc_nav_to:
            await session.open_url("https://example.com/")
        assert exc_nav_to.value.code == ErrorCode.TIMEOUT

    anyio.run(_run)


def test_browser_errors_and_entrypoint_no_shadowing() -> None:
    """Browser runtime failures map to UPSTREAM_ERROR and CLI launches cleanly."""
    harness = _FakePlaywrightHarness()
    harness.playwright.chromium.launch = AsyncMock(
        side_effect=RuntimeError("executable missing")
    )
    session = BrowserSession(
        config=BrowserServerConfig(http_proxy="http://egress-proxy:3128"),
        playwright_factory=harness.factory,
    )

    async def _run_err() -> None:
        with pytest.raises(BrowserServerError) as exc_info:
            await session.open_url("https://example.com/")
        assert exc_info.value.code == ErrorCode.UPSTREAM_ERROR
        assert harness.pw_exited is True

    anyio.run(_run_err)

    proc = subprocess.run(
        [sys.executable, str(SERVER_PATH), "--help"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "OverWatch session-scoped Playwright browser MCP server" in proc.stdout
    assert not (REPO_ROOT / "mcp" / "__init__.py").exists()
