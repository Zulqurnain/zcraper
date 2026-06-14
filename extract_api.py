"""
Thin HTTP API wrapping zcraper's render + extract pipeline.
POST /extract  { "url": "https://..." }
           →   { ok, title, price, currency, location, bedrooms, bathrooms, areaSqft, images, ... }
Run: python extract_api.py   (listens on 127.0.0.1:5500)
"""
import os, sys, re, logging
from flask import Flask, request, jsonify

sys.path.insert(0, os.path.dirname(__file__))
from app.scraper import _render_page, extract_page_data, _attr_value

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

def _num(v):
    if not v:
        return None
    try:
        return int(re.sub(r"[^\d]", "", str(v))) or None
    except Exception:
        return None

@app.post("/extract")
def extract():
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "url required"}), 400

    log.info(f"Extracting: {url}")
    try:
        html = _render_page(url)
        if not html:
            return jsonify({"ok": False, "error": "Failed to fetch page"}), 422

        data = extract_page_data(html, url)
        attrs = data.get("attributes", {})

        bedrooms  = _attr_value(attrs, ["bedroom", "bed", "bilik tidur"])
        bathrooms = _attr_value(attrs, ["bathroom", "bath", "bilik air"])
        floor_sqft = _attr_value(attrs, ["floor size", "built-up", "land area", "size", "sqft", "sqm"])

        # Currency split from price string
        price_raw = data.get("price", "")
        currency = ""
        price_num = None
        if price_raw:
            m = re.match(r"([£$€¥₹₩₪₺₽฿]|RM|USD|EUR|GBP|MYR|AUD|NZD|SGD|CAD)\s*", price_raw, re.IGNORECASE)
            if m:
                currency = m.group(1).upper()
                num_str = price_raw[m.end():]
                price_num = _num(num_str)

        return jsonify({
            "ok": True,
            "title": data.get("title", ""),
            "price": price_raw,
            "priceNum": price_num,
            "currency": currency,
            "description": data.get("description", ""),
            "location": data.get("location", ""),
            "category": data.get("category", ""),
            "bedrooms": _num(bedrooms),
            "bathrooms": _num(bathrooms),
            "areaSqft": _num(floor_sqft),
            "images": data.get("images", []),
            "attributes": attrs,
            "sourceUrl": url,
        })
    except Exception as e:
        log.exception(f"Extract error for {url}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/scrape")
def scrape():
    """Scrape URL and save draft to PocketBase."""
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "url required"}), 400

    try:
        from app.scraper import scrape_and_create_draft
        success, message = scrape_and_create_draft(url)
        return jsonify({"ok": success, "message": message})
    except Exception as e:
        log.exception(f"Scrape error for {url}")
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5500))
    app.run(host="127.0.0.1", port=port)
