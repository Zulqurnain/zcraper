"""
Async gRPC client for ZScraperService.

Usage:
    python -m client.client                        # default PropertyGuru URL
    python -m client.client https://example.com    # custom URL
    python -m client.client <url> --stream         # stream image URLs
"""

import asyncio
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import grpc
import grpc.aio
from server import zcraper_pb2, zcraper_pb2_grpc

DEFAULT_URL = (
    "https://www.propertyguru.com.my/property-listing/"
    "kenwingston-avenue-for-rent-by-crystal-chin-501171214"
)
SERVER = "localhost:50051"


async def scrape(stub, url: str):
    req = zcraper_pb2.ZScrapeRequest(url=url)
    resp = await stub.Scrape(req)
    print(f"\nSuccess : {resp.success}")
    print(f"Message : {resp.message}")
    if resp.post_id:
        print(f"Post ID : {resp.post_id}")
        print(f"Draft   : {resp.draft_url}")


async def stream_images(stub, url: str):
    req = zcraper_pb2.ZScrapeRequest(url=url)
    print(f"\nStreaming image URLs from: {url}\n")
    async for img in stub.StreamImageURLs(req):
        print(f"  {img.url}")


async def main():
    parser = argparse.ArgumentParser(description='ZScraperService gRPC client')
    parser.add_argument('url', nargs='?', default=DEFAULT_URL)
    parser.add_argument('--stream', action='store_true', help='Stream image URLs instead')
    parser.add_argument('--server', default=SERVER)
    args = parser.parse_args()

    async with grpc.aio.insecure_channel(args.server) as channel:
        stub = zcraper_pb2_grpc.ZScraperServiceStub(channel)
        if args.stream:
            await stream_images(stub, args.url)
        else:
            await scrape(stub, args.url)


if __name__ == '__main__':
    asyncio.run(main())
