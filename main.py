#!/usr/bin/env python3
"""
WaggleDance Swarm AI — Local HiveNode Runtime (On-Prem)
========================================================
Jani Korpi (Ahkerat Mehiläiset)
Claude 4.6 • v0.0.3 • Built: 2026-02-22

v0.0.3: OpsAgent + UTF-8 fix + audit fixes.
"""
import asyncio
import sys
import os
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# KRIITTINEN: Windows UTF-8 -korjaus
# Ilman tätä: Päämehiläishoitaja → PÃ¤Ã¤mehilÃ¤ishoitaja
# Kolme kerrosta:
#   1. chcp 65001 → Windows-konsolin koodisivu UTF-8:ksi
#   2. PYTHONUTF8=1 → Python käyttää UTF-8:aa kaikkialla
#   3. reconfigure → stdout/stderr UTF-8 (varmuudeksi)
# ═══════════════════════════════════════════════════════════════
if sys.platform == "win32":
    # Kerros 1: Windows-konsolin koodisivu
    os.system("chcp 65001 > nul 2>&1")

    # Kerros 2: Python UTF-8 Mode (PEP 540, Python 3.7+)
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    # Kerros 2b: Unbuffered stdout — prints näkyvät heti Windowsissa
    os.environ["PYTHONUNBUFFERED"] = "1"

    # Kerros 3: Rekonfiguroi stdout/stderr
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# PHASE1 TASK7: Ollama model keep-alive and max loaded models
os.environ.setdefault("OLLAMA_KEEP_ALIVE", "24h")
os.environ.setdefault("OLLAMA_MAX_LOADED_MODELS", "4")

sys.path.insert(0, str(Path(__file__).parent))
from hivemind import HiveMind

AUTO_SPAWN = [
    # Ydinbisnes
    "tarhaaja", "core_dispatcher", "lentosaa",
    # Ympäristö
    "meteorologi", "hortonomi", "ornitologi",
    # Turvallisuus
    "pesaturvallisuus", "kybervahti", "pihavahti",
    # Kiinteistö
    "sahkoasentaja", "nuohooja", "luontokuvaaja",
    # Metsä & riista
    "metsanhoitaja", "riistanvartija",
    # Arki
    "erakokki", "saunamajuri",
    # Erikois
    "oracle", "hacker",
]


async def main():
    hive = HiveMind("configs/settings.yaml")
    await hive.start()

    spawned = 0
    for agent_type in AUTO_SPAWN:
        try:
            existing = hive.spawner.get_agents_by_type(agent_type)
            if not existing:
                agent = await hive.spawner.spawn(agent_type)
                spawned += 1
                # FIX-4: Auto-register scheduleriin hook-metodilla
                if agent:
                    hive.register_agent_to_scheduler(agent)
        except Exception as e:
            print(f"  ⚠️  {agent_type}: {e}")

    # YAML auto_spawn agentit
    for t, tmpl in hive.spawner.agent_templates.items():
        if tmpl.get("auto_spawn") and t not in AUTO_SPAWN:
            try:
                existing = hive.spawner.get_agents_by_type(t)
                if not existing:
                    agent = await hive.spawner.spawn(t)
                    spawned += 1
                    if agent:
                        hive.register_agent_to_scheduler(agent)
            except Exception:
                pass
            if spawned >= 25:
                break

    # Bulk-register: kaikki agentit YAMLBridgestä scheduleriin
    # Tämä rekisteröi myös ne agentit joita ei ole vielä spawnattu
    # mutta jotka löytyvät YAML-tiedostoista (50 agenttia).
    # Scheduler oppii niiden tags/skills valmiiksi.
    if hive._swarm_enabled and hive.scheduler and hasattr(hive.spawner, 'yaml_bridge'):
        bulk_count = hive.scheduler.register_from_yaml_bridge(
            hive.spawner.yaml_bridge,
            hive.spawner
        )
        print(f"  ✅ Swarm Scheduler: {bulk_count} agenttia rekisteröity YAMLBridgestä", flush=True)

    total = len(hive.spawner.active_agents)
    hb_model = (hive.llm_heartbeat.model
                if hasattr(hive, "llm_heartbeat") else hive.llm.model)
    hb_device = "CPU" if (hasattr(hive, "llm_heartbeat")
                          and hive.llm_heartbeat.num_gpu == 0) else "GPU"

    print(f"\n🐝 WaggleDance Swarm AI käynnissä! {total} agenttia.")
    print(f"   Dashboard: http://localhost:8000")
    print(f"   Chat:      {hive.llm.model} (GPU)")
    print(f"   Heartbeat: {hb_model} ({hb_device})")
    if hive.scheduler:
        stats = hive.scheduler.get_stats()
        print(f"   Swarm:     {stats['total_agents']} agenttia schedulerissa "
              f"({'ENABLED' if hive._swarm_enabled else 'DISABLED'})")
        print(f"              Roolit: {stats['roles']}")
    print(f"   Pysäytä:   Ctrl+C\n")

    try:
        from web.dashboard import create_app
        app = create_app(hive)
        import uvicorn
        config = uvicorn.Config(
            app, host="0.0.0.0", port=8000, log_level="warning"
        )
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
        print("\n👋 WaggleDance sammutettu.")
