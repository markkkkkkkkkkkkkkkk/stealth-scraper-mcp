"""Page fetchers: a fast curl_cffi HTTP client and a Playwright browser fallback."""

import asyncio
import random
import re
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import UnicodeDammit
from curl_cffi import CurlInfo, CurlOpt
from curl_cffi.requests import AsyncSession
from playwright.async_api import Browser, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from . import config
from .egress import EgressGuard
from .safety import BlockedURLError, ensure_public_url

REDIRECT_CODES = {301, 302, 303, 307, 308}
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
BLOCK_STATUS = {401, 403, 429, 503}
# Challenge pages are small, so markers are only checked on short documents.
CHALLENGE_MARKERS = (
    "cf-chl", "challenge-platform", "just a moment...", "attention required! | cloudflare",
    "px-captcha", "captcha-delivery.com", "datadome", "/_incapsula_resource", "are you a robot",
    "verify you are human", "verifying you are human", "checking your browser", "challenges.cloudflare.com",
    "g-recaptcha", "h-captcha", "opfcaptcha", "/errors/validatecaptcha",
)
IMPERSONATION_ROTATION = ("chrome", "safari", "firefox", "edge")
BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}


class FetchError(Exception):
    """A fetch failure with a machine-readable code and a hint for the calling agent."""

    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code = code
        self.hint = hint


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    html: str
    content_type: str
    method: str  # "http" or "browser"
    engine: str = ""  # e.g. "curl_cffi:safari" or "playwright:chrome"
    proxied: bool = False
    # Bytes on the wire (compressed bodies plus headers), measured for proxy cost tracking.
    transfer_bytes: int = 0
    proxy_bytes: int = 0  # set by the caller: total proxy bandwidth across every tier tried
    blocked: bool = False


def looks_blocked(status_code: int, html: str) -> bool:
    if len(html) > 30_000:
        return False
    lowered = html.lower()
    has_marker = any(marker in lowered for marker in CHALLENGE_MARKERS)
    return has_marker and (status_code in BLOCK_STATUS or len(html) < 15_000)


def _decode(raw: bytes, content_type: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type, re.I)
    hints = [match.group(1)] if match else []
    return UnicodeDammit(raw, hints, is_html=True).unicode_markup or ""


def _backoff(attempt: int, retry_after: str | None = None) -> float:
    if retry_after and retry_after.isdigit():
        return min(float(retry_after), 10.0)
    return min(0.5 * 2 ** attempt, 8.0) + random.uniform(0, 0.4)


async def _http_once(url: str, impersonate: str, timeout: float, proxy: str | None) -> FetchResult:
    current = url
    transferred = 0
    for _ in range(config.MAX_REDIRECTS + 1):
        address = await ensure_public_url(current)
        parts = urlsplit(current)
        # Connect to the address that was checked rather than letting curl resolve again,
        # so a DNS answer that changes in between (rebinding) cannot reach an internal host.
        # Through a proxy, the proxy resolves the name on its own network instead.
        pin = {}
        if proxy is None and parts.hostname != address:
            port = parts.port or (443 if parts.scheme == "https" else 80)
            pinned = f"[{address}]" if ":" in address else address
            pin = {CurlOpt.RESOLVE: [f"{parts.hostname}:{port}:{pinned}"]}
        async with AsyncSession(proxy=proxy, curl_options=pin) as session, session.stream(
            "GET",
            current,
            impersonate=impersonate,
            headers={"Accept-Language": "en-US,en;q=0.9", "Upgrade-Insecure-Requests": "1"},
            timeout=timeout,
            allow_redirects=False,
        ) as response:
            location = response.headers.get("location")
            if response.status_code in REDIRECT_CODES and location:
                transferred += response.curl.getinfo(CurlInfo.HEADER_SIZE)
                current = urljoin(current, location)
                continue
            content_type = response.headers.get("content-type", "")
            chunks, size = [], 0
            async for chunk in response.aiter_content():
                size += len(chunk)
                if size > config.MAX_RESPONSE_BYTES:
                    raise FetchError(
                        "too_large",
                        f"Response exceeded {config.MAX_RESPONSE_BYTES} bytes.",
                        "Target a smaller page, or raise SCRAPER_MAX_RESPONSE_BYTES.",
                    )
                chunks.append(chunk)
            html = _decode(b"".join(chunks), content_type)
            transferred += response.curl.getinfo(CurlInfo.SIZE_DOWNLOAD_T) + response.curl.getinfo(CurlInfo.HEADER_SIZE)
            return FetchResult(
                url=url,
                final_url=current,
                status_code=response.status_code,
                html=html,
                content_type=content_type,
                method="http",
                engine=f"curl_cffi:{impersonate}",
                blocked=looks_blocked(response.status_code, html),
                transfer_bytes=transferred,
            )
    raise FetchError("too_many_redirects", f"More than {config.MAX_REDIRECTS} redirects.")


def playwright_proxy(proxy_url: str) -> dict:
    """Convert http://user:pass@host:port into Playwright's proxy settings."""
    parts = urlsplit(proxy_url)
    settings = {"server": f"{parts.scheme}://{parts.hostname}" + (f":{parts.port}" if parts.port else "")}
    if parts.username:
        settings["username"] = unquote(parts.username)
        settings["password"] = unquote(parts.password or "")
    return settings


async def fetch_http(
    url: str, impersonate: str = "chrome", timeout: float | None = None, proxy: str | None = None
) -> FetchResult:
    """Fetch with TLS impersonation, rotating browser profiles on retryable failures."""
    timeout = timeout or config.FETCH_TIMEOUT_SECONDS
    profiles = [impersonate] + [p for p in IMPERSONATION_ROTATION if p != impersonate]
    last_error: Exception | None = None
    result: FetchResult | None = None
    transferred = 0  # retries through a proxy are billed by the provider too

    for attempt in range(config.MAX_RETRIES + 1):
        profile = profiles[attempt % len(profiles)]
        try:
            result = await _http_once(url, profile, timeout, proxy)
        except (BlockedURLError, FetchError):
            raise
        except Exception as exc:  # network errors from curl
            last_error = exc
            if attempt < config.MAX_RETRIES:
                await asyncio.sleep(_backoff(attempt))
            continue
        result.proxied = proxy is not None
        transferred += result.transfer_bytes
        result.transfer_bytes = transferred
        if result.status_code in RETRYABLE_STATUS or result.blocked:
            if attempt < config.MAX_RETRIES:
                await asyncio.sleep(_backoff(attempt))
                continue
        return result

    if result is not None:
        return result
    raise FetchError(
        "connection_error",
        f"Could not connect to {urlsplit(url).netloc}: {last_error}",
        "Check the URL is reachable; try render_js='always' or a proxy if the site drops bot traffic.",
    )


async def _settled_content(page) -> str:
    """Read the DOM, waiting out client-side redirects (e.g. after a passed challenge)."""
    for _ in range(3):
        try:
            return await page.content()
        except PlaywrightError as exc:
            if "navigating" not in str(exc):
                raise
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10_000)
            except PlaywrightTimeout:
                pass
    return await page.content()


class BrowserPool:
    """A lazily launched, shared headless Chromium with bounded concurrency."""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._user_agent: str | None = None
        self.engine = ""
        self._lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(config.BROWSER_CONCURRENCY)
        self._egress = EgressGuard()

    async def _get_browser(self) -> Browser:
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                await self._egress.start()
                if self._playwright is None:
                    self._playwright = await async_playwright().start()
                options = {
                    "headless": config.BROWSER_HEADLESS,
                    "args": ["--disable-blink-features=AutomationControlled"],
                }
                self._browser = None
                if config.BROWSER_CHANNEL:
                    try:
                        self._browser = await self._playwright.chromium.launch(channel=config.BROWSER_CHANNEL, **options)
                        self.engine = f"playwright:{config.BROWSER_CHANNEL}"
                    except PlaywrightError:
                        pass  # channel not installed; fall back to bundled Chromium
                if self._browser is None:
                    self._browser = await self._playwright.chromium.launch(**options)
                    self.engine = "playwright:chromium"
                page = await self._browser.new_page()
                try:
                    # Headless Chromium advertises itself as "HeadlessChrome"; drop that token.
                    self._user_agent = (await page.evaluate("navigator.userAgent")).replace("HeadlessChrome", "Chrome")
                finally:
                    await page.close()
            return self._browser

    async def close(self) -> None:
        async with self._lock:
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None
            await self._egress.close()

    async def fetch(
        self,
        url: str,
        timeout: float | None = None,
        wait_for_selector: str | None = None,
        scroll: bool = False,
        proxy: str | None = None,
    ) -> FetchResult:
        timeout = timeout or config.FETCH_TIMEOUT_SECONDS
        await ensure_public_url(url)
        async with self._slots:
            browser = await self._get_browser()
            context = await browser.new_context(
                user_agent=self._user_agent,
                locale=config.BROWSER_LOCALE,
                timezone_id=config.BROWSER_TIMEZONE,
                extra_http_headers={"Accept-Language": f"{config.BROWSER_LOCALE},{config.BROWSER_LOCALE.split('-')[0]};q=0.9"},
                viewport={"width": 1366, "height": 900},
                # Direct traffic goes through the egress guard, which checks and pins each
                # connection's address; a residential proxy resolves names on its own network.
                # "<-loopback>" stops Chrome from bypassing the proxy for localhost.
                proxy={**(playwright_proxy(proxy) if proxy else {"server": self._egress.url}), "bypass": "<-loopback>"},
            )
            await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            checked_hosts: dict[str, bool] = {}

            async def guard(route):
                request = route.request
                if request.resource_type in BLOCKED_RESOURCE_TYPES:
                    return await route.abort()
                # Residential bandwidth is billed per GB, and CSS does not change the extracted text.
                if proxy and request.resource_type == "stylesheet":
                    return await route.abort()
                host = urlsplit(request.url).hostname or ""
                if host not in checked_hosts:
                    try:
                        await ensure_public_url(request.url)
                        checked_hosts[host] = True
                    except BlockedURLError:
                        checked_hosts[host] = False
                if not checked_hosts[host] and urlsplit(request.url).scheme in ("http", "https"):
                    return await route.abort("blockedbyclient")
                await route.continue_()

            await context.route("**/*", guard)
            try:
                page = await context.new_page()
                # Track the latest main-document status, since challenges redirect after load.
                last_status = {"code": 200}

                def on_response(resp):
                    if resp.request.is_navigation_request() and resp.frame == page.main_frame:
                        last_status["code"] = resp.status

                page.on("response", on_response)
                size_lookups: list[asyncio.Task] = []
                if proxy:
                    # Count every finished request's wire size; proxy providers bill per byte.
                    page.on("requestfinished", lambda request: size_lookups.append(asyncio.ensure_future(request.sizes())))
                try:
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                except PlaywrightTimeout as exc:
                    raise FetchError("timeout", f"Browser navigation timed out after {timeout}s.",
                                     "Retry later or try render_js='never'.") from exc
                except PlaywrightError as exc:
                    raise FetchError("connection_error", f"Browser navigation failed: {exc.message}") from exc

                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                except PlaywrightTimeout:
                    pass
                # Give interstitial JS challenges a chance to clear on their own.
                challenged = False
                for _ in range(8):
                    if not looks_blocked(last_status["code"], await _settled_content(page)):
                        break
                    challenged = True
                    await page.wait_for_timeout(1000)
                if challenged:
                    # A passed challenge navigates to the real page; let it load before reading.
                    for state in ("load", "networkidle"):
                        try:
                            await page.wait_for_load_state(state, timeout=8000)
                        except PlaywrightTimeout:
                            pass
                if wait_for_selector:
                    try:
                        await page.wait_for_selector(wait_for_selector, timeout=timeout * 1000)
                    except PlaywrightTimeout as exc:
                        raise FetchError("selector_timeout", f"Selector '{wait_for_selector}' did not appear.",
                                         "Check the selector, or omit wait_for_selector.") from exc
                if scroll:
                    for _ in range(10):
                        await page.mouse.wheel(0, 2500)
                        await page.wait_for_timeout(400)

                html = await _settled_content(page)
                status = last_status["code"]
                sizes = [s for s in await asyncio.gather(*size_lookups, return_exceptions=True) if isinstance(s, dict)]
                transferred = sum(
                    s["requestHeadersSize"] + s["requestBodySize"] + s["responseHeadersSize"] + max(s["responseBodySize"], 0)
                    for s in sizes
                )
                return FetchResult(
                    url=url,
                    final_url=page.url,
                    status_code=status,
                    html=html,
                    content_type=(response.headers.get("content-type", "") if response else "text/html"),
                    method="browser",
                    engine=self.engine,
                    proxied=proxy is not None,
                    transfer_bytes=transferred,
                    blocked=looks_blocked(status, html),
                )
            finally:
                await context.close()
