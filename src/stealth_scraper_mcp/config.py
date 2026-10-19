"""Runtime settings, read from environment variables."""

import os


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Bind to loopback by default; set SCRAPER_HOST=0.0.0.0 (with an API key) to expose it.
HOST = os.getenv("SCRAPER_HOST", "127.0.0.1")
PORT = int(os.getenv("SCRAPER_PORT", "8080"))

# When set, every HTTP request except /health must send "Authorization: Bearer <key>".
# Ignored when a plugin authenticates callers itself (e.g. per-customer billing keys).
API_KEY = os.getenv("SCRAPER_API_KEY") or None
# Listening on a non-loopback address without an API key or an authenticating plugin is refused
# unless this is set, so a misconfigured deploy cannot expose a free open scraper.
ALLOW_OPEN = _env_bool("SCRAPER_ALLOW_OPEN", False)

# Optional residential proxy, e.g. http://user:pass@host:port. By default it is used only
# after a direct fetch is blocked (callers can override per call with the proxy parameter).
PROXY_URL = os.getenv("SCRAPER_PROXY_URL") or None

CACHE_TTL_SECONDS = int(os.getenv("SCRAPER_CACHE_TTL", "3600"))
CACHE_MAX_ENTRIES = int(os.getenv("SCRAPER_CACHE_MAX_ENTRIES", "256"))

# Off by default so the server cannot be used to reach localhost or internal networks.
ALLOW_PRIVATE_NETWORKS = _env_bool("SCRAPER_ALLOW_PRIVATE_NETWORKS", False)

FETCH_TIMEOUT_SECONDS = float(os.getenv("SCRAPER_FETCH_TIMEOUT", "20"))
MAX_RETRIES = int(os.getenv("SCRAPER_MAX_RETRIES", "2"))
MAX_REDIRECTS = int(os.getenv("SCRAPER_MAX_REDIRECTS", "5"))
MAX_RESPONSE_BYTES = int(os.getenv("SCRAPER_MAX_RESPONSE_BYTES", str(15 * 1024 * 1024)))
BROWSER_CONCURRENCY = int(os.getenv("SCRAPER_BROWSER_CONCURRENCY", "3"))

# Installed Google Chrome ("chrome") is detected far less often than bundled Chromium.
# Set to "" to always use Playwright's bundled Chromium; it is also the automatic fallback.
BROWSER_CHANNEL = os.getenv("SCRAPER_BROWSER_CHANNEL", "chrome")
# Headed mode is harder to detect but opens visible windows; use it on a desktop or under xvfb.
BROWSER_HEADLESS = _env_bool("SCRAPER_BROWSER_HEADLESS", True)
BROWSER_LOCALE = os.getenv("SCRAPER_BROWSER_LOCALE", "en-US")
# Keep the timezone consistent with the proxy's location, e.g. America/New_York.
BROWSER_TIMEZONE = os.getenv("SCRAPER_BROWSER_TIMEZONE") or None
