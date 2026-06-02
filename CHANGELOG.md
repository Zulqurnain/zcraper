# Changelog

## [1.0.0] — 2026-06-03

### Added
- Generic web scraper supporting any website (property, e-commerce, jobs, news)
- Cloudflare bypass via headless Firefox + cloudscraper fallback
- Full image pipeline: `<img>` tags, lazy-load attrs, `__NEXT_DATA__`, CSS backgrounds
- Image downloading with Referer spoofing and Next.js proxy unwrapping
- gRPC API: `Scrape` (unary) + `StreamImageURLs` (server-side streaming)
- Django `Post` model with title, price, description, location, bedrooms, bathrooms, floor size, raw attributes, image URLs, downloaded image paths
- **PatternAI**: self-contained domain pattern learner — no external APIs, learns CSS selectors from every scrape
- `ScraperPattern` model for per-domain pattern caching
- Docker support with Firefox + Chromium pre-installed
- Environment-based configuration (no secrets in code)

### Tested sites
PropertyGuru · iProperty · Speedhome · Mudah.my · DotProperty · Nextsix ·
FazWaz · AsiaVillas · NuProp · Zameen.com · GlobalListings · Rentola · RentInKL · MutiaraLake
