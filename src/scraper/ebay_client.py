"""HTTP client for making requests to eBay with anti-detection measures."""

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


# Rotate through different User-Agents
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
]


class EbayClient:
    """HTTP client for fetching eBay sold listings pages."""

    BASE_URL = "https://www.ebay.com/sch/i.html"

    def __init__(
        self,
        requests_per_minute: float = 6.0,  # ~10 seconds between requests
        max_retries: int = 5,
        timeout: float = 45.0,
    ):
        self.rate_limiter = RateLimiter(
            requests_per_second=requests_per_minute / 60.0,
            burst_size=1,
        )
        self.max_retries = max_retries
        self.timeout = timeout
        self._client: Optional[httpx.Client] = None
        self._request_count = 0

    def _get_headers(self) -> dict:
        """Get randomized headers for each request."""
        user_agent = random.choice(USER_AGENTS)

        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"' if "Mac" in user_agent else '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        return headers

    def _get_client(self) -> httpx.Client:
        """Get or create the HTTP client with fresh headers."""
        if self._client is None or self._request_count >= 5:
            # Recreate client periodically to refresh session
            if self._client is not None:
                self._client.close()
            self._client = httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
            )
            self._request_count = 0
        return self._client

    def _is_challenge_page(self, html: str, url: str) -> bool:
        """Check if the response is a challenge/captcha page."""
        indicators = [
            "splashui/challenge" in url,
            "pardon our interruption" in html.lower(),
            "captcha" in html.lower(),
            len(html) < 10000 and "s-card" not in html and "srp-results" not in html,
        ]
        return any(indicators)

    def _random_delay(self, base: float = 3.0, variance: float = 5.0) -> None:
        """Add a random delay to appear more human-like."""
        delay = base + random.uniform(0, variance)
        logger.debug(f"Waiting {delay:.1f}s...")
        time.sleep(delay)

    def build_sold_listings_url(
        self,
        query: str,
        page: int = 1,
        items_per_page: int = 120,  # Use 120 instead of 240 to be less aggressive
    ) -> str:
        """Build URL for eBay sold listings search."""
        params = {
            "_nkw": query,
            "_sacat": "0",
            "LH_Sold": "1",
            "LH_Complete": "1",
            "_ipg": str(items_per_page),
            "_sop": "13",
            "rt": "nc",  # Add this to seem more like browser navigation
        }

        if page > 1:
            params["_pgn"] = str(page)

        return f"{self.BASE_URL}?{urlencode(params)}"

    def fetch_page(self, url: str, is_retry: bool = False) -> str:
        """Fetch a single page from eBay with retry logic."""
        last_error = None

        for attempt in range(self.max_retries):
            # Wait for rate limiter
            self.rate_limiter.acquire_sync()

            # Add random delay (longer for retries)
            if attempt > 0 or is_retry:
                base_delay = 5.0 + (attempt * 3.0)
                self._random_delay(base=base_delay, variance=8.0)
            else:
                self._random_delay(base=2.0, variance=4.0)

            try:
                client = self._get_client()
                headers = self._get_headers()

                # Add referer on retries to look more natural
                if attempt > 0:
                    headers["Referer"] = "https://www.ebay.com/"

                response = client.get(url, headers=headers)
                self._request_count += 1

                # Handle rate limiting responses
                if response.status_code == 429:
                    logger.warning(f"Rate limited (429), waiting longer...")
                    time.sleep(30 + random.uniform(0, 30))
                    continue

                response.raise_for_status()
                html = response.text
                final_url = str(response.url)

                # Check for challenge page
                if self._is_challenge_page(html, final_url):
                    logger.warning(f"Challenge page detected on attempt {attempt + 1}")
                    # Reset client to get new session
                    self.close()
                    last_error = ChallengePageError("eBay returned a challenge page")
                    # Longer backoff for challenge pages
                    time.sleep(15 + random.uniform(0, 15))
                    continue

                return html

            except httpx.HTTPError as e:
                logger.warning(f"HTTP error on attempt {attempt + 1}: {e}")
                last_error = e
                continue

        if last_error:
            raise last_error
        raise ChallengePageError("Failed to fetch page after all retries")

    def fetch_sold_listings(
        self,
        query: str,
        page: int = 1,
        items_per_page: int = 120,
    ) -> str:
        """Fetch sold listings search results from eBay."""
        url = self.build_sold_listings_url(query, page, items_per_page)
        is_retry = page > 1  # Be more careful on subsequent pages
        return self.fetch_page(url, is_retry=is_retry)

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None
            self._request_count = 0

    def __enter__(self) -> "EbayClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
