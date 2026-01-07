"""eBay API client for fetching sold listings via the Finding API."""

import base64
import httpx
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from xml.etree import ElementTree as ET

from dotenv import load_dotenv


load_dotenv()
logger = logging.getLogger(__name__)


@dataclass
class SoldItem:
    """Represents a sold item from eBay API."""

    listing_id: str
    title: str
    price: float
    shipping_price: Optional[float]
    sold_date: Optional[datetime]
    listing_url: str
    scraped_at: datetime

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "listing_id": self.listing_id,
            "title": self.title,
            "price": self.price,
            "shipping_price": self.shipping_price,
            "sold_date": self.sold_date.isoformat() if self.sold_date else None,
            "listing_url": self.listing_url,
            "scraped_at": self.scraped_at.isoformat(),
        }


class EbayApiClient:
    """Client for eBay Finding API to get completed/sold listings."""

    FINDING_API_URL = "https://svcs.ebay.com/services/search/FindingService/v1"
    OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ):
        self.client_id = client_id or os.getenv("EBAY_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("EBAY_CLIENT_SECRET")

        if not self.client_id or not self.client_secret:
            raise ValueError("EBAY_CLIENT_ID and EBAY_CLIENT_SECRET must be set")

        self._client = httpx.Client(timeout=30.0)
        self._access_token: Optional[str] = None

    def _get_access_token(self) -> str:
        """Get OAuth access token using client credentials flow."""
        if self._access_token:
            return self._access_token

        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        response = self._client.post(
            self.OAUTH_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {credentials}",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
        )
        response.raise_for_status()

        data = response.json()
        self._access_token = data["access_token"]
        logger.info("Successfully obtained eBay OAuth token")
        return self._access_token

    def search_sold_items(
        self,
        query: str,
        page: int = 1,
        per_page: int = 100,
    ) -> tuple[list[SoldItem], int]:
        """Search for completed/sold items.

        Args:
            query: Search keywords
            page: Page number (1-indexed)
            per_page: Items per page (max 100)

        Returns:
            Tuple of (list of SoldItem, total_pages)
        """
        # Finding API uses the App ID directly (no OAuth needed for this endpoint)
        headers = {
            "X-EBAY-SOA-SECURITY-APPNAME": self.client_id,
            "X-EBAY-SOA-OPERATION-NAME": "findCompletedItems",
            "X-EBAY-SOA-SERVICE-VERSION": "1.13.0",
            "X-EBAY-SOA-RESPONSE-DATA-FORMAT": "XML",
            "X-EBAY-SOA-GLOBAL-ID": "EBAY-US",
            "Content-Type": "application/xml",
        }

        # Build XML request
        xml_request = f"""<?xml version="1.0" encoding="UTF-8"?>
<findCompletedItemsRequest xmlns="http://www.ebay.com/marketplace/search/v1/services">
    <keywords>{query}</keywords>
    <itemFilter>
        <name>SoldItemsOnly</name>
        <value>true</value>
    </itemFilter>
    <sortOrder>EndTimeSoonest</sortOrder>
    <paginationInput>
        <entriesPerPage>{per_page}</entriesPerPage>
        <pageNumber>{page}</pageNumber>
    </paginationInput>
</findCompletedItemsRequest>"""

        response = self._client.post(
            self.FINDING_API_URL,
            headers=headers,
            content=xml_request,
        )
        response.raise_for_status()

        return self._parse_finding_response(response.text)

    def _parse_finding_response(self, xml_text: str) -> tuple[list[SoldItem], int]:
        """Parse Finding API XML response."""
        # Define namespace
        ns = {"ns": "http://www.ebay.com/marketplace/search/v1/services"}

        root = ET.fromstring(xml_text)
        scraped_at = datetime.utcnow()
        items = []

        # Check for errors
        ack = root.find(".//ns:ack", ns)
        if ack is not None and ack.text != "Success":
            error = root.find(".//ns:errorMessage/ns:error/ns:message", ns)
            error_msg = error.text if error is not None else "Unknown error"
            logger.error(f"eBay API error: {error_msg}")
            raise Exception(f"eBay API error: {error_msg}")

        # Get pagination info
        total_pages = 0
        pagination = root.find(".//ns:paginationOutput/ns:totalPages", ns)
        if pagination is not None:
            total_pages = int(pagination.text)

        # Parse items
        for item in root.findall(".//ns:searchResult/ns:item", ns):
            try:
                listing_id = item.find("ns:itemId", ns)
                title = item.find("ns:title", ns)
                url = item.find("ns:viewItemURL", ns)

                # Price
                price_elem = item.find(".//ns:sellingStatus/ns:currentPrice", ns)
                price = float(price_elem.text) if price_elem is not None else 0.0

                # Shipping
                shipping_elem = item.find(".//ns:shippingInfo/ns:shippingServiceCost", ns)
                shipping_price = float(shipping_elem.text) if shipping_elem is not None else None

                # End time (sold date)
                end_time_elem = item.find(".//ns:listingInfo/ns:endTime", ns)
                sold_date = None
                if end_time_elem is not None:
                    # Parse ISO format: 2024-01-15T14:30:00.000Z
                    sold_date = datetime.fromisoformat(
                        end_time_elem.text.replace("Z", "+00:00")
                    ).replace(tzinfo=None)

                if listing_id is not None and title is not None:
                    items.append(SoldItem(
                        listing_id=listing_id.text,
                        title=title.text,
                        price=price,
                        shipping_price=shipping_price,
                        sold_date=sold_date,
                        listing_url=url.text if url is not None else "",
                        scraped_at=scraped_at,
                    ))
            except Exception as e:
                logger.warning(f"Failed to parse item: {e}")
                continue

        logger.info(f"Parsed {len(items)} items, total pages: {total_pages}")
        return items, total_pages

    def search_all_sold_items(
        self,
        query: str,
        max_pages: int = 10,
    ) -> list[SoldItem]:
        """Search and paginate through all sold items.

        Args:
            query: Search keywords
            max_pages: Maximum pages to fetch

        Returns:
            List of all SoldItem objects
        """
        all_items = []

        for page in range(1, max_pages + 1):
            logger.info(f"Fetching page {page}...")
            items, total_pages = self.search_sold_items(query, page=page)
            all_items.extend(items)

            logger.info(f"Page {page}/{total_pages}: Got {len(items)} items (total: {len(all_items)})")

            if page >= total_pages:
                break

        return all_items

    def close(self):
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
