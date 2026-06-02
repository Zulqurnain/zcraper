"""
EthicalWebScraper — generic web scraper with JS support.

Static pages  → requests + BeautifulSoup (fast)
JS-heavy pages → Playwright headless browser (full rendering)

Usage:
    python scraper.py                          # uses default URL
    python scraper.py https://example.com      # custom URL
    python scraper.py https://example.com --pages 10 --js
"""

import sys
import argparse
import requests
import cloudscraper
from bs4 import BeautifulSoup
import csv
import time
import random
from urllib.parse import urljoin, urlparse
from typing import Optional, List, Dict
import logging

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

JS_INDICATORS = [
    "__NEXT_DATA__",
    "ng-version",
    "data-reactroot",
    "__vue__",
    "window.__NUXT__",
    "ember-application",
    "ng-app",
]


def _looks_like_js_rendered(html: str) -> bool:
    """
    Returns True only when the fetched HTML has too little text content to be useful.
    SPA framework markers alone are not enough — cloudscraper often returns fully
    hydrated Next.js/React pages that already have all content.
    """
    # Cloudflare challenge — Playwright won't help either
    if "challenges.cloudflare.com" in html or "Just a moment" in html[:500]:
        return False
    soup = BeautifulSoup(html, "lxml")
    body_text = soup.body.get_text(strip=True) if soup.body else ""
    return len(body_text) < 200


class EthicalWebScraper:
    def __init__(self, start_url: str, max_pages: int = 50, force_js: bool = False):
        self.start_url = start_url
        self.max_pages = max_pages
        self.force_js = force_js
        self.visited_urls: set = set()
        self.url_queue = [start_url]
        self.domain = urlparse(start_url).netloc
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
        self._pw_browser = None
        self._pw_context = None
        self._playwright = None

    # ------------------------------------------------------------------ #
    #  Fetching                                                            #
    # ------------------------------------------------------------------ #

    def _fetch_static(self, url: str) -> Optional[str]:
        # cloudscraper handles Cloudflare JS challenges automatically
        try:
            scraper = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False}
            )
            r = scraper.get(url, headers=self.headers, timeout=15)
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.error(f"Static fetch failed for {url}: {e}")
            return None

    def _ensure_playwright(self):
        if self._pw_browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )
            raise

        self._playwright = sync_playwright().__enter__()
        self._pw_browser = self._playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._pw_context = self._pw_browser.new_context(
            user_agent=self.headers["User-Agent"],
            java_script_enabled=True,
            # Spoof a real viewport so anti-bot checks see a real browser
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            },
        )
        # Apply stealth patches to evade bot detection
        try:
            from playwright_stealth import stealth_sync

            self._stealth = stealth_sync
        except ImportError:
            self._stealth = None
            logger.warning(
                "playwright-stealth not installed; bot detection bypass limited"
            )

    def _fetch_js(self, url: str) -> Optional[str]:
        try:
            self._ensure_playwright()
            page = self._pw_context.new_page()
            # Apply stealth patches per-page if available
            if getattr(self, "_stealth", None):
                self._stealth(page)
            try:
                page.goto(url, wait_until="load", timeout=30000)
            except Exception:
                pass  # Grab whatever rendered so far on timeout
            # Extra wait for JS to paint content
            page.wait_for_timeout(2500)
            html = page.content()
            page.close()

            # Detect Cloudflare challenge page and log a clear warning
            if "Just a moment" in html or "challenges.cloudflare.com" in html:
                logger.warning(
                    f"Cloudflare challenge page returned for {url} — content may be empty"
                )

            return html
        except Exception as e:
            logger.error(f"JS fetch failed for {url}: {e}")
            return None

    def fetch_page(self, url: str) -> Optional[str]:
        if self.force_js:
            return self._fetch_js(url)

        html = self._fetch_static(url)
        if html and _looks_like_js_rendered(html):
            logger.info(f"JS-rendered page detected, switching to Playwright: {url}")
            html = self._fetch_js(url)
        return html

    # ------------------------------------------------------------------ #
    #  Parsing                                                             #
    # ------------------------------------------------------------------ #

    def parse_page(self, html: str, current_url: str) -> Dict:
        soup = BeautifulSoup(html, "lxml")

        title = (
            soup.title.string.strip()
            if soup.title and soup.title.string
            else "No Title"
        )
        headings = [
            h.get_text(strip=True)
            for h in soup.find_all(["h1", "h2", "h3"])
            if h.get_text(strip=True)
        ]
        paragraphs = [
            p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)
        ][:5]

        links = []
        for a in soup.find_all("a", href=True):
            link_url = urljoin(current_url, a["href"])
            parsed = urlparse(link_url)
            # Stay on same domain; skip anchors and non-http schemes
            if (
                self.domain in parsed.netloc
                and parsed.scheme in ("http", "https")
                and "#" not in link_url
            ):
                links.append(link_url)

        return {
            "url": current_url,
            "title": title,
            "headings": headings,
            "paragraphs": paragraphs,
            "links": links[:10],
        }

    # ------------------------------------------------------------------ #
    #  Crawl loop                                                          #
    # ------------------------------------------------------------------ #

    def scrape(self) -> List[Dict]:
        results = []

        try:
            while self.url_queue and len(self.visited_urls) < self.max_pages:
                current_url = self.url_queue.pop(0)

                if current_url in self.visited_urls:
                    continue
                self.visited_urls.add(current_url)
                logger.info(
                    f"[{len(self.visited_urls)}/{self.max_pages}] Scraping: {current_url}"
                )

                html = self.fetch_page(current_url)
                if not html:
                    continue

                data = self.parse_page(html, current_url)
                results.append(data)

                for link in data["links"]:
                    if (
                        link not in self.visited_urls
                        and len(self.url_queue) < self.max_pages
                    ):
                        self.url_queue.append(link)

                time.sleep(0.2)

        finally:
            self._cleanup()

        return results

    def _cleanup(self):
        try:
            if self._pw_context:
                self._pw_context.close()
            if self._pw_browser:
                self._pw_browser.close()
            if self._playwright:
                self._playwright.__exit__(None, None, None)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Output                                                              #
    # ------------------------------------------------------------------ #

    def save_to_csv(self, results: List[Dict], filename: str = "scraped_data.csv"):
        if not results:
            logger.warning("No data to save.")
            return

        with open(filename, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["URL", "Title", "Headings", "Paragraphs"])
            for row in results:
                writer.writerow(
                    [
                        row["url"],
                        row["title"],
                        "; ".join(row["headings"]),
                        "; ".join(row["paragraphs"]),
                    ]
                )

        logger.info(f"Saved {len(results)} rows → {filename}")


# ------------------------------------------------------------------ #
#  CLI entry point                                                     #
# ------------------------------------------------------------------ #


def main():
    parser = argparse.ArgumentParser(
        description="EthicalWebScraper — static + JS support"
    )
    parser.add_argument(
        "url",
        nargs="?",
        default="https://books.toscrape.com/",
        help="Start URL to scrape",
    )
    parser.add_argument(
        "--pages", type=int, default=5, help="Max pages to visit (default: 5)"
    )
    parser.add_argument(
        "--js", action="store_true", help="Force Playwright JS rendering for every page"
    )
    parser.add_argument(
        "--output", default="scraped_data.csv", help="Output CSV filename"
    )
    args = parser.parse_args()

    logger.info(
        f"Target: {args.url}  |  Max pages: {args.pages}  |  Force JS: {args.js}"
    )

    scraper = EthicalWebScraper(args.url, max_pages=args.pages, force_js=args.js)
    data = scraper.scrape()
    scraper.save_to_csv(data, filename=args.output)
    print(f"\nDone! Scraped {len(data)} page(s). Output: {args.output}")


if __name__ == "__main__":
    main()
