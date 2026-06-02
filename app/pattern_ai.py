"""
PatternAI — self-contained domain pattern learner for zcraper.

No external API keys. No third-party AI services. Fully self-sufficient.

How it works
────────────
1. After every successful scrape, learn() reverse-engineers which CSS selector
   produced each field value and saves it to ScraperPattern (one DB row / domain).
2. On the next scrape of the same domain, enhance() applies those cached selectors
   instantly — sub-millisecond, zero network calls.
3. Confidence score (0–1) grows with each successful scrape; patterns are only
   replaced when a better result is found.

The library IS the AI — it gets smarter with every URL you give it.
"""

import re
import logging
from urllib.parse import urlparse
from bs4 import BeautifulSoup, Tag
from typing import Dict

logger = logging.getLogger(__name__)

_FIELDS = ("title", "price", "description", "location")


class PatternAI:

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def enhance(self, soup: BeautifulSoup, url: str, data: Dict) -> Dict:
        """
        Fill empty fields in `data` using cached selectors for this domain.
        Called after the generic extraction cascade so it patches any gaps.
        """
        from app.models import ScraperPattern
        domain = _domain(url)
        try:
            pat = ScraperPattern.objects.get(domain=domain)
        except ScraperPattern.DoesNotExist:
            return data

        if pat.confidence < 0.3:
            return data

        for field, sel in (
            ("title",       pat.title_sel),
            ("price",       pat.price_sel),
            ("description", pat.desc_sel),
            ("location",    pat.location_sel),
        ):
            if not sel or data.get(field):
                continue
            tag = soup.select_one(sel)
            if tag:
                val = tag.get("content") or tag.get_text(" ", strip=True)
                if val:
                    data[field] = val[:2000] if field == "description" else val[:500]
                    logger.debug(f"PatternAI [{domain}] patched '{field}' via '{sel}'")

        if pat.image_sel and not data.get("images"):
            imgs = [
                t.get("src") or t.get("data-src") or ""
                for t in soup.select(pat.image_sel)
            ]
            data["images"] = [i for i in imgs if i and not i.startswith("data:")][:50]

        return data

    def learn(self, url: str, soup: BeautifulSoup, data: Dict) -> None:
        """
        Reverse-engineer and cache the CSS selectors that produced each field.
        Overwrites only when this scrape is equal or better than the last.
        """
        from app.models import ScraperPattern
        domain = _domain(url)
        confidence = _confidence(data)

        pat, _ = ScraperPattern.objects.get_or_create(domain=domain)

        if confidence >= pat.confidence:
            discovered = self._discover(soup, data)
            pat.title_sel    = discovered.get("title",       pat.title_sel)
            pat.price_sel    = discovered.get("price",       pat.price_sel)
            pat.desc_sel     = discovered.get("description", pat.desc_sel)
            pat.location_sel = discovered.get("location",    pat.location_sel)
            pat.image_sel    = discovered.get("images",      pat.image_sel)
            pat.confidence   = max(confidence, pat.confidence)

        pat.scrape_count += 1
        pat.save()
        logger.info(f"PatternAI [{domain}] conf={pat.confidence:.2f} scrapes={pat.scrape_count}")

    # ------------------------------------------------------------------ #
    #  Pattern discovery                                                   #
    # ------------------------------------------------------------------ #

    def _discover(self, soup: BeautifulSoup, data: Dict) -> Dict:
        """Find the CSS selector that produced each extracted value."""
        found: Dict[str, str] = {}

        for field in _FIELDS:
            value = data.get(field, "")
            if value and len(value) >= 3:
                sel = _find_selector(soup, value[:60])
                if sel:
                    found[field] = sel

        imgs = data.get("images", [])
        if imgs:
            found["images"] = _image_selector(soup, imgs[0])

        return found


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

def _domain(url: str) -> str:
    return urlparse(url).netloc.lstrip("www.")


def _confidence(data: Dict) -> float:
    """Score 0.0–1.0 based on fields successfully extracted."""
    score = 0.0
    if data.get("title") and data["title"] not in ("Untitled", ""):
        score += 0.25
    if data.get("price"):
        score += 0.20
    if data.get("description") and len(data["description"]) > 50:
        score += 0.20
    if data.get("location"):
        score += 0.15
    if data.get("images"):
        score += 0.20
    return round(min(score, 1.0), 2)


def _find_selector(soup: BeautifulSoup, value: str) -> str:
    """Return the tightest CSS selector whose element text contains `value`."""
    needle = value.strip()[:50]
    for tag in soup.find_all(True):
        if not isinstance(tag, Tag):
            continue
        text = tag.get_text(" ", strip=True)
        if needle.lower() in text.lower() and len(text) < len(needle) * 3:
            return _css_selector(tag)
    return ""


def _css_selector(el: Tag) -> str:
    if el.get("id"):
        return f"#{el['id']}"
    classes = [c for c in (el.get("class") or []) if not re.search(r'\d{3,}|active|open|hover', c)]
    if classes:
        return f"{el.name}.{classes[0]}"
    if el.parent and el.parent.get("class"):
        return f".{el.parent['class'][0]} > {el.name}"
    return el.name


def _image_selector(soup: BeautifulSoup, first_img_url: str) -> str:
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if first_img_url in src or src in first_img_url:
            return _css_selector(img)
    return "img"
