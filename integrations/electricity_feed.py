"""
WaggleDance — Phase 8: Electricity Spot Price Feed
====================================================
Fetches Finnish electricity spot prices from porssisahko.net API.
Free, no API key required. JSON format.

Stores current price + cheapest hours in ChromaDB via consciousness.learn().
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("electricity_feed")


class ElectricityFeed:
    """Fetches electricity spot prices from porssisahko.net API."""

    API_URL = "https://api.porssisahko.net/v1/latest-prices.json"

    def __init__(self, config: dict):
        self._current_price: Optional[dict] = None
        self._today_prices: list[dict] = []
        self._total_stored = 0
        self._last_update: Optional[float] = None
        self._errors = 0

    async def fetch_prices(self) -> list[dict]:
        """Fetch latest prices from porssisahko.net API.

        Returns list of {price, startDate, endDate} sorted by startDate.
        price = c/kWh including VAT 25.5%.
        """
        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.API_URL, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        log.warning(f"Porssisahko API returned {resp.status}")
                        self._errors += 1
                        return []
                    data = await resp.json()
        except Exception as e:
            log.warning(f"Electricity price fetch failed: {e}")
            self._errors += 1
            return []

        prices = data.get("prices", [])
        if not prices:
            log.warning("Porssisahko API returned no prices")
            return []

        # Sort by startDate ascending
        try:
            prices.sort(key=lambda p: p.get("startDate", ""))
        except Exception:
            pass

        self._today_prices = prices
        return prices

    def get_current_price(self) -> Optional[dict]:
        """Find the price entry where now() falls between startDate and endDate.

        Returns {price, startDate, endDate} or None.
        """
        if not self._today_prices:
            return None

        now = datetime.now(timezone.utc)
        now_str = now.isoformat()

        for entry in self._today_prices:
            start = entry.get("startDate", "")
            end = entry.get("endDate", "")
            if start <= now_str <= end:
                self._current_price = entry
                return entry

        # Fallback: return the most recent past entry
        past = [
            p for p in self._today_prices if p.get("startDate", "") <= now_str
        ]
        if past:
            self._current_price = past[-1]
            return past[-1]

        return None

    def find_cheapest_hours(self, hours_needed: int = 3) -> list[dict]:
        """Find cheapest consecutive hours using sliding window.

        Returns list of {hour, price} dicts for the cheapest window.
        """
        if not self._today_prices or hours_needed < 1:
            return []

        prices = self._today_prices
        if len(prices) < hours_needed:
            return []

        # Extract hour and price from each entry
        hourly = []
        for entry in prices:
            try:
                start = entry.get("startDate", "")
                # Parse ISO datetime to get hour
                if "T" in start:
                    hour = int(start.split("T")[1][:2])
                else:
                    hour = len(hourly)
                hourly.append({
                    "hour": hour,
                    "price": entry.get("price", 0.0),
                    "startDate": start,
                })
            except (ValueError, IndexError):
                continue

        if len(hourly) < hours_needed:
            return []

        # Sliding window for cheapest consecutive hours
        best_start = 0
        best_sum = sum(h["price"] for h in hourly[:hours_needed])
        current_sum = best_sum

        for i in range(1, len(hourly) - hours_needed + 1):
            current_sum = current_sum - hourly[i - 1]["price"] + hourly[i + hours_needed - 1]["price"]
            if current_sum < best_sum:
                best_sum = current_sum
                best_start = i

        window = hourly[best_start:best_start + hours_needed]
        avg_price = best_sum / hours_needed

        return [{
            "hour": h["hour"],
            "price": h["price"],
            "avg_price": round(avg_price, 2),
        } for h in window]

    async def update_context(self, consciousness) -> int:
        """Fetch prices, store current price + cheapest window in ChromaDB.

        Returns number of facts stored (up to 2).
        """
        stored = 0

        prices = await self.fetch_prices()
        if not prices:
            return 0

        current = self.get_current_price()
        if current:
            # Calculate today's average
            avg_price = sum(p.get("price", 0) for p in prices) / len(prices)
            price_val = current.get("price", 0.0)

            text = (
                f"Sähkön spot-hinta nyt: {price_val:.2f} c/kWh "
                f"(päivän keskiarvo: {avg_price:.2f} c/kWh)"
            )
            consciousness.learn(
                text,
                agent_id="electricity_feed",
                source_type="external_feed",
                confidence=0.95,
                metadata={
                    "category": "electricity",
                    "ttl_hours": 1,
                    "feed": "porssisahko",
                },
            )
            stored += 1
            log.info(f"Electricity stored: {text}")

        # Find cheapest 3h window
        cheapest = self.find_cheapest_hours(3)
        if cheapest and len(cheapest) >= 2:
            first_hour = cheapest[0]["hour"]
            last_hour = (cheapest[-1]["hour"] + 1) % 24
            avg = cheapest[0].get("avg_price", 0.0)

            text = (
                f"Halvin 3h sähköikkuna tänään: "
                f"{first_hour:02d}:00-{last_hour:02d}:00 "
                f"({avg:.2f} c/kWh)"
            )
            consciousness.learn(
                text,
                agent_id="electricity_feed",
                source_type="external_feed",
                confidence=0.90,
                metadata={
                    "category": "electricity_optimization",
                    "ttl_hours": 6,
                    "feed": "porssisahko",
                },
            )
            stored += 1
            log.info(f"Electricity cheapest stored: {text}")

        self._total_stored += stored
        self._last_update = time.monotonic()
        return stored

    @property
    def stats(self) -> dict:
        return {
            "current_price": self._current_price,
            "prices_count": len(self._today_prices),
            "total_stored": self._total_stored,
            "errors": self._errors,
            "last_update": self._last_update,
        }
