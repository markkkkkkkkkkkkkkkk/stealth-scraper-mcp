"""Print live JSON-RPC initialization and tool discovery responses."""

import asyncio
import json
import sys

import httpx2

URL = "http://localhost:8080/mcp"


async def post(client, headers, payload):
    async with client.stream("POST", URL, headers=headers, json=payload) as response:
        response.raise_for_status()
        session = response.headers.get("mcp-session-id")
        if "text/event-stream" in response.headers.get("content-type", ""):
            lines = []
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    lines.append(line[5:].lstrip(" "))
                elif not line and lines:
                    message = json.loads("\n".join(lines))
                    lines.clear()
                    if message.get("id") == payload.get("id"):
                        break
            else:
                raise RuntimeError("SSE stream ended without the expected JSON-RPC response")
        else:
            message = json.loads(await response.aread())
        print(json.dumps(message, indent=2), flush=True)
        if "error" in message:
            raise RuntimeError(f"Server returned JSON-RPC error: {message['error']}")
        if message.get("id") != payload.get("id"):
            raise RuntimeError("Unexpected JSON-RPC response ID")
        return message, session


async def main():
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    async with httpx2.AsyncClient(trust_env=False, timeout=15) as client:
        print("initialize response:", flush=True)
        initialized, session = await post(client, headers, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "raw-tool-discovery", "version": "1.0"},
            },
        })
        if session:
            headers["Mcp-Session-Id"] = session
        headers["MCP-Protocol-Version"] = initialized["result"]["protocolVersion"]
        async with client.stream("POST", URL, headers=headers, json={
            "jsonrpc": "2.0", "method": "notifications/initialized",
        }) as response:
            response.raise_for_status()
        print("tools/list response:", flush=True)
        await post(client, headers, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(main(), timeout=40))
    except Exception as exc:
        print(f"Live discovery failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
