#!/usr/bin/env python3
"""
OpenClaw v1.4 — YAML Integration (Windows-yhteensopiva)
=========================================================
Yhdistää 50 agentin YAML-tietopohjan olemassa olevaan
OpenClaw runtime-projektiin.

Käyttö:
  cd S:\\Python\\openclaw
  python integrate_yaml_agents.py

  TAI:
  python integrate_yaml_agents.py --runtime S:\\Python\\openclaw
"""

import re, shutil, sys, os
from pathlib import Path
from datetime import datetime


def find_runtime_root(hint=None):
    candidates = []
    if hint: candidates.append(Path(hint))
    candidates.append(Path.cwd())
    candidates.append(Path.home() / "openclaw")
    for drive in ["S:", "C:", "D:"]:
        candidates.append(Path(f"{drive}/Python/openclaw"))
    for p in candidates:
        if not p.exists(): continue
        has_runtime = ((p / "main.py").exists() or (p / "hivemind.py").exists())
        has_core = (p / "memory").exists() or (p / "core").exists()
        if has_runtime and has_core:
            return p
    return None


def backup(filepath, backup_dir):
    if filepath.exists():
        dest = backup_dir / filepath.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(filepath, dest)


def patch_file(filepath, search, replacement, name, backup_dir):
    if not filepath.exists():
        print(f"    ⚠️  {filepath.name} ei löydy — ohitetaan ({name})")
        return False
    backup(filepath, backup_dir)
    content = filepath.read_text(encoding="utf-8")
    if search in content:
        content = content.replace(search, replacement, 1)
        filepath.write_text(content, encoding="utf-8")
        print(f"    ✅ {name}")
        return True
    elif replacement[:40] in content:
        print(f"    ℹ️  {name}: jo patchattu")
        return False
    else:
        print(f"    ⚠️  {name}: hakutekstiä ei löytynyt")
        return False


def main():
    print("\n═══ OpenClaw v1.4 YAML Integration ═══\n")

    runtime_hint = None
    for i, arg in enumerate(sys.argv):
        if arg == "--runtime" and i + 1 < len(sys.argv):
            runtime_hint = sys.argv[i + 1]

    kb_root = Path.cwd()
    rt_root = find_runtime_root(runtime_hint)

    print(f"📁 Tietopohja: {kb_root}")
    print(f"📁 Runtime:    {rt_root or 'EI LÖYTYNYT'}")

    if not rt_root:
        print("\n⚠️  Runtime-projektia ei löytynyt!")
        print("    Aja tämä OpenClaw-projektin juuresta:")
        print("    cd S:\\Python\\openclaw")
        print("    python <polku>\\integrate_yaml_agents.py")
        rt_root = kb_root

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    bak = kb_root / "backups" / f"pre_yaml_{ts}"
    bak.mkdir(parents=True, exist_ok=True)
    print(f"📦 Backup: {bak}\n")

    # 1. yaml_bridge.py
    print("1️⃣  yaml_bridge.py → core/")
    src = kb_root / "core" / "yaml_bridge.py"
    if not src.exists(): src = kb_root / "yaml_bridge.py"
    dst = rt_root / "core" / "yaml_bridge.py"
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"    ✅ {dst}")
    else:
        print(f"    ❌ yaml_bridge.py ei löydy!"); return

    # 2. YAML-agentit → knowledge/
    print("\n2️⃣  YAML-agentit → knowledge/")
    kb_agents = kb_root / "agents"
    copied = 0
    if kb_agents.exists():
        for d in sorted(os.listdir(str(kb_agents))):
            cy = kb_agents / d / "core.yaml"
            if not cy.exists(): continue
            kd = rt_root / "knowledge" / d
            kd.mkdir(parents=True, exist_ok=True)
            for fn in ["core.yaml", "sources.yaml"]:
                sf = kb_agents / d / fn
                if sf.exists(): shutil.copy2(sf, kd / fn)
            copied += 1
    print(f"    ✅ {copied} agentin YAML kopioitu")

    # 3. spawner.py
    print("\n3️⃣  spawner.py")
    spawner = rt_root / "agents" / "spawner.py"
    if not spawner.exists(): spawner = rt_root / "spawner.py"
    patch_file(spawner, "from agents.base_agent import Agent",
        "from agents.base_agent import Agent\nfrom core.yaml_bridge import YAMLBridge",
        "Import YAMLBridge", bak)
    patch_file(spawner,
        'self.agent_templates = {**DEFAULT_TEMPLATES, **config.get("agent_templates", {})}',
        '''# ═══ YAML Bridge ═══
        self.yaml_bridge = YAMLBridge(
            config.get("yaml_bridge", {}).get("agents_dir", "knowledge")
        )
        yaml_templates = self.yaml_bridge.get_spawner_templates()
        self.agent_templates = {**yaml_templates, **DEFAULT_TEMPLATES, **config.get("agent_templates", {})}
        print(f"  📚 Spawner: {len(self.agent_templates)} templateia")''',
        "YAML Bridge templateit", bak)

    # 4. hivemind.py
    print("\n4️⃣  hivemind.py")
    hivemind = rt_root / "hivemind.py"
    patch_file(hivemind, '        routing_rules = {',
        '''        # ═══ DYNAMIC ROUTING ═══
        if self.spawner and hasattr(self.spawner, 'yaml_bridge'):
            routing_rules = self.spawner.yaml_bridge.get_routing_rules()
        else:
            routing_rules = {''',
        "Dynamic routing", bak)

    # 5. whisper_protocol.py
    print("\n5️⃣  whisper_protocol.py")
    whisper = rt_root / "core" / "whisper_protocol.py"
    patch_file(whisper, 'AGENT_GLYPHS = {',
        '''AGENT_GLYPHS = {
    "hivemind": "🧠", "hacker": "⚙️", "oracle": "🔮",
    "beekeeper": "🐝", "video_producer": "🎬", "business": "💰", "tech": "🔧", "property": "🏡",
    "core_dispatcher": "🧠", "ornithologist": "🦅", "entomologist": "🪲", "phenologist": "🌸",
    "horticulturist": "🌿", "forester": "🌲", "wildlife_ranger": "🦌",
    "nature_photographer": "📸", "pest_control": "🐭",
    "beekeeper": "🐝", "flight_weather": "🌤️", "swarm_watcher": "🔔",
    "hive_temperature": "🌡️", "nectar_scout": "🍯",
    "disease_monitor": "🦠", "hive_security": "🐻",
    "limnologist": "🏊", "fishing_guide": "🎣", "fish_identifier": "🐟",
    "shore_guard": "🏖️", "ice_specialist": "🧊",
    "meteorologist": "⛅", "storm_alert": "⛈️",
    "microclimate": "🌡️", "air_quality": "💨", "frost_soil": "🪨",
    "electrician": "⚡", "hvac_specialist": "🔧",
    "carpenter": "🪵", "chimney_sweep": "🔥", "lighting_master": "💡",
    "fire_officer": "🚒", "equipment_tech": "🔩",
    "cyber_guard": "🛡️", "locksmith": "🔐", "yard_guard": "👁️", "privacy_guard": "🕶️",
    "wilderness_chef": "🍳", "baker": "🍞", "nutritionist": "🥗",
    "sauna_master": "♨️", "entertainment_chief": "🎮", "movie_expert": "🎬",
    "inventory_chief": "📦", "recycling": "♻️",
    "cleaning_manager": "🧹", "logistics": "🚛",
    "astronomer": "🔭", "light_shadow": "☀️", "math_physicist": "📐",
}
_OLD_AGENT_GLYPHS = {''', "50 Agent Glyphs", bak)

    # 6. knowledge_loader.py
    print("\n6️⃣  knowledge_loader.py")
    kl = rt_root / "core" / "knowledge_loader.py"
    patch_file(kl, '".pdf", ".txt", ".md", ".csv", ".json"',
        '".pdf", ".txt", ".md", ".csv", ".json", ".yaml", ".yml"',
        "YAML tiedostolistaan", bak)
    patch_file(kl, '    def _file_hash(self',
        '''
    def _read_yaml(self, file_path):
        try:
            import yaml
            with open(file_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data: return None
            parts = []
            h = data.get("header", {})
            if h: parts.append(f"# {h.get('agent_name', '')}\\nRooli: {h.get('role', '')}")
            for k, v in data.get("DECISION_METRICS_AND_THRESHOLDS", {}).items():
                if isinstance(v, dict): parts.append(f"- {k}: {v.get('value','')} → {v.get('action','')}")
            for s in data.get("SEASONAL_RULES", []):
                parts.append(f"- {s.get('season','')}: {s.get('action','')}")
            return "\\n".join(parts)
        except: return None

    def _file_hash(self''', "YAML reader", bak)
    patch_file(kl, '            elif suffix == ".json":',
        '            elif suffix in (".yaml", ".yml"):\n                return self._read_yaml(file_path)\n            elif suffix == ".json":',
        "YAML-haara", bak)

    # 7. Validoi
    print("\n7️⃣  Validointi")
    try:
        sys.path.insert(0, str(rt_root))
        from core.yaml_bridge import YAMLBridge
        agents_dir = rt_root / "knowledge"
        if not agents_dir.exists(): agents_dir = kb_root / "agents"
        bridge = YAMLBridge(str(agents_dir))
        stats = bridge.get_stats()
        print(f"    ✅ {stats['total_agents']} agenttia, {stats['total_metrics']} metriiikkaa, {stats['total_questions']} kysymystä")
    except Exception as e:
        print(f"    ⚠️  {e}")

    print(f"\n{'═'*50}\n✅ INTEGRAATIO VALMIS\n{'═'*50}\n")
    print(f"Backup: {bak}")
    print(f"Seuraavaksi: cd {rt_root} && python main.py")


if __name__ == "__main__":
    main()
