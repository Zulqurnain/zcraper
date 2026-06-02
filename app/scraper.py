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
    """
    Headless Firefox bypasses Cloudflare's JS challenge automatically.
    Falls back to Chromium if Firefox is not installed.
    Passes --no-sandbox for VPS/Docker environments.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("Playwright not installed")
        return None

    try:
        with sync_playwright() as p:
            # Firefox is less fingerprinted by Cloudflare than Chromium
            try:
                browser = p.firefox.launch(
                    headless=True,
                    firefox_user_prefs={
                        'media.navigator.enabled': False,
                        'privacy.resistFingerprinting': False,
                    },
                )
                ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0'
            except Exception:
                logger.warning("Firefox not available, falling back to Chromium")
                browser = p.chromium.launch(
                    headless=True,
                    args=['--disable-blink-features=AutomationControlled', '--no-sandbox', '--disable-dev-shm-usage'],
                )
                ua = HEADERS['User-Agent']

            ctx = browser.new_context(
                user_agent=ua,
                viewport={'width': 1280, 'height': 800},
                locale='en-US',
                extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'},
            )
            page = ctx.new_page()
            try:
                page.goto(url, wait_until='load', timeout=30000)
            except Exception:
                pass
            # Wait for Cloudflare challenge to auto-resolve and redirect (up to 8s)
            page.wait_for_timeout(8000)
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
    import json as _json
    soup = BeautifulSoup(html, 'lxml')

    # ── JSON-LD structured data (most reliable source) ──────────────────
    ld_data: Dict = {}
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            obj = _json.loads(script.string or '{}')
            if isinstance(obj, list):
                obj = next((o for o in obj if o.get('@type') in ('Residence', 'Product', 'Apartment', 'House', 'RealEstateListing')), {})
            if obj.get('@type') and obj.get('@type') not in ('FAQPage', 'BreadcrumbList', 'WebPage', 'WebSite', 'Organization'):
                ld_data = obj
                break
        except Exception:
            continue

    # ── Title ────────────────────────────────────────────────────────────
    title = (
        ld_data.get('name')
        or (soup.select_one('h1') and soup.select_one('h1').get_text(strip=True))
        or (soup.select_one('meta[property="og:title"]') and soup.select_one('meta[property="og:title"]').get('content', ''))
        or (soup.select_one('title') and soup.select_one('title').get_text(strip=True))
        or 'Untitled Property'
    )

    # ── Price ────────────────────────────────────────────────────────────
    # Search visible text only — not script tags
    price = ""
    body_text = soup.body.get_text(' ', strip=True) if soup.body else ""
    price_match = re.search(r'RM\s?[\d,]+(?:\s*/\s*mo(?:nth)?)?', body_text)
    if not price_match:
        price_match = re.search(r'USD\s?[\d,]+|\$[\d,]+', body_text)
    if price_match:
        price = price_match.group(0).strip()

    # ── Description ──────────────────────────────────────────────────────
    desc = ld_data.get('description', '')
    if not desc:
        for sel in ['[class*="description"]', '#description', 'meta[name="description"]']:
            tag = soup.select_one(sel)
            if tag:
                desc = tag.get('content', '') or tag.get_text(separator=' ', strip=True)
                if len(desc) > 30:
                    break

    # ── Bedrooms / Bathrooms / Floor size ────────────────────────────────
    # First try: regex patterns directly on the visible body text (most reliable)
    body_text = soup.body.get_text(' ', strip=True) if soup.body else ""

    m_bed = re.search(r'(\d{1,2})\s*bed(?:room)?s?', body_text, re.IGNORECASE)
    bedrooms = m_bed.group(1) if m_bed else ""

    m_bath = re.search(r'(\d{1,2})\s*bath(?:room)?s?', body_text, re.IGNORECASE)
    bathrooms = m_bath.group(1) if m_bath else ""

    m_size = re.search(r'([\d,]+)\s*(?:sq\.?\s?ft|sqft|sqm)', body_text, re.IGNORECASE)
    floor_size = m_size.group(1) if m_size else ""

    # Fallback: parse same patterns from description string
    if not bedrooms:
        m = re.search(r'(\d{1,2})\s*bedroom', desc, re.IGNORECASE)
        if m:
            bedrooms = m.group(1)
    if not bathrooms:
        m = re.search(r'(\d{1,2})\s*bathroom', desc, re.IGNORECASE)
        if m:
            bathrooms = m.group(1)
    if not floor_size:
        m = re.search(r'([\d,]+)\s*(?:sq\.?\s?ft|sqft|sqm)', desc, re.IGNORECASE)
        if m:
            floor_size = m.group(1)

    # ── Location ─────────────────────────────────────────────────────────
    location = ""
    addr = ld_data.get('address', {})
    if isinstance(addr, dict):
        parts = [addr.get('streetAddress', ''), addr.get('addressLocality', ''), addr.get('addressRegion', '')]
        location = ', '.join(p for p in parts if p)
    if not location:
        for sel in ['[class*="address"]', '[class*="location"]', 'meta[property="og:locality"]']:
            tag = soup.select_one(sel)
            if tag:
                location = tag.get('content', '') or tag.get_text(strip=True)
                if location:
                    break

    # ── Property type ────────────────────────────────────────────────────
    property_type = ld_data.get('@type', '')
    if not property_type:
        for sel in ['[class*="property-type"]', '[class*="listing-type"]', '[class*="propertyType"]']:
            tag = soup.select_one(sel)
            if tag:
                property_type = tag.get_text(strip=True)
                if property_type:
                    break

    # ── Images ───────────────────────────────────────────────────────────
    image_urls: List[str] = []
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    og_img = soup.select_one('meta[property="og:image"]')
    if og_img and og_img.get('content'):
        image_urls.append(og_img['content'])

    for img in soup.find_all('img'):
        src = img.get('data-src') or img.get('data-lazy-src') or img.get('src', '')
        if src and not src.startswith('data:') and not src.endswith('.svg'):
            full = urljoin(base, src)
            if urlparse(full).scheme in ('http', 'https'):
                image_urls.append(full)

    return {
        'title': title,
        'price': price,
        'description': desc[:2000],
        'bedrooms': bedrooms,
        'bathrooms': bathrooms,
        'floor_size': floor_size,
        'location': location,
        'property_type': property_type,
        'source_url': url,
        'image_urls': list(dict.fromkeys(image_urls))[:20],
    }


def _find_detail_label(soup: BeautifulSoup, keywords: List[str]) -> str:
    """
    Find a numeric value associated with a label keyword.
    Looks for a tag whose text IS the label and grabs the adjacent sibling/parent value.
    """
    for kw in keywords:
        # Try finding a tag whose text matches the label
        tag = soup.find(string=re.compile(rf'\b{re.escape(kw)}\b', re.IGNORECASE))
        if not tag:
            continue
        parent = tag.parent
        # Check sibling spans/divs for a number
        for sibling in parent.find_next_siblings()[:3]:
            nums = re.findall(r'\d[\d,]*(?:\.\d+)?', sibling.get_text())
            if nums:
                return nums[0]
        # Check parent's parent children
        for child in (parent.parent.children if parent.parent else []):
            text = child.get_text(strip=True) if hasattr(child, 'get_text') else ''
            nums = re.findall(r'\d[\d,]*(?:\.\d+)?', text)
            if nums and text != parent.get_text(strip=True):
                return nums[0]
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
