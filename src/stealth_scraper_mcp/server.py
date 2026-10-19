import argparse
import asyncio
import hashlib
import hmac
import subprocess
import sys
import time
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Annotated, Literal
from urllib.parse import urlsplit

from cachetools import LRUCache
from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from . import config, plugins
from . import extract
from . import rank
from .fetchers import BLOCK_STATUS, BrowserPool, FetchError, FetchResult, fetch_http
from .safety import BlockedURLError

# 1. Initialize FastMCP instance. Claude Code's tool search matches on these instructions
# (truncated at 4,096 chars), so the keywords an agent would search for come first.
mcp = FastMCP(
    "stealth-web-scraper",
    instructions=(
        "Web scraping, page fetching and reading: fetch any URL (articles, docs, product pages, "
        "JavaScript apps, bot-protected sites) and get clean Markdown, text, HTML or links. "
        "Use scrape_url_stealth whenever you need the contents of a web page. Pass query to get "
        "only the passages relevant to a question instead of the whole page; this saves the most "
        "tokens. Long results are paginated: pass next_offset back as offset."
    ),
)

browser_pool = BrowserPool()
extensions = plugins.load()

# Raw fetch results keyed by fetch options; extraction is cheap and redone per call.
# Entries carry their fetch time so each call can apply its own max_age_seconds.
FETCH_CACHE: LRUCache = LRUCache(maxsize=config.CACHE_MAX_ENTRIES)

TEXT_CONTENT_TYPES = ("text/plain", "application/json", "text/csv", "text/markdown", "application/xml", "text/xml")
THIN_CONTENT_CHARS = 300
APP_SHELL_MARKERS = ('id="root"', 'id="app"', 'id="__next"', "ng-app", "data-reactroot", "<noscript>")
PASSAGE_SEPARATOR = "\n\n[…]\n\n"


class ScrapeResult(BaseModel):
    final_url: str = Field(description="URL after redirects")
    status_code: int = Field(description="HTTP status code of the final page")
    title: str | None = Field(default=None, description="Page title, when detected")
    content: str = Field(description="The requested window of extracted content")
    total_chars: int = Field(description="Length of the full (or query-filtered) content")
    next_offset: int | None = Field(default=None, description="Pass as offset to read more; null when nothing remains")
    est_tokens: int = Field(description="Approximate tokens in content (chars / 4)")
    cache: Literal["hit", "miss", "refreshed"] = Field(description="Whether the page came from the local cache")
    hint: str | None = Field(default=None, description="How to narrow or continue the request, when relevant")
    # Fields below are only filled when response_format="detailed".
    fetch_method: Literal["http", "browser"] | None = Field(default=None, description="Fast HTTP client or headless browser")
    engine: str | None = Field(default=None, description="Fingerprint or browser that served the page")
    escalation: str | None = Field(default=None, description="Which cheaper tiers were tried first and why they failed")
    metadata: dict | None = Field(default=None, description="Description, author, site name, date and language")
    passages_matched: int | None = Field(default=None, description="Number of passages kept by query")
    content_hash: str | None = Field(default=None, description="SHA-256 of the full extracted content")
    cache_age_seconds: int | None = Field(default=None, description="Age of the fetch this result was built from")
    proxy_bytes: int | None = Field(default=None, description="Residential proxy bandwidth used by this fetch")
    cost_usd: float | None = Field(default=None, description="Amount charged for this call; failures are free")
    credits_remaining_usd: float | None = Field(default=None, description="Balance left on this API key")


def compute_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tool_error(code: str, message: str, hint: str = "") -> ToolError:
    return ToolError(f"[{code}] {message}" + (f" Hint: {hint}" if hint else ""))


async def _is_thin(result: FetchResult) -> bool:
    if "html" not in result.content_type and result.content_type:
        return False
    # Short static pages are fine as-is; only script-driven pages benefit from rendering.
    lowered = result.html.lower()
    if lowered.count("<script") < 2 and not any(marker in lowered for marker in APP_SHELL_MARKERS):
        return False
    text = await asyncio.to_thread(extract.convert, result.html, result.final_url, "text", False)
    return len(text) < THIN_CONTENT_CHARS


async def _fetch(
    url: str,
    render_js: str,
    impersonate: str,
    wait_for_selector: str | None,
    scroll: bool,
    proxy: str,
) -> tuple[FetchResult, str | None]:
    """Try each fetch tier in order of cost and return the first usable result.

    Tiers, cheapest first: direct HTTP, browser, HTTP via proxy, browser via proxy. Proxy
    tiers only run after a block, and HTTP tiers stop once the page is known to need JavaScript.
    Returns the result and a note on how it escalated, if it did.
    """
    if proxy == "always" and not config.PROXY_URL:
        raise FetchError("no_proxy", "This server has no proxy configured.", "Use proxy='auto' or 'never'.")
    if proxy == "never" or not config.PROXY_URL:
        routes = [None]
    elif proxy == "always":
        routes = [config.PROXY_URL]
    else:
        routes = [None, config.PROXY_URL]

    needs_browser = render_js == "always" or (render_js == "auto" and (wait_for_selector or scroll))
    kinds = ["browser"] if needs_browser else ["http"] if render_js == "never" else ["http", "browser"]
    # Cheapest first: http, browser, http via proxy, browser via proxy.
    tiers = [(kind, route) for route in routes for kind in kinds]

    notes: list[str] = []
    proxy_bytes = 0
    fallback: FetchResult | None = None
    last_error: FetchError | None = None
    needs_js = False
    for kind, route in tiers:
        if kind == "http" and needs_js:
            continue  # a proxy cannot fix a page that only renders with JavaScript
        label = kind + (" via proxy" if route else "")
        try:
            if kind == "http":
                result = await fetch_http(url, impersonate=impersonate, proxy=route)
            else:
                result = await browser_pool.fetch(url, wait_for_selector=wait_for_selector, scroll=scroll, proxy=route)
        except FetchError as exc:
            if exc.code == "too_large":
                raise
            last_error = exc
            notes.append(f"{label} failed ({exc.code})")
            continue
        if route:
            proxy_bytes += result.transfer_bytes
            result.proxy_bytes = proxy_bytes

        if result.blocked or result.status_code in BLOCK_STATUS:
            notes.append(f"{label} blocked (status {result.status_code})")
            fallback = fallback or result
            continue
        if kind == "http" and "browser" in kinds and await _is_thin(result):
            notes.append(f"{label} returned almost no text; page likely needs JavaScript")
            needs_js = True
            fallback = result
            continue
        return result, "; ".join(notes) or None

    if fallback is not None:
        fallback.proxy_bytes = proxy_bytes
        return fallback, "; ".join(notes) or None
    raise last_error or FetchError("connection_error", f"Could not fetch {url}.")


# 2. Define the primary stealth scraping tool
@mcp.tool(
    annotations={
        "title": "Scrape URL",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    meta={
        # Load upfront under Claude Code tool search, and allow results above its 50k-char file spill.
        "anthropic/alwaysLoad": True,
        "anthropic/maxResultSizeChars": 120_000,
    },
)
async def scrape_url_stealth(
    url: Annotated[str, Field(description="Absolute http(s) URL to fetch")],
    query: Annotated[
        str | None,
        Field(description="Question or keywords; returns only the most relevant passages instead of the whole page. Strongly recommended for long pages."),
    ] = None,
    top_k: Annotated[int, Field(ge=1, le=30, description="With query: number of passages to return")] = 5,
    output_format: Annotated[
        Literal["markdown", "text", "html", "links"],
        Field(description="markdown (default): main content; text: plain text; html: cleaned HTML; links: every link on the page"),
    ] = "markdown",
    response_format: Annotated[
        Literal["concise", "detailed"],
        Field(description="concise (default) omits fetch diagnostics and page metadata; detailed includes them"),
    ] = "concise",
    max_chars: Annotated[int, Field(ge=500, le=100_000, description="Maximum characters of content to return (about 4 chars per token)")] = 15_000,
    offset: Annotated[int, Field(ge=0, description="Character offset to start from; use next_offset from a previous call")] = 0,
    max_age_seconds: Annotated[
        int | None,
        Field(ge=-1, description="Accept a cached copy up to this old. 0 always fetches fresh; -1 uses only the cache. Default: server setting (1 hour)."),
    ] = None,
    render_js: Annotated[
        Literal["auto", "always", "never"],
        Field(description="auto (default) uses a headless browser only when the fast fetch is blocked or nearly empty"),
    ] = "auto",
    css_selector: Annotated[
        str | None, Field(description="Only return elements matching this CSS selector, e.g. 'article' or '#pricing table'")
    ] = None,
    wait_for_selector: Annotated[
        str | None, Field(description="Browser only: wait until this CSS selector appears before reading the page")
    ] = None,
    scroll: Annotated[bool, Field(description="Browser only: scroll down to trigger lazy-loaded content")] = False,
    include_links: Annotated[bool, Field(description="Keep hyperlinks in markdown output; false saves tokens")] = True,
    proxy: Annotated[
        Literal["auto", "never", "always"],
        Field(description="auto (default) retries through the residential proxy only when blocked, since proxy bandwidth is paid. never avoids it; always skips the direct attempt"),
    ] = "auto",
    impersonate_browser: Annotated[
        Literal["chrome", "safari", "firefox", "edge"], Field(description="TLS fingerprint to present first; others are tried on retry")
    ] = "chrome",
) -> ScrapeResult:
    """Fetch a web page and return its main content as clean Markdown.

    Pass query to get only the passages that answer a question. Handles JavaScript-rendered
    and bot-protected pages by falling back to a real Chrome browser. Results are cached;
    long pages are paginated with offset/next_offset.
    """
    reservation = extensions.metering.reserve()
    result: FetchResult | None = None
    cache_state = ""
    try:
        cache_key = (url, render_js, impersonate_browser, wait_for_selector, scroll)
        max_age = config.CACHE_TTL_SECONDS if max_age_seconds is None else max_age_seconds
        entry = FETCH_CACHE.get(cache_key)
        fresh_enough = entry is not None and max_age != 0 and (max_age < 0 or time.time() - entry[1] <= max_age)

        if fresh_enough:
            result, fetched_at, escalation = entry
            cache_state = "hit"
        elif max_age < 0:
            raise tool_error("cache_miss", f"{url} is not in the cache.", "Use max_age_seconds >= 0 to fetch it.")
        else:
            try:
                result, escalation = await _fetch(url, render_js, impersonate_browser, wait_for_selector, scroll, proxy)
            except BlockedURLError as exc:
                raise tool_error("url_not_allowed", str(exc)) from exc
            except FetchError as exc:
                raise tool_error(exc.code, str(exc), exc.hint) from exc
            fetched_at = time.time()
            cache_state = "refreshed" if entry else "miss"

        if result.blocked:
            raise tool_error(
                "blocked",
                f"{result.final_url} returned a bot-protection challenge (status {result.status_code}).",
                "Try a different impersonate_browser, or proxy='always' if this server has a residential proxy." if config.PROXY_URL else "Try a different impersonate_browser or try again later; this server has no proxy configured.",
            )
        if result.status_code < 400 and cache_state != "hit":
            FETCH_CACHE[cache_key] = (result, fetched_at, escalation)

        content_type = result.content_type.split(";")[0].strip().lower()
        page_meta: dict = {}
        if content_type.startswith(TEXT_CONTENT_TYPES):
            content = result.html
        elif content_type and "html" not in content_type:
            raise tool_error(
                "unsupported_content_type",
                f"{result.final_url} returned {content_type}, which this tool cannot convert yet.",
            )
        else:
            html = result.html
            if css_selector:
                fragment = await asyncio.to_thread(extract.select_fragment, html, css_selector)
                if fragment is None:
                    raise tool_error(
                        "selector_not_found",
                        f"No elements match '{css_selector}'.",
                        "Fetch without css_selector first, or use render_js='always' if the element is added by JavaScript.",
                    )
            content = await asyncio.to_thread(
                extract.convert,
                fragment if css_selector else html,
                result.final_url,
                output_format,
                include_links,
                bool(css_selector),
            )
            page_meta = await asyncio.to_thread(extract.metadata, html, result.final_url)

        full_hash = compute_hash(content)
        passages_matched = None
        hint = None
        if query and output_format != "links":
            passages = await asyncio.to_thread(rank.top_passages, content, query, top_k)
            passages_matched = len(passages)
            if passages:
                content = PASSAGE_SEPARATOR.join(passages)
            else:
                hint = f"No passages matched query. Retry with different keywords, or omit query to read the page ({len(content)} chars)."
                content = ""

        window, next_offset = extract.paginate(content, offset, max_chars)
        if next_offset is not None:
            hint = f"Truncated: {len(content) - next_offset} chars remain. Pass offset={next_offset} to continue" + (
                "." if query else ", or pass query to get only relevant passages."
            )

        detailed = response_format == "detailed"
        response = ScrapeResult(
            final_url=result.final_url,
            status_code=result.status_code,
            title=page_meta.pop("title", None),
            content=window,
            total_chars=len(content),
            next_offset=next_offset,
            est_tokens=len(window) // 4,
            cache=cache_state,
            hint=hint,
            fetch_method=result.method if detailed else None,
            engine=result.engine if detailed else None,
            escalation=escalation if detailed else None,
            metadata=(page_meta or None) if detailed else None,
            passages_matched=passages_matched if detailed else None,
            content_hash=full_hash if detailed else None,
        proxy_bytes=(result.proxy_bytes or None) if detailed and cache_state != "hit" else None,
            cache_age_seconds=int(time.time() - fetched_at) if detailed else None,
        )
    except BaseException:
        extensions.metering.settle(reservation, None, "", result.proxy_bytes if result and cache_state != "hit" else 0)
        raise

    if result.status_code >= 400:
        tier = None
    elif cache_state == "hit":
        tier = "cache"
    else:
        tier = result.method + ("_proxy" if result.proxied else "")
    proxy_bytes = result.proxy_bytes if cache_state != "hit" else 0
    charge = extensions.metering.settle(reservation, tier, urlsplit(result.final_url).hostname or "", proxy_bytes)
    if charge:
        response.cost_usd = charge.cost_usd
        response.credits_remaining_usd = charge.remaining_usd
    return response


# 3. Initialize mounted transports along with the FastAPI application.
http_transport = mcp.http_app(path="/mcp", transport="streamable-http")
sse_transport = mcp.http_app(path="/sse", transport="sse")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(http_transport.lifespan(app))
        await stack.enter_async_context(sse_transport.lifespan(app))
        stack.push_async_callback(browser_pool.close)
        yield


class APIKeyMiddleware:
    """Require "Authorization: Bearer <SCRAPER_API_KEY>" on everything except /health and plugin public paths."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Plugin pages (e.g. payment callbacks) are reached by browsers that cannot send the key.
        # Only http scopes have a path; lifespan events must pass straight through.
        public = (
            scope["type"] != "http"
            or scope["path"] == "/health"
            or scope["path"].startswith(tuple(extensions.public_path_prefixes))
        )
        # A plugin that authenticates callers itself (per-customer keys) replaces the master key.
        if config.API_KEY and not extensions.handles_auth and not public:
            supplied = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
            if not hmac.compare_digest(supplied, f"Bearer {config.API_KEY}"):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


app = FastAPI(title="Stealth Scraper MCP Server", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(APIKeyMiddleware)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "stealth-web-scraper"}


for router in extensions.routers:
    app.include_router(router)


# Register the legacy SSE routes before the catch-all Streamable HTTP mount.
app.router.routes.extend(sse_transport.routes)
app.mount("/", http_transport)



def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] and argv[0] in extensions.commands:
        return extensions.commands[argv[0]](argv[1:])

    parser = argparse.ArgumentParser(prog="stealth-scraper-mcp", description="Stealth web scraping MCP server.")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio",
                        help="stdio (default) for MCP clients that launch the server; http to serve /mcp over the network")
    parser.add_argument("--host", default=config.HOST, help="HTTP bind address (default: SCRAPER_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, default=config.PORT, help="HTTP port (default: SCRAPER_PORT or 8080)")
    parser.add_argument("--install-browser", action="store_true",
                        help="Download Playwright's Chromium and exit; only needed when Google Chrome is not installed")
    args = parser.parse_args(argv)

    if args.install_browser:
        return subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"])
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        import uvicorn

        loopback = args.host in ("127.0.0.1", "localhost", "::1")
        if not loopback and not (config.API_KEY or extensions.handles_auth or config.ALLOW_OPEN):
            print(
                f"Refusing to listen on {args.host} without authentication. Set SCRAPER_API_KEY, "
                "or SCRAPER_ALLOW_OPEN=true to run an open server on purpose.",
                file=sys.stderr,
            )
            return 2
        if config.API_KEY and extensions.handles_auth:
            print("Note: SCRAPER_API_KEY is ignored because a plugin authenticates callers.", file=sys.stderr)

        uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
