# Stealth Web Scraper MCP

<!-- mcp-name: io.github.markkkkkkkkkkkkkkkk/stealth-scraper-mcp -->

An MCP server that fetches any web page and returns clean, token-efficient Markdown for AI agents.

- **Tiered fetching, cheapest first:** direct HTTP, browser, HTTP via residential proxy, browser via proxy. Each tier runs only when the previous one is blocked.
- **Fast path:** `curl_cffi` with real browser TLS fingerprints (Chrome, Safari, Firefox, Edge), rotated on retry.
- **Browser fallback:** when a page is blocked or needs JavaScript, it is rendered in your installed Google Chrome (or Playwright's Chromium).
- **Fewer tokens:** pass `query` to get only the passages that answer a question, ranked with BM25. Long pages are paginated.
- **Safe by default:** refuses localhost and private-network URLs, binds to `127.0.0.1`, optional API key.

## Benchmark

Measured on 30 real sites (docs, reference, news, shops, JavaScript apps and bot-protected sites), with no proxy. Each site was fetched once. Full results are in [benchmark/RESULTS.md](benchmark/RESULTS.md); re-run with `PYTHONPATH=src python benchmark/run.py`.

| | Result |
|---|---|
| Success rate, no proxy | 24/30 (80%) |
| Tokens vs raw HTML, clean Markdown | 97.9% fewer (median 90,222 → 1,901) |
| Tokens vs raw HTML, with `query` | 99.6% fewer (median 341) |
| Median time | 2.6 s HTTP, 11.4 s browser |

The 6 misses: three sites behind strict bot protection that need a residential proxy (Etsy, G2, Glassdoor), and three image-heavy homepages with little text.

## Install

Claude Code:

```bash
claude mcp add stealth-scraper -- uvx stealth-scraper-mcp
```

Claude Desktop, Cursor, VS Code and other clients (`mcpServers` config):

```json
{
  "mcpServers": {
    "stealth-scraper": {
      "command": "uvx",
      "args": ["stealth-scraper-mcp"]
    }
  }
}
```

The browser fallback uses Google Chrome if it is installed. Otherwise download Chromium once:

```bash
uvx stealth-scraper-mcp --install-browser
```

## Tool: `scrape_url_stealth`

| Parameter | Default | Purpose |
|---|---|---|
| `url` | required | Page to fetch |
| `query` | none | Return only the passages relevant to this question |
| `top_k` | 5 | Passages to return with `query` |
| `output_format` | `markdown` | `markdown`, `text`, `html` or `links` |
| `response_format` | `concise` | `detailed` adds engine, metadata and cache diagnostics |
| `max_chars` / `offset` | 15000 / 0 | Pagination; pass `next_offset` back as `offset` |
| `max_age_seconds` | 1 hour | Cache freshness; `0` forces a fresh fetch, `-1` reads only the cache |
| `render_js` | `auto` | `always` or `never` force or skip the browser |
| `proxy` | `auto` | `auto` uses the residential proxy only after a block; `never` or `always` |
| `css_selector` | none | Only return matching elements |
| `wait_for_selector`, `scroll` | none / false | Browser options for dynamic and lazy-loaded pages |

Errors come back as `[code] message Hint: ...`, e.g. `blocked`, `timeout`, `url_not_allowed`, `selector_not_found`.

## Running over HTTP

```bash
uvx stealth-scraper-mcp --transport http --port 8080
```

The endpoint is `http://127.0.0.1:8080/mcp`, with `/health` for checks. To expose it on a network, set an API key first:

```bash
SCRAPER_API_KEY=change-me SCRAPER_HOST=0.0.0.0 uvx stealth-scraper-mcp --transport http
```

Clients then send `Authorization: Bearer change-me`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SCRAPER_PROXY_URL` | none | Residential proxy, used only after a direct fetch is blocked |
| `SCRAPER_API_KEY` | none | Require a bearer token on HTTP |
| `SCRAPER_HOST` / `SCRAPER_PORT` | `127.0.0.1` / `8080` | HTTP bind address |
| `SCRAPER_CACHE_TTL` | `3600` | Default cache freshness in seconds |
| `SCRAPER_CACHE_MAX_ENTRIES` | `256` | Cache size |
| `SCRAPER_BROWSER_CHANNEL` | `chrome` | Browser to launch; empty for bundled Chromium |
| `SCRAPER_BROWSER_HEADLESS` | `true` | `false` runs a visible, harder-to-detect browser |
| `SCRAPER_BROWSER_LOCALE` / `SCRAPER_BROWSER_TIMEZONE` | `en-US` / system | Keep these consistent with your proxy's location |
| `SCRAPER_ALLOW_PRIVATE_NETWORKS` | `false` | Allow localhost and private IPs |
| `SCRAPER_FETCH_TIMEOUT`, `SCRAPER_MAX_RETRIES` | `20`, `2` | Request timeout and retries |

## Plugins

Packages can extend the server through the `stealth_scraper_mcp.plugins` entry point group. They can add usage metering, extra HTTP routes and CLI commands; see `src/stealth_scraper_mcp/plugins.py`. Without plugins the server is free and unmetered.

## Responsible use

Respect each site's terms of service and robots.txt, and avoid heavy request rates.

## License

[AGPL-3.0-or-later](LICENSE). You can use, modify and self-host this freely. If you offer a modified version to others as a network service, you must publish your changes under the same license.
