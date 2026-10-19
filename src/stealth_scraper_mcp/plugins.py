"""Optional extensions, discovered through the "stealth_scraper_mcp.plugins" entry point group.

A plugin is a callable that receives the Extensions registry and fills in what it provides,
such as usage metering, extra HTTP routes or CLI commands. Without plugins the server is
free and unmetered.
"""

from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any, Callable

ENTRY_POINT_GROUP = "stealth_scraper_mcp.plugins"


@dataclass
class Charge:
    cost_usd: float
    remaining_usd: float


class Metering:
    """Called around every tool call. The default charges nothing."""

    def reserve(self) -> Any:
        """Authorize the call before any fetching; raise ToolError to refuse it."""
        return None

    def settle(self, reservation: Any, tier: str | None, host: str, proxy_bytes: int = 0) -> Charge | None:
        """Finish the call: tier is the price tier of a successful result, or None when it failed."""
        return None


@dataclass
class Extensions:
    metering: Metering = field(default_factory=Metering)
    routers: list = field(default_factory=list)  # FastAPI routers mounted on the HTTP app
    commands: dict[str, Callable[[list[str]], int]] = field(default_factory=dict)  # CLI subcommands
    public_path_prefixes: list[str] = field(default_factory=list)  # reachable without the master API key
    handles_auth: bool = False  # the plugin authenticates callers itself (e.g. per-customer keys)


def load() -> Extensions:
    extensions = Extensions()
    for entry_point in entry_points(group=ENTRY_POINT_GROUP):
        entry_point.load()(extensions)
    return extensions
