"""Benchmark the scraper on real sites: success rate, tokens saved and latency.

Each site is fetched once (later calls reuse the cache). Tokens are estimated as
characters / 4 for every column, so the ratios between columns are what matter.

    PYTHONPATH=src venv/bin/python benchmark/run.py [--concurrency 3] [--sites benchmark/sites.json]
"""

import argparse
import asyncio
import datetime
import json
import platform
import statistics
import time
from pathlib import Path

from fastmcp import Client

from stealth_scraper_mcp import __version__, config, server

MIN_CONTENT_CHARS = 1000  # about 250 tokens; less counts as a failed scrape (empty, challenge or wall page)
HERE = Path(__file__).resolve().parent


def tokens(chars: int | None) -> int | None:
    return None if chars is None else chars // 4


async def bench_site(client: Client, site: dict, slots: asyncio.Semaphore) -> dict:
    row = {"category": site["category"], "url": site["url"]}
    async with slots:
        started = time.monotonic()
        try:
            result = await client.call_tool(
                "scrape_url_stealth",
                {"url": site["url"], "max_chars": 100_000, "response_format": "detailed", "proxy": "never"},
            )
            data = result.structured_content
        except Exception as exc:
            row.update(ok=False, seconds=round(time.monotonic() - started, 1), error=str(exc)[:160])
            return row
        row["seconds"] = round(time.monotonic() - started, 1)

    cached = server.FETCH_CACHE.get((site["url"], "auto", "chrome", None, False))
    raw_chars = len(cached[0].html) if cached else None
    row.update(
        status=data["status_code"],
        method=data.get("fetch_method"),
        engine=data.get("engine"),
        escalation=data.get("escalation"),
        raw_tokens=tokens(raw_chars),
        markdown_tokens=tokens(data["total_chars"]),
    )
    row["ok"] = data["status_code"] < 400 and data["total_chars"] >= MIN_CONTENT_CHARS
    if not row["ok"]:
        row["error"] = f"status {data['status_code']}, {data['total_chars']} chars"
        return row

    query = await client.call_tool(
        "scrape_url_stealth",
        {"url": site["url"], "query": site["query"], "top_k": 3, "max_chars": 100_000, "proxy": "never"},
    )
    row["query_tokens"] = tokens(query.structured_content["total_chars"])
    return row


def percent(part: float, whole: float) -> str:
    return f"{part / whole:.1%}" if whole else "-"


def median(values: list) -> float | str:
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else "-"


def fmt(value: float | str) -> str:
    return f"{value:,.0f}" if isinstance(value, (int, float)) else value


def write_report(rows: list[dict], meta: dict, path: Path) -> None:
    ok = [r for r in rows if r["ok"]]
    lines = [
        "# Benchmark results",
        "",
        f"Run {meta['date']} with stealth-scraper-mcp {meta['version']} from {meta['machine']}, "
        f"no proxy, browser channel `{meta['browser_channel'] or 'chromium'}`. "
        "Each site was fetched once. Tokens are estimated as characters / 4.",
        "",
        "## Summary",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Success rate | {len(ok)}/{len(rows)} ({percent(len(ok), len(rows))}) |",
    ]
    raw = median([r["raw_tokens"] for r in ok])
    markdown = median([r["markdown_tokens"] for r in ok])
    query = median([r.get("query_tokens") for r in ok])
    if ok:
        lines += [
            f"| Median tokens: raw HTML | {fmt(raw)} |",
            f"| Median tokens: clean Markdown | {fmt(markdown)} ({percent(raw - markdown, raw)} fewer) |",
            f"| Median tokens: with `query` (top 3 passages) | {fmt(query)} ({percent(raw - query, raw)} fewer) |",
        ]
    for method in ("http", "browser"):
        times = [r["seconds"] for r in ok if r["method"] == method]
        if times:
            lines.append(f"| Median time, {method} ({len(times)} pages) | {median(times):.1f} s |")

    lines += ["", "## By category", "", "| Category | Success | Median Markdown tokens | Median query tokens |", "|---|---|---|---|"]
    for category in dict.fromkeys(r["category"] for r in rows):
        group = [r for r in rows if r["category"] == category]
        good = [r for r in group if r["ok"]]
        lines.append(
            f"| {category} | {len(good)}/{len(group)} | {fmt(median([r['markdown_tokens'] for r in good]))} "
            f"| {fmt(median([r.get('query_tokens') for r in good]))} |"
        )

    lines += ["", "## Per site", "", "| Site | Result | Tier | Time | Raw → Markdown → query tokens |", "|---|---|---|---|---|"]
    for r in rows:
        if r["ok"]:
            detail = f"{r['raw_tokens']:,} → {r['markdown_tokens']:,} → {r.get('query_tokens', 0):,}"
            lines.append(f"| {r['url']} | ok | {r['method']} | {r['seconds']} s | {detail} |")
        else:
            lines.append(f"| {r['url']} | failed: {r.get('error', '')} | {r.get('method') or '-'} | {r['seconds']} s | - |")
    path.write_text("\n".join(lines) + "\n")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sites", type=Path, default=HERE / "sites.json")
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()

    sites = json.loads(args.sites.read_text())
    slots = asyncio.Semaphore(args.concurrency)
    async with Client(server.mcp) as client:
        rows = await asyncio.gather(*(bench_site(client, site, slots) for site in sites))
    await server.browser_pool.close()

    meta = {
        "date": datetime.date.today().isoformat(),
        "version": __version__,
        "machine": f"{platform.system()} {platform.machine()}, residential connection",
        "browser_channel": config.BROWSER_CHANNEL,
    }
    (HERE / "results.json").write_text(json.dumps({"meta": meta, "results": rows}, indent=2))
    write_report(rows, meta, HERE / "RESULTS.md")
    ok = sum(r["ok"] for r in rows)
    print(f"{ok}/{len(rows)} succeeded. Wrote benchmark/RESULTS.md and benchmark/results.json")


if __name__ == "__main__":
    asyncio.run(main())
