"""
PatternAI — lightweight domain pattern learner for zcraper.

How it works
────────────
1. After every successful scrape, `learn()` finds the CSS selector that
   produced each field and saves it to ScraperPattern (one row per domain).
2. On the next scrape of the same domain, `enhance()` applies those cached
   selectors FIRST — no API call, sub-millisecond.
3. Only when a brand-new domain yields low confidence AND an OpenRouter /
   Nous API key is set, `_llm_suggest()` sends a tiny HTML snippet (~2 KB)
   to a free DeepSeek model and stores the suggested selectors.

Zero heavy dependencies — only stdlib + requests (already in requirements).
"""

import os
import re
import json
import logging
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup, Tag
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Free model on OpenRouter — change via ZCRAPER_LLM_MODEL env var
_DEFAULT_LLM_MODEL = "deepseek/deepseek-v3-0324:free"
_LLM_URL           = "https://openrouter.ai/api/v1/chat/completions"

# Fields we try to learn selectors for
_FIELDS = ("title", "price", "description", "location")


class PatternAI:
    """Singleton-style helper; instantiate once per request."""

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def enhance(self, soup: BeautifulSoup, url: str, data: Dict) -> Dict:
        """
        Apply cached domain patterns to fill empty fields in `data`.
        Called BEFORE the generic extraction cascade completes, so it can
        patch gaps without replacing values that already exist.
        """
        from app.models import ScraperPattern
        domain = _domain(url)
        try:
            pat = ScraperPattern.objects.get(domain=domain)
        except ScraperPattern.DoesNotExist:
            return data
        if pat.confidence < 0.3:
            return data

        sel_map = {
            "title":       pat.title_sel,
            "price":       pat.price_sel,
            "description": pat.desc_sel,
            "location":    pat.location_sel,
        }
        for field, sel in sel_map.items():
            if not sel or data.get(field):
                continue
            tag = soup.select_one(sel)
            if tag:
                val = tag.get("content") or tag.get_text(" ", strip=True)
                if val:
                    data[field] = val[:2000] if field == "description" else val[:500]
                    logger.debug(f"PatternAI [{domain}] filled '{field}' via '{sel}'")

        # Image selector
        if pat.image_sel and not data.get("images"):
            imgs = [
                t.get("src") or t.get("data-src") or ""
                for t in soup.select(pat.image_sel)
            ]
            data["images"] = [i for i in imgs if i and not i.startswith("data:")][:50]

        return data

    def learn(self, url: str, soup: BeautifulSoup, data: Dict) -> None:
        """
        Learn and cache which CSS selectors produced each field.
        Runs asynchronously-safe (Django ORM, no threads needed).
        """
        from app.models import ScraperPattern
        domain = _domain(url)
        confidence = _confidence(data)

        pat, created = ScraperPattern.objects.get_or_create(domain=domain)

        # Only overwrite if we got a better result than last time
        if created or confidence >= pat.confidence:
            selectors = self._discover(soup, data)
            pat.title_sel    = selectors.get("title",       pat.title_sel)
            pat.price_sel    = selectors.get("price",       pat.price_sel)
            pat.desc_sel     = selectors.get("description", pat.desc_sel)
            pat.location_sel = selectors.get("location",    pat.location_sel)
            pat.image_sel    = selectors.get("images",      pat.image_sel)
            pat.confidence   = max(confidence, pat.confidence)
            pat.source       = "auto"

        pat.scrape_count += 1
        pat.save()

        # Call LLM only once for new domains where confidence is poor
        if confidence < 0.4 and pat.scrape_count <= 1 and pat.source != "llm":
            self._llm_suggest(domain, soup, pat)

    # ------------------------------------------------------------------ #
    #  Pattern discovery                                                   #
    # ------------------------------------------------------------------ #

    def _discover(self, soup: BeautifulSoup, data: Dict) -> Dict:
        """Return {field: css_selector} for each field that has a value."""
        found: Dict[str, str] = {}

        for field in _FIELDS:
            value = data.get(field, "")
            if not value or len(value) < 3:
                continue
            sel = _find_selector(soup, value[:60])
            if sel:
                found[field] = sel

        # Image selector — find the parent class used for gallery images
        imgs = data.get("images", [])
        if imgs:
            found["images"] = _image_selector(soup, imgs[0])

        return found

    # ------------------------------------------------------------------ #
    #  LLM suggestion (optional, free model, tiny payload)               #
    # ------------------------------------------------------------------ #

    def _llm_suggest(self, domain: str, soup: BeautifulSoup, pat) -> None:
        api_key = (
            os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("NOUS_API_KEY")
        )
        if not api_key:
            return

        model = os.environ.get("ZCRAPER_LLM_MODEL", _DEFAULT_LLM_MODEL)

        # Send only the visible structure — strip scripts/styles, cap at 2500 chars
        mini = BeautifulSoup(str(soup), "lxml")
        for tag in mini.find_all(["script", "style", "noscript", "head"]):
            tag.decompose()
        snippet = str(mini)[:2500]

        prompt = (
            f"You are a web scraping assistant.\n"
            f"Domain: {domain}\n"
            f"HTML snippet (truncated):\n{snippet}\n\n"
            f"Return ONLY a compact JSON object with CSS selectors for these fields "
            f"(use empty string if not found): title, price, description, location, images.\n"
            f"Example: {{\"title\":\"h1.listing-title\",\"price\":\".price\","
            f"\"description\":\".desc\",\"location\":\".address\",\"images\":\"img.gallery\"}}"
        )

        try:
            resp = requests.post(
                _LLM_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 200,
                },
                timeout=15,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            m = re.search(r'\{[^{}]+\}', content, re.DOTALL)
            if not m:
                return
            sels = json.loads(m.group(0))
            logger.info(f"PatternAI LLM suggested selectors for {domain}: {sels}")

            pat.title_sel    = sels.get("title",       pat.title_sel)    or pat.title_sel
            pat.price_sel    = sels.get("price",       pat.price_sel)    or pat.price_sel
            pat.desc_sel     = sels.get("description", pat.desc_sel)     or pat.desc_sel
            pat.location_sel = sels.get("location",    pat.location_sel) or pat.location_sel
            pat.image_sel    = sels.get("images",      pat.image_sel)    or pat.image_sel
            pat.confidence   = max(pat.confidence, 0.5)
            pat.source       = "llm"
            pat.save()

        except Exception as e:
            logger.warning(f"PatternAI LLM call failed for {domain}: {e}")


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

def _domain(url: str) -> str:
    return urlparse(url).netloc.lstrip("www.")


def _confidence(data: Dict) -> float:
    """0.0–1.0 based on how many fields were successfully extracted."""
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
    """Return a CSS selector for the element whose text matches `value`."""
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
        pc = el.parent["class"][0]
        return f".{pc} > {el.name}"
    return el.name


def _image_selector(soup: BeautifulSoup, first_img_url: str) -> str:
    """Find the CSS selector of the img tag that has this URL."""
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if first_img_url in src or src in first_img_url:
            return _css_selector(img)
    return "img"
