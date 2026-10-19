"""Run the HTTP server from this source checkout; start_server.py launches this file."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from stealth_scraper_mcp.server import app, main  # noqa: E402,F401

if __name__ == "__main__":
    sys.exit(main(["--transport", "http", *sys.argv[1:]]))
