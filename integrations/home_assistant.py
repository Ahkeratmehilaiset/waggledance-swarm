"""
WaggleDance — Phase 5: Home Assistant Bridge
==============================================
REST API poll integration with Home Assistant.
Discovers entities, detects significant changes, stores Finnish-formatted
state in ChromaDB via consciousness.learn().

Used by: SensorHub
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("home_assistant")

# Entity domain → Finnish category name
DOMAIN_CATEGORIES = {
    "sensor": "sensori",
    "binary_sensor": "binaarisensori",
    "light": "valo",
    "switch": "kytkin",
    "climate": "ilmastointi",
    "weather": "sää",
    "lock": "lukko",
    "cover": "kaihdin",
    "camera": "kamera",
    "automation": "automaatio",
    "person": "henkilö",
}

# Finnish state translations
STATE_TRANSLATIONS = {
    "on": "päällä",
    "off": "pois",
    "home": "kotona",
    "not_home": "poissa",
    "open": "auki",
    "closed": "kiinni",
    "locked": "lukittu",
    "unlocked": "avaamaton",
    "heating": "lämmitys",
    "cooling": "jäähdytys",
    "idle": "valmiustila",
    "unavailable": "ei saatavilla",
    "unknown": "tuntematon",
}

# Friendly name translations for common HA entity names
NAME_TRANSLATIONS = {
    "living room": "olohuone",
    "bedroom": "makuuhuone",
    "kitchen": "keittiö",
    "bathroom": "kylpyhuone",
    "hallway": "eteinen",
    "garage": "autotalli",
    "garden": "puutarha",
    "front door": "etuovi",
    "back door": "takaovi",
    "temperature": "lämpötila",
    "humidity": "kosteus",
    "motion": "liike",
    "door": "ovi",
    "window": "ikkuna",
    "light": "valo",
    "outdoor": "ulko",
    "indoor": "sisä",
}

# Unit translations
UNIT_TRANSLATIONS = {
    "°C": "°C",
    "°F": "°F",
    "%": "%",
    "lx": "lx",
    "W": "W",
    "kWh": "kWh",
    "hPa": "hPa",
}


class HomeAssistantBridge:
    """Home Assistant REST API integration with significance filter."""

    def __init__(self, config: dict, consciousness=None):
        self.enabled = config.get("enabled", False)
        self.url = config.get("url", "http://homeassistant.local:8123").rstrip("/")
        import os
        self.token = os.environ.get("WAGGLEDANCE_HA_TOKEN", "") or config.get("token", "")
        self.poll_interval_s = config.get("poll_interval_s", 60)
        self.auto_discover = config.get("auto_discover", True)

        self.consciousness = consciousness
        self._entities: dict[str, dict] = {}  # entity_id -> last state
        self._categories: dict[str, list[str]] = {}  # domain -> [entity_ids]
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._discovered = False

        # Stats
        self._poll_count = 0
        self._changes_stored = 0
        self._errors = 0
        self._last_poll: Optional[float] = None

    async def start(self):
        """Start polling loop."""
        if not self.enabled:
            log.info("Home Assistant Bridge disabled")
            return

        if not self.token:
            log.warning("Home Assistant token not configured — bridge disabled")
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        log.info(f"Home Assistant Bridge started → {self.url}")

    async def stop(self):
        """Stop polling loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Home Assistant Bridge stopped")

    async def _poll_loop(self):
        """Main loop: poll HA REST API, detect changes, store."""
        await asyncio.sleep(2)  # Let system settle

        while self._running:
            try:
                if not self._discovered and self.auto_discover:
                    await self._discover_entities()

                await self._poll_states()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._errors += 1
                log.warning(f"HA poll error: {e}")

            try:
                await asyncio.sleep(self.poll_interval_s)
            except asyncio.CancelledError:
                break

    async def _discover_entities(self):
        """GET /api/states → categorize by domain prefix."""
        import aiohttp

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.url}/api/states",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 401:
                        log.warning("HA authentication failed — check token")
                        self._errors += 1
                        return
                    if resp.status != 200:
                        log.warning(f"HA discovery returned {resp.status}")
                        self._errors += 1
                        return

                    states = await resp.json()

        except Exception as e:
            self._errors += 1
            log.warning(f"HA discovery failed: {e}")
            return

        self._categories.clear()
        for state in states:
            entity_id = state.get("entity_id", "")
            domain = entity_id.split(".")[0] if "." in entity_id else ""

            if domain not in DOMAIN_CATEGORIES:
                continue

            if domain not in self._categories:
                self._categories[domain] = []
            self._categories[domain].append(entity_id)

            # Store initial state
            self._entities[entity_id] = {
                "state": state.get("state"),
                "attributes": state.get("attributes", {}),
                "last_changed": state.get("last_changed"),
            }

        total = sum(len(v) for v in self._categories.values())
        self._discovered = True
        log.info(
            f"HA discovered {total} entities across "
            f"{len(self._categories)} domains"
        )

    async def _poll_states(self):
        """Poll all tracked entities, detect significant changes."""
        if not self._entities:
            return

        import aiohttp

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.url}/api/states",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        self._errors += 1
                        return
                    states = await resp.json()
        except Exception as e:
            self._errors += 1
            log.warning(f"HA poll failed: {e}")
            return

        self._poll_count += 1
        self._last_poll = time.monotonic()

        # Build lookup
        current = {}
        for s in states:
            eid = s.get("entity_id", "")
            if eid in self._entities:
                current[eid] = {
                    "state": s.get("state"),
                    "attributes": s.get("attributes", {}),
                    "last_changed": s.get("last_changed"),
                }

        # Detect and store significant changes
        for eid, new_state in current.items():
            old_state = self._entities.get(eid)
            if old_state and self._is_significant_change(eid, old_state, new_state):
                self._store_change(eid, new_state)

            self._entities[eid] = new_state

    def _is_significant_change(
        self, entity_id: str, old: dict, new: dict
    ) -> bool:
        """Check if state change is significant enough to store."""
        old_val = old.get("state")
        new_val = new.get("state")

        if old_val == new_val:
            # Check attribute changes for lights (brightness)
            domain = entity_id.split(".")[0]
            if domain == "light":
                old_bright = old.get("attributes", {}).get("brightness", 0)
                new_bright = new.get("attributes", {}).get("brightness", 0)
                if old_bright and new_bright:
                    # Brightness is 0-255, >20% change = significant
                    return abs(new_bright - old_bright) > 51  # ~20% of 255
            return False

        domain = entity_id.split(".")[0]

        # Binary states: any change is significant
        if domain in ("binary_sensor", "lock", "switch", "cover"):
            return True

        # Numeric sensors: change > 1.0 unit
        try:
            old_num = float(old_val)
            new_num = float(new_val)
            return abs(new_num - old_num) > 1.0
        except (ValueError, TypeError):
            pass

        # Any other state change
        return old_val != new_val

    def _store_change(self, entity_id: str, state: dict):
        """Store significant change in ChromaDB via consciousness."""
        if not self.consciousness:
            return

        text = self._format_state_finnish(entity_id, state)
        if not text:
            return

        try:
            self.consciousness.learn(
                text,
                agent_id="home_assistant",
                source_type="sensor_reading",
                confidence=0.90,
                metadata={
                    "category": "home_state",
                    "entity_id": entity_id,
                    "ttl_hours": 1,
                    "source": "home_assistant",
                },
            )
            self._changes_stored += 1
            log.debug(f"HA stored: {text}")
        except Exception as e:
            log.warning(f"HA store failed for {entity_id}: {e}")

    def _format_state_finnish(self, entity_id: str, state: dict) -> str:
        """Format entity state as Finnish text."""
        attrs = state.get("attributes", {})
        friendly_name = attrs.get("friendly_name", entity_id)
        state_val = state.get("state", "")
        unit = attrs.get("unit_of_measurement", "")

        # Translate name to Finnish
        fi_name = friendly_name
        for en, fi in NAME_TRANSLATIONS.items():
            fi_name = fi_name.lower().replace(en, fi)
        # Capitalize first letter
        if fi_name:
            fi_name = fi_name[0].upper() + fi_name[1:]

        domain = entity_id.split(".")[0]

        # Light with brightness
        if domain == "light":
            fi_state = STATE_TRANSLATIONS.get(state_val, state_val)
            brightness = attrs.get("brightness")
            if brightness is not None and state_val == "on":
                pct = round(brightness / 255 * 100)
                return f"{fi_name}: {fi_state} ({pct}%)"
            return f"{fi_name}: {fi_state}"

        # Numeric sensor
        try:
            num = float(state_val)
            fi_unit = UNIT_TRANSLATIONS.get(unit, unit)
            return f"{fi_name}: {num}{fi_unit}"
        except (ValueError, TypeError):
            pass

        # Binary / text state
        fi_state = STATE_TRANSLATIONS.get(state_val, state_val)
        return f"{fi_name}: {fi_state}"

    def get_home_context(self) -> str:
        """Generate Finnish summary of home state for agent context."""
        if not self._entities:
            return ""

        parts = []
        for entity_id, state in self._entities.items():
            state_val = state.get("state", "")
            if state_val in ("unavailable", "unknown"):
                continue
            text = self._format_state_finnish(entity_id, state)
            if text:
                parts.append(text)

        if not parts:
            return ""

        return "Kodin tila: " + "; ".join(parts[:15])  # Limit to 15 entities

    def get_entities(self) -> dict:
        """Return current entity states for API."""
        result = {}
        for eid, state in self._entities.items():
            result[eid] = {
                "state": state.get("state"),
                "friendly_name": state.get("attributes", {}).get(
                    "friendly_name", eid
                ),
                "finnish": self._format_state_finnish(eid, state),
            }
        return result

    def get_status(self) -> dict:
        """Status dict for dashboard/API."""
        return {
            "enabled": self.enabled,
            "running": self._running,
            "url": self.url,
            "discovered": self._discovered,
            "entity_count": len(self._entities),
            "categories": {
                k: len(v) for k, v in self._categories.items()
            },
            "poll_count": self._poll_count,
            "changes_stored": self._changes_stored,
            "errors": self._errors,
            "last_poll": self._last_poll,
        }
