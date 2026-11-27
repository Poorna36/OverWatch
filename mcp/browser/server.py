"""Phase 3 browser MCP server (F-03).

Exposes the three contract-defined browser tools via the installed MCP Python
SDK (``MCPServer``):
- ``browser.open``
- ``browser.read_page``
- ``browser.screenshot``

Security boundaries:
- Uses Playwright Chromium in a non-persistent browser context and maintains
  page state across calls within this single server process.
- Routes all browser traffic through the session's ``HTTP_PROXY`` /
  ``HTTPS_PROXY`` egress proxy, failing closed when proxy configuration is
  missing or invalid, with no bypass list and without disabling TLS
  verification.
- Accepts only valid ``http`` / ``https`` URLs without embedded credentials or
  control characters, and validates redirected navigation targets as well.
- Enforces policy default timeouts (``30s``) and output byte caps (``200_000``
  bytes), treating all web page content strictly as untrusted data.
- Gateway-level policy decisions, profile egress allowlist generation (Squid),
  approvals, and audit logging remain the responsibility of the control plane.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from control_plane.contracts import ErrorCode
from control_plane.gateway.argument_validation import (
    ArgumentValidationError,
    validate_tool_arguments,
)
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict

DEFAULT_TIMEOUT_S: float = 30.0
DEFAULT_OUTPUT_CAP_BYTES: int = 200_000
_JPEG_QUALITY_FALLBACKS: tuple[int, ...] = (75, 50, 30, 15)


class BrowserServerError(Exception):
    """Base error raised by the browser MCP server."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class BrowserOpenResult(BaseModel):
    """Exact ``browser.open`` return schema per ``docs/CONTRACTS.md`` §4."""

    model_config = ConfigDict(extra="forbid")

    title: str
    status: int


class BrowserReadPageResult(BaseModel):
    """Exact ``browser.read_page`` return schema per ``docs/CONTRACTS.md`` §4."""

    model_config = ConfigDict(extra="forbid")

    text: str


class BrowserScreenshotResult(BaseModel):
    """Exact ``browser.screenshot`` return schema per ``docs/CONTRACTS.md`` §4."""

    model_config = ConfigDict(extra="forbid")

    image_b64: str


@dataclass(frozen=True)
class BrowserServerConfig:
    """Runtime configuration for the browser MCP server."""

    http_proxy: str | None = None
    https_proxy: str | None = None
    timeout_s: float = DEFAULT_TIMEOUT_S
    output_cap_bytes: int = DEFAULT_OUTPUT_CAP_BYTES
    headless: bool = True


PlaywrightFactory = Callable[[], AbstractAsyncContextManager[Any]]


def _default_playwright_factory() -> AbstractAsyncContextManager[Any]:
    """Lazily import Playwright async API so unit tests need no browser binary."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - covered via runtime env checks
        raise BrowserServerError(
            ErrorCode.UPSTREAM_ERROR,
            "Playwright runtime is not installed in this environment.",
        ) from exc
    return async_playwright()


def _has_control_or_space(value: str) -> bool:
    """Return True if ``value`` contains ASCII control characters or whitespace."""
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F or ch.isspace() for ch in value)


def _validate_args(tool: str, raw_args: dict[str, Any]) -> dict[str, Any]:
    """Validate arguments strictly against gateway contract schemas."""
    try:
        validated = validate_tool_arguments(tool, raw_args)
    except ArgumentValidationError as exc:
        raise BrowserServerError(exc.code, exc.message) from exc
    return validated.args


def validate_browser_url(url: str) -> str:
    """Validate an ``http``/``https`` URL without credentials or control chars."""
    validated = _validate_args("browser.open", {"url": url})
    candidate: str = validated["url"]

    if _has_control_or_space(candidate):
        raise BrowserServerError(
            ErrorCode.ARG_INVALID,
            "URL must not contain whitespace or control characters.",
        )

    try:
        parsed = urlparse(candidate)
    except ValueError as exc:
        raise BrowserServerError(
            ErrorCode.ARG_INVALID,
            f"Malformed URL: {exc}",
        ) from exc

    if parsed.scheme not in ("http", "https"):
        raise BrowserServerError(
            ErrorCode.ARG_INVALID,
            "URL scheme must be http or https.",
        )

    if (
        parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
    ):
        raise BrowserServerError(
            ErrorCode.ARG_INVALID,
            "URL must not contain embedded credentials.",
        )

    if not parsed.hostname:
        raise BrowserServerError(
            ErrorCode.ARG_INVALID,
            "URL must specify a valid hostname.",
        )

    try:
        port = parsed.port
    except ValueError as exc:
        raise BrowserServerError(
            ErrorCode.ARG_INVALID,
            f"URL contains an invalid port: {exc}",
        ) from exc
    if port is not None and not (1 <= port <= 65535):
        raise BrowserServerError(
            ErrorCode.ARG_INVALID,
            "URL port must be between 1 and 65535.",
        )

    return candidate


def _validate_navigation_target(url: str, *, context_label: str) -> None:
    """Validate an HTTP(S) redirect or navigated URL using strict URL rules."""
    try:
        validate_browser_url(url)
    except BrowserServerError as exc:
        raise BrowserServerError(
            ErrorCode.ARG_INVALID,
            f"Blocked unsafe {context_label} target: {exc.message}",
        ) from exc


def _validate_proxy_url(raw: str, *, var_name: str) -> str:
    """Validate a configured HTTP/HTTPS proxy URL and return normalized form."""
    if not isinstance(raw, str) or not raw:
        raise BrowserServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"{var_name} proxy configuration is empty or invalid.",
        )
    if _has_control_or_space(raw):
        raise BrowserServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"{var_name} contains whitespace or control characters.",
        )
    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise BrowserServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"{var_name} is malformed: {exc}",
        ) from exc

    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise BrowserServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"{var_name} must be a valid http or https proxy URL.",
        )
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.params:
        raise BrowserServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"{var_name} must not include path, query, or fragment components.",
        )
    if (
        parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
    ):
        raise BrowserServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"{var_name} must not embed credentials.",
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise BrowserServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"{var_name} contains an invalid port: {exc}",
        ) from exc
    if port is not None and not (1 <= port <= 65535):
        raise BrowserServerError(
            ErrorCode.UPSTREAM_ERROR,
            f"{var_name} port must be between 1 and 65535.",
        )

    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port_suffix = f":{port}" if port is not None else ""
    return f"{parsed.scheme}://{host}{port_suffix}"


def _read_proxy_var(configured: str | None, upper: str, lower: str) -> str | None:
    """Read proxy setting from explicit config or environment without masking ''."""
    if configured is not None:
        return configured
    if upper in os.environ:
        return os.environ[upper]
    if lower in os.environ:
        return os.environ[lower]
    return None


def resolve_proxy_url(config: BrowserServerConfig) -> str:
    """Resolve and validate the mandatory session egress proxy URL (fail closed)."""
    raw_http = _read_proxy_var(config.http_proxy, "HTTP_PROXY", "http_proxy")
    raw_https = _read_proxy_var(config.https_proxy, "HTTPS_PROXY", "https_proxy")

    if raw_http is None and raw_https is None:
        raise BrowserServerError(
            ErrorCode.UPSTREAM_ERROR,
            "Session egress proxy (HTTP_PROXY / HTTPS_PROXY) is not configured.",
        )

    norm_http = (
        _validate_proxy_url(raw_http, var_name="HTTP_PROXY")
        if raw_http is not None
        else None
    )
    norm_https = (
        _validate_proxy_url(raw_https, var_name="HTTPS_PROXY")
        if raw_https is not None
        else None
    )

    if norm_http is not None and norm_https is not None and norm_http != norm_https:
        raise BrowserServerError(
            ErrorCode.UPSTREAM_ERROR,
            "HTTP_PROXY and HTTPS_PROXY point to different servers.",
        )

    chosen = norm_https if norm_https is not None else norm_http
    assert chosen is not None
    return chosen


def _sanitized_browser_env(proxy_url: str) -> dict[str, str]:
    """Build Chromium subprocess environment without proxy bypass rules."""
    env = {k: v for k, v in os.environ.items() if k.lower() != "no_proxy"}
    env["HTTP_PROXY"] = proxy_url
    env["HTTPS_PROXY"] = proxy_url
    return env


def _is_timeout_error(exc: Exception) -> bool:
    """Return True if ``exc`` is a Python or Playwright timeout error."""
    return isinstance(exc, TimeoutError) or type(exc).__name__.endswith("TimeoutError")


def _json_byte_len(payload: dict[str, Any]) -> int:
    """Compute exact UTF-8 byte size of a serialized tool result dict."""
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _cap_string_for_json_payload(
    value: str,
    *,
    build_payload: Callable[[str], dict[str, Any]],
    cap_bytes: int,
) -> str:
    """Truncate ``value`` cleanly so ``build_payload(value)`` fits ``cap_bytes``."""
    if cap_bytes <= 0:
        return ""
    if _json_byte_len(build_payload(value)) <= cap_bytes:
        return value

    # Binary search over character length handles both multi-byte UTF-8 and JSON
    # escape expansion (e.g., newlines/quotes) strictly within cap_bytes.
    lo = 0
    hi = len(value)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = value[:mid]
        if _json_byte_len(build_payload(candidate)) <= cap_bytes:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _collect_redirect_urls(response: Any) -> list[str]:
    """Extract all URLs in the Playwright redirect chain leading to ``response``."""
    urls: list[str] = []
    seen_ids: set[int] = set()
    req = getattr(response, "request", None)
    while req is not None and id(req) not in seen_ids:
        seen_ids.add(id(req))
        req_url = getattr(req, "url", None)
        if isinstance(req_url, str) and req_url:
            urls.append(req_url)
        req = getattr(req, "redirected_from", None)
    return urls


class BrowserSession:
    """Manages one Playwright Chromium non-persistent context and active page."""

    def __init__(
        self,
        config: BrowserServerConfig | None = None,
        *,
        playwright_factory: PlaywrightFactory = _default_playwright_factory,
    ) -> None:
        self.config = config or BrowserServerConfig(
            timeout_s=float(
                os.environ.get("OVERWATCH_BROWSER_TIMEOUT_S", str(DEFAULT_TIMEOUT_S))
            ),
            output_cap_bytes=int(
                os.environ.get(
                    "OVERWATCH_OUTPUT_CAP_BYTES", str(DEFAULT_OUTPUT_CAP_BYTES)
                )
            ),
        )
        self._playwright_factory = playwright_factory
        self._pw_cm: AbstractAsyncContextManager[Any] | None = None
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._configured_proxy: str | None = None

    def _timeout_ms(self) -> int:
        val = float(self.config.timeout_s)
        if not math.isfinite(val) or val <= 0:
            raise BrowserServerError(
                ErrorCode.ARG_INVALID,
                "Configured timeout_s must be finite and greater than 0.",
            )
        return max(1, int(val * 1000))

    async def _close_page_only(self) -> None:
        page = self._page
        self._page = None
        if page is not None:
            close_fn = getattr(page, "close", None)
            if callable(close_fn):
                try:
                    await close_fn()
                except Exception:
                    pass

    async def aclose(self) -> None:
        """Close page, non-persistent context, Chromium browser, and Playwright."""
        await self._close_page_only()

        context = self._context
        self._context = None
        if context is not None:
            close_ctx = getattr(context, "close", None)
            if callable(close_ctx):
                try:
                    await close_ctx()
                except Exception:
                    pass

        browser = self._browser
        self._browser = None
        if browser is not None:
            close_browser = getattr(browser, "close", None)
            if callable(close_browser):
                try:
                    await close_browser()
                except Exception:
                    pass

        pw_cm = self._pw_cm
        self._pw_cm = None
        playwright = self._playwright
        self._playwright = None
        self._configured_proxy = None

        if pw_cm is not None:
            try:
                await pw_cm.__aexit__(None, None, None)
            except Exception:
                pass
        elif playwright is not None:
            stop_fn = getattr(playwright, "stop", None)
            if callable(stop_fn):
                try:
                    await stop_fn()
                except Exception:
                    pass

    async def _attach_navigation_guard(self, page: Any) -> None:
        """Install a navigation route guard to block unsafe redirect schemes."""
        route_fn = getattr(page, "route", None)
        if not callable(route_fn):
            return

        async def _guard(route: Any) -> None:
            req = getattr(route, "request", None)
            is_nav = getattr(req, "is_navigation_request", None)
            nav_request = bool(is_nav()) if callable(is_nav) else True
            req_url = getattr(req, "url", None)
            if nav_request and isinstance(req_url, str) and req_url != "about:blank":
                try:
                    validate_browser_url(req_url)
                except BrowserServerError:
                    await route.abort("blockedbyclient")
                    return
            await route.continue_()

        await route_fn("**/*", _guard)

    async def _ensure_page(self) -> Any:
        """Lazily launch Playwright Chromium with proxy and return active page."""
        proxy_url = resolve_proxy_url(self.config)
        if self._configured_proxy is not None and self._configured_proxy != proxy_url:
            await self.aclose()

        timeout_ms = self._timeout_ms()

        try:
            if self._playwright is None:
                pw_cm = self._playwright_factory()
                self._pw_cm = pw_cm
                self._playwright = await pw_cm.__aenter__()

            if self._browser is None:
                # Non-sandbox + disable-dev-shm-usage allow Chromium to run safely
                # inside OverWatch's read-only, cap-dropped session container.
                self._browser = await self._playwright.chromium.launch(
                    headless=self.config.headless,
                    proxy={"server": proxy_url},
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                    env=_sanitized_browser_env(proxy_url),
                    timeout=timeout_ms,
                )

            if self._context is None:
                # Explicitly create an ephemeral (non-persistent) browser context
                # and preserve strict TLS verification.
                self._context = await self._browser.new_context(
                    proxy={"server": proxy_url},
                    ignore_https_errors=False,
                )
                set_ctx_timeout = getattr(self._context, "set_default_timeout", None)
                if callable(set_ctx_timeout):
                    set_ctx_timeout(timeout_ms)

            if self._page is None:
                self._page = await self._context.new_page()
                set_timeout = getattr(self._page, "set_default_timeout", None)
                if callable(set_timeout):
                    set_timeout(timeout_ms)
                set_nav_timeout = getattr(
                    self._page, "set_default_navigation_timeout", None
                )
                if callable(set_nav_timeout):
                    set_nav_timeout(timeout_ms)
                await self._attach_navigation_guard(self._page)

            self._configured_proxy = proxy_url
            return self._page
        except BrowserServerError:
            await self.aclose()
            raise
        except Exception as exc:
            await self.aclose()
            if _is_timeout_error(exc):
                raise BrowserServerError(
                    ErrorCode.TIMEOUT,
                    f"Browser startup timed out after {self.config.timeout_s}s.",
                ) from exc
            raise BrowserServerError(
                ErrorCode.UPSTREAM_ERROR,
                f"Failed to initialize browser session: {exc}",
            ) from exc

    async def open_url(self, url: str) -> BrowserOpenResult:
        """Navigate to ``url`` inside the non-persistent context."""
        target_url = validate_browser_url(url)
        resolve_proxy_url(self.config)
        page = await self._ensure_page()
        timeout_ms = self._timeout_ms()

        try:
            response = await page.goto(
                target_url, timeout=timeout_ms, wait_until="load"
            )
        except BrowserServerError:
            await self._close_page_only()
            raise
        except Exception as exc:
            await self._close_page_only()
            if _is_timeout_error(exc):
                raise BrowserServerError(
                    ErrorCode.TIMEOUT,
                    f"Navigation timed out after {self.config.timeout_s}s.",
                ) from exc
            raise BrowserServerError(
                ErrorCode.UPSTREAM_ERROR,
                f"Browser navigation failed: {exc}",
            ) from exc

        if response is None:
            await self._close_page_only()
            raise BrowserServerError(
                ErrorCode.UPSTREAM_ERROR,
                "Navigation did not produce an HTTP response.",
            )

        for chain_url in _collect_redirect_urls(response):
            try:
                _validate_navigation_target(chain_url, context_label="redirect")
            except BrowserServerError:
                await self._close_page_only()
                raise

        resp_url = getattr(response, "url", None)
        if isinstance(resp_url, str) and resp_url:
            try:
                _validate_navigation_target(resp_url, context_label="redirect")
            except BrowserServerError:
                await self._close_page_only()
                raise

        page_url = getattr(page, "url", None)
        if isinstance(page_url, str) and page_url and page_url != "about:blank":
            try:
                _validate_navigation_target(page_url, context_label="navigation")
            except BrowserServerError:
                await self._close_page_only()
                raise

        raw_status = getattr(response, "status", None)
        if isinstance(raw_status, bool) or not isinstance(raw_status, int):
            await self._close_page_only()
            raise BrowserServerError(
                ErrorCode.UPSTREAM_ERROR,
                f"Browser returned invalid HTTP status: {raw_status!r}.",
            )

        try:
            raw_title = await page.title()
        except Exception as exc:
            if _is_timeout_error(exc):
                raise BrowserServerError(
                    ErrorCode.TIMEOUT,
                    f"Reading page title timed out after {self.config.timeout_s}s.",
                ) from exc
            raise BrowserServerError(
                ErrorCode.UPSTREAM_ERROR,
                f"Failed to read page title: {exc}",
            ) from exc

        title_str = raw_title if isinstance(raw_title, str) else ""
        capped_title = _cap_string_for_json_payload(
            title_str,
            build_payload=lambda candidate: {
                "title": candidate,
                "status": raw_status,
            },
            cap_bytes=self.config.output_cap_bytes,
        )
        return BrowserOpenResult(title=capped_title, status=raw_status)

    async def read_page(self, *, max_chars: int | None = None) -> BrowserReadPageResult:
        """Extract visible body text from the active page within caps."""
        raw_args: dict[str, Any] = {}
        if max_chars is not None:
            raw_args["max_chars"] = max_chars
        validated = _validate_args("browser.read_page", raw_args)

        if self._page is None:
            raise BrowserServerError(
                ErrorCode.UPSTREAM_ERROR,
                "No page is currently open; call browser.open first.",
            )

        timeout_ms = self._timeout_ms()
        try:
            raw_text = await self._page.inner_text("body", timeout=timeout_ms)
        except Exception as exc:
            if _is_timeout_error(exc):
                raise BrowserServerError(
                    ErrorCode.TIMEOUT,
                    f"Reading page text timed out after {self.config.timeout_s}s.",
                ) from exc
            raise BrowserServerError(
                ErrorCode.UPSTREAM_ERROR,
                f"Failed to read page content: {exc}",
            ) from exc

        if not isinstance(raw_text, str):
            raise BrowserServerError(
                ErrorCode.UPSTREAM_ERROR,
                "Browser returned non-string page content.",
            )

        effective_text = raw_text
        if "max_chars" in validated:
            effective_text = effective_text[: int(validated["max_chars"])]

        capped_text = _cap_string_for_json_payload(
            effective_text,
            build_payload=lambda candidate: {"text": candidate},
            cap_bytes=self.config.output_cap_bytes,
        )
        return BrowserReadPageResult(text=capped_text)

    async def screenshot(self) -> BrowserScreenshotResult:
        """Capture a screenshot of the active page within ``output_cap_bytes``."""
        _validate_args("browser.screenshot", {})

        if self._page is None:
            raise BrowserServerError(
                ErrorCode.UPSTREAM_ERROR,
                "No page is currently open; call browser.open first.",
            )

        timeout_ms = self._timeout_ms()
        cap_bytes = max(0, int(self.config.output_cap_bytes))

        async def _capture(
            *, img_type: str, quality: int | None = None
        ) -> tuple[str, int]:
            kwargs: dict[str, Any] = {
                "type": img_type,
                "full_page": False,
                "timeout": timeout_ms,
            }
            if quality is not None:
                kwargs["quality"] = quality
            try:
                raw_img = await self._page.screenshot(**kwargs)
            except Exception as exc:
                if _is_timeout_error(exc):
                    raise BrowserServerError(
                        ErrorCode.TIMEOUT,
                        (
                            "Browser screenshot timed out after "
                            f"{self.config.timeout_s}s."
                        ),
                    ) from exc
                raise BrowserServerError(
                    ErrorCode.UPSTREAM_ERROR,
                    f"Browser screenshot failed: {exc}",
                ) from exc

            if not isinstance(raw_img, (bytes, bytearray)) or not raw_img:
                raise BrowserServerError(
                    ErrorCode.UPSTREAM_ERROR,
                    "Browser screenshot did not return valid image bytes.",
                )
            encoded = base64.b64encode(bytes(raw_img)).decode("ascii")
            payload_bytes = _json_byte_len({"image_b64": encoded})
            return encoded, payload_bytes

        image_b64, payload_bytes = await _capture(img_type="png")
        if cap_bytes > 0 and payload_bytes > cap_bytes:
            for quality in _JPEG_QUALITY_FALLBACKS:
                image_b64, payload_bytes = await _capture(
                    img_type="jpeg", quality=quality
                )
                if payload_bytes <= cap_bytes:
                    break

        if cap_bytes > 0 and payload_bytes > cap_bytes:
            raise BrowserServerError(
                ErrorCode.UPSTREAM_ERROR,
                f"Screenshot payload ({payload_bytes} bytes) exceeds output cap "
                f"({cap_bytes} bytes).",
            )

        return BrowserScreenshotResult(image_b64=image_b64)


async def execute_browser_open(
    *,
    url: str,
    session: BrowserSession,
) -> BrowserOpenResult:
    """Execute ``browser.open`` against a ``BrowserSession``."""
    return await session.open_url(url)


async def execute_browser_read_page(
    *,
    max_chars: int | None = None,
    session: BrowserSession,
) -> BrowserReadPageResult:
    """Execute ``browser.read_page`` against a ``BrowserSession``."""
    return await session.read_page(max_chars=max_chars)


async def execute_browser_screenshot(
    *,
    session: BrowserSession,
) -> BrowserScreenshotResult:
    """Execute ``browser.screenshot`` against a ``BrowserSession``."""
    return await session.screenshot()


def _make_lifespan(
    session: BrowserSession,
) -> Callable[[MCPServer[Any]], AbstractAsyncContextManager[None]]:
    """Build an MCPServer lifespan hook that cleans up Playwright on exit."""

    @asynccontextmanager
    async def _lifespan(_server: MCPServer[Any]) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await session.aclose()

    return _lifespan


def create_browser_server(
    config: BrowserServerConfig | None = None,
    *,
    playwright_factory: PlaywrightFactory = _default_playwright_factory,
    session: BrowserSession | None = None,
) -> MCPServer:
    """Build and return an ``MCPServer`` exposing the three browser tools."""
    active_session = session or BrowserSession(
        config=config,
        playwright_factory=playwright_factory,
    )

    server = MCPServer(
        name="browser",
        instructions=(
            "Non-persistent Playwright Chromium browser inside session container."
        ),
        lifespan=_make_lifespan(active_session),
    )

    async def _call(coro: Awaitable[BaseModel]) -> dict[str, Any]:
        try:
            result = await coro
        except BrowserServerError as exc:
            raise ToolError(str(exc)) from exc
        return result.model_dump()

    @server.tool(
        name="browser.open",
        description="Open an http/https URL in a non-persistent Chromium context.",
        structured_output=True,
    )
    async def browser_open(url: str) -> dict[str, Any]:
        """Navigate to ``url`` and return page title and HTTP status."""
        return await _call(execute_browser_open(url=url, session=active_session))

    @server.tool(
        name="browser.read_page",
        description="Read visible text from the active browser page.",
        structured_output=True,
    )
    async def browser_read_page(max_chars: int | None = None) -> dict[str, Any]:
        """Return visible body text from the active browser page."""
        return await _call(
            execute_browser_read_page(max_chars=max_chars, session=active_session)
        )

    @server.tool(
        name="browser.screenshot",
        description="Capture a base64 screenshot of the active browser page.",
        structured_output=True,
    )
    async def browser_screenshot() -> dict[str, Any]:
        """Capture a screenshot of the active browser page."""
        return await _call(execute_browser_screenshot(session=active_session))

    return server


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entry point running the browser MCP server over stdio transport."""
    parser = argparse.ArgumentParser(
        description="OverWatch session-scoped Playwright browser MCP server."
    )
    parser.add_argument(
        "--headless",
        dest="headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run Chromium headless (default: true).",
    )
    args = parser.parse_args(argv)
    cfg = BrowserServerConfig(
        timeout_s=float(
            os.environ.get("OVERWATCH_BROWSER_TIMEOUT_S", str(DEFAULT_TIMEOUT_S))
        ),
        output_cap_bytes=int(
            os.environ.get("OVERWATCH_OUTPUT_CAP_BYTES", str(DEFAULT_OUTPUT_CAP_BYTES))
        ),
        headless=bool(args.headless),
    )
    create_browser_server(config=cfg).run(transport="stdio")


if __name__ == "__main__":
    main()
