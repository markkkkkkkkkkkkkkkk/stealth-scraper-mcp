"""Start the project server and verify its live MCP connection in one terminal."""

import json
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
PYTHON = PROJECT / "venv/bin/python"
LOG = PROJECT / "server.log"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def healthy():
    try:
        with OPENER.open("http://127.0.0.1:8080/health", timeout=1) as response:
            data = json.load(response)
            return data.get("service") == "stealth-web-scraper" and data.get("status") == "ok"
    except (OSError, ValueError):
        return False


def verify():
    return subprocess.run(
        [str(PYTHON), "-u", str(PROJECT / "check_connection.py")],
        cwd=PROJECT,
        timeout=35,
        check=False,
    ).returncode == 0


def main():
    if healthy():
        print("The project server is already running; checking MCP connectivity.", flush=True)
        return 0 if verify() else 1

    server = None
    try:
        with LOG.open("w") as output:
            server = subprocess.Popen(
                [str(PYTHON), "-u", str(PROJECT / "main.py")],
                cwd=PROJECT,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            print(f"Starting server. Logs: {LOG}", flush=True)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    print(LOG.read_text(), file=sys.stderr)
                    return 1
                if healthy():
                    break
                time.sleep(0.25)
            else:
                print("Server health check timed out. See server.log.", file=sys.stderr)
                return 1

            if not verify():
                print("Live MCP verification failed. See connection-diagnostic.log and server.log.", file=sys.stderr)
                return 1
            print("Live MCP verified: http://127.0.0.1:8080/mcp", flush=True)
            print("Keep this terminal open. Press Ctrl-C to stop the server.", flush=True)
            return server.wait()
    except KeyboardInterrupt:
        return 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        return 1
    finally:
        # Stop only the process started by this launcher, never unrelated servers.
        if server is not None and server.poll() is None:
            server.send_signal(signal.SIGINT)
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    sys.exit(main())
