"""
Core scraping functions for the ZScraper gRPC service.

  scrape_and_create_draft(url)  → (success: bool, message: str)
  _render_page(url)             → html: str | None   (sync, Playwright or cloudscraper)
  extract_property_data(html, url) → dict
  download_image(img_url, slug) → local_path: str | None
"""

import os
import re
import logging
import requests
import cloudscraper
from pathlib import Path
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from django.utils.text import slugify
from django.conf import settings
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
}


# ------------------------------------------------------------------ #
#  Page rendering                                                      #
# ------------------------------------------------------------------ #

def _render_page(url: str) -> Optional[str]:
    """
    Fetch page HTML.
    1. Try cloudscraper (handles Cloudflare JS challenges).
    2. If body text is too sparse (<200 chars), fall back to Playwright.
    """
    html = _fetch_cloudscraper(url)
    if html and _has_enough_text(html):
        return html

    logger.info(f"Falling back to Playwright for: {url}")
    return _fetch_playwright(url)


def _fetch_cloudscraper(url: str) -> Optional[str]:
    try:
        scraper = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
        )
        r = scraper.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.error(f"cloudscraper failed for {url}: {e}")
        return None


def _fetch_playwright(url: str) -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("Playwright not installed")
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox'],
            )
            ctx = browser.new_context(
                user_agent=HEADERS['User-Agent'],
                viewport={'width': 1280, 'height': 800},
                locale='en-US',
                extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'},
            )
            ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = ctx.new_page()
            try:
                page.goto(url, wait_until='load', timeout=30000)
            except Exception:
                pass
            page.wait_for_timeout(2500)
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        logger.error(f"Playwright failed for {url}: {e}")
        return None


def _has_enough_text(html: str) -> bool:
    if 'challenges.cloudflare.com' in html or 'Just a moment' in html[:500]:
        return False
    soup = BeautifulSoup(html, 'lxml')
    body_text = soup.body.get_text(strip=True) if soup.body else ""
    return len(body_text) >= 200


# ------------------------------------------------------------------ #
#  Data extraction                                                     #
# ------------------------------------------------------------------ #

def extract_property_data(html: str, url: str) -> Dict:
    """Extract structured property data from raw HTML."""
    soup = BeautifulSoup(html, 'lxml')

    # Title
    title = ""
    for sel in ['h1', 'meta[property="og:title"]', 'title']:
        tag = soup.select_one(sel)
        if tag:
            title = tag.get('content', '') or tag.get_text(strip=True)
            if title:
                break

    # Price — look for common currency patterns
    price = ""
    price_tag = soup.find(string=re.compile(r'RM\s?[\d,]+|USD\s?[\d,]+|\$[\d,]+'))
    if price_tag:
        price = price_tag.strip()

    # Description
    desc = ""
    for sel in ['.description', '#description', '[class*="description"]', 'meta[name="description"]']:
        tag = soup.select_one(sel)
        if tag:
            desc = tag.get('content', '') or tag.get_text(separator=' ', strip=True)
            if len(desc) > 30:
                break

    # Bedrooms / Bathrooms / Floor size — generic label search
    bedrooms = _find_detail(soup, ['bed', 'bedroom', 'Bedroom'])
    bathrooms = _find_detail(soup, ['bath', 'bathroom', 'Bathroom'])
    floor_size = _find_detail(soup, ['sqft', 'sq ft', 'sq. ft', 'sqm', 'floor size', 'built-up'])

    # Location / address
    location = ""
    for sel in ['[class*="address"]', '[class*="location"]', 'meta[property="og:locality"]']:
        tag = soup.select_one(sel)
        if tag:
            location = tag.get('content', '') or tag.get_text(strip=True)
            if location:
                break

    # Property type
    property_type = ""
    for sel in ['[class*="property-type"]', '[class*="listing-type"]']:
        tag = soup.select_one(sel)
        if tag:
            property_type = tag.get_text(strip=True)
            if property_type:
                break

    # Image URLs
    image_urls: List[str] = []
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    for img in soup.find_all('img'):
        src = img.get('data-src') or img.get('src', '')
        if src and not src.startswith('data:'):
            image_urls.append(urljoin(base, src))
    # Also grab og:image
    og_img = soup.select_one('meta[property="og:image"]')
    if og_img and og_img.get('content'):
        image_urls.insert(0, og_img['content'])

    return {
        'title': title or 'Untitled Property',
        'price': price,
        'description': desc,
        'bedrooms': bedrooms,
        'bathrooms': bathrooms,
        'floor_size': floor_size,
        'location': location,
        'property_type': property_type,
        'source_url': url,
        'image_urls': list(dict.fromkeys(image_urls))[:20],  # deduplicated, max 20
    }


def _find_detail(soup: BeautifulSoup, keywords: List[str]) -> str:
    """Search nearby text for a numeric value next to a keyword."""
    for kw in keywords:
        tag = soup.find(string=re.compile(kw, re.IGNORECASE))
        if tag:
            parent = tag.parent
            # Look for a number in the parent or sibling text
            numbers = re.findall(r'[\d,]+(?:\.\d+)?', parent.get_text())
            if numbers:
                return numbers[0]
    return ""


# ------------------------------------------------------------------ #
#  Image download                                                      #
# ------------------------------------------------------------------ #

def download_image(img_url: str, slug: str) -> Optional[str]:
    """Download image to MEDIA_ROOT/images/<slug>/ and return relative path."""
    try:
        dest_dir = Path(settings.MEDIA_ROOT) / 'images' / slug
        dest_dir.mkdir(parents=True, exist_ok=True)

        filename = os.path.basename(urlparse(img_url).path) or 'image.jpg'
        # Sanitise filename
        filename = re.sub(r'[^\w.\-]', '_', filename)[:100]
        dest = dest_dir / filename

        if dest.exists():
            return str(dest.relative_to(settings.MEDIA_ROOT))

        r = requests.get(img_url, headers=HEADERS, timeout=15, stream=True)
        r.raise_for_status()
        with open(dest, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

        return str(dest.relative_to(settings.MEDIA_ROOT))
    except Exception as e:
        logger.warning(f"Image download failed ({img_url}): {e}")
        return None


# ------------------------------------------------------------------ #
#  High-level: scrape + create Django Post draft                      #
# ------------------------------------------------------------------ #

def scrape_and_create_draft(url: str):
    """
    Scrape the URL and create a Post draft in the database.
    Returns (success: bool, message: str).
    """
    from app.models import Post

    html = _render_page(url)
    if not html:
        return False, f"Failed to fetch page: {url}"

    data = extract_property_data(html, url)

    if not data.get('title'):
        return False, "Could not extract a title from the page"

    # Build a unique slug
    base_slug = slugify(data['title'])[:200] or 'property'
    slug = base_slug
    counter = 1
    while Post.objects.filter(slug=slug).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1

    post = Post.objects.create(
        title=data['title'],
        slug=slug,
        source_url=data['source_url'],
        price=data['price'],
        description=data['description'],
        bedrooms=data['bedrooms'],
        bathrooms=data['bathrooms'],
        floor_size=data['floor_size'],
        location=data['location'],
        property_type=data['property_type'],
        status=Post.STATUS_DRAFT,
    )

    return True, f"Draft created: '{post.title}' (id={post.pk})"
