# Benchmark results

Run 2026-10-19 with stealth-scraper-mcp 0.3.0 from Darwin x86_64, residential connection, no proxy, browser channel `chrome`. Each site was fetched once. Tokens are estimated as characters / 4.

## Summary

| Measure | Result |
|---|---|
| Success rate | 24/30 (80.0%) |
| Median tokens: raw HTML | 90,222 |
| Median tokens: clean Markdown | 1,901 (97.9% fewer) |
| Median tokens: with `query` (top 3 passages) | 341 (99.6% fewer) |
| Median time, http (21 pages) | 2.6 s |
| Median time, browser (3 pages) | 11.4 s |

## By category

| Category | Success | Median Markdown tokens | Median query tokens |
|---|---|---|---|
| docs | 5/5 | 2,080 | 364 |
| reference | 4/4 | 6,610 | 370 |
| news | 6/6 | 1,518 | 144 |
| ecommerce | 3/5 | 793 | 182 |
| js_app | 2/4 | 1,816 | 328 |
| protected | 4/6 | 1,235 | 476 |

## Per site

| Site | Result | Tier | Time | Raw → Markdown → query tokens |
|---|---|---|---|---|
| https://docs.python.org/3/library/asyncio.html | ok | http | 2.0 s | 6,349 → 913 → 318 |
| https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/403 | ok | http | 2.0 s | 52,521 → 547 → 456 |
| https://fastapi.tiangolo.com/tutorial/first-steps/ | ok | http | 2.0 s | 36,215 → 3,719 → 444 |
| https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api | ok | http | 1.6 s | 84,268 → 3,272 → 364 |
| https://github.com/microsoft/playwright | ok | http | 2.6 s | 108,375 → 2,080 → 170 |
| https://en.wikipedia.org/wiki/Web_scraping | ok | http | 2.3 s | 58,993 → 10,967 → 487 |
| https://en.wikipedia.org/wiki/Large_language_model | ok | http | 10.9 s | 259,671 → 41,610 → 464 |
| https://stackoverflow.com/questions/231767/what-does-the-yield-keyword-do-in-python | ok | browser | 19.7 s | 213,467 → 2,253 → 215 |
| https://www.gov.uk/browse/tax | ok | http | 1.4 s | 15,733 → 274 → 275 |
| https://www.bbc.com/news | ok | http | 5.5 s | 96,176 → 2,015 → 458 |
| https://www.theguardian.com/international | ok | http | 9.8 s | 359,639 → 4,991 → 70 |
| https://techcrunch.com/ | ok | http | 7.1 s | 112,166 → 3,335 → 219 |
| https://news.ycombinator.com/ | ok | http | 16.3 s | 8,673 → 1,022 → 399 |
| https://www.reuters.com/technology/ | ok | http | 10.8 s | 144,559 → 463 → 0 |
| https://www.nytimes.com/ | ok | http | 13.7 s | 383,821 → 705 → 0 |
| https://books.toscrape.com/ | ok | http | 1.2 s | 12,818 → 707 → 0 |
| https://www.ebay.com/ | failed: status 200, 836 chars | browser | 22.5 s | - |
| https://www.amazon.com/ | ok | browser | 11.4 s | 145,395 → 793 → 596 |
| https://www.walmart.com/ | ok | http | 9.5 s | 286,880 → 5,326 → 182 |
| https://www.etsy.com/ | failed: [blocked] https://www.etsy.com/ returned a bot-protection challenge (status 403). Hint: Try a different impersonate_browser or try again later; this server has  | - | 19.3 s | - |
| https://quotes.toscrape.com/js/ | ok | browser | 7.6 s | 2,235 → 270 → 180 |
| https://react.dev/learn | ok | http | 3.8 s | 66,255 → 3,363 → 477 |
| https://vercel.com/ | failed: status 200, 810 chars | http | 3.3 s | - |
| https://www.airbnb.com/ | failed: status 200, 461 chars | browser | 10.6 s | - |
| https://www.bestbuy.com/ | ok | http | 0.4 s | 1,759 → 275 → 0 |
| https://www.g2.com/products/slack/reviews | failed: [blocked] https://www.g2.com/products/slack/reviews returned a bot-protection challenge (status 403). Hint: Try a different impersonate_browser or try again lat | - | 16.4 s | - |
| https://www.zillow.com/ | ok | http | 3.9 s | 106,016 → 3,745 → 579 |
| https://www.indeed.com/ | ok | http | 2.5 s | 148,177 → 1,787 → 481 |
| https://www.glassdoor.com/index.htm | failed: [blocked] https://www.glassdoor.com/index.htm returned a bot-protection challenge (status 403). Hint: Try a different impersonate_browser or try again later; th | - | 15.8 s | - |
| https://www.crunchbase.com/ | ok | http | 2.0 s | 32,409 → 683 → 471 |
