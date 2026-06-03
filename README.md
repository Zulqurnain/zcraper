# ZCraper

A generic web scraping library with a **gRPC API**, **Django integration**, and a **self-learning PatternAI** that gets smarter with every URL you give it.

Built by [Zulqurnain Haider](https://zulqurnainj.com)

---

## Features

- **Works on any website** — property portals, e-commerce, job boards, news, anything
- **Cloudflare bypass** — headless Firefox auto-solves JS challenges; cloudscraper as fast-path fallback
- **Full image pipeline** — finds images from `<img>` tags, lazy-load attributes, embedded JSON (`__NEXT_DATA__`), CSS backgrounds; downloads them to disk
- **gRPC API** — `Scrape` (unary) + `StreamImageURLs` (server-side streaming) on port `50051`
- **Django Post drafts** — title, price, description, location, bedrooms, bathrooms, floor size, attributes, image URLs and downloaded paths
- **PatternAI** — self-contained domain pattern learner; no external API keys; reverse-engineers CSS selectors from every successful scrape and reuses them instantly on repeat visits

---

## Tested Sites

| Site | Result |
|---|---|
| PropertyGuru | ✅ |
| iProperty | ✅ |
| Speedhome | ✅ |
| Mudah.my | ✅ |
| DotProperty | ✅ |
| Nextsix | ✅ |
| FazWaz | ✅ |
| AsiaVillas | ✅ |
| NuProp | ✅ |
| Zameen.com | ✅ |
| GlobalListings | ✅ |
| Rentola | ✅ |
| RentInKL | ✅ |
| MutiaraLake | ✅ |

---

## Project Structure

```
zcraper/
├── app/
│   ├── models.py          # Post + ScraperPattern Django models
│   ├── scraper.py         # Core fetch + extraction engine
│   └── pattern_ai.py      # Self-learning PatternAI
├── server/
│   ├── zscraper_service.py  # gRPC service implementation
│   ├── zcraper_pb2.py       # Generated proto stubs
│   └── zcraper_pb2_grpc.py
├── client/
│   └── client.py          # Example async gRPC client
├── proto/
│   └── zcraper.proto      # gRPC contract
├── mysite/                # Django project settings
├── run_server.py          # Entry point
├── Dockerfile
└── requirements.txt
```

---

## Quick Start

### 1. Install

```bash
git clone https://github.com/Zulqurnain/zcraper.git
cd zcraper
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install firefox chromium --with-deps
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env — set DJANGO_SECRET_KEY for production
```

### 3. Setup database

```bash
python manage.py migrate
```

### 4. Start the gRPC server

```bash
python run_server.py
# Listening on port 50051
```

### 5. Scrape any URL

```bash
# Python client
python -m client.client https://www.propertyguru.com.my/property-listing/...

# Stream images live
python -m client.client https://www.mudah.my/... --stream
```

---

## gRPC API

Defined in `proto/zcraper.proto`:

```proto
service ZScraperService {
  rpc Scrape (ZScrapeRequest) returns (ZScrapeResponse);
  rpc StreamImageURLs (ZScrapeRequest) returns (stream ImageURL);
}
```

**Request:**
```json
{ "url": "https://any-website.com/listing/..." }
```

**Response:**
```json
{
  "success": true,
  "message": "Draft created: 'Property Title' (id=1) — 24 images found, 24 downloaded",
  "post_id": 1,
  "draft_url": "/admin/app/post/1/change/"
}
```

---

## PatternAI

ZCraper includes a self-contained learning system that requires **no external APIs or API keys**.

- After each successful scrape it reverse-engineers which CSS selectors produced each field
- Stores one `ScraperPattern` row per domain in the database
- On the next visit to the same domain, cached selectors are applied instantly (0 ms, no network)
- Confidence score grows from 0 → 1.0 as more pages from the same site are scraped

```
First scrape  →  extract data  →  learn selectors  →  save (conf=0.85)
Second scrape →  apply cache   →  patch gaps        →  update (conf=1.00)
```

---

## VPS Setup (Ubuntu/Debian)

```bash
git clone https://github.com/Zulqurnain/zcraper.git && cd zcraper
bash setup_vps.sh        # installs all system deps, browsers, runs migrations
source venv/bin/activate
python run_server.py     # gRPC on :50051
```

`setup_vps.sh` installs all required Linux system libraries for headless Firefox and Chromium, generates a secure `DJANGO_SECRET_KEY`, and runs migrations automatically.

## Docker

```bash
docker build -t zcraper .
docker run -p 50051:50051 \
  -e DJANGO_SECRET_KEY=your-secret-key \
  zcraper
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | `change-me-in-production` | Django secret key — **must be set in production** |
| `DJANGO_DEBUG` | `true` | Set to `false` in production |
| `DJANGO_ALLOWED_HOSTS` | `*` | Comma-separated list of allowed hosts |

---

## License

MIT © [Zulqurnain Haider](https://zulqurnainj.com)
