"""
Example gRPC client for EthicalWebScraper.

Usage:
    python client.py                                      # scrape default URL
    python client.py https://example.com                  # scrape custom URL
    python client.py https://example.com --pages 10 --js  # JS mode
    python client.py https://example.com --stream         # stream results live
"""

import argparse
import grpc
import scraper_pb2
import scraper_pb2_grpc

SERVER = "localhost:50051"


def scrape(stub, url: str, pages: int, force_js: bool, output_csv: str):
    req = scraper_pb2.ScrapeRequest(
        url=url, max_pages=pages, force_js=force_js, output_csv=output_csv
    )
    resp = stub.Scrape(req)

    if resp.error:
        print(f"Error: {resp.error}")
        return

    print(f"\nScraped {resp.total_pages} page(s):\n")
    for page in resp.pages:
        print(f"  URL   : {page.url}")
        print(f"  Title : {page.title}")
        print(f"  H1-H3 : {'; '.join(page.headings[:3])}")
        print(f"  Para  : {'; '.join(page.paragraphs[:2])}")
        print()

    if resp.csv_path:
        print(f"CSV saved to: {resp.csv_path}")


def scrape_stream(stub, url: str, pages: int, force_js: bool):
    req = scraper_pb2.ScrapeRequest(url=url, max_pages=pages, force_js=force_js)
    print(f"\nStreaming scrape of {url} …\n")
    for page in stub.ScrapeStream(req):
        print(f"  [{page.url}]  {page.title}")
        if page.headings:
            print(f"    {page.headings[0]}")


def main():
    parser = argparse.ArgumentParser(description="EthicalWebScraper gRPC client")
    parser.add_argument(
        "url", nargs="?", default="https://books.toscrape.com/", help="Target URL"
    )
    parser.add_argument("--pages", type=int, default=5)
    parser.add_argument(
        "--js", action="store_true", help="Force Playwright JS rendering"
    )
    parser.add_argument("--stream", action="store_true", help="Use streaming RPC")
    parser.add_argument(
        "--output", default="", help="Save results to this CSV path on the server"
    )
    parser.add_argument(
        "--server",
        default=SERVER,
        help="gRPC server address (default: localhost:50051)",
    )
    args = parser.parse_args()

    with grpc.insecure_channel(args.server) as channel:
        stub = scraper_pb2_grpc.ScraperServiceStub(channel)
        if args.stream:
            scrape_stream(stub, args.url, args.pages, args.js)
        else:
            scrape(stub, args.url, args.pages, args.js, args.output)


if __name__ == "__main__":
    main()
