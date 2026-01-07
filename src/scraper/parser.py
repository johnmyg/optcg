"""Parser for extracting sold listing data from eBay HTML."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from bs4 import BeautifulSoup, Tag


@dataclass
class SoldListing:
    """Represents a single sold listing from eBay."""

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


class EbayParser:
    """Parser for eBay sold listings HTML pages."""

    @staticmethod
    def _parse_price(price_text: str) -> Optional[float]:
        """Parse price string to float."""
        if not price_text:
            return None
        # Remove currency symbols and commas, extract number
        cleaned = re.sub(r"[^\d.]", "", price_text)
        try:
            return float(cleaned)
        except ValueError:
            return None

    @staticmethod
    def _parse_shipping(shipping_text: str) -> Optional[float]:
        """Parse shipping cost from text like '+$5.15 delivery' or 'Free delivery'."""
        if not shipping_text:
            return None
        lower = shipping_text.lower()
        if "free" in lower:
            return 0.0
        # Extract numeric value
        match = re.search(r"\$?([\d.]+)", shipping_text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass
        return None

    @staticmethod
    def _parse_sold_date(date_text: str) -> Optional[datetime]:
        """Parse sold date from text like 'Sold  Jan 15, 2024'."""
        if not date_text:
            return None
        # Remove "Sold" prefix and extra whitespace
        cleaned = re.sub(r"^Sold\s+", "", date_text, flags=re.IGNORECASE).strip()
        # Try various date formats
        formats = [
            "%b %d, %Y",  # Jan 15, 2024
            "%B %d, %Y",  # January 15, 2024
            "%m/%d/%Y",   # 01/15/2024
            "%d %b %Y",   # 15 Jan 2024
        ]
        for fmt in formats:
            try:
                return datetime.strptime(cleaned, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _clean_title(title: str) -> str:
        """Clean title by removing common suffixes added by eBay."""
        # Remove "Opens in a new window or tab" suffix
        title = re.sub(r"Opens in a new window.*$", "", title, flags=re.IGNORECASE)
        # Remove "New Listing" prefix
        title = re.sub(r"^New Listing", "", title, flags=re.IGNORECASE)
        return title.strip()

    def parse_listings(self, html: str) -> list[SoldListing]:
        """Parse sold listings from eBay search results HTML.

        Args:
            html: Raw HTML content from eBay search page

        Returns:
            List of SoldListing objects
        """
        soup = BeautifulSoup(html, "lxml")
        listings = []
        scraped_at = datetime.utcnow()

        # eBay uses s-card class for each listing (new 2024+ structure)
        items = soup.select("li.s-card")

        for item in items:
            try:
                listing = self._parse_card_item(item, scraped_at)
                if listing:
                    listings.append(listing)
            except Exception:
                # Skip items that fail to parse
                continue

        return listings

    def _parse_card_item(
        self, item: Tag, scraped_at: datetime
    ) -> Optional[SoldListing]:
        """Parse a single listing item using the new s-card structure."""
        # Get listing ID from data attribute
        listing_id = item.get("data-listingid")
        if not listing_id:
            return None

        # Get title
        title_elem = item.select_one(".s-card__title")
        if not title_elem:
            return None

        title = self._clean_title(title_elem.get_text(strip=True))

        # Skip placeholder items
        if not title or title.lower() == "shop on ebay":
            return None

        # Get URL
        link_elem = item.select_one("a.s-card__link")
        url = link_elem.get("href", "") if link_elem else ""
        if not url:
            return None

        # Check if this is a sold listing by looking for "Sold" text
        sold_text = None
        for elem in item.find_all(string=lambda t: t and "Sold" in str(t)):
            text = str(elem).strip()
            if text.startswith("Sold"):
                sold_text = text
                break

        # Only include listings that have been sold
        if not sold_text:
            return None

        sold_date = self._parse_sold_date(sold_text)

        # Get price
        price_elem = item.select_one(".s-card__price")
        price_text = price_elem.get_text(strip=True) if price_elem else ""
        price = self._parse_price(price_text)

        if price is None:
            return None

        # Get shipping from attribute rows
        shipping_price = None

        attr_rows = item.select(".s-card__attribute-row")
        for row in attr_rows:
            row_text = row.get_text(strip=True)

            # Check for shipping/delivery info
            if "delivery" in row_text.lower() or "shipping" in row_text.lower():
                shipping_price = self._parse_shipping(row_text)

        return SoldListing(
            listing_id=str(listing_id),
            title=title,
            price=price,
            shipping_price=shipping_price,
            sold_date=sold_date,
            listing_url=url,
            scraped_at=scraped_at,
        )

    def get_total_results(self, html: str) -> Optional[int]:
        """Extract total number of results from search page."""
        soup = BeautifulSoup(html, "lxml")
        # Look for results count in various possible locations
        for selector in [".srp-controls__count-heading", ".srp-controls__count", "[class*='result']"]:
            count_elem = soup.select_one(selector)
            if count_elem:
                text = count_elem.get_text()
                match = re.search(r"([\d,]+)\s*(?:results?|items?)", text, re.IGNORECASE)
                if match:
                    return int(match.group(1).replace(",", ""))
        return None

    def has_next_page(self, html: str) -> bool:
        """Check if there's a next page of results."""
        soup = BeautifulSoup(html, "lxml")
        # Check for pagination controls
        next_btn = soup.select_one("a.pagination__next, a[aria-label*='next'], a[rel='next']")
        if next_btn:
            classes = next_btn.get("class", [])
            return "disabled" not in classes
        # Also check if there's a page 2 link
        page_links = soup.select("a.pagination__item")
        return len(page_links) > 1
