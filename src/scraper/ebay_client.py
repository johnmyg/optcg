"""HTTP client for making requests to eBay."""

import httpx
import random
import time
import logging
from urllib.parse import urlencode
from typing import Optional

from .rate_limiter import RateLimiter


logger = logging.getLogger(__name__)


class ChallengePageError(Exception):
    """Raised when eBay returns a challenge/captcha page."""
    pass


class EbayClient:
    """HTTP client for fetching eBay sold listings pages."""

    BASE_URL = "https://www.ebay.com/sch/i.html"

    # More complete headers to better mimic a real browser
    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Connection": "keep-alive",
        "Cache-Control": "max-age=0",
        "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "DNT": "1",
    }

    def __init__(
        self,
        rate_limiter: Optional[RateLimiter] = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ):
        self.rate_limiter = rate_limiter or RateLimiter()
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.Client] = None

    def _get_client(self) -> httpx.Client:
        """Get or create the HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                headers=self.DEFAULT_HEADERS,
                timeout=self.timeout,
                follow_redirects=True,
                cookies=httpx.Cookies(),
            )
        return self._client

    def _is_challenge_page(self, html: str, url: str) -> bool:
        """Check if the response is a challenge/captcha page."""
        return (
            "splashui/challenge" in url or
            "captcha" in html.lower() or
            "blocked" in html.lower() or
            len(html) < 5000 and "s-card" not in html
        )

    def build_sold_listings_url(
        self,
        query: str,
        page: int = 1,
        items_per_page: int = 240,
    ) -> str:
        """Build URL for eBay sold listings search.

        Args:
            query: Search query string
            page: Page number (1-indexed)
            items_per_page: Number of items per page (60, 120, or 240)

        Returns:
            Full URL for the eBay search
        """
        params = {
            "_nkw": query,
            "_sacat": "0",  # All categories
            "LH_Sold": "1",  # Sold listings only
            "LH_Complete": "1",  # Completed listings
            "_ipg": str(items_per_page),
            "_sop": "13",  # Sort by date: most recent first
        }

        # Add pagination offset
        if page > 1:
            params["_pgn"] = str(page)

        return f"{self.BASE_URL}?{urlencode(params)}"

    def fetch_page(self, url: str) -> str:
        """Fetch a single page from eBay with retry logic.

        Args:
            url: URL to fetch

        Returns:
            HTML content of the page

        Raises:
            httpx.HTTPError: If the request fails after retries
            ChallengePageError: If eBay keeps returning challenge pages
        """
        last_error = None

        for attempt in range(self.max_retries):
            self.rate_limiter.acquire_sync()

            # Add random delay between requests to appear more human-like
            if attempt > 0:
                delay = random.uniform(2.0, 5.0) * (attempt + 1)
                logger.info(f"Retry {attempt + 1}/{self.max_retries}, waiting {delay:.1f}s...")
                time.sleep(delay)

            try:
                client = self._get_client()
                response = client.get(url)
                response.raise_for_status()

                html = response.text
                final_url = str(response.url)

                # Check if we got a challenge page
                if self._is_challenge_page(html, final_url):
                    logger.warning(f"Got challenge page on attempt {attempt + 1}")
                    # Close and recreate client to get fresh session
                    self.close()
                    last_error = ChallengePageError("eBay returned a challenge page")
                    continue

                return html

            except httpx.HTTPError as e:
                logger.warning(f"HTTP error on attempt {attempt + 1}: {e}")
                last_error = e
                continue

        # All retries failed
        if last_error:
            raise last_error
        raise ChallengePageError("Failed to fetch page after all retries")

    def fetch_sold_listings(
        self,
        query: str,
        page: int = 1,
        items_per_page: int = 240,
    ) -> str:
        """Fetch sold listings search results from eBay.

        Args:
            query: Search query string
            page: Page number (1-indexed)
            items_per_page: Number of items per page

        Returns:
            HTML content of the search results page
        """
        url = self.build_sold_listings_url(query, page, items_per_page)
        return self.fetch_page(url)

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "EbayClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
