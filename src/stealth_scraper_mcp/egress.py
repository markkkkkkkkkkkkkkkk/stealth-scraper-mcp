"""A loopback-only forward proxy that enforces the SSRF policy at connect time.

The browser resolves hostnames itself, so checking a URL before Chrome connects leaves
a gap for DNS rebinding, and Playwright's request routing never sees WebSockets. Every
direct browser connection is therefore sent through this proxy: it resolves each host
once, rejects non-public addresses, and connects to exactly the address it checked.
CONNECT covers https and all WebSockets; plain http is forwarded one request per connection.
"""

import asyncio
import contextlib
from urllib.parse import urlsplit

from .safety import BlockedURLError, resolve_public

HEADER_LIMIT = 64 * 1024
HOP_BY_HOP = {b"proxy-connection", b"connection", b"keep-alive", b"proxy-authorization", b"te", b"upgrade"}


def _split_host_port(target: str, default_port: int) -> tuple[str, int]:
    if target.startswith("["):  # [IPv6]:port
        host, _, rest = target[1:].partition("]")
        return host, int(rest.lstrip(":") or default_port)
    host, _, port = target.rpartition(":")
    return (host, int(port)) if host and port.isdigit() else (target, default_port)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()


class EgressGuard:
    def __init__(self) -> None:
        self._server: asyncio.base_events.Server | None = None
        self.port = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        if self._server is None:
            self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0, limit=HEADER_LIMIT)
            self.port = self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream_writer = None
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
            request_line, _, header_block = head.partition(b"\r\n")
            method, target, version = request_line.decode("latin-1").split(" ", 2)

            if method.upper() == "CONNECT":
                host, port = _split_host_port(target, 443)
                address = await resolve_public(host, port)
                upstream_reader, upstream_writer = await asyncio.open_connection(address, port)
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await writer.drain()
            else:
                parts = urlsplit(target)
                if parts.scheme != "http" or not parts.hostname:
                    raise BlockedURLError(f"Unsupported proxy request target '{target}'.")
                port = parts.port or 80
                address = await resolve_public(parts.hostname, port)
                upstream_reader, upstream_writer = await asyncio.open_connection(address, port)
                path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
                headers = [
                    line for line in header_block.split(b"\r\n")
                    if line and line.split(b":", 1)[0].strip().lower() not in HOP_BY_HOP
                ]
                # One request per connection, so a reused proxy connection can never be
                # steered to a host that was not checked.
                headers.append(b"Connection: close")
                upstream_writer.write(f"{method} {path} {version}\r\n".encode("latin-1") + b"\r\n".join(headers) + b"\r\n\r\n")
                await upstream_writer.drain()

            await asyncio.gather(_pipe(reader, upstream_writer), _pipe(upstream_reader, writer))
        except BlockedURLError:
            await self._reject(writer, b"403 Forbidden")
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
            await self._reject(writer, b"400 Bad Request")
        except OSError:
            await self._reject(writer, b"502 Bad Gateway")
        finally:
            for stream in (writer, upstream_writer):
                if stream is not None:
                    with contextlib.suppress(Exception):
                        stream.close()

    @staticmethod
    async def _reject(writer: asyncio.StreamWriter, status: bytes) -> None:
        with contextlib.suppress(Exception):
            writer.write(b"HTTP/1.1 " + status + b"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
