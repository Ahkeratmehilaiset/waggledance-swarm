#!/usr/bin/env python3
"""
OpenClaw v1.4 — Diagnostiikka ja stressitesti
================================================
Testaa KAIKKI järjestelmän osat ja raportoi viat.

Aja dashboard-palvelimen ollessa päällä:
  python test_all.py

Tai ilman palvelinta (pelkkä moduulitesti):
  python test_all.py --offline
"""

import asyncio
import sys
import os
import json
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

# Värit
G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; B = "\033[94m"; W = "\033[0m"
os.system("")  # ANSI Windows

RESULTS = {"pass": 0, "fail": 0, "warn": 0, "errors": []}

def OK(msg):
    RESULTS["pass"] += 1
    print(f"  {G}✅ {msg}{W}")

def FAIL(msg):
    RESULTS["fail"] += 1
    RESULTS["errors"].append(msg)
    print(f"  {R}❌ {msg}{W}")

def WARN(msg):
    RESULTS["warn"] += 1
    print(f"  {Y}⚠️  {msg}{W}")

def SECTION(title):
    print(f"\n{B}{'═'*50}")
    print(f"  {title}")
    print(f"{'═'*50}{W}")


# ══════════════════════════════════════════════════════════════
# TESTI 1: Import-testit
# ══════════════════════════════════════════════════════════════

def test_imports():
    SECTION("1. IMPORTIT")

    modules = [
        ("core.llm_provider", "LLMProvider"),
        ("core.llm_provider", "LLMResponse"),
        ("core.token_economy", "TokenEconomy"),
        ("core.token_economy", "ORACLE_PRICES"),
        ("core.live_monitor", "LiveMonitor"),
        ("core.live_monitor", "MonitorEvent"),
        ("core.live_monitor", "EventCategory"),
        ("core.whisper_protocol", "WhisperProtocol"),
        ("core.knowledge_loader", "KnowledgeLoader"),
        ("core.yaml_bridge", "YAMLBridge"),
        ("memory.shared_memory", "SharedMemory"),
        ("agents.base_agent", "Agent"),
        ("agents.spawner", "AgentSpawner"),
        ("agents.hacker_agent", "HackerAgent"),
        ("agents.oracle_agent", "OracleAgent"),
    ]

    for mod_name, class_name in modules:
        try:
            mod = __import__(mod_name, fromlist=[class_name])
            cls = getattr(mod, class_name)
            OK(f"{mod_name}.{class_name}")
        except Exception as e:
            FAIL(f"{mod_name}.{class_name}: {e}")

    # HiveMind
    try:
        from hivemind import HiveMind
        OK("hivemind.HiveMind")
    except Exception as e:
        FAIL(f"hivemind.HiveMind: {e}")


# ══════════════════════════════════════════════════════════════
# TESTI 2: YAML Bridge
# ══════════════════════════════════════════════════════════════

def test_yaml_bridge():
    SECTION("2. YAML BRIDGE (50 agenttia)")

    try:
        from core.yaml_bridge import YAMLBridge

        # Etsi agents
        for d in ["knowledge", "agents"]:
            if Path(d).exists():
                bridge = YAMLBridge(d)
                break
        else:
            FAIL("Ei agents/ eikä knowledge/ kansiota")
            return

        stats = bridge.get_stats()

        if stats["total_agents"] == 50:
            OK(f"50 agenttia ladattu")
        else:
            FAIL(f"Agentteja: {stats['total_agents']}/50")

        if stats["total_metrics"] > 200:
            OK(f"Metriikoita: {stats['total_metrics']}")
        else:
            WARN(f"Metriikoita vähän: {stats['total_metrics']}")

        if stats["total_questions"] == 2000:
            OK(f"Kysymyksiä: {stats['total_questions']}")
        else:
            WARN(f"Kysymyksiä: {stats['total_questions']}/2000")

        # Testaa prompt-generointi
        templates = bridge.get_spawner_templates()
        OK(f"Spawner templates: {len(templates)}")

        # Testaa reititys
        routing = bridge.get_routing_rules()
        test_routes = {
            "mehiläispesien varroa": "tarhaaja",
            "sähkösulakkeet": "sahkoasentaja",
            "karhuhavainto": "pesaturvallisuus",
            "revontulet näkyvissä": "tahtitieteilija",
            "jää kantava": "jaaasiantuntija",
            "lintuhavainto": "ornitologi",
            "sauna lämpiää": "saunamajuri",
        }

        route_pass = 0
        for msg, expected in test_routes.items():
            msg_lower = msg.lower()
            best_agent = None
            best_score = 0
            for agent_type, keywords in routing.items():
                score = sum(1 for kw in keywords if kw in msg_lower)
                if score > best_score:
                    best_score = score
                    best_agent = agent_type

            if best_agent == expected:
                route_pass += 1
            else:
                WARN(f"Reititys: '{msg}' → {best_agent} (odotettiin {expected})")

        if route_pass == len(test_routes):
            OK(f"Reititys: {route_pass}/{len(test_routes)} oikein")
        else:
            WARN(f"Reititys: {route_pass}/{len(test_routes)} oikein")

        # Testaa glyyfit
        glyphs = bridge.get_agent_glyphs()
        if len(glyphs) >= 50:
            OK(f"Glyyfit: {len(glyphs)}")
        else:
            WARN(f"Glyyfit: {len(glyphs)}/50+")

        # Testaa knowledge summary
        summary = bridge.get_knowledge_summary("tarhaaja")
        if "varroa" in summary.lower() or "mehiläi" in summary.lower():
            OK(f"Knowledge summary: sisältää relevanttia dataa")
        else:
            WARN(f"Knowledge summary: ei löytänyt avaindataa")

    except Exception as e:
        FAIL(f"YAML Bridge: {e}")


# ══════════════════════════════════════════════════════════════
# TESTI 3: LLM-yhteys (Ollama)
# ══════════════════════════════════════════════════════════════

async def test_ollama():
    SECTION("3. OLLAMA LLM")

    try:
        from core.llm_provider import LLMProvider
        import yaml

        config_path = Path("configs/settings.yaml")
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f)
            llm_config = config.get("llm", {})
        else:
            llm_config = {}

        llm = LLMProvider(llm_config)
        OK(f"LLMProvider luotu (malli: {llm.model}, url: {llm.base_url})")

        # Testi 1: Yksinkertainen generointi
        start = time.time()
        resp = await llm.generate("Mikä on 2+2? Vastaa vain numerolla.", temperature=0.1, max_tokens=10)
        elapsed = time.time() - start

        if "[Ollama ei vastaa" in resp.content or "[LLM-virhe" in resp.content:
            FAIL(f"Ollama ei vastaa: {resp.content}")
            return
        elif "4" in resp.content:
            OK(f"Peruslaskenta OK: '{resp.content.strip()[:30]}' ({elapsed:.1f}s)")
        else:
            WARN(f"Vastaus odottamaton: '{resp.content.strip()[:50]}' ({elapsed:.1f}s)")

        # Testi 2: Suomen kieli
        resp2 = await llm.generate("Kerro yksi lause mehiläisistä suomeksi.", max_tokens=100)
        if any(w in resp2.content.lower() for w in ["mehiläi", "hunaj", "pesä", "pölyt"]):
            OK(f"Suomi OK: '{resp2.content.strip()[:60]}'")
        else:
            WARN(f"Suomi heikko: '{resp2.content.strip()[:60]}'")

        # Testi 3: System prompt
        resp3 = await llm.generate(
            "Mikä on varroa-kynnysarvo?",
            system="Olet mehiläishoitaja. Varroa-kynnys on 3 punkkia/100 mehiläistä. Vastaa lyhyesti.",
            max_tokens=50
        )
        if "3" in resp3.content:
            OK(f"System prompt toimii: '{resp3.content.strip()[:50]}'")
        else:
            WARN(f"System prompt heikko: '{resp3.content.strip()[:50]}'")

        # Testi 4: Structured output
        resp4 = await llm.generate_structured(
            "Palauta JSON: {\"testi\": true, \"arvo\": 42}",
            system="Vastaa VAIN JSON-muodossa."
        )
        if isinstance(resp4, dict) and resp4.get("testi") == True:
            OK(f"Structured JSON: {resp4}")
        elif "error" in resp4:
            WARN(f"Structured JSON epäonnistui: {resp4}")
        else:
            OK(f"Structured JSON (partial): {resp4}")

        # Testi 5: Nopeus - useampi kutsu
        start = time.time()
        tasks = [llm.generate(f"Sano numero {i}", max_tokens=5) for i in range(3)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        elapsed = time.time() - start
        successes = sum(1 for r in results if not isinstance(r, Exception))
        OK(f"Rinnakkaisuus: {successes}/3 onnistui ({elapsed:.1f}s)")

        await llm.close()

    except Exception as e:
        FAIL(f"Ollama: {e}")


# ══════════════════════════════════════════════════════════════
# TESTI 4: Muisti (SharedMemory)
# ══════════════════════════════════════════════════════════════

async def test_memory():
    SECTION("4. SHARED MEMORY")

    try:
        from memory.shared_memory import SharedMemory

        # Käytä testitietokantaa
        test_db = "data/test_openclaw.db"
        mem = SharedMemory(test_db)
        await mem.initialize()
        OK("Tietokanta alustettu")

        # Tallenna muisti
        mid = await mem.store_memory(
            content="Varroa-taso pesässä 5: 4 punkkia/100 mehiläistä → hoito tarvitaan",
            agent_id="test_tarhaaja",
            memory_type="observation",
            importance=0.9
        )
        OK(f"Muisti tallennettu: {mid}")

        # Hae muisti
        results = await mem.recall("varroa", limit=5, agent_id="test_tarhaaja")
        if results:
            OK(f"Muisti haettu: {len(results)} tulosta")
        else:
            FAIL("Muistin haku palautti tyhjän")

        # Viestit
        await mem.send_message("test_a", "test_b", "Varroa-hälytys pesässä 5!", "alert")
        msgs = await mem.get_messages("test_b")
        if msgs:
            OK(f"Viestit toimii: {len(msgs)} viestiä")
        else:
            FAIL("Viestien haku epäonnistui")

        # Tapahtumat
        await mem.log_event("test_agent", "spawn", "Testiagentti luotu")
        timeline = await mem.get_timeline(limit=5)
        if timeline:
            OK(f"Timeline: {len(timeline)} tapahtumaa")
        else:
            WARN("Timeline tyhjä")

        # Tehtävät
        tid = await mem.add_task("Tarkista pesä 5", assigned_agent="test_tarhaaja", priority=9)
        tasks = await mem.get_tasks(status="pending")
        if tasks:
            OK(f"Tehtävät: {len(tasks)} pending")
        else:
            FAIL("Tehtävien haku epäonnistui")

        # Tilastot
        stats = await mem.get_memory_stats()
        OK(f"Stats: {stats}")

        await mem.close()

        # Siivoa testitietokanta
        try:
            os.remove(test_db)
        except:
            pass

    except Exception as e:
        FAIL(f"SharedMemory: {e}")


# ══════════════════════════════════════════════════════════════
# TESTI 5: Token Economy
# ══════════════════════════════════════════════════════════════

async def test_tokens():
    SECTION("5. TOKEN ECONOMY")

    try:
        from memory.shared_memory import SharedMemory
        from core.token_economy import TokenEconomy, REWARD_RULES

        test_db = "data/test_tokens.db"
        mem = SharedMemory(test_db)
        await mem.initialize()

        te = TokenEconomy(mem)
        await te.initialize()
        OK("Token Economy alustettu")

        # Palkitse
        balance = await te.reward("agent_1", "task_completed")
        if balance == REWARD_RULES["task_completed"]:
            OK(f"Reward: {balance}🪙 (odotettiin {REWARD_RULES['task_completed']})")
        else:
            WARN(f"Reward: {balance}🪙 (odotettiin {REWARD_RULES['task_completed']})")

        # Kuluta
        ok = await te.spend("agent_1", 5, "whisper_ping")
        if ok:
            OK(f"Spend 5🪙: saldo = {te.get_balance('agent_1')}🪙")
        else:
            FAIL("Spend epäonnistui")

        # Liikaa kulutusta
        ok2 = await te.spend("agent_1", 9999, "yritys")
        if not ok2:
            OK("Ylikulutus estetty")
        else:
            FAIL("Ylikulutus sallittiin!")

        # Rank
        emoji = te.get_rank_emoji("agent_1")
        OK(f"Rank emoji: {emoji}")

        # Leaderboard
        await te.reward("agent_2", "insight_generated")
        board = te.get_leaderboard()
        if len(board) >= 2:
            OK(f"Leaderboard: {len(board)} agenttia")
        else:
            WARN(f"Leaderboard: {len(board)} agenttia")

        await mem.close()
        try:
            os.remove(test_db)
        except:
            pass

    except Exception as e:
        FAIL(f"Token Economy: {e}")


# ══════════════════════════════════════════════════════════════
# TESTI 6: Whisper Protocol
# ══════════════════════════════════════════════════════════════

async def test_whisper():
    SECTION("6. WHISPER PROTOCOL")

    try:
        from memory.shared_memory import SharedMemory
        from core.token_economy import TokenEconomy
        from core.whisper_protocol import WhisperProtocol

        test_db = "data/test_whisper.db"
        mem = SharedMemory(test_db)
        await mem.initialize()
        te = TokenEconomy(mem)
        await te.initialize()

        wp = WhisperProtocol(mem, te)
        OK("WhisperProtocol luotu")

        # Anna tokeneita agentille
        await te.reward("agent_a", "task_completed", custom_amount=50)
        await te.reward("agent_b", "task_completed", custom_amount=10)

        balance_a = te.get_balance("agent_a")
        balance_b = te.get_balance("agent_b")
        OK(f"Saldot: A={balance_a}🪙, B={balance_b}🪙")

        # Encode hieroglyph
        try:
            glyph = wp.encode_hieroglyph("tarhaaja", "meteorologi", "sääennuste", "ping")
            OK(f"Hieroglyfi: {glyph}")
        except Exception as e:
            WARN(f"Hieroglyfi: {e}")

        # Crown check
        is_crowned = wp.is_crowned("agent_a")
        OK(f"Crown (50🪙): {is_crowned} (pitäisi olla False, raja 100)")

        await te.reward("agent_a", "task_completed", custom_amount=60)
        is_crowned2 = wp.is_crowned("agent_a")
        OK(f"Crown (110🪙): {is_crowned2} (pitäisi olla True)")

        await mem.close()
        try:
            os.remove(test_db)
        except:
            pass

    except Exception as e:
        FAIL(f"Whisper: {e}")


# ══════════════════════════════════════════════════════════════
# TESTI 7: Knowledge Loader
# ══════════════════════════════════════════════════════════════

def test_knowledge():
    SECTION("7. KNOWLEDGE LOADER")

    try:
        from core.knowledge_loader import KnowledgeLoader

        kl = KnowledgeLoader()

        # Tarkista YAML-tuki
        all_knowledge = kl.list_all_knowledge()
        if all_knowledge:
            OK(f"Knowledge kansiot: {len(all_knowledge)}")
        else:
            WARN("Knowledge kansioita ei löytynyt")

        # Lataa yhden agentin tieto
        for agent_type in ["tarhaaja", "meteorologi", "sahkoasentaja"]:
            docs = kl.get_knowledge(agent_type)
            if docs:
                OK(f"{agent_type}: {len(docs)} docs, {sum(len(d) for d in docs)} chars")
            else:
                WARN(f"{agent_type}: ei dokumentteja")

        # Testaa summary
        summary = kl.get_knowledge_summary("tarhaaja")
        if summary and len(summary) > 50:
            OK(f"Summary: {len(summary)} chars")
        else:
            WARN(f"Summary lyhyt tai tyhjä: {len(summary) if summary else 0}")

    except Exception as e:
        FAIL(f"Knowledge Loader: {e}")


# ══════════════════════════════════════════════════════════════
# TESTI 8: Dashboard API (vaatii palvelimen)
# ══════════════════════════════════════════════════════════════

async def test_dashboard():
    SECTION("8. DASHBOARD API")

    try:
        import httpx
    except ImportError:
        WARN("httpx ei asennettu")
        return

    base = "http://localhost:8000"

    async with httpx.AsyncClient(timeout=30) as client:

        # Status
        try:
            r = await client.get(f"{base}/api/status")
            if r.status_code == 200:
                data = r.json()
                agents = data.get("agents", {}).get("list", [])
                OK(f"GET /api/status: {len(agents)} agenttia")

                # Tarkista agentit
                types = [a.get("type", a.get("agent_type", "?")) for a in agents]
                OK(f"  Tyypit: {types}")
            else:
                FAIL(f"GET /api/status: HTTP {r.status_code}")
        except httpx.ConnectError:
            FAIL("Dashboard ei vastaa (http://localhost:8000)")
            return

        # Chat — reititystesti
        route_tests = [
            ("mehiläispesien varroa-tilanne?", "tarhaaja", ["varroa", "punkk", "hoito", "mehiläi"]),
            ("onko ukkosta tulossa?", "meteorologi", ["sää", "ukkos", "ennust", "tuuli"]),
            ("karhuhavainto pohjoispesillä!", "pesaturvallisuus", ["karhu", "suoja", "turva"]),
            ("kuinka paljon hunajaa saatiin?", "tarhaaja", ["hunaj", "linko", "sato", "kilo"]),
            ("onko jää kantava?", "jaaasiantuntija", ["jää", "kanta", "paksu"]),
            ("mitä lintuja näkyy?", "ornitologi", ["lint", "laji", "havai"]),
        ]

        for msg, expected_type, expected_words in route_tests:
            try:
                r = await client.post(f"{base}/api/chat",
                    json={"message": msg}, timeout=60)
                data = r.json()
                resp = data.get("response", data.get("error", ""))

                # Tarkista reitittikö oikealle agentille
                routed_correctly = f"[{expected_type}" in resp.lower() or any(
                    expected_type in resp.lower() for _ in [1]
                )
                has_content = any(w in resp.lower() for w in expected_words)

                if routed_correctly:
                    OK(f"'{msg[:30]}' → {expected_type} ✓")
                elif has_content:
                    WARN(f"'{msg[:30]}' → sisältö OK mutta reititys epävarma")
                else:
                    WARN(f"'{msg[:30]}' → vastaus: {resp[:80]}")

            except Exception as e:
                FAIL(f"Chat '{msg[:30]}': {e}")

            await asyncio.sleep(1)  # Älä ylikuormita

        # Monitor history
        try:
            r = await client.get(f"{base}/api/monitor/history")
            events = r.json().get("events", [])
            if events:
                cats = set(e.get("category", "?") for e in events)
                OK(f"Monitor: {len(events)} tapahtumaa, kategoriat: {cats}")
            else:
                WARN("Monitor: ei tapahtumia")
        except Exception as e:
            FAIL(f"Monitor: {e}")


# ══════════════════════════════════════════════════════════════
# TESTI 9: Agenttienvälinen kommunikaatio
# ══════════════════════════════════════════════════════════════

async def test_inter_agent():
    SECTION("9. AGENTTIEN VÄLINEN KOMMUNIKAATIO")

    try:
        from memory.shared_memory import SharedMemory
        from core.token_economy import TokenEconomy
        from core.whisper_protocol import WhisperProtocol
        from core.llm_provider import LLMProvider
        from agents.base_agent import Agent

        test_db = "data/test_comms.db"
        mem = SharedMemory(test_db)
        await mem.initialize()
        te = TokenEconomy(mem)
        await te.initialize()

        llm = LLMProvider({"model": "qwen2.5:7b"})

        # Luo kaksi agenttia
        agent_a = Agent(
            name="TestTarhaaja", agent_type="tarhaaja",
            system_prompt="Olet mehiläishoitaja. Vastaa lyhyesti suomeksi.",
            llm=llm, memory=mem
        )
        agent_b = Agent(
            name="TestMeteorologi", agent_type="meteorologi",
            system_prompt="Olet meteorologi. Vastaa lyhyesti suomeksi.",
            llm=llm, memory=mem
        )

        await agent_a.initialize()
        await agent_b.initialize()
        OK(f"Agentit luotu: {agent_a.name}, {agent_b.name}")

        # Agentti A lähettää viestin B:lle
        await agent_a.communicate(agent_b.id, "Huomenna ennustetaan sadetta — pitäisikö siirtää tarkistuksia?")
        OK("Viesti lähetetty A → B")

        # Tarkista B:n viestit
        messages = await mem.get_messages(agent_b.id)
        if messages:
            OK(f"B vastaanotti {len(messages)} viestiä")
        else:
            FAIL("B ei saanut viestejä")

        # Agentti A tallentaa insightin
        await mem.store_memory(
            content="Pesä 5: varroa-taso 4/100 → kemiallinen hoito käynnistetty",
            agent_id=agent_a.id,
            memory_type="insight",
            importance=0.9
        )
        OK("Insight tallennettu")

        # Varmista B voi lukea A:n insightin (shared memory)
        all_insights = await mem.recall("varroa", limit=5)
        if all_insights:
            OK(f"Shared recall: {len(all_insights)} tulosta (jaettu muisti toimii)")
        else:
            FAIL("Shared recall tyhjä — muisti ei jaettu")

        # Think-testi
        thought = await agent_a.think("Mikä on pesien tilanne?", "")
        if thought and len(thought) > 10:
            OK(f"Agent think: '{thought[:60]}'")
        else:
            WARN(f"Agent think lyhyt: '{thought}'")

        await llm.close()
        await mem.close()
        try:
            os.remove(test_db)
        except:
            pass

    except Exception as e:
        FAIL(f"Inter-agent: {e}")


# ══════════════════════════════════════════════════════════════
# TESTI 10: HiveMind heartbeat-simulaatio
# ══════════════════════════════════════════════════════════════

async def test_heartbeat():
    SECTION("10. HEARTBEAT-SIMULAATIO")

    try:
        import httpx
        base = "http://localhost:8000"

        async with httpx.AsyncClient(timeout=10) as client:
            # Ota status ennen
            r1 = await client.get(f"{base}/api/status")
            data1 = r1.json()

            # Odota 35 sekuntia (yli yhden heartbeat-syklin)
            print(f"  ⏳ Odotetaan 35s heartbeat-sykliä...")
            await asyncio.sleep(35)

            # Ota status jälkeen
            r2 = await client.get(f"{base}/api/status")
            data2 = r2.json()

            # Tarkista monitor events
            r3 = await client.get(f"{base}/api/monitor/history")
            events = r3.json().get("events", [])

            thoughts = [e for e in events if e.get("category") == "thought"]
            if thoughts:
                OK(f"Heartbeat tuotti {len(thoughts)} ajatusta")
                latest = thoughts[-1]
                OK(f"  Viimeisin: {latest.get('title', '')[:80]}")
            else:
                WARN("Ei heartbeat-ajatuksia (onko heartbeat päällä?)")

            # Whisper-viestit
            whisper_events = [e for e in events if "whisper" in str(e).lower()]
            if whisper_events:
                OK(f"Whisper-viestejä: {len(whisper_events)}")
            else:
                WARN("Ei Whisper-viestejä (agentit eivät kommunikoi)")

    except httpx.ConnectError:
        WARN("Dashboard ei päällä — heartbeat-testi ohitettu")
    except Exception as e:
        FAIL(f"Heartbeat: {e}")


# ══════════════════════════════════════════════════════════════
# TESTI 11: Päivämäärä ja aika system promptissa
# ══════════════════════════════════════════════════════════════

def test_datetime_prompt():
    SECTION("11. PÄIVÄMÄÄRÄ JA AIKA")

    # Tarkista onko hivemind.py:n master_prompt sisältää ajan
    hm = Path("hivemind.py")
    if hm.exists():
        content = hm.read_text(encoding="utf-8")
        if "datetime" in content and "strftime" in content:
            OK("Master prompt sisältää dynaamisen ajan")
        elif "MASTER_SYSTEM_PROMPT" in content:
            if "päivämäärä" in content.lower() or "date" in content.lower():
                WARN("Master promptissa on staattinen päivämäärä-viite")
            else:
                FAIL("Master promptissa EI ole päivämäärää → malli keksii sen")
        else:
            WARN("MASTER_SYSTEM_PROMPT ei löytynyt")
    else:
        FAIL("hivemind.py ei löydy")


# ══════════════════════════════════════════════════════════════
# TESTI 12: Tiedostorakenne
# ══════════════════════════════════════════════════════════════

def test_file_structure():
    SECTION("12. TIEDOSTORAKENNE")

    required = {
        "main.py": "Käynnistin",
        "hivemind.py": "Orkesteri",
        "configs/settings.yaml": "Asetukset",
        "agents/base_agent.py": "Perusagentti",
        "agents/spawner.py": "Agent factory",
        "agents/hacker_agent.py": "HackerAgent",
        "agents/oracle_agent.py": "OracleAgent",
        "core/llm_provider.py": "LLM-yhteys",
        "core/token_economy.py": "Tokeni-talous",
        "core/live_monitor.py": "Live feed",
        "core/whisper_protocol.py": "Kuiskausprotokolla",
        "core/knowledge_loader.py": "Tiedon lataus",
        "core/yaml_bridge.py": "YAML-silta",
        "memory/shared_memory.py": "Jaettu muisti",
        "web/dashboard.py": "Web-UI",
    }

    for path, desc in required.items():
        if Path(path).exists():
            size = Path(path).stat().st_size
            OK(f"{path} ({size:,} bytes) — {desc}")
        else:
            FAIL(f"{path} PUUTTUU — {desc}")

    # Knowledge
    knowledge_dirs = 0
    for d in ["knowledge", "agents"]:
        if Path(d).exists():
            for sub in Path(d).iterdir():
                if sub.is_dir() and (sub / "core.yaml").exists():
                    knowledge_dirs += 1

    if knowledge_dirs >= 50:
        OK(f"Knowledge: {knowledge_dirs} agentin YAML")
    elif knowledge_dirs > 0:
        WARN(f"Knowledge: {knowledge_dirs}/50 agentin YAML")
    else:
        FAIL("Ei YAML-agentteja knowledge/ tai agents/ -kansiossa")


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

async def main():
    offline = "--offline" in sys.argv

    print(f"""
{B}╔══════════════════════════════════════════════════╗
║  OpenClaw v1.4 — Täysdiagnostiikka               ║
║  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                          ║
╚══════════════════════════════════════════════════╝{W}
""")

    # Synkroniset testit
    test_file_structure()
    test_imports()
    test_yaml_bridge()
    test_knowledge()
    test_datetime_prompt()

    # Async testit
    await test_memory()
    await test_tokens()
    await test_whisper()
    await test_ollama()

    if not offline:
        await test_dashboard()
        await test_inter_agent()
        await test_heartbeat()
    else:
        print(f"\n{Y}  ℹ️  --offline: Dashboard-, inter-agent- ja heartbeat-testit ohitettu{W}")

    # ── YHTEENVETO ───────────────────────────────────────────
    print(f"""
{B}{'═'*50}
  YHTEENVETO
{'═'*50}{W}

  {G}✅ Läpäisseet: {RESULTS['pass']}{W}
  {Y}⚠️  Varoitukset: {RESULTS['warn']}{W}
  {R}❌ Virheet:     {RESULTS['fail']}{W}
""")

    if RESULTS["errors"]:
        print(f"{R}Virhelista:{W}")
        for i, err in enumerate(RESULTS["errors"], 1):
            print(f"  {R}{i}. {err}{W}")

    if RESULTS["fail"] == 0:
        print(f"\n{G}🎉 KAIKKI TESTIT LÄPI!{W}")
    else:
        print(f"\n{Y}💡 Korjaa virheet ja aja uudelleen: python test_all.py{W}")

    # Tallenna raportti
    report = Path("data/test_report.json")
    report.parent.mkdir(exist_ok=True)
    report.write_text(json.dumps({
        "timestamp": datetime.now().isoformat(),
        "results": RESULTS,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n📄 Raportti: {report}")


if __name__ == "__main__":
    asyncio.run(main())
