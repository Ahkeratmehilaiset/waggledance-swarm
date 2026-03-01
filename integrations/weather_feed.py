"""
WaggleDance — Phase 8: Weather Feed (FMI Open Data)
=====================================================
Fetches current weather from Finnish Meteorological Institute (FMI) Open Data API.
Free, no API key required. XML/WFS format.

Stores Finnish-language weather facts in ChromaDB via consciousness.learn().
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("weather_feed")


class WeatherFeed:
    """Fetches weather from FMI Open Data API (free, no key)."""

    FMI_URL = "https://opendata.fmi.fi/wfs"
    FMI_STORED_QUERY = "fmi::observations::weather::simple"

    def __init__(self, config: dict):
        self.locations = config.get("locations", ["Helsinki"])
        self._last_data: dict = {}  # location -> weather dict
        self._total_stored = 0
        self._last_update: Optional[float] = None
        self._errors = 0

    async def fetch_current(self, location: str) -> dict:
        """Fetch current weather for a location from FMI WFS API.

        Returns: {temp_c, humidity_pct, wind_ms, precipitation_mm, timestamp}
        On error: returns {} and logs warning.
        """
        import aiohttp
        import xml.etree.ElementTree as ET

        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "getFeature",
            "storedquery_id": self.FMI_STORED_QUERY,
            "place": location,
            "parameters": "temperature,relativehumidity,windspeedms,precipitation1h",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.FMI_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        log.warning(f"FMI API returned {resp.status} for {location}")
                        self._errors += 1
                        return {}
                    xml_text = await resp.text()
        except Exception as e:
            log.warning(f"FMI fetch failed for {location}: {e}")
            self._errors += 1
            return {}

        return self._parse_fmi_xml(xml_text, location)

    def _parse_fmi_xml(self, xml_text: str, location: str) -> dict:
        """Parse FMI WFS simple observation XML response."""
        import xml.etree.ElementTree as ET

        result = {
            "location": location,
            "temp_c": None,
            "humidity_pct": None,
            "wind_ms": None,
            "precipitation_mm": None,
            "timestamp": None,
        }

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            log.warning(f"FMI XML parse error for {location}: {e}")
            self._errors += 1
            return {}

        # FMI WFS uses BsWfs namespace for simple features
        ns = {
            "wfs": "http://www.opengis.net/wfs/2.0",
            "BsWfs": "http://xml.fmi.fi/schema/wfs/2.0",
            "gml": "http://www.opengis.net/gml/3.2",
        }

        # Find all BsWfsElement members — take the latest values
        members = root.findall(".//BsWfs:BsWfsElement", ns)
        if not members:
            # Try without namespace as fallback
            members = root.findall(".//{http://xml.fmi.fi/schema/wfs/2.0}BsWfsElement")

        param_map = {
            "temperature": "temp_c",
            "t2m": "temp_c",
            "relativehumidity": "humidity_pct",
            "rh": "humidity_pct",
            "windspeedms": "wind_ms",
            "ws_10min": "wind_ms",
            "precipitation1h": "precipitation_mm",
            "r_1h": "precipitation_mm",
        }

        for member in members:
            param_el = member.find("BsWfs:ParameterName", ns)
            value_el = member.find("BsWfs:ParameterValue", ns)
            time_el = member.find("BsWfs:Time", ns)

            if param_el is None or value_el is None:
                # Try without namespace prefix
                param_el = member.find(
                    "{http://xml.fmi.fi/schema/wfs/2.0}ParameterName"
                )
                value_el = member.find(
                    "{http://xml.fmi.fi/schema/wfs/2.0}ParameterValue"
                )
                time_el = member.find(
                    "{http://xml.fmi.fi/schema/wfs/2.0}Time"
                )

            if param_el is None or value_el is None:
                continue

            param_name = param_el.text.strip().lower() if param_el.text else ""
            value_text = value_el.text.strip() if value_el.text else ""

            key = param_map.get(param_name)
            if key and value_text and value_text != "NaN":
                try:
                    result[key] = round(float(value_text), 1)
                except ValueError:
                    pass

            if time_el is not None and time_el.text and result["timestamp"] is None:
                result["timestamp"] = time_el.text.strip()

        # Check we got at least temperature
        if result["temp_c"] is None:
            log.warning(f"FMI returned no temperature data for {location}")
            return {}

        self._last_data[location] = result
        return result

    async def update_context(self, consciousness) -> int:
        """Fetch all locations, store Finnish weather text in ChromaDB.

        Returns number of facts stored.
        """
        stored = 0
        for location in self.locations:
            try:
                weather = await self.fetch_current(location)
                if not weather or weather.get("temp_c") is None:
                    continue

                # Build Finnish text for ChromaDB
                parts = [f"Sää {location}: {weather['temp_c']}°C"]
                if weather.get("humidity_pct") is not None:
                    parts.append(f"kosteus {weather['humidity_pct']}%")
                if weather.get("wind_ms") is not None:
                    parts.append(f"tuuli {weather['wind_ms']} m/s")
                if weather.get("precipitation_mm") is not None:
                    parts.append(f"sade {weather['precipitation_mm']} mm/h")

                text = ", ".join(parts)

                consciousness.learn(
                    text,
                    agent_id="weather_feed",
                    source_type="external_feed",
                    confidence=0.95,
                    metadata={
                        "category": "weather",
                        "location": location,
                        "ttl_hours": 1,
                        "feed": "fmi",
                    },
                )
                stored += 1
                log.info(f"Weather stored: {text}")

            except Exception as e:
                log.warning(f"Weather update failed for {location}: {e}")
                self._errors += 1

        self._total_stored += stored
        self._last_update = time.monotonic()
        return stored

    @property
    def stats(self) -> dict:
        return {
            "locations": self.locations,
            "total_stored": self._total_stored,
            "last_data": self._last_data,
            "errors": self._errors,
            "last_update": self._last_update,
        }
