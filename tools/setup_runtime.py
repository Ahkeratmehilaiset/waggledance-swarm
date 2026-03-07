#!/usr/bin/env python3
"""
OpenClaw v1.4 — Täysasennus (Windows)
=======================================
Luo KAIKKI puuttuvat runtime-tiedostot ja asentaa riippuvuudet.

Aja: python setup_runtime.py

Asentaa:
  1. Python-paketit (aiosqlite, pyyaml, httpx, fastapi, uvicorn, ...)
  2. Luo puuttuvat moduulit:
     - main.py
     - core/llm_provider.py
     - core/token_economy.py
     - core/live_monitor.py
     - memory/shared_memory.py
     - web/dashboard.py
     - configs/settings.yaml
  3. Siirtää runtime-tiedostot oikeisiin kansioihin
  4. Testaa että kaikki importit toimivat
"""

import subprocess, sys, os, shutil
from pathlib import Path

ROOT = Path(__file__).parent


def pip_install(packages: list):
    print(f"\n📦 Asennetaan: {', '.join(packages)}")
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "--quiet", *packages
    ])
    print("   ✅ OK")


def write_file(rel_path: str, content: str):
    p = ROOT / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        print(f"   ℹ️  {rel_path} — jo olemassa, ohitetaan")
        return False
    p.write_text(content, encoding="utf-8")
    print(f"   ✅ {rel_path}")
    return True


def main():
    print("""
╔══════════════════════════════════════════════╗
║  OpenClaw v1.4 — Runtime Setup (Windows)     ║
╚══════════════════════════════════════════════╝
""")
    print(f"📁 Projekti: {ROOT}\n")

    # ══════════════════════════════════════════════════════════
    # 1. RIIPPUVUUDET
    # ══════════════════════════════════════════════════════════
    print("1️⃣  Python-paketit\n")
    pip_install([
        "aiosqlite",       # Async SQLite
        "pyyaml",          # YAML-tuki
        "httpx",           # Async HTTP (Ollama)
        "fastapi",         # Web dashboard
        "uvicorn",         # ASGI-palvelin
        "websockets",      # WebSocket live feed
        "duckduckgo-search",  # Oracle web search
    ])

    # Valinnainen: PyMuPDF (PDF-tuki)
    try:
        pip_install(["PyMuPDF"])
    except Exception:
        print("   ⚠️  PyMuPDF ei asentunut (PDF-tuki pois, ei kriittinen)")

    # ══════════════════════════════════════════════════════════
    # 2. KANSIORAKENNE
    # ══════════════════════════════════════════════════════════
    print("\n2️⃣  Kansiorakenne\n")
    for d in ["core", "memory", "agents", "web", "configs", "data",
              "data/hacker_workspace", "knowledge", "knowledge/shared"]:
        (ROOT / d).mkdir(parents=True, exist_ok=True)

    # __init__.py joka kansioon
    for d in ["core", "memory", "agents", "web"]:
        init = ROOT / d / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")
    print("   ✅ Kansiot ja __init__.py")

    # ══════════════════════════════════════════════════════════
    # 3. RUNTIME-TIEDOSTOT
    # ══════════════════════════════════════════════════════════
    print("\n3️⃣  Runtime-moduulit\n")

    # ── main.py ──────────────────────────────────────────────
    write_file("main.py", r'''#!/usr/bin/env python3
"""
OpenClaw v1.4 — Pääkäynnistin
"""
import asyncio
import sys
from pathlib import Path

# Lisää projekti PYTHONPATH:iin
sys.path.insert(0, str(Path(__file__).parent))

from hivemind import HiveMind


async def main():
    hive = HiveMind("configs/settings.yaml")
    await hive.start()

    # Auto-spawn perusagentit
    auto_types = [t for t, tmpl in hive.spawner.agent_templates.items()
                  if tmpl.get("auto_spawn")]
    for agent_type in auto_types[:7]:  # Max 7 alkuun
        try:
            await hive.spawner.spawn(agent_type)
        except Exception as e:
            print(f"  ⚠️  Spawn {agent_type}: {e}")

    print(f"\n🟢 OpenClaw käynnissä! {len(hive.spawner.active_agents)} agenttia.")
    print(f"   Dashboard: http://localhost:8000")
    print(f"   Pysäytä: Ctrl+C\n")

    # Käynnistä dashboard
    try:
        from web.dashboard import create_app
        app = create_app(hive)
        import uvicorn
        config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning")
        server = uvicorn.Server(config)
        await server.serve()
    except KeyboardInterrupt:
        pass
    finally:
        await hive.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 OpenClaw sammutettu.")
''')

    # ── core/llm_provider.py ─────────────────────────────────
    write_file("core/llm_provider.py", r'''"""
OpenClaw LLM Provider — Ollama-yhteensopiva
"""
import httpx
import json
import asyncio
from dataclasses import dataclass
from typing import Optional


@dataclass
class LLMResponse:
    content: str
    model: str = ""
    tokens_used: int = 0
    raw: dict = None

    def __post_init__(self):
        if self.raw is None:
            self.raw = {}


class LLMProvider:
    """Yhteys Ollama-palvelimeen (tai muuhun LLM:ään)."""

    def __init__(self, config: dict = None):
        config = config or {}
        self.base_url = config.get("base_url", "http://localhost:11434")
        self.model = config.get("model", "gemma3:4b")
        self.timeout = config.get("timeout", 120)
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout)
            )
        return self._client

    async def generate(self, prompt: str, system: str = "",
                       temperature: float = 0.7,
                       max_tokens: int = 1000) -> LLMResponse:
        """Generoi vastaus Ollamalla."""
        client = await self._get_client()

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            }
        }
        if system:
            payload["system"] = system

        try:
            resp = await client.post("/api/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return LLMResponse(
                content=data.get("response", ""),
                model=data.get("model", self.model),
                tokens_used=data.get("eval_count", 0),
                raw=data
            )
        except httpx.ConnectError:
            return LLMResponse(
                content="[Ollama ei vastaa — tarkista: ollama serve]",
                model=self.model
            )
        except Exception as e:
            return LLMResponse(content=f"[LLM-virhe: {e}]", model=self.model)

    async def generate_structured(self, prompt: str,
                                   schema_description: str = "",
                                   system: str = "") -> dict:
        """Generoi JSON-vastaus."""
        sys_prompt = (system or "") + "\nVastaa VAIN JSON-muodossa, ei muuta."
        response = await self.generate(prompt, system=sys_prompt, temperature=0.3)

        # Yritä parsea JSON
        text = response.content.strip()
        # Poista markdown-koodiblokki
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Etsi ensimmäinen { ... }
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except (json.JSONDecodeError, ValueError):
                    pass
            return {"error": "JSON parse failed", "raw": response.content}

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
''')

    # ── core/token_economy.py ────────────────────────────────
    write_file("core/token_economy.py", r'''"""
OpenClaw Token Economy — Agentit ansaitsevat ja käyttävät tokeneita
"""
import json
from datetime import datetime


# Oracle-hinnat (kuiskaukset käyttävät whisper_protocol.py:n WHISPER_COSTS)
ORACLE_PRICES = {
    "web_search": 3,
    "claude_question": 10,
    "deep_research": 20,
}

# Palkitsemissäännöt
REWARD_RULES = {
    "task_completed": 10,
    "insight_generated": 5,
    "reflection_done": 3,
    "message_sent": 1,
    "question_answered": 8,
}

# Emoji-progressio
RANK_EMOJIS = {
    0: "😊",
    20: "😄",
    50: "🤩",
    80: "🔥",
    100: "👑",
}


class TokenEconomy:
    """Hallinnoi agenttien tokeni-taloutta."""

    def __init__(self, memory):
        self.memory = memory
        self._balances: dict[str, int] = {}
        self._history: list[dict] = []

    async def initialize(self):
        """Luo taulut ja lataa saldot."""
        await self.memory._db.execute("""
            CREATE TABLE IF NOT EXISTS token_economy (
                agent_id TEXT PRIMARY KEY,
                balance INTEGER DEFAULT 0,
                total_earned INTEGER DEFAULT 0,
                total_spent INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self.memory._db.execute("""
            CREATE TABLE IF NOT EXISTS token_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT,
                balance_after INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self.memory._db.commit()

        # Lataa olemassa olevat saldot
        cursor = await self.memory._db.execute(
            "SELECT agent_id, balance FROM token_economy"
        )
        for row in await cursor.fetchall():
            self._balances[row[0]] = row[1]

    def get_balance(self, agent_id: str) -> int:
        return self._balances.get(agent_id, 0)

    async def reward(self, agent_id: str, reason: str,
                     custom_amount: int = None) -> int:
        """Anna tokeneita agentille."""
        amount = custom_amount or REWARD_RULES.get(reason, 1)
        balance = self._balances.get(agent_id, 0) + amount
        self._balances[agent_id] = balance

        await self.memory._db.execute("""
            INSERT INTO token_economy (agent_id, balance, total_earned)
            VALUES (?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                balance = ?,
                total_earned = total_earned + ?,
                updated_at = CURRENT_TIMESTAMP
        """, (agent_id, balance, amount, balance, amount))

        await self.memory._db.execute(
            "INSERT INTO token_transactions (agent_id, amount, reason, balance_after) VALUES (?,?,?,?)",
            (agent_id, amount, reason, balance)
        )
        await self.memory._db.commit()

        self._history.append({
            "agent_id": agent_id, "amount": amount,
            "reason": reason, "balance": balance,
            "time": datetime.now().isoformat()
        })
        return balance

    async def spend(self, agent_id: str, amount: int, reason: str) -> bool:
        """Käytä tokeneita."""
        balance = self._balances.get(agent_id, 0)
        if balance < amount:
            return False
        new_balance = balance - amount
        self._balances[agent_id] = new_balance

        await self.memory._db.execute(
            "UPDATE token_economy SET balance = ?, total_spent = total_spent + ? WHERE agent_id = ?",
            (new_balance, amount, agent_id)
        )
        await self.memory._db.execute(
            "INSERT INTO token_transactions (agent_id, amount, reason, balance_after) VALUES (?,?,?,?)",
            (agent_id, -amount, reason, new_balance)
        )
        await self.memory._db.commit()
        return True

    def get_rank_emoji(self, agent_id: str) -> str:
        balance = self.get_balance(agent_id)
        emoji = "😊"
        for threshold, e in sorted(RANK_EMOJIS.items()):
            if balance >= threshold:
                emoji = e
        return emoji

    def get_leaderboard(self) -> list[dict]:
        board = []
        for aid, balance in sorted(self._balances.items(),
                                    key=lambda x: x[1], reverse=True):
            board.append({
                "agent_id": aid,
                "balance": balance,
                "rank": self.get_rank_emoji(aid)
            })
        return board
''')

    # ── core/live_monitor.py ─────────────────────────────────
    write_file("core/live_monitor.py", r'''"""
OpenClaw Live Monitor — Reaaliaikainen tapahtumasyöte
"""
import asyncio
import json
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class EventCategory(Enum):
    SYSTEM = "system"
    THOUGHT = "thought"
    CHAT = "chat"
    TASK = "task"
    AGENT = "agent"
    ERROR = "error"


@dataclass
class MonitorEvent:
    category: EventCategory
    agent_id: str
    agent_name: str
    title: str = ""
    content: str = ""
    metadata: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "title": self.title,
            "content": self.content,
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
        }


class LiveMonitor:
    """Kerää ja jakaa tapahtumia reaaliaikaisesti."""

    def __init__(self, max_history: int = 200):
        self.events: list[MonitorEvent] = []
        self.max_history = max_history
        self._callbacks: list = []

    async def emit(self, event: MonitorEvent):
        self.events.append(event)
        if len(self.events) > self.max_history:
            self.events = self.events[-self.max_history:]

        for cb in self._callbacks:
            try:
                await cb(event.to_dict())
            except Exception:
                pass

        # Tulosta konsoliin
        cat = event.category.value[:4].upper()
        print(f"  [{cat}] {event.agent_name}: {event.title[:80]}")

    async def system(self, message: str):
        await self.emit(MonitorEvent(
            EventCategory.SYSTEM, "system", "System", title=message
        ))

    async def thought(self, agent_id: str, agent_name: str,
                      prompt: str = "", response: str = ""):
        await self.emit(MonitorEvent(
            EventCategory.THOUGHT, agent_id, agent_name,
            title=f"💭 {response[:100]}",
            content=response,
            metadata={"prompt_preview": prompt[:200]}
        ))

    async def chat(self, from_id: str, from_name: str,
                   to_id: str, to_name: str, message: str):
        await self.emit(MonitorEvent(
            EventCategory.CHAT, from_id, from_name,
            title=f"💬 → {to_name}: {message[:80]}",
            content=message,
            metadata={"to_agent": to_id, "to_name": to_name}
        ))

    async def agent_spawned(self, agent_id: str, agent_name: str,
                             agent_type: str):
        await self.emit(MonitorEvent(
            EventCategory.AGENT, agent_id, agent_name,
            title=f"🐣 Luotu: {agent_name} ({agent_type})"
        ))

    def register_callback(self, callback):
        self._callbacks.append(callback)

    def get_history(self, limit: int = 50) -> list[dict]:
        return [e.to_dict() for e in self.events[-limit:]]
''')

    # ── memory/shared_memory.py ──────────────────────────────
    write_file("memory/shared_memory.py", r'''"""
OpenClaw Shared Memory — Jaettu muisti kaikille agenteille (aiosqlite)
"""
import aiosqlite
import json
import uuid
from datetime import datetime
from typing import Optional


class SharedMemory:
    """Async SQLite-pohjainen jaettu muisti."""

    def __init__(self, db_path: str = "data/openclaw.db"):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self):
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row

        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                name TEXT, type TEXT, status TEXT DEFAULT 'idle',
                config TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                agent_id TEXT,
                project_id TEXT,
                memory_type TEXT DEFAULT 'observation',
                importance REAL DEFAULT 0.5,
                metadata TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT, event_type TEXT,
                description TEXT, data TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_agent TEXT, to_agent TEXT,
                content TEXT, message_type TEXT DEFAULT 'info',
                read INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT, description TEXT,
                assigned_agent TEXT, project_id TEXT,
                status TEXT DEFAULT 'pending',
                priority INTEGER DEFAULT 5,
                result TEXT, started_at TEXT, completed_at TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT, description TEXT,
                status TEXT DEFAULT 'active',
                tags TEXT DEFAULT '[]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await self._db.commit()

    async def close(self):
        if self._db:
            await self._db.close()

    # ── Agentit ──────────────────────────────────────────────

    async def register_agent(self, agent_id, name, agent_type, config=None):
        await self._db.execute(
            "INSERT OR REPLACE INTO agents (id, name, type, config) VALUES (?,?,?,?)",
            (agent_id, name, agent_type, json.dumps(config or {}))
        )
        await self._db.commit()

    async def update_agent_status(self, agent_id, status):
        await self._db.execute(
            "UPDATE agents SET status = ? WHERE id = ?", (status, agent_id)
        )
        await self._db.commit()

    # ── Muisti ───────────────────────────────────────────────

    async def store_memory(self, content, agent_id=None, project_id=None,
                           memory_type="observation", importance=0.5,
                           metadata=None):
        mid = uuid.uuid4().hex[:12]
        await self._db.execute(
            """INSERT INTO memories (id, content, agent_id, project_id,
               memory_type, importance, metadata) VALUES (?,?,?,?,?,?,?)""",
            (mid, content, agent_id, project_id, memory_type,
             importance, json.dumps(metadata or {}))
        )
        await self._db.commit()
        return mid

    async def recall(self, query, limit=10, agent_id=None):
        """Yksinkertainen tekstihaku muistista."""
        if agent_id:
            cursor = await self._db.execute(
                """SELECT * FROM memories WHERE agent_id = ?
                   AND content LIKE ? ORDER BY importance DESC, created_at DESC LIMIT ?""",
                (agent_id, f"%{query[:50]}%", limit)
            )
        else:
            cursor = await self._db.execute(
                """SELECT * FROM memories WHERE content LIKE ?
                   ORDER BY importance DESC, created_at DESC LIMIT ?""",
                (f"%{query[:50]}%", limit)
            )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_recent_memories(self, limit=10, agent_id=None):
        if agent_id:
            cursor = await self._db.execute(
                "SELECT * FROM memories WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit)
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_full_context(self, topic="", limit=15):
        """Koosta täysi konteksti aiheesta."""
        memories = await self.recall(topic, limit=limit)
        recent = await self.get_recent_memories(limit=5)
        all_memories = memories + [m for m in recent if m not in memories]
        return "\n".join(m.get("content", "")[:200] for m in all_memories[:limit])

    # ── Viestit ──────────────────────────────────────────────

    async def send_message(self, from_agent, to_agent, content, message_type="info"):
        await self._db.execute(
            "INSERT INTO messages (from_agent, to_agent, content, message_type) VALUES (?,?,?,?)",
            (from_agent, to_agent, content, message_type)
        )
        await self._db.commit()

    async def get_messages(self, agent_id):
        cursor = await self._db.execute(
            "SELECT * FROM messages WHERE to_agent = ? AND read = 0 ORDER BY created_at DESC",
            (agent_id,)
        )
        rows = [dict(r) for r in await cursor.fetchall()]
        return rows

    async def mark_messages_read(self, agent_id):
        await self._db.execute(
            "UPDATE messages SET read = 1 WHERE to_agent = ?", (agent_id,)
        )
        await self._db.commit()

    # ── Tapahtumat ───────────────────────────────────────────

    async def log_event(self, agent_id, event_type, description, data=None):
        await self._db.execute(
            "INSERT INTO events (agent_id, event_type, description, data) VALUES (?,?,?,?)",
            (agent_id, event_type, description, json.dumps(data or {}))
        )
        await self._db.commit()

    async def get_timeline(self, limit=20, agent_id=None):
        if agent_id:
            cursor = await self._db.execute(
                "SELECT * FROM events WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit)
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [dict(r) for r in await cursor.fetchall()]

    # ── Tehtävät ─────────────────────────────────────────────

    async def add_task(self, title, description="", assigned_agent=None,
                       project_id=None, priority=5):
        tid = uuid.uuid4().hex[:12]
        await self._db.execute(
            """INSERT INTO tasks (id, title, description, assigned_agent,
               project_id, priority) VALUES (?,?,?,?,?,?)""",
            (tid, title, description, assigned_agent, project_id, priority)
        )
        await self._db.commit()
        return tid

    async def get_tasks(self, status=None):
        if status:
            cursor = await self._db.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY priority DESC", (status,)
            )
        else:
            cursor = await self._db.execute("SELECT * FROM tasks ORDER BY priority DESC")
        return [dict(r) for r in await cursor.fetchall()]

    async def update_task(self, task_id, **kwargs):
        for key, value in kwargs.items():
            await self._db.execute(
                f"UPDATE tasks SET {key} = ? WHERE id = ?", (value, task_id)
            )
        await self._db.commit()

    # ── Projektit ────────────────────────────────────────────

    async def add_project(self, name, description="", tags=None):
        pid = uuid.uuid4().hex[:12]
        await self._db.execute(
            "INSERT INTO projects (id, name, description, tags) VALUES (?,?,?,?)",
            (pid, name, description, json.dumps(tags or []))
        )
        await self._db.commit()
        return pid

    async def get_projects(self):
        cursor = await self._db.execute("SELECT * FROM projects")
        return [dict(r) for r in await cursor.fetchall()]

    # ── Tilastot ─────────────────────────────────────────────

    async def get_memory_stats(self):
        stats = {}
        for table in ["memories", "events", "messages", "tasks", "agents"]:
            cursor = await self._db.execute(f"SELECT COUNT(*) FROM {table}")
            row = await cursor.fetchone()
            stats[table] = row[0]
        return stats
''')

    # ── web/dashboard.py ─────────────────────────────────────
    write_file("web/dashboard.py", r'''"""
OpenClaw Dashboard — FastAPI + WebSocket
"""
import json
import asyncio
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse


def create_app(hivemind):
    app = FastAPI(title="OpenClaw Dashboard")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return """<!DOCTYPE html>
<html><head><title>OpenClaw v1.4</title>
<style>
  body{font-family:monospace;background:#0d1117;color:#e6edf3;margin:0;padding:20px}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;max-width:1400px;margin:auto}
  .card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px}
  h1{color:#58a6ff;text-align:center} h2{color:#79c0ff;margin-top:0;font-size:16px}
  .feed{max-height:300px;overflow-y:auto;font-size:12px;line-height:1.8}
  .feed div{border-bottom:1px solid #21262d;padding:4px 0}
  input,textarea{background:#0d1117;border:1px solid #30363d;color:#e6edf3;border-radius:6px;padding:8px;width:100%;box-sizing:border-box}
  button{background:#238636;color:white;border:none;border-radius:6px;padding:8px 16px;cursor:pointer;margin:4px}
  button:hover{background:#2ea043}
  .stat{display:inline-block;background:#21262d;border-radius:4px;padding:4px 8px;margin:2px;font-size:12px}
  #livefeed div{animation:fadeIn 0.3s}
  @keyframes fadeIn{from{opacity:0}to{opacity:1}}
</style></head>
<body>
<h1>🧠 OpenClaw v1.4 Dashboard</h1>
<div class="grid">
  <div class="card">
    <h2>💬 Chat</h2>
    <div id="chatlog" class="feed" style="min-height:200px"></div>
    <div style="display:flex;gap:8px;margin-top:8px">
      <input id="chatinput" placeholder="Kirjoita viesti..." onkeypress="if(event.key==='Enter')sendChat()">
      <button onclick="sendChat()">Lähetä</button>
    </div>
  </div>
  <div class="card">
    <h2>📡 Live Feed</h2>
    <div id="livefeed" class="feed"></div>
  </div>
  <div class="card">
    <h2>🤖 Agentit</h2>
    <div id="agents"></div>
    <button onclick="loadStatus()" style="margin-top:8px">🔄 Päivitä</button>
  </div>
  <div class="card">
    <h2>🏆 Token Economy</h2>
    <div id="leaderboard"></div>
  </div>
</div>
<script>
const ws = new WebSocket(`ws://${location.host}/ws`);
ws.onmessage = e => {
  const d = JSON.parse(e.data);
  const feed = document.getElementById('livefeed');
  const div = document.createElement('div');
  const t = new Date().toLocaleTimeString();
  div.innerHTML = `<span style="color:#666">${t}</span> ${d.title||d.type||'event'}`;
  feed.prepend(div);
  if(feed.children.length > 50) feed.lastChild.remove();
};
async function sendChat(){
  const input = document.getElementById('chatinput');
  const msg = input.value.trim(); if(!msg) return;
  const log = document.getElementById('chatlog');
  log.innerHTML += `<div>🧑 ${msg}</div>`;
  input.value = '';
  const r = await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg})});
  const d = await r.json();
  log.innerHTML += `<div style="color:#79c0ff">🤖 ${d.response||d.error}</div>`;
  log.scrollTop = log.scrollHeight;
}
async function loadStatus(){
  const r = await fetch('/api/status'); const d = await r.json();
  const a = document.getElementById('agents');
  a.innerHTML = d.agents?.list?.map(ag=>`<div class="stat">${ag.name} [${ag.status}] XP:${ag.experience_points||0}</div>`).join('')||'Ei agentteja';
  const lb = document.getElementById('leaderboard');
  lb.innerHTML = d.token_economy?.leaderboard?.map(e=>`<div class="stat">${e.rank} ${e.agent_id.slice(0,20)} = ${e.balance}🪙</div>`).join('')||'-';
}
setTimeout(loadStatus, 1000);
setInterval(loadStatus, 15000);
</script></body></html>"""

    @app.post("/api/chat")
    async def chat(data: dict):
        msg = data.get("message", "")
        if not msg: return {"error": "Tyhjä viesti"}
        try:
            response = await hivemind.chat(msg)
            return {"response": response}
        except Exception as e:
            return {"error": str(e)}

    @app.get("/api/status")
    async def status():
        try:
            return await hivemind.get_status()
        except Exception as e:
            return {"error": str(e)}

    @app.get("/api/monitor/history")
    async def monitor_history():
        if hivemind.monitor:
            return {"events": hivemind.monitor.get_history(50)}
        return {"events": []}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        async def ws_callback(data):
            try:
                await websocket.send_json(data)
            except Exception: pass

        hivemind.register_ws_callback(ws_callback)
        if hivemind.monitor:
            hivemind.monitor.register_callback(
                lambda e: websocket.send_json(e)
            )
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            hivemind.unregister_ws_callback(ws_callback)

    return app
''')

    # ── configs/settings.yaml ────────────────────────────────
    write_file("configs/settings.yaml", r'''# OpenClaw v1.4 Asetukset
llm:
  base_url: "http://localhost:11434"
  model: "gemma3:4b"
  timeout: 120

memory:
  db_path: "data/openclaw.db"

hivemind:
  heartbeat_interval: 30
  max_concurrent_agents: 20

token_economy:
  welcome_bonus: 5

yaml_bridge:
  agents_dir: "knowledge"
''')

    # ══════════════════════════════════════════════════════════
    # 4. SIIRRA RUNTIME-TIEDOSTOT OIKEISIIN KANSIOIHIN
    # ══════════════════════════════════════════════════════════
    print("\n4️⃣  Runtime-tiedostojen sijoittelu\n")

    # Tarkista onko whisper_protocol.zip:n tiedostot juuressa
    runtime_moves = {
        "base_agent.py": "agents/base_agent.py",
        "spawner.py": "agents/spawner.py",
        "hacker_agent.py": "agents/hacker_agent.py",
        "oracle_agent.py": "agents/oracle_agent.py",
        "whisper_protocol.py": "core/whisper_protocol.py",
        "knowledge_loader.py": "core/knowledge_loader.py",
        "web_search.py": "tools/web_search.py",
        "patch_dashboard.py": "tools/patch_dashboard.py",
        "dashboard_oracle_patch.py": "tools/dashboard_oracle_patch.py",
    }

    for src_name, dst_path in runtime_moves.items():
        src = ROOT / src_name
        dst = ROOT / dst_path
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            print(f"   ✅ {src_name} → {dst_path}")
        elif dst.exists():
            print(f"   ℹ️  {dst_path} — jo paikallaan")
        # Jos ei löydy kumpaakaan, ohita hiljaa

    # hivemind.py jää juureen (se importataan main.py:stä)

    # ══════════════════════════════════════════════════════════
    # 5. TESTAA IMPORTIT
    # ══════════════════════════════════════════════════════════
    print("\n5️⃣  Import-testi\n")

    import importlib
    sys.path.insert(0, str(ROOT))

    test_modules = [
        ("core.llm_provider", "LLMProvider"),
        ("core.token_economy", "TokenEconomy"),
        ("core.live_monitor", "LiveMonitor"),
        ("memory.shared_memory", "SharedMemory"),
        ("core.yaml_bridge", "YAMLBridge"),
    ]

    all_ok = True
    for module_name, class_name in test_modules:
        try:
            mod = importlib.import_module(module_name)
            cls = getattr(mod, class_name)
            print(f"   ✅ {module_name}.{class_name}")
        except Exception as e:
            print(f"   ❌ {module_name}: {e}")
            all_ok = False

    # Tarkista että hivemind importtaa
    try:
        # Ei importata kokonaan (tarvitsee async), mutta parsitaan syntaksi
        import py_compile
        hm = ROOT / "hivemind.py"
        if hm.exists():
            py_compile.compile(str(hm), doraise=True)
            print(f"   ✅ hivemind.py — syntaksi OK")
    except Exception as e:
        print(f"   ⚠️  hivemind.py: {e}")

    # ══════════════════════════════════════════════════════════
    # 6. VALMIS
    # ══════════════════════════════════════════════════════════
    print(f"""
{'═'*50}
{"✅ ASENNUS VALMIS!" if all_ok else "⚠️  ASENNUS VALMIS (joitain varoituksia)"}
{'═'*50}

Käynnistä:
  1. Varmista Ollama: ollama serve
  2. Varmista malli:  ollama pull gemma3:4b
  3. Käynnistä:       python main.py
  4. Dashboard:       http://localhost:8000

Kansiorakenne:
  {ROOT}/
  ├── main.py              ← käynnistä tästä
  ├── hivemind.py           ← orkesteri
  ├── configs/settings.yaml ← asetukset (malli, portti)
  ├── agents/               ← base_agent, spawner, hacker, oracle
  ├── core/                 ← llm, tokens, whisper, monitor, yaml_bridge
  ├── memory/               ← shared_memory (SQLite)
  ├── web/                  ← dashboard (FastAPI)
  ├── knowledge/            ← 50 agentin YAML-tietopankit
  ├── tools/                ← web_search, build pipeline
  └── data/                 ← SQLite db, workspace
""")


if __name__ == "__main__":
    main()
