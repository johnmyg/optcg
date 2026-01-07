"""Main scraper logic for eBay sold listings."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .ebay_client import EbayClient, ChallengePageError
from .parser import EbayParser, SoldListing


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@dataclass
class ScrapeResult:
    """Result of a scraping operation."""

    query: str
    total_listings: int
    pages_scraped: int
    listings: list[SoldListing]
    scraped_at: datetime

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "query": self.query,
            "total_listings": self.total_listings,
            "pages_scraped": self.pages_scraped,
            "scraped_at": self.scraped_at.isoformat(),
            "listings": [listing.to_dict() for listing in self.listings],
        }


class SoldListingsScraper:
    """Scraper for eBay sold listings."""

    def __init__(
        self,
        requests_per_minute: float = 4.0,  # Conservative: ~15 seconds between requests
        max_retries: int = 5,
    ):
        """Initialize the scraper.

        Args:
            requests_per_minute: Rate limit (default ~15s between requests)
            max_retries: Maximum retry attempts per page
        """
        self.client = EbayClient(
            requests_per_minute=requests_per_minute,
            max_retries=max_retries,
        )
        self.parser = EbayParser()

    def scrape(
        self,
        query: str,
        max_pages: int = 10,
        max_listings: Optional[int] = None,
    ) -> ScrapeResult:
        """Scrape sold listings for a given query.

        Args:
            query: Search query (e.g., "one piece tcg OP01")
            max_pages: Maximum number of pages to scrape
            max_listings: Maximum number of listings to collect (optional)

        Returns:
            ScrapeResult containing all scraped listings
        """
        scraped_at = datetime.utcnow()
        all_listings: list[SoldListing] = []
        pages_scraped = 0
        seen_ids: set[str] = set()

        logger.info(f"Starting scrape for query: {query}")

        for page in range(1, max_pages + 1):
            logger.info(f"Scraping page {page}...")

            try:
                html = self.client.fetch_sold_listings(query, page=page)
                listings = self.parser.parse_listings(html)

                # Deduplicate listings
                new_listings = []
                for listing in listings:
                    if listing.listing_id not in seen_ids:
                        seen_ids.add(listing.listing_id)
                        new_listings.append(listing)

                all_listings.extend(new_listings)
                pages_scraped += 1

                logger.info(
                    f"Page {page}: Found {len(new_listings)} new listings "
                    f"(total: {len(all_listings)})"
                )

                # Check if we've hit the max listings limit
                if max_listings and len(all_listings) >= max_listings:
                    all_listings = all_listings[:max_listings]
                    logger.info(f"Reached max listings limit: {max_listings}")
                    break

                # Check if there are more pages
                if not self.parser.has_next_page(html):
                    logger.info("No more pages available")
                    break

            except ChallengePageError as e:
                logger.warning(f"Challenge page on page {page}, stopping to avoid detection")
                break
            except Exception as e:
                logger.error(f"Error scraping page {page}: {e}")
                # Continue to next page instead of breaking immediately
                continue

        logger.info(
            f"Scrape complete: {len(all_listings)} listings from {pages_scraped} pages"
        )

        return ScrapeResult(
            query=query,
            total_listings=len(all_listings),
            pages_scraped=pages_scraped,
            listings=all_listings,
            scraped_at=scraped_at,
        )

    def scrape_set(
        self,
        set_code: str,
        max_pages: int = 10,
        max_listings: Optional[int] = None,
    ) -> ScrapeResult:
        """Scrape sold listings for a specific One Piece TCG set.

        Args:
            set_code: Set code (e.g., "OP01", "OP05")
            max_pages: Maximum number of pages to scrape
            max_listings: Maximum number of listings to collect

        Returns:
            ScrapeResult containing all scraped listings
        """
        query = f"one piece tcg {set_code}"
        return self.scrape(query, max_pages=max_pages, max_listings=max_listings)

    def save_to_json(
        self,
        result: ScrapeResult,
        output_dir: Path,
        filename: Optional[str] = None,
    ) -> Path:
        """Save scrape results to a JSON file.

        Args:
            result: ScrapeResult to save
            output_dir: Directory to save the file
            filename: Optional filename (default: auto-generated)

        Returns:
            Path to the saved file
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if filename is None:
            timestamp = result.scraped_at.strftime("%Y%m%d_%H%M%S")
            safe_query = result.query.replace(" ", "_").lower()
            filename = f"sold_listings_{safe_query}_{timestamp}.json"

        output_path = output_dir / filename
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)

        logger.info(f"Saved results to: {output_path}")
        return output_path

    def close(self) -> None:
        """Close the scraper and release resources."""
        self.client.close()

    def __enter__(self) -> "SoldListingsScraper":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def main():
    """CLI entry point for the scraper."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Scrape eBay sold listings for One Piece TCG"
    )
    parser.add_argument(
        "--query", "-q",
        type=str,
        help="Search query (e.g., 'one piece tcg OP01')"
    )
    parser.add_argument(
        "--set", "-s",
        type=str,
        dest="set_code",
        help="Set code to scrape (e.g., 'OP01', 'OP05')"
    )
    parser.add_argument(
        "--max-pages", "-p",
        type=int,
        default=5,
        help="Maximum number of pages to scrape (default: 5)"
    )
    parser.add_argument(
        "--max-listings", "-l",
        type=int,
        default=None,
        help="Maximum number of listings to collect"
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=str,
        default="data/raw",
        help="Output directory for JSON files (default: data/raw)"
    )

    args = parser.parse_args()

    if not args.query and not args.set_code:
        parser.error("Either --query or --set is required")

    with SoldListingsScraper() as scraper:
        if args.set_code:
            result = scraper.scrape_set(
                args.set_code,
                max_pages=args.max_pages,
                max_listings=args.max_listings,
            )
        else:
            result = scraper.scrape(
                args.query,
                max_pages=args.max_pages,
                max_listings=args.max_listings,
            )

        output_path = scraper.save_to_json(result, Path(args.output_dir))
        print(f"\nScrape complete!")
        print(f"  Total listings: {result.total_listings}")
        print(f"  Pages scraped: {result.pages_scraped}")
        print(f"  Output file: {output_path}")


if __name__ == "__main__":
    main()
