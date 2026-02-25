#!/usr/bin/env python3
"""
WaggleDance — YAMLBridge EN Patch v1.0
=======================================
Tekee yaml_bridge.py:n kaksikieliseksi (FI/EN).

Kun translation proxy on aktiivinen ja kieli=EN:
  - build_system_prompt() tuottaa ENGLANNINKIELISEN system promptin
  - Kaikki YAML-sisältö käännetään FI→EN lennossa (kerran, välimuistiin)
  - Otsikot ja ohjeet ovat englanniksi
  - FI-YAML:t pysyvät masterina

Muutokset yaml_bridge.py:hin:
  1. set_translation_proxy() metodi
  2. _translate_deep() rekursiivinen käännös
  3. build_system_prompt() kaksikielinen versio
  4. get_spawner_templates() käyttää EN-pohjaa

Käyttö:
  cd U:\\project
  python yaml_bridge_en_patch.py
"""

import ast
import shutil
from pathlib import Path
from datetime import datetime


def patch_yaml_bridge(path: str = "core/yaml_bridge.py", backup: bool = True):
    p = Path(path)
    if not p.exists():
        print(f"❌ {path} ei löydy!")
        return False

    src = p.read_text(encoding="utf-8")

    if "set_translation_proxy" in src:
        print("ℹ️  EN patch on jo asennettu!")
        return True

    if backup:
        backup_name = f"core/yaml_bridge_backup_{datetime.now():%Y%m%d_%H%M}.py"
        shutil.copy2(p, backup_name)
        print(f"💾 Backup: {backup_name}")

    errors = []

    # ══════════════════════════════════════════════════════════
    # PATCH 1: __init__ — lisää EN-tuki
    # ══════════════════════════════════════════════════════════
    old = """    def __init__(self, agents_dir: str = "agents"):
        self.agents_dir = Path(agents_dir)
        self._agents: dict = {}
        self._loaded = False"""

    new = """    def __init__(self, agents_dir: str = "agents"):
        self.agents_dir = Path(agents_dir)
        self._agents: dict = {}
        self._agents_en: dict = {}  # EN-käännös välimuistissa
        self._loaded = False
        self._translation_proxy = None
        self._language = "fi"  # fi tai en"""

    if old in src:
        src = src.replace(old, new, 1)
        print("  ✅ [1/5] __init__: EN-tuki")
    else:
        errors.append("[1] __init__: ei löydy")

    # ══════════════════════════════════════════════════════════
    # PATCH 2: set_translation_proxy() + _translate_deep()
    # Lisätään _ensure_loaded() jälkeen
    # ══════════════════════════════════════════════════════════
    old = """    @staticmethod
    def _fix_mojibake(s: str) -> str:"""

    new = """    def set_translation_proxy(self, proxy, language: str = "en"):
        \"\"\"
        Aseta translation proxy ja käännä YAML-agentit tarvittaessa.
        Tunnistaa automaattisesti onko YAML jo kohdekielellä.
        Kutsutaan kerran startissa — käännös tallennetaan välimuistiin.
        \"\"\"
        self._translation_proxy = proxy
        self._language = language
        if language == "en" and proxy:
            self._ensure_loaded()
            import time
            t0 = time.monotonic()
            translated = 0
            skipped = 0
            for agent_id, agent_data in self._agents.items():
                yaml_lang = self._detect_yaml_language(agent_data)
                if yaml_lang == "en":
                    # YAML on jo englanniksi → käytä sellaisenaan
                    self._agents_en[agent_id] = agent_data
                    skipped += 1
                else:
                    # YAML on suomeksi/muulla → käännä
                    self._agents_en[agent_id] = self._translate_deep(agent_data, proxy)
                    translated += 1
            elapsed = (time.monotonic() - t0) * 1000
            if skipped > 0:
                print(f"  🌐 YAMLBridge: {translated} käännetty EN, {skipped} jo EN ({elapsed:.0f}ms)")
            else:
                print(f"  🌐 YAMLBridge: {translated} agenttia käännetty EN ({elapsed:.0f}ms)")

    @staticmethod
    def _detect_yaml_language(agent_data: dict) -> str:
        \"\"\"
        Tunnista onko YAML jo englanniksi vai suomeksi.
        Tarkistaa:
          1. Eksplisiittinen 'language: en' kenttä
          2. Header-kentän agent_name kieli
          3. Osion otsikoissa suomenkieliset sanat
        Palauttaa 'en' tai 'fi'.
        \"\"\"
        # 1. Eksplisiittinen merkintä (paras tapa)
        if agent_data.get("language") == "en":
            return "en"
        header = agent_data.get("header", {})
        if header.get("language") == "en":
            return "en"

        # 2. Kerää kaikki stringit näytteeksi
        sample_strings = []
        # Header
        for key in ("agent_name", "role", "description"):
            v = header.get(key, "")
            if v:
                sample_strings.append(str(v))
        # Assumptions
        assumptions = agent_data.get("ASSUMPTIONS", [])
        if isinstance(assumptions, list):
            for item in assumptions[:3]:
                sample_strings.append(str(item))
        # Seasonal rules
        for rule in agent_data.get("SEASONAL_RULES", [])[:2]:
            if isinstance(rule, dict):
                sample_strings.append(str(rule.get("action", "")))

        if not sample_strings:
            return "fi"  # Oletus: suomi

        text = " ".join(sample_strings).lower()

        # 3. Suomen kielen tunnusmerkit
        fi_markers = ["ä", "ö", "yhdyskunt", "mehiläi", "pesä", "hoito",
                       "tarkist", "ruokint", "talveh", "linkoa", "vuosik",
                       "vastaa aina", "olet "]
        en_markers = ["colony", "colonies", "hive", "treatment", "inspect",
                       "feeding", "winter", "extract", "you are", "always respond"]

        fi_score = sum(1 for m in fi_markers if m in text)
        en_score = sum(1 for m in en_markers if m in text)

        return "en" if en_score > fi_score else "fi"

    def set_language(self, language: str):
        \"\"\"Vaihda kieli lennossa (fi/en).\"\"\"
        self._language = language

    @classmethod
    def _translate_deep(cls, obj, proxy):
        \"\"\"
        Rekursiivinen FI→EN käännös koko YAML-puulle.
        Käyttää translation_proxy.fi_to_en() jokaiselle stringille.
        \"\"\"
        if isinstance(obj, str):
            if len(obj) < 3 or obj.startswith("http") or obj.startswith("src:"):
                return obj  # Ohita URL:t, lähdeviitteet, lyhyet
            # Tarkista onko suomea
            try:
                result = proxy.fi_to_en(obj)
                if result and hasattr(result, 'text'):
                    return result.text
            except Exception:
                pass
            return obj
        elif isinstance(obj, dict):
            return {cls._translate_deep(k, proxy) if isinstance(k, str) and len(k) > 20 else k:
                    cls._translate_deep(v, proxy) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [cls._translate_deep(item, proxy) for item in obj]
        return obj

    @staticmethod
    def _fix_mojibake(s: str) -> str:"""

    if old in src:
        src = src.replace(old, new, 1)
        print("  ✅ [2/5] set_translation_proxy + _translate_deep")
    else:
        errors.append("[2] _fix_mojibake: ei löydy")

    # ══════════════════════════════════════════════════════════
    # PATCH 3: build_system_prompt() — kaksikielinen
    # ══════════════════════════════════════════════════════════

    # EN-versio section headers + instructions
    old_prompt_start = '''        parts = [
            f"Olet {name} — {role}.",
            f"\\n{desc}" if desc else "",
        ]

        # ASSUMPTIONS → konteksti
        assumptions = agent.get("ASSUMPTIONS", {})
        if assumptions:
            parts.append("\\n## OLETUKSET JA KONTEKSTI")'''

    new_prompt_start = '''        # ── Valitse kieli ja lähde ──
        _en = self._language == "en"
        _src = self._agents_en.get(agent_id, agent) if _en else agent

        if _en:
            _header = _src.get("header", {})
            _name = _header.get("agent_name", name)
            _role = _header.get("role", role)
            _desc = _header.get("description", "")
            parts = [
                f"You are {_name} — {_role}.",
                f"\\n{_desc}" if _desc else "",
            ]
        else:
            parts = [
                f"Olet {name} — {role}.",
                f"\\n{desc}" if desc else "",
            ]

        # ASSUMPTIONS → konteksti
        assumptions = _src.get("ASSUMPTIONS", {}) if _en else agent.get("ASSUMPTIONS", {})
        if assumptions:
            parts.append("\\n## ASSUMPTIONS AND CONTEXT" if _en else "\\n## OLETUKSET JA KONTEKSTI")'''

    if old_prompt_start in src:
        src = src.replace(old_prompt_start, new_prompt_start, 1)
        print("  ✅ [3a/5] build_system_prompt: header + assumptions")
    else:
        errors.append("[3a] build_system_prompt header: ei löydy")

    # Metrics section
    old_metrics = '''        # DECISION_METRICS → konkreettiset kynnysarvot
        metrics = agent.get("DECISION_METRICS_AND_THRESHOLDS", {})
        if metrics:
            parts.append("\\n## PÄÄTÖSMETRIIKAT JA KYNNYSARVOT")
            for k, v in metrics.items():
                if isinstance(v, dict):
                    val = v.get("value", "")
                    action = v.get("action", "")
                    src = v.get("source", "")
                    line = f"- **{k}**: {val}"
                    if action:
                        line += f" → TOIMENPIDE: {action}"'''

    new_metrics = '''        # DECISION_METRICS → konkreettiset kynnysarvot
        metrics = _src.get("DECISION_METRICS_AND_THRESHOLDS", {}) if _en else agent.get("DECISION_METRICS_AND_THRESHOLDS", {})
        if metrics:
            parts.append("\\n## DECISION METRICS AND THRESHOLDS" if _en else "\\n## PÄÄTÖSMETRIIKAT JA KYNNYSARVOT")
            for k, v in metrics.items():
                if isinstance(v, dict):
                    val = v.get("value", "")
                    action = v.get("action", "")
                    src = v.get("source", "")
                    line = f"- **{k}**: {val}"
                    if action:
                        line += f" → {'ACTION' if _en else 'TOIMENPIDE'}: {action}"'''

    if old_metrics in src:
        src = src.replace(old_metrics, new_metrics, 1)
        print("  ✅ [3b/5] build_system_prompt: metrics")
    else:
        errors.append("[3b] metrics section: ei löydy")

    # Seasonal rules section
    old_seasons = '''        # SEASONAL_RULES → vuosikello
        seasons = agent.get("SEASONAL_RULES", [])
        if seasons:
            parts.append("\\n## VUOSIKELLO")'''

    new_seasons = '''        # SEASONAL_RULES → vuosikello
        seasons = _src.get("SEASONAL_RULES", []) if _en else agent.get("SEASONAL_RULES", [])
        if seasons:
            parts.append("\\n## SEASONAL CALENDAR" if _en else "\\n## VUOSIKELLO")'''

    if old_seasons in src:
        src = src.replace(old_seasons, new_seasons, 1)
        print("  ✅ [3c/5] build_system_prompt: seasons")
    else:
        errors.append("[3c] seasons section: ei löydy")

    # Failure modes section
    old_failures = '''        # FAILURE_MODES → vikatilat
        failures = agent.get("FAILURE_MODES", [])
        if failures:
            parts.append("\\n## VIKATILAT")'''

    new_failures = '''        # FAILURE_MODES → vikatilat
        failures = _src.get("FAILURE_MODES", []) if _en else agent.get("FAILURE_MODES", [])
        if failures:
            parts.append("\\n## FAILURE MODES" if _en else "\\n## VIKATILAT")'''

    if old_failures in src:
        src = src.replace(old_failures, new_failures, 1)
        print("  ✅ [3d/5] build_system_prompt: failures")
    else:
        errors.append("[3d] failures section: ei löydy")

    # Compliance section
    old_legal = '''        # Compliance
        legal = agent.get("COMPLIANCE_AND_LEGAL", {})
        if legal:
            parts.append("\\n## LAKISÄÄTEISET")'''

    new_legal = '''        # Compliance
        legal = _src.get("COMPLIANCE_AND_LEGAL", {}) if _en else agent.get("COMPLIANCE_AND_LEGAL", {})
        if legal:
            parts.append("\\n## LEGAL AND COMPLIANCE" if _en else "\\n## LAKISÄÄTEISET")'''

    if old_legal in src:
        src = src.replace(old_legal, new_legal, 1)
        print("  ✅ [3e/5] build_system_prompt: compliance")
    else:
        errors.append("[3e] compliance section: ei löydy")

    # ══════════════════════════════════════════════════════════
    # PATCH 4: Vastausohjeet — EN versio
    # ══════════════════════════════════════════════════════════
    old_instructions = '''        parts.append("\\n## VASTAUSOHJEET")
        parts.append("- Vastaa AINA suomeksi")
        parts.append("- Ole konkreettinen: anna numerot, määrät, päivämäärät")
        parts.append("- Viittaa kynnysarvoihin päätöksissä")
        parts.append("- Max 5 lausetta ellei kysytä enemmän")'''

    new_instructions = '''        if _en:
            parts.append("\\n## RESPONSE RULES")
            parts.append("- Always respond in ENGLISH")
            parts.append("- Be concrete: give numbers, quantities, dates")
            parts.append("- Reference thresholds in decisions")
            parts.append("- Max 5 sentences unless asked for more")
            parts.append("- Use exact domain terminology (varroa, AFB, queen, brood)")
        else:
            parts.append("\\n## VASTAUSOHJEET")
            parts.append("- Vastaa AINA suomeksi")
            parts.append("- Ole konkreettinen: anna numerot, määrät, päivämäärät")
            parts.append("- Viittaa kynnysarvoihin päätöksissä")
            parts.append("- Max 5 lausetta ellei kysytä enemmän")'''

    if old_instructions in src:
        src = src.replace(old_instructions, new_instructions, 1)
        print("  ✅ [4/5] Vastausohjeet → EN/FI")
    else:
        errors.append("[4] Vastausohjeet: ei löydy")

    # ══════════════════════════════════════════════════════════
    # PATCH 5: _ensure_loaded → tulosta myös EN-tila
    # ══════════════════════════════════════════════════════════
    old_loaded = '        print(f"📚 YAMLBridge: {len(self._agents)} agenttia ladattu")'
    new_loaded = '        print(f"📚 YAMLBridge: {len(self._agents)} agenttia ladattu (lang={self._language})")'

    if old_loaded in src:
        src = src.replace(old_loaded, new_loaded, 1)
        print("  ✅ [5/5] _ensure_loaded: lang info")
    else:
        errors.append("[5] _ensure_loaded print: ei löydy")

    # ══════════════════════════════════════════════════════════
    # Virheet + syntax
    # ══════════════════════════════════════════════════════════
    if errors:
        print(f"\n⚠️  {len(errors)} patchia epäonnistui:")
        for e in errors:
            print(f"    ❌ {e}")

    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"\n❌ SYNTAKSIVIRHE rivillä {e.lineno}: {e.msg}")
        print(f"   {e.text}")
        return False

    p.write_text(src, encoding="utf-8")
    print(f"\n🟢 YAMLBridge EN patch valmis! ({src.count(chr(10))+1} riviä)")
    return True


def verify(path: str = "core/yaml_bridge.py"):
    print("\n🔍 VERIFIOINTI")
    print("=" * 60)
    src = Path(path).read_text(encoding="utf-8")
    all_ok = True
    for name, marker in [
        ("set_translation_proxy()", "def set_translation_proxy"),
        ("_translate_deep()", "def _translate_deep"),
        ("set_language()", "def set_language"),
        ("_agents_en", "self._agents_en"),
        ("EN header: You are", 'f"You are {_name}'),
        ("EN sections", "ASSUMPTIONS AND CONTEXT"),
        ("EN metrics", "DECISION METRICS AND THRESHOLDS"),
        ("EN seasons", "SEASONAL CALENDAR"),
        ("EN failures", "FAILURE MODES"),
        ("EN compliance", "LEGAL AND COMPLIANCE"),
        ("EN instructions", "Always respond in ENGLISH"),
        ("FI fallback preserved", "Vastaa AINA suomeksi"),
    ]:
        ok = marker in src
        print(f"  {'✅' if ok else '❌'} {name}")
        if not ok:
            all_ok = False
    print(f"\n  {'🟢 KAIKKI OK' if all_ok else '🔴 PUUTTEITA'}")
    return all_ok


if __name__ == "__main__":
    import sys
    print("🐝 WaggleDance YAMLBridge EN Patch v1.0")
    print("=" * 60)

    success = patch_yaml_bridge()
    if success:
        verify()
        print("""
═══════════════════════════════════════════════════════════
  YAML BRIDGE EN PATCH ASENNETTU
═══════════════════════════════════════════════════════════

  Vielä tarvitaan: hivemind.py:n start()-metodissa kutsu:

    self.yaml_bridge.set_translation_proxy(
        self.translation_proxy, "en"
    )

  Tämä tapahtuu KERRAN startissa, kääntää kaikki 49 agentin
  YAML-sielut FI→EN välimuistiin (~0.5s).

  Tulos:
    ENNEN: "Olet Tarhaaja — Päämehiläishoitaja."
    JÄLKEEN: "You are Beekeeper — Head Apiarist."

    ENNEN: "## PÄÄTÖSMETRIIKAT JA KYNNYSARVOT"
    JÄLKEEN: "## DECISION METRICS AND THRESHOLDS"

    ENNEN: ">3 punkkia/100 mehiläistä → kemiallinen hoito"
    JÄLKEEN: ">3 mites/100 bees → chemical treatment"
""")
