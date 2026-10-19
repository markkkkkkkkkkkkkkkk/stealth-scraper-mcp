"""Verify the requested packages and Chromium without fetching a website."""

import importlib
import importlib.metadata
import sys
from pathlib import Path


def main():
    failed = []
    packages = {
        "fastmcp": "fastmcp",
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "curl_cffi": "curl_cffi",
        "trafilatura": "trafilatura",
        "playwright": "playwright.sync_api",
        "pydantic": "pydantic",
        "beautifulsoup4": "bs4",
    }
    for distribution, module in packages.items():
        try:
            importlib.import_module(module)
            print(f"OK {distribution} {importlib.metadata.version(distribution)}", flush=True)
        except Exception as exc:
            failed.append(distribution)
            print(f"FAILED {distribution}: {exc}", flush=True)

    if "playwright" not in failed:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, timeout=15000)
                try:
                    page = browser.new_page()
                    page.set_content("<title>Setup verified</title><h1>Chromium works</h1>")
                    assert page.title() == "Setup verified"
                    assert page.locator("h1").inner_text() == "Chromium works"
                    print(f"OK Chromium {browser.version}", flush=True)
                finally:
                    browser.close()
        except Exception as exc:
            failed.append("Chromium launch")
            Path(__file__).with_name("browser-diagnostic.log").write_text(str(exc))
            print("FAILED Chromium: see browser-diagnostic.log", flush=True)

    if failed:
        print("Setup incomplete: " + ", ".join(failed), flush=True)
        return 1
    print("All requested packages and Chromium passed verification.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
