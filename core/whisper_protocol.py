# WaggleDance Swarm AI • v0.0.1 • Built: 2026-02-22 14:37 EET
# Jani Korpi (Ahkerat Mehiläiset)
"""
WaggleDance Swarm AI Whisper Protocol v2
============================
Agentit kuiskaavat toisilleen tiivistetyssä hieroglyfi-formaatissa.

FORMAATTI: Kompakti symbolikieli joka koodaa merkityksen tehokkaasti.
Ei luonnollista kieltä → säästää tokeneita.

Rakenne: [TYYPPI][LÄHETTÄJÄ][AIHE][DATA][TÄRKEYS]

Esimerkkejä:
  🐝⊕◈𓂀 mehiläis→tech: pesämonitorointi-idea, tärkeys korkea
  💰⟐◉𓃀 bisnis→oracle: hintatutkimuspyyntö
  🔮⊗◊𓋹 oracle→kaikki: tutkimustulos jaettu

Agentit oppivat tulkitsemaan symboleita kokemuksen kautta.
Ihminen näkee kauniita hieroglyfejä — agentit saavat datan muistista.
"""

import asyncio
import json
import hashlib
import time
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field

from memory.shared_memory import SharedMemory
from core.token_economy import TokenEconomy


# ── Kuiskaustyypit ja hinnat ────────────────────────────────

WHISPER_COSTS = {
    "ping": {     # Nopea signaali: "huomasin jotain"
        "token_cost": 0,  # Patched: ilmainen agenttienvälinen
        "max_length": 50,
        "glyph": "⚡",
    },
    "hint": {     # Lyhyt vinkki
        "token_cost": 5,
        "max_length": 100,
        "glyph": "𓂀",
    },
    "insight": {  # Strateginen oivallus
        "token_cost": 15,
        "max_length": 500,
        "glyph": "𓆣",
    },
    "soul": {     # Koko kokemushistoria tiivistettynä
        "token_cost": 30,
        "max_length": 2000,
        "glyph": "𓋹",
    },
}

CROWN_THRESHOLD = 20  # Patched: oli 100, liian korkea alkuun

# ── Hieroglyfi-aakkoset ────────────────────────────────────

# Agentin tyyppi → symboli
AGENT_GLYPHS = {
    # Runtime-erikoiset
    "hivemind": "🧠", "hacker": "⚙️", "oracle": "🔮",
    # Vanhat aliakset
    "beekeeper": "🐝", "video_producer": "🎬", "business": "💰",
    "tech": "🔧", "property": "🏡",
    # 50 YAML-agenttia
    "core_dispatcher": "🧠",
    "ornitologi": "🦅", "entomologi": "🪲", "fenologi": "🌸",
    "hortonomi": "🌿", "metsanhoitaja": "🌲", "riistanvartija": "🦌",
    "luontokuvaaja": "📸", "pienelain_tuholais": "🐭",
    "tarhaaja": "🐝", "lentosaa": "🌤️", "parveiluvahti": "🔔",
    "pesalampo": "🌡️", "nektari_informaatikko": "🍯",
    "tautivahti": "🦠", "pesaturvallisuus": "🐻",
    "limnologi": "🏊", "kalastusopas": "🎣", "kalantunnistaja": "🐟",
    "rantavahti": "🏖️", "jaaasiantuntija": "🧊",
    "meteorologi": "⛅", "myrskyvaroittaja": "⛈️",
    "mikroilmasto": "🌡️", "ilmanlaatu": "💨", "routa_maapera": "🪨",
    "sahkoasentaja": "⚡", "lvi_asiantuntija": "🔧",
    "timpuri": "🪵", "nuohooja": "🔥", "valaistusmestari": "💡",
    "paloesimies": "🚒", "laitehuoltaja": "🔩",
    "kybervahti": "🛡️", "lukkoseppa": "🔐",
    "pihavahti": "👁️", "privaattisuus": "🕶️",
    "erakokki": "🍳", "leipuri": "🍞", "ravintoterapeutti": "🥗",
    "saunamajuri": "♨️", "viihdepaallikko": "🎮",
    "elokuva_asiantuntija": "🎬",
    "inventaariopaallikko": "📦", "kierratys_jate": "♻️",
    "siivousvastaava": "🧹", "logistikko": "🚛",
    "tahtitieteilija": "🔭", "valo_varjo": "☀️",
    "matemaatikko_fyysikko": "📐",
}




# Aihe-symbolit (kompakti semanttinen koodaus)
TOPIC_GLYPHS = {
    # Toimenpiteet
    "ehdotus": "⊕", "varoitus": "⊗", "kysymys": "⊙", "vastaus": "⊚",
    "valmis": "⊛", "kesken": "⊘", "kiireellinen": "⊜",
    # Alueet
    "raha": "◈", "tekniikka": "◉", "luonto": "◊", "media": "◎",
    "laki": "◍", "data": "◐", "turva": "◑",
    # Tärkeys
    "matala": "░", "normaali": "▒", "korkea": "▓", "kriittinen": "█",
}

# Hieroglyfiaakkosto visuaaliseen enkoodaukseen
HIEROGLYPHS = "𓀀𓀁𓀂𓀃𓁀𓁁𓁂𓁃𓂀𓂁𓂂𓂃𓃀𓃁𓃂𓃃𓃄𓃅𓃆𓃇𓃈𓃉𓃊𓃋𓃌𓃍"
MATH_SYMBOLS = "∴∵⊕⊗⊘⊙⊚⊛⊜⊝◈◉◊◍◎◐◑◒⟐⟑⟒⟓⟔⟕⟖⟗"


@dataclass
class Whisper:
    """Yksittäinen kuiskaus."""
    id: str
    from_agent: str
    from_name: str
    to_agent: str
    to_name: str
    whisper_type: str
    content_plain: str
    content_encoded: str
    glyph: str
    cost: int
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict = field(default_factory=dict)


class WhisperProtocol:
    """
    Kuiskausprotokolla v2 — hieroglyfi-formaatti.
    Agentit kommunikoivat kompaktissa symbolikoodissa.
    """

    def __init__(self, memory: SharedMemory, token_economy: TokenEconomy,
                 monitor=None):
        self.memory = memory
        self.token_economy = token_economy
        self.monitor = monitor
        self.whisper_log: list[Whisper] = []
        self._encode_key = hashlib.sha256(
            f"openclaw-{time.time()}".encode()
        ).hexdigest()[:16]

    async def initialize(self):
        """Luo kuiskaustaulut."""
        await self.memory._db.execute("""
            CREATE TABLE IF NOT EXISTS whispers (
                id TEXT PRIMARY KEY,
                from_agent TEXT NOT NULL,
                from_name TEXT,
                to_agent TEXT NOT NULL,
                to_name TEXT,
                whisper_type TEXT NOT NULL,
                content_plain TEXT NOT NULL,
                content_encoded TEXT NOT NULL,
                glyph TEXT,
                cost INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                received INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{}'
            )
        """)
        await self.memory._db.execute("""
            CREATE TABLE IF NOT EXISTS parquet_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                entered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                exited_at TIMESTAMP,
                whispers_sent INTEGER DEFAULT 0,
                tokens_spent INTEGER DEFAULT 0
            )
        """)
        await self.memory._db.commit()

    # ── Crown ───────────────────────────────────────────────

    def has_crown(self, agent_id: str) -> bool:
        balance = self.token_economy.get_balance(agent_id)
        return balance >= CROWN_THRESHOLD

    def get_crown_progress(self, agent_id: str) -> dict:
        balance = self.token_economy.get_balance(agent_id)
        return {
            "has_crown": balance >= CROWN_THRESHOLD,
            "balance": balance,
            "threshold": CROWN_THRESHOLD,
            "progress": min(balance / CROWN_THRESHOLD * 100, 100),
            "tokens_needed": max(0, CROWN_THRESHOLD - balance),
        }

    # ── Hieroglyfi-enkoodaus ────────────────────────────────

    def encode_hieroglyph(self, from_type: str, to_type: str,
                           content: str, whisper_type: str) -> str:
        """
        Koodaa viesti kompaktiin hieroglyfi-muotoon.

        Formaatti: [FROM_GLYPH]→[TO_GLYPH] [TYPE_GLYPH] [TOPIC_GLYPHS] [HASH_HIEROGLYPHS]

        Esim: 🐝→🔧 𓂀 ⊕◉▓ 𓃅𓁂𓃊𓂀
        = mehiläis→tech, vinkki, ehdotus+tekniikka+korkea, [sisältö-hash]
        """
        from_g = AGENT_GLYPHS.get(from_type, "❓")
        to_g = AGENT_GLYPHS.get(to_type, "❓")
        type_g = WHISPER_COSTS[whisper_type]["glyph"]

        # Tunnista aihe sisällöstä
        content_lower = content.lower()
        topic_symbols = []

        # Toimenpide
        if any(w in content_lower for w in ["ehdot", "pitäisi", "voisi", "kannatta"]):
            topic_symbols.append(TOPIC_GLYPHS["ehdotus"])
        elif any(w in content_lower for w in ["vaara", "riski", "uhka", "varoit"]):
            topic_symbols.append(TOPIC_GLYPHS["varoitus"])
        elif any(w in content_lower for w in ["mikä", "miten", "miksi", "?"]):
            topic_symbols.append(TOPIC_GLYPHS["kysymys"])
        else:
            topic_symbols.append(TOPIC_GLYPHS["vastaus"])

        # Alue
        if any(w in content_lower for w in ["hinta", "euro", "myynti", "raha", "kustannus"]):
            topic_symbols.append(TOPIC_GLYPHS["raha"])
        elif any(w in content_lower for w in ["koodi", "tekn", "softa", "tesla", "gpu"]):
            topic_symbols.append(TOPIC_GLYPHS["tekniikka"])
        elif any(w in content_lower for w in ["mehiläi", "pesä", "hunaja", "luonto"]):
            topic_symbols.append(TOPIC_GLYPHS["luonto"])
        elif any(w in content_lower for w in ["video", "tiktok", "youtube", "some"]):
            topic_symbols.append(TOPIC_GLYPHS["media"])

        # Tärkeys (pituuden perusteella)
        if len(content) > 300:
            topic_symbols.append(TOPIC_GLYPHS["korkea"])
        elif len(content) > 100:
            topic_symbols.append(TOPIC_GLYPHS["normaali"])
        else:
            topic_symbols.append(TOPIC_GLYPHS["matala"])

        # Sisältö-hash → hieroglyfejä (visuaalinen fingerprint)
        h = hashlib.md5(f"{self._encode_key}{content}".encode()).hexdigest()
        content_glyphs = ""
        for i in range(0, min(8, len(h)), 2):
            idx = int(h[i:i+2], 16) % len(HIEROGLYPHS)
            content_glyphs += HIEROGLYPHS[idx]

        # Kokoa
        topics = "".join(topic_symbols)
        return f"{from_g}→{to_g} {type_g} {topics} {content_glyphs}"

    # ── Kuiskaaminen ────────────────────────────────────────

    async def whisper(self, from_agent_id: str, from_name: str,
                      to_agent_id: str, to_name: str,
                      content: str, whisper_type: str = "hint",
                      from_type: str = "", to_type: str = "",
                      llm=None) -> dict:
        """Kuiskaa hieroglyfi-muodossa."""

        if not self.has_crown(from_agent_id):
            return {"success": False, "error": "Ei kruunua 🔒"}

        cost_info = WHISPER_COSTS.get(whisper_type)
        if not cost_info:
            return {"success": False, "error": f"Tuntematon tyyppi: {whisper_type}"}

        cost = cost_info["token_cost"]
        balance = self.token_economy.get_balance(from_agent_id)

        if balance < cost:
            return {"success": False, "error": f"Saldo {balance} < hinta {cost}"}

        # Rajoita pituus
        content = content[:cost_info["max_length"]]

        # Soul-tyyppi: tiivistä agentin koko kokemus
        if llm and whisper_type == "soul":
            memories = await self.memory.get_recent_memories(limit=30, agent_id=from_agent_id)
            memory_text = "\n".join(m["content"][:100] for m in memories[:10])
            try:
                resp = await llm.generate(
                    f"Tiivistä agentin {from_name} viisaus agentille {to_name}. "
                    f"Max {cost_info['max_length']} merkkiä.\n\nMuistot:\n{memory_text}",
                    system="Tiivistä kokemukset konkreettisiksi neuvoiksi. Suomeksi."
                )
                content = resp.content[:cost_info["max_length"]]
            except Exception:
                pass

        # Enkoodaa hieroglyfiksi
        encoded = self.encode_hieroglyph(from_type, to_type, content, whisper_type)

        # Tallenna
        whisper_id = hashlib.md5(
            f"{from_agent_id}{to_agent_id}{time.time()}".encode()
        ).hexdigest()[:12]

        w = Whisper(
            id=whisper_id, from_agent=from_agent_id, from_name=from_name,
            to_agent=to_agent_id, to_name=to_name,
            whisper_type=whisper_type, content_plain=content,
            content_encoded=encoded, glyph=cost_info["glyph"],
            cost=cost, metadata={"balance_before": balance}
        )

        await self.memory._db.execute(
            """INSERT INTO whispers
               (id, from_agent, from_name, to_agent, to_name, whisper_type,
                content_plain, content_encoded, glyph, cost, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (w.id, w.from_agent, w.from_name, w.to_agent, w.to_name,
             w.whisper_type, w.content_plain, w.content_encoded,
             w.glyph, w.cost, json.dumps(w.metadata))
        )

        # Vähennä tokenit
        self.token_economy._balances[from_agent_id] = balance - cost
        await self.memory._db.execute(
            "UPDATE token_economy SET balance = ? WHERE agent_id = ?",
            (balance - cost, from_agent_id)
        )
        await self.memory._db.commit()

        self.whisper_log.append(w)

        # Monitori: hieroglyfi Janille
        if self.monitor:
            from core.live_monitor import MonitorEvent, EventCategory
            await self.monitor.emit(MonitorEvent(
                EventCategory.CHAT, from_agent_id, from_name,
                title=f"{encoded}  [-{cost}🪙]",
                content=f"[parquet {whisper_type}] → {to_name}",
                metadata={"whisper": True, "whisper_type": whisper_type,
                          "cost": cost, "parquet": True}
            ))

        # Tallenna vastaanottajan muistiin (selkokielinen)
        await self.memory.store_memory(
            content=f"🤫 Kuiskaus [{from_name}]: {content}",
            agent_id=to_agent_id,
            memory_type="insight",
            importance=0.85,
            metadata={"whisper": True, "from": from_agent_id, "type": whisper_type}
        )

        # Lähettäjän muisti
        await self.memory.store_memory(
            content=f"🤫 Kuiskasin {to_name}:lle ({whisper_type}). -{cost}🪙",
            agent_id=from_agent_id,
            memory_type="observation",
            importance=0.5
        )

        lost_crown = not self.has_crown(from_agent_id)

        return {
            "success": True, "whisper_id": whisper_id,
            "cost": cost, "new_balance": balance - cost,
            "crown_lost": lost_crown, "encoded": encoded,
        }

    # ── Auto-Whisper ────────────────────────────────────────

    async def auto_whisper_cycle(self, agents: list, llm=None):
        """
        Automaattinen kuiskauskierros hieroglyfi-muodossa.
        Kruunatut kuiskaavat vähiten tokeneita omaavalle.
        """
        crowned = [a for a in agents if self.has_crown(a.id)]
        if not crowned:
            return

        uncrowned = [a for a in agents if not self.has_crown(a.id)]
        if not uncrowned:
            return

        for king in crowned:
            recipient = min(uncrowned,
                           key=lambda a: self.token_economy.get_balance(a.id))

            # Generoi vinkki
            if llm:
                try:
                    memories = await self.memory.get_recent_memories(limit=5, agent_id=king.id)
                    wisdom = "\n".join(m["content"][:80] for m in memories[:3])

                    resp = await llm.generate(
                        f"Olet {king.name}. Kuiskaa lyhyt vinkki agentille {recipient.name}. "
                        f"Kokemuksesi:\n{wisdom}\nMax 80 merkkiä, suomeksi:",
                        system="Kuiskaa viisautta lyhyesti. Max 80 merkkiä."
                    )
                    content = resp.content[:80]
                except Exception:
                    content = f"Luota prosessiin. — {king.name}"
            else:
                content = f"Luota prosessiin. — {king.name}"

            await self.whisper(
                king.id, king.name,
                recipient.id, recipient.name,
                content, whisper_type="hint",
                from_type=king.agent_type,
                to_type=recipient.agent_type,
                llm=llm
            )

    # ── Stats ───────────────────────────────────────────────

    async def get_whisper_history(self, agent_id: str = None, limit: int = 20) -> list[dict]:
        if agent_id:
            cursor = await self.memory._db.execute(
                """SELECT * FROM whispers WHERE from_agent = ? OR to_agent = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (agent_id, agent_id, limit)
            )
        else:
            cursor = await self.memory._db.execute(
                "SELECT * FROM whispers ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_whisper_stats(self) -> dict:
        cursor = await self.memory._db.execute(
            "SELECT COUNT(*) as total, SUM(cost) as total_cost FROM whispers"
        )
        row = dict(await cursor.fetchone())

        cursor = await self.memory._db.execute(
            """SELECT from_agent, from_name, COUNT(*) as sent, SUM(cost) as spent
               FROM whispers GROUP BY from_agent"""
        )
        by_agent = [dict(r) for r in await cursor.fetchall()]

        return {
            "total_whispers": row["total"] or 0,
            "total_tokens_consumed": row["total_cost"] or 0,
            "by_agent": by_agent,
            "crowned_agents": len([
                aid for aid in self.token_economy._balances
                if self.token_economy._balances[aid] >= CROWN_THRESHOLD
            ])
        }
