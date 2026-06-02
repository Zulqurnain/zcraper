"""
gRPC server for EthicalWebScraper.

Start with:
    python server.py              # listens on 0.0.0.0:50051

Then call from any gRPC client (see client.py for a Python example).
"""

import grpc
import logging
from concurrent import futures

import scraper_pb2
import scraper_pb2_grpc
from scraper import EthicalWebScraper

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

PORT = 50051


class ScraperServicer(scraper_pb2_grpc.ScraperServiceServicer):

    def _run_scraper(self, request) -> tuple:
        """Shared setup for both RPC methods. Returns (results, csv_path, error)."""
        url = request.url or "https://books.toscrape.com/"
        max_pages = request.max_pages if request.max_pages > 0 else 5
        force_js = request.force_js

        logger.info(
            f"Scrape request: url={url} max_pages={max_pages} force_js={force_js}"
        )

        try:
            scraper = EthicalWebScraper(url, max_pages=max_pages, force_js=force_js)
            results = scraper.scrape()

            csv_path = ""
            if request.output_csv:
                scraper.save_to_csv(results, filename=request.output_csv)
                csv_path = request.output_csv

            return results, csv_path, ""
        except Exception as e:
            logger.error(f"Scrape failed: {e}")
            return [], "", str(e)

    def _to_proto(self, result: dict) -> scraper_pb2.PageResult:
        return scraper_pb2.PageResult(
            url=result.get("url", ""),
            title=result.get("title", ""),
            headings=result.get("headings", []),
            paragraphs=result.get("paragraphs", []),
            links=result.get("links", []),
        )

    def Scrape(self, request, context):
        results, csv_path, error = self._run_scraper(request)
        if error:
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(error)
            return scraper_pb2.ScrapeResponse(error=error)

        return scraper_pb2.ScrapeResponse(
            pages=[self._to_proto(r) for r in results],
            total_pages=len(results),
            csv_path=csv_path,
        )

    def ScrapeStream(self, request, context):
        url = request.url or "https://books.toscrape.com/"
        max_pages = request.max_pages if request.max_pages > 0 else 5
        force_js = request.force_js

        logger.info(f"ScrapeStream request: url={url} max_pages={max_pages}")

        try:
            scraper = EthicalWebScraper(url, max_pages=max_pages, force_js=force_js)

            # Patch scrape() to yield results one by one via a generator
            for result in _stream_scrape(scraper):
                yield self._to_proto(result)
        except Exception as e:
            logger.error(f"ScrapeStream failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))


def _stream_scrape(scraper: EthicalWebScraper):
    """Drives the scraper page-by-page so ScrapeStream can yield results live."""
    import time

    try:
        while scraper.url_queue and len(scraper.visited_urls) < scraper.max_pages:
            current_url = scraper.url_queue.pop(0)
            if current_url in scraper.visited_urls:
                continue
            scraper.visited_urls.add(current_url)

            html = scraper.fetch_page(current_url)
            if not html:
                continue

            data = scraper.parse_page(html, current_url)

            for link in data["links"]:
                if (
                    link not in scraper.visited_urls
                    and len(scraper.url_queue) < scraper.max_pages
                ):
                    scraper.url_queue.append(link)

            yield data
            time.sleep(0.2)
    finally:
        scraper._cleanup()


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    scraper_pb2_grpc.add_ScraperServiceServicer_to_server(ScraperServicer(), server)
    server.add_insecure_port(f"[::]:{PORT}")
    server.start()
    logger.info(f"gRPC ScraperService listening on port {PORT}")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
