"""Check the live MCP server; run from macOS Terminal using the project venv."""

import asyncio
import os
import sys
import traceback
from pathlib import Path

import httpx2
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

URL = "http://127.0.0.1:8080/mcp"


async def log_request(request):
    print(f"> {request.method} {request.url.path}", flush=True)


async def log_response(response):
    print(f"< HTTP {response.status_code}", flush=True)


def local_http_client(**kwargs):
    kwargs["trust_env"] = False
    if os.getenv("SCRAPER_API_KEY"):
        kwargs["headers"] = {**(kwargs.get("headers") or {}), "Authorization": f"Bearer {os.environ['SCRAPER_API_KEY']}"}
    kwargs["event_hooks"] = {
        "request": [log_request],
        "response": [log_response],
    }
    return httpx2.AsyncClient(**kwargs)


async def check(transport=None):
    if transport is None:
        transport = StreamableHttpTransport(URL, httpx_client_factory=local_http_client)
    async with Client(transport, timeout=15) as client:
        tools = [tool.name for tool in await client.list_tools()]
        if "scrape_url_stealth" not in tools:
            raise RuntimeError(f"Unexpected tool list: {tools}")
        print(f"Connected. Tools: {tools}", flush=True)


if __name__ == "__main__":
    print(f"Checking {URL}", flush=True)
    try:
        asyncio.run(asyncio.wait_for(check(), timeout=25))
    except Exception:
        log = Path(__file__).with_name("connection-diagnostic.log")
        log.write_text(traceback.format_exc())
        print(f"Connection failed. Details saved to {log}", file=sys.stderr)
        sys.exit(1)
