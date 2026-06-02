"""
gRPC ZScraper service — Django-integrated async server.
"""

import asyncio
import logging
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mysite.settings')
import django
django.setup()

import grpc
import grpc.aio
from server import zcraper_pb2, zcraper_pb2_grpc
from app.scraper import scrape_and_create_draft, _render_page, extract_property_data, download_image
from app.models import Post
from django.utils.text import slugify

logger = logging.getLogger(__name__)


class ZScraperService(zcraper_pb2_grpc.ZScraperServiceServicer):

    async def Scrape(
        self,
        request: zcraper_pb2.ZScrapeRequest,
        context: grpc.aio.ServicerContext,
    ) -> zcraper_pb2.ZScrapeResponse:
        url = request.url.strip()
        if not url:
            return zcraper_pb2.ZScrapeResponse(success=False, message="URL is required")

        logger.info(f"Scrape request: {url}")

        loop = asyncio.get_event_loop()
        success, msg = await loop.run_in_executor(None, scrape_and_create_draft, url)

        post_id = 0
        draft_url = ""
        if success:
            try:
                post = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: Post.objects.latest('created_at')
                )
                post_id = post.pk
                draft_url = f"/admin/app/post/{post_id}/change/"
            except Exception:
                pass

        return zcraper_pb2.ZScrapeResponse(
            success=success,
            message=msg,
            post_id=post_id,
            draft_url=draft_url,
        )

    async def StreamImageURLs(
        self,
        request: zcraper_pb2.ZScrapeRequest,
        context: grpc.aio.ServicerContext,
    ):
        url = request.url.strip()
        if not url:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "URL is required")
            return

        loop = asyncio.get_event_loop()
        html = await loop.run_in_executor(None, _render_page, url)
        if not html:
            await context.abort(grpc.StatusCode.INTERNAL, "Failed to render page")
            return

        data = extract_property_data(html, url)
        slug = slugify(data.get('title') or 'property')

        for img_url in data.get('image_urls', []):
            await loop.run_in_executor(None, download_image, img_url, slug)
            yield zcraper_pb2.ImageURL(url=img_url)
            await asyncio.sleep(0.1)


async def serve() -> None:
    server = grpc.aio.server()
    zcraper_pb2_grpc.add_ZScraperServiceServicer_to_server(ZScraperService(), server)
    server.add_insecure_port('[::]:50051')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger.info("ZScraperService listening on port 50051")
    await server.start()
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())
