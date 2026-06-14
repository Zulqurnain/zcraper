"""
Shared PocketBase HTTP client for zcraper.
Replaces Django ORM with PocketBase REST API calls.
"""
import os
import time
import httpx
import logging

import pathlib as _pl

def _load_dotenv():
    """Load .env from the project root without requiring python-dotenv."""
    env_path = _pl.Path(__file__).resolve().parent.parent / '.env'
    if not env_path.exists():
        return
    for _line in env_path.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith('#') or '=' not in _line:
            continue
        _k, _, _v = _line.partition('=')
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

_load_dotenv()

logger = logging.getLogger(__name__)

PB_URL = os.environ.get("PB_URL", "http://127.0.0.1:8090")
PB_ADMIN_EMAIL = os.environ.get("PB_ADMIN_EMAIL", "zulqurnainapps@gmail.com")
PB_ADMIN_PASSWORD = os.environ.get("PB_ADMIN_PASSWORD", "")

_token: str = ""
_token_expires: float = 0.0


def admin_token() -> str:
    global _token, _token_expires
    if _token and time.time() < _token_expires:
        return _token
    r = httpx.post(
        f"{PB_URL}/api/collections/_superusers/auth-with-password",
        json={"identity": PB_ADMIN_EMAIL, "password": PB_ADMIN_PASSWORD},
        timeout=10,
    )
    if r.status_code != 200:
        raise RuntimeError(f"PocketBase auth failed: {r.text[:200]}")
    _token = r.json()["token"]
    _token_expires = time.time() + 570
    return _token


def pb_get(path: str, params: dict = None) -> dict:
    r = httpx.get(
        f"{PB_URL}{path}",
        params=params,
        headers={"Authorization": admin_token()},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def pb_post(path: str, data: dict) -> dict:
    r = httpx.post(
        f"{PB_URL}{path}",
        json=data,
        headers={"Authorization": admin_token()},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def pb_patch(path: str, data: dict) -> dict:
    r = httpx.patch(
        f"{PB_URL}{path}",
        json=data,
        headers={"Authorization": admin_token()},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


# ── zc_posts helpers ──────────────────────────────────────────────

def slug_exists(slug: str) -> bool:
    try:
        result = pb_get(
            "/api/collections/zc_posts/records",
            {"filter": f'slug="{slug}"', "perPage": 1},
        )
        return result.get("totalItems", 0) > 0
    except Exception:
        return False


def create_post(data: dict) -> dict:
    return pb_post("/api/collections/zc_posts/records", data)


def update_post(record_id: str, data: dict) -> dict:
    return pb_patch(f"/api/collections/zc_posts/records/{record_id}", data)


# ── zc_scraper_patterns helpers ───────────────────────────────────

def get_pattern(domain: str) -> dict | None:
    try:
        result = pb_get(
            "/api/collections/zc_scraper_patterns/records",
            {"filter": f'domain="{domain}"', "perPage": 1},
        )
        items = result.get("items", [])
        return items[0] if items else None
    except Exception as e:
        logger.warning(f"get_pattern error: {e}")
        return None


def get_or_create_pattern(domain: str) -> tuple[dict, bool]:
    """Returns (pattern_dict, created: bool)."""
    pat = get_pattern(domain)
    if pat:
        return pat, False
    new_pat = pb_post(
        "/api/collections/zc_scraper_patterns/records",
        {"domain": domain, "confidence": 0.0, "scrape_count": 0},
    )
    return new_pat, True


def save_pattern(pattern: dict) -> dict:
    record_id = pattern["id"]
    data = {k: v for k, v in pattern.items() if k not in ("id", "collectionId", "collectionName", "created", "updated")}
    return pb_patch(f"/api/collections/zc_scraper_patterns/records/{record_id}", data)
