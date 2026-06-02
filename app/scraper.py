"""
Core scraping functions for the ZScraper gRPC service.

  scrape_and_create_draft(url)  → (success: bool, message: str)
  _render_page(url)             → html: str | None
  extract_page_data(html, url)  → dict   ← generic, works on any website
  download_image(img_url, slug) → local_path: str | None
"""

import os
import re
import json
import logging
import requests
import cloudscraper
from pathlib import Path
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from django.utils.text import slugify
from django.conf import settings
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
}

# Any currency symbol or code followed by a number
_PRICE_RE = re.compile(
    r'(?:'
    r'[£$€¥₹₩₪₺₽฿]'           # symbol-first currencies
    r'|(?:RM|USD|EUR|GBP|MYR|AUD|NZD|SGD|CAD|CHF|IDR|THB|VND|PHP|PKR|BDT|INR)\s?'
    r')'
    r'[\d,]+(?:\.\d{1,2})?'
    r'(?:\s*/\s*(?:mo(?:nth)?|yr|year|week|night|sqft|sqm))?',
    re.IGNORECASE,
)

# Noise tags whose text we strip before body-text extraction
_NOISE_TAGS = {'script', 'style', 'noscript', 'head', 'nav', 'footer', 'aside'}


# ------------------------------------------------------------------ #
#  Page rendering                                                      #
# ------------------------------------------------------------------ #

def _render_page(url: str) -> Optional[str]:
    """
    Fetch page HTML for any URL.
    1. Try cloudscraper (fast, handles most Cloudflare JS challenges).
    2. Fall back to Playwright (headless Firefox) for JS-heavy or protected pages.
    Returns None if the page is still blocked after all attempts.
    """
    html = _fetch_cloudscraper(url)
    if html and _has_enough_text(html):
        return html

    logger.info(f"cloudscraper insufficient, switching to Playwright: {url}")
    html = _fetch_playwright(url)

    # If Playwright still returned a Cloudflare challenge page, return None
    if html and not _has_enough_text(html):
        logger.warning(f"Cloudflare Managed Challenge could not be bypassed: {url}")
        return None

    return html


def _fetch_cloudscraper(url: str) -> Optional[str]:
    try:
        scraper = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
        )
        r = scraper.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.error(f"cloudscraper failed ({url}): {e}")
        return None


def _fetch_playwright(url: str) -> Optional[str]:
    """
    Headless Firefox bypasses Cloudflare's JS challenge automatically.
    Falls back to Chromium (with --no-sandbox for VPS/Docker) if Firefox unavailable.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("Playwright not installed — run: pip install playwright && playwright install firefox")
        return None

    try:
        with sync_playwright() as p:
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
                logger.warning("Firefox unavailable, falling back to Chromium")
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
            # Allow up to 12s for Cloudflare challenge to auto-resolve
            page.wait_for_timeout(12000)
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        logger.error(f"Playwright failed ({url}): {e}")
        return None


def _has_enough_text(html: str) -> bool:
    if 'challenges.cloudflare.com' in html or 'Just a moment' in html[:500]:
        return False
    soup = BeautifulSoup(html, 'lxml')
    body_text = soup.body.get_text(strip=True) if soup.body else ""
    return len(body_text) >= 200


# ------------------------------------------------------------------ #
#  Generic data extraction — works on any website                     #
# ------------------------------------------------------------------ #

def extract_page_data(html: str, url: str) -> Dict[str, Any]:
    """
    Extract structured data from any web page.

    Priority cascade for each field:
      JSON-LD structured data → OpenGraph/Twitter meta → visible HTML patterns

    Returns a dict with these keys (empty string / empty list when not found):
      title, price, description, location, category,
      images (list), attributes (dict of any key-value pairs on the page),
      source_url
    """
    soup = BeautifulSoup(html, 'lxml')

    ld      = _parse_json_ld(soup)
    og      = _parse_opengraph(soup)
    body_tx = _clean_body_text(soup)

    title       = _extract_title(soup, ld, og)
    price       = _extract_price(soup, ld, og, body_tx)
    description = _extract_description(soup, ld, og)
    location    = _extract_location(soup, ld, og, body_tx)
    category    = _extract_category(soup, ld, og)
    images      = _extract_images(soup, og, url)
    attributes  = _extract_attributes(soup, body_tx)

    return {
        'title':       title,
        'price':       price,
        'description': description[:2000],
        'location':    location,
        'category':    category,
        'images':      images,
        'attributes':  attributes,
        'source_url':  url,
    }


# kept for backward-compat (zscraper_service imports this name)
extract_property_data = extract_page_data


# ────────────────────────────────────────────────────────────────── #
#  Private helpers                                                    #
# ────────────────────────────────────────────────────────────────── #

def _parse_json_ld(soup: BeautifulSoup) -> Dict:
    """Return the most informative JSON-LD object on the page."""
    LOW_PRIORITY = {
        'FAQPage', 'BreadcrumbList', 'WebPage', 'WebSite', 'Organization',
        'SearchResultsPage', 'ItemList', 'SiteLinksSearchBox',
        # Agent/business types — their 'name' is the company, not the listing
        'RealEstateAgent', 'LocalBusiness', 'Corporation', 'EducationalOrganization',
        'GovernmentOrganization', 'MedicalOrganization', 'NGO', 'SportsOrganization',
    }
    best: Dict = {}
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            raw = json.loads(script.string or '{}')
            objs = raw if isinstance(raw, list) else [raw]
            for obj in objs:
                t = obj.get('@type', '')
                if t and t not in LOW_PRIORITY:
                    if not best or len(str(obj)) > len(str(best)):
                        best = obj
        except Exception:
            continue
    return best


def _parse_opengraph(soup: BeautifulSoup) -> Dict:
    og: Dict = {}
    for meta in soup.find_all('meta'):
        prop = meta.get('property', '') or meta.get('name', '')
        content = meta.get('content', '')
        if prop and content:
            og[prop] = content
    return og


def _clean_body_text(soup: BeautifulSoup) -> str:
    """Body text with noise tags removed."""
    clone = BeautifulSoup(str(soup), 'lxml')
    for tag in clone.find_all(_NOISE_TAGS):
        tag.decompose()
    return (clone.body.get_text(' ', strip=True) if clone.body else "")


def _extract_title(soup: BeautifulSoup, ld: Dict, og: Dict) -> str:
    return (
        ld.get('name') or ld.get('headline')
        or og.get('og:title') or og.get('twitter:title')
        or (soup.select_one('h1') and soup.select_one('h1').get_text(strip=True))
        or (soup.select_one('title') and soup.select_one('title').get_text(strip=True))
        or 'Untitled'
    )


def _extract_price(soup: BeautifulSoup, ld: Dict, og: Dict, body_tx: str) -> str:
    # JSON-LD offer price (skip zero/null values)
    offers = ld.get('offers', {})
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if isinstance(offers, dict):
        raw_price = offers.get('price')
        try:
            if raw_price is not None and float(str(raw_price).replace(',', '')) > 0:
                currency = offers.get('priceCurrency', '')
                return f"{currency} {raw_price}".strip()
        except (ValueError, TypeError):
            pass

    # Generic currency pattern on visible text (collapse whitespace)
    m = _PRICE_RE.search(body_tx)
    return re.sub(r'\s+', ' ', m.group(0)).strip() if m else ""


def _extract_description(soup: BeautifulSoup, ld: Dict, og: Dict) -> str:
    # JSON-LD
    desc = ld.get('description', '')
    if desc:
        return desc

    # OpenGraph / Twitter / meta description
    for key in ('og:description', 'twitter:description', 'description'):
        if og.get(key):
            return og[key]

    # First substantial paragraph in main content area
    for sel in ('main', 'article', '[role="main"]', '.content', '#content', 'body'):
        container = soup.select_one(sel)
        if not container:
            continue
        for p in container.find_all('p'):
            text = p.get_text(strip=True)
            if len(text) > 80:
                return text
    return ""


def _extract_location(soup: BeautifulSoup, ld: Dict, og: Dict, body_tx: str) -> str:
    # JSON-LD address (values may themselves be dicts, e.g. addressCountry: {"name": "Malaysia"})
    addr = ld.get('address') or ld.get('location', {})
    if isinstance(addr, dict):
        def _str(v: Any) -> str:
            if isinstance(v, dict):
                return v.get('name', '') or v.get('@id', '')
            return str(v) if v else ''
        parts = [_str(addr.get('streetAddress')), _str(addr.get('addressLocality')),
                 _str(addr.get('addressRegion')), _str(addr.get('addressCountry'))]
        loc = ', '.join(p for p in parts if p)
        if loc:
            return loc
    if isinstance(addr, str) and addr:
        return addr

    # OpenGraph locality
    for key in ('og:locality', 'og:region', 'og:country-name', 'geo.placename'):
        if og.get(key):
            return og[key]

    # Elements whose class/id hints at address or location
    for sel in ('[class*="address"]', '[class*="location"]', '[id*="address"]',
                '[class*="geo"]', '[itemprop="address"]', '[itemprop="location"]'):
        tag = soup.select_one(sel)
        if tag:
            txt = tag.get_text(' ', strip=True)
            if txt and len(txt) < 200:
                return txt
    return ""


def _extract_category(soup: BeautifulSoup, ld: Dict, og: Dict) -> str:
    # JSON-LD type or category
    cat = ld.get('@type', '') or ld.get('category', '')
    if cat:
        return cat if isinstance(cat, str) else cat[0]

    # Breadcrumb last item (often the category)
    breadcrumb = soup.select('[class*="breadcrumb"] a, [aria-label="breadcrumb"] a, nav[aria-label*="bread"] a')
    if len(breadcrumb) >= 2:
        return breadcrumb[-1].get_text(strip=True)

    # og:type
    return og.get('og:type', '')


def _extract_images(soup: BeautifulSoup, og: Dict, url: str) -> List[str]:
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    seen: Dict[str, None] = {}

    # og:image first
    if og.get('og:image'):
        seen[og['og:image']] = None

    # Twitter image
    if og.get('twitter:image'):
        seen[og['twitter:image']] = None

    # All <img> tags — skip SVGs, data URIs, tracking pixels
    for img in soup.find_all('img'):
        src = (img.get('data-src') or img.get('data-lazy-src')
               or img.get('data-original') or img.get('src', ''))
        if not src or src.startswith('data:'):
            continue
        full = urljoin(base, src)
        parsed = urlparse(full)
        if parsed.scheme not in ('http', 'https'):
            continue
        # Skip tiny images (tracking pixels, icons often end in .gif or have w/h=1)
        w = img.get('width', '0')
        h = img.get('height', '0')
        if str(w) == '1' or str(h) == '1':
            continue
        # Skip SVG and tiny icons
        if full.lower().endswith('.svg'):
            continue
        seen[full] = None

    return list(seen.keys())[:20]


def _extract_attributes(soup: BeautifulSoup, body_tx: str) -> Dict[str, str]:
    """
    Generic key-value attribute extractor.
    Captures specs/details from: <dl>, 2-col <table>, and label-value HTML patterns.
    Works for property sites, e-commerce, job boards, news, etc.
    """
    attrs: Dict[str, str] = {}

    # ── Definition lists <dl><dt>key</dt><dd>value</dd></dl> ─────────
    for dl in soup.find_all('dl'):
        dts = dl.find_all('dt')
        dds = dl.find_all('dd')
        for dt, dd in zip(dts, dds):
            k = dt.get_text(strip=True)
            v = dd.get_text(' ', strip=True)
            if k and v and len(k) < 80:
                attrs[k] = v[:200]

    # ── 2-column tables ───────────────────────────────────────────────
    for table in soup.find_all('table'):
        for row in table.find_all('tr'):
            cells = row.find_all(['td', 'th'])
            if len(cells) == 2:
                k = cells[0].get_text(strip=True)
                v = cells[1].get_text(' ', strip=True)
                if k and v and len(k) < 80:
                    attrs[k] = v[:200]

    # ── Label/value sibling patterns ─────────────────────────────────
    # Look for elements with class names containing: label, key, spec, detail, feature, attr, info
    label_sel = (
        '[class*="label"],[class*="-key"],[class*="spec-name"],'
        '[class*="detail-name"],[class*="attr-name"],[class*="feature-name"],'
        '[class*="info-label"],[class*="property-label"]'
    )
    for label_tag in soup.select(label_sel):
        k = label_tag.get_text(strip=True)
        if not k or len(k) > 80:
            continue
        # Value is the next sibling element
        val_tag = label_tag.find_next_sibling()
        if not val_tag:
            # Or the parent's next sibling
            val_tag = label_tag.parent and label_tag.parent.find_next_sibling()
        if val_tag and hasattr(val_tag, 'get_text'):
            v = val_tag.get_text(' ', strip=True)
            if v and len(v) < 200:
                attrs[k] = v

    # ── Microdata itemprop ────────────────────────────────────────────
    for el in soup.find_all(itemprop=True):
        k = el.get('itemprop', '')
        v = el.get('content') or el.get_text(' ', strip=True)
        if k and v and len(k) < 80 and k not in ('name', 'description', 'image', 'url'):
            attrs[k] = str(v)[:200]

    return attrs


# ------------------------------------------------------------------ #
#  Image download                                                      #
# ------------------------------------------------------------------ #

def download_image(img_url: str, slug: str) -> Optional[str]:
    """Download image to MEDIA_ROOT/images/<slug>/ and return relative path."""
    try:
        dest_dir = Path(settings.MEDIA_ROOT) / 'images' / slug
        dest_dir.mkdir(parents=True, exist_ok=True)

        filename = os.path.basename(urlparse(img_url).path) or 'image.jpg'
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
    Scrape any URL and create a Post draft in the database.
    Returns (success: bool, message: str).
    """
    from app.models import Post

    html = _render_page(url)
    if not html:
        return False, f"Failed to fetch page: {url}"

    data = extract_page_data(html, url)

    if not data.get('title') or data['title'] == 'Untitled':
        return False, "Could not extract a title from the page"

    # Map generic attributes → known Post fields where applicable
    attrs = data.get('attributes', {})
    bedrooms     = _attr_value(attrs, ['bedroom', 'bed', 'bilik tidur'])
    bathrooms    = _attr_value(attrs, ['bathroom', 'bath', 'bilik air'])
    floor_size   = _attr_value(attrs, ['floor size', 'built-up', 'land area', 'size', 'sqft', 'sqm'])
    property_type = _attr_value(attrs, ['property type', 'type', 'listing type', 'category'])

    # Fallback: parse from body text for common patterns
    body = html  # raw html used for regex fallbacks
    if not bedrooms:
        m = re.search(r'(\d{1,2})\s*bed(?:room)?s?', data['description'], re.IGNORECASE)
        bedrooms = m.group(1) if m else ""
    if not bathrooms:
        m = re.search(r'(\d{1,2})\s*bath(?:room)?s?', data['description'], re.IGNORECASE)
        bathrooms = m.group(1) if m else ""
    if not floor_size:
        m = re.search(r'([\d,]+)\s*(?:sq\.?\s?ft|sqft|sqm)', data['description'], re.IGNORECASE)
        floor_size = m.group(1) if m else ""

    # Build a unique slug
    base_slug = slugify(data['title'])[:200] or 'page'
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
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        floor_size=floor_size,
        location=data['location'],
        property_type=property_type or data['category'],
        raw_data=data['attributes'],
        status=Post.STATUS_DRAFT,
    )

    return True, f"Draft created: '{post.title}' (id={post.pk})"


def _attr_value(attrs: Dict[str, str], keywords: List[str]) -> str:
    """Case-insensitive lookup of attrs dict by keyword prefix/contains."""
    for k, v in attrs.items():
        for kw in keywords:
            if kw.lower() in k.lower():
                return v
    return ""
