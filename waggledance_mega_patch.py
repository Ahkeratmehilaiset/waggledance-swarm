#!/usr/bin/env python3
"""
WaggleDance — Mega Translation Patch v3.0
==========================================
Patchaa OIKEAN hivemind.py:n (1392+ riviä, SwarmScheduler, LearningEngine).

Kohdistuu tarkkaan nykyiseen rakenteeseen:
  - chat() = prioriteetti-wrapper → _do_chat()
  - _do_chat() = reititys + multi-agent
  - _delegate_to_agent() = _enriched_prompt context manager
  - stop() = "WaggleDance sammutettu"

Käyttö:
  cd U:\\project
  1. Palauta alkuperäinen: copy hivemind_backup_XXXXXXXX_XXXX.py hivemind.py
  2. python waggledance_mega_patch.py
"""

import re
import ast
import shutil
from pathlib import Path
from datetime import datetime


# ═══════════════════════════════════════════════════════════════
# EN SYSTEM PROMPTIT
# ═══════════════════════════════════════════════════════════════

AGENT_EN_PROMPTS = {
    "hivemind": """CRITICAL FACTS (ALWAYS use):
- Jani Korpi, JKH Service (Business ID: 2828492-2), Evira: 18533284
- 202 colonies (NOT 300), 35 apiary locations (2024)
- Breeds: italMeh (Italian), grnMeh (Carniolan/Carnica)
- Regions: Tuusula (36), Helsinki (20), Vantaa (16), Espoo (66), Polvijärvi (3), Kouvola (61)
- Karhuniementie 562 D (70% business / 30% personal)
RESPONSE RULES:
- Answer ONLY what is asked, max 5 sentences
- Owner is Jani (NOT Janina, NOT Janne)
- Do NOT invent numbers or dates — say "I don't know exactly" if unsure
- Be direct and concrete. No preamble.
You are HiveMind, the central intelligence of Jani's personal agent system.
Delegate to specialists. Be brief and concrete.""",

    "beekeeper": """You are a beekeeping specialist for JKH Service (202 colonies across Finland).
Expert in: varroa treatment (formic/oxalic acid), seasonal management, queen rearing,
honey harvest, feeding schedules, disease identification (AFB, EFB, nosema, chalkbrood).
Breeds: Italian & Carniolan honeybees.
Answer max 3 sentences, practical advice only. Use metric units.""",

    "video_producer": """You are a video production specialist for beekeeping content.
Expert in: TikTok/YouTube optimization, multilingual subtitles (Finnish primary),
AI transcription (Whisper), editing workflows, platform-specific formatting.
Focus: beekeeping educational content, urban beekeeping, honey harvesting.
Answer max 3 sentences, actionable tips.""",

    "property": """You are a property management specialist.
Properties: Huhdasjärvi cottage (Karhuniementie 562 D, 70% business / 30% personal).
Expert in: winterization, sauna maintenance, plumbing, electrical, insulation,
rural property upkeep, short-term rental compliance.
Answer max 3 sentences, practical solutions.""",

    "tech": """You are a technology specialist.
Expert in: Python, Ollama/local LLMs, AI systems, Whisper transcription,
Windows/WSL, hardware optimization (24GB VRAM RTX), automation.
Current projects: WaggleDance/OpenClaw AI swarm, translation proxy, benchmarking.
Answer max 3 sentences, working code when possible.""",

    "business": """You are a business specialist for JKH Service (Y-tunnus: 2828492-2).
Expert in: Finnish VAT (ALV), sole proprietorship accounting, honey sales
(Wolt, online, direct), food safety regulations (Evira), pricing strategy.
Annual production: ~10,000 kg honey from 202 colonies.
Answer max 3 sentences, concrete numbers.""",

    "hacker": """You are a code security and optimization specialist.
Expert in: bug hunting, refactoring, security scanning, performance optimization,
Python async patterns, database optimization, Windows compatibility.
Answer max 3 sentences, show code fixes.""",

    "oracle": """You are a research and web search specialist.
Expert in: finding current information, trend analysis, competitor research,
fact-checking, market analysis for beekeeping industry.
Answer max 3 sentences with sources when possible.""",
}


# ═══════════════════════════════════════════════════════════════
# KIELENTUNNISTUSKOODI
# ═══════════════════════════════════════════════════════════════

LANGUAGE_DETECT_CODE = r'''

# ═══════════════════════════════════════════════════════════════
# KIELENTUNNISTUS
# ═══════════════════════════════════════════════════════════════

_FI_MARKERS = {
    "chars": set("äöåÄÖÅ"),
    "words": {"ja", "on", "ei", "se", "miten", "mikä", "missä",
              "mutta", "tai", "kun", "jos", "niin", "myös", "ovat",
              "voi", "oli", "ole", "mitä", "miksi", "milloin",
              "tämä", "joka", "sitä", "sen", "olla", "pitää",
              "kuin", "nyt", "sitten", "vielä", "aina", "paljon",
              "hyvä", "uusi", "kaikki", "mutta", "kanssa", "ennen",
              "monta", "paljonko", "kuinka", "onko", "voiko",
              "saa", "anna", "tee", "ota", "laita", "muista",
              "minun", "sinun", "meillä", "teillä", "heillä",
              "tarvitaan", "pitäisi", "kannattaa", "saako",
              "vuosi", "kesä", "talvi", "kevät", "syksy"},
    "suffixes": ["ssa", "ssä", "lla", "llä", "sta", "stä",
                 "lle", "lta", "ltä", "ksi", "iin", "aan", "ään",
                 "tta", "ttä", "mme", "tte", "vat", "vät"],
}

_EN_MARKERS = {
    "words": {"the", "is", "are", "was", "were", "have", "has",
              "been", "will", "would", "could", "should", "with",
              "from", "this", "that", "what", "how", "when",
              "where", "which", "there", "their", "about",
              "into", "your", "they", "been", "does", "than",
              "for", "and", "but", "not", "you", "all", "can",
              "her", "one", "our", "out", "day", "get", "make",
              "like", "just", "know", "take", "come", "think",
              "also", "after", "year", "give", "most", "find",
              "here", "many", "much", "need", "best", "each"},
}


def detect_language(text: str) -> str:
    """Tunnista fi/en/unknown. Nopea heuristinen (~0.01ms)."""
    if not text or len(text.strip()) < 2:
        return "unknown"
    text_lower = text.lower()
    words = set(re.findall(r'[a-zäöå]+', text_lower))
    if _FI_MARKERS["chars"] & set(text):
        return "fi"
    fi_score = len(words & _FI_MARKERS["words"])
    en_score = len(words & _EN_MARKERS["words"])
    for word in words:
        for sfx in _FI_MARKERS["suffixes"]:
            if word.endswith(sfx) and len(word) > len(sfx) + 2:
                fi_score += 0.5
    if fi_score > en_score and fi_score >= 0.5:
        return "fi"
    elif en_score > fi_score and en_score >= 0.5:
        return "en"
    return "unknown"


def is_finnish(text: str) -> bool:
    return detect_language(text) == "fi"

'''


def patch_translation_proxy(path: str = "translation_proxy.py"):
    """Lisää kielentunnistus translation_proxy.py:hin."""
    p = Path(path)
    if not p.exists():
        print(f"  ⚠️  {path} ei löydy — ohitetaan")
        return False
    src = p.read_text(encoding="utf-8")
    if "def detect_language" in src:
        print(f"  ℹ️  {path} sisältää jo kielentunnistuksen")
        return True
    marker = "class OpusMTFallback"
    if marker in src:
        src = src.replace(marker, LANGUAGE_DETECT_CODE + "\n" + marker)
    else:
        src += LANGUAGE_DETECT_CODE
    p.write_text(src, encoding="utf-8")
    print(f"  ✅ Kielentunnistus lisätty: {path}")
    return True


# ═══════════════════════════════════════════════════════════════
# HIVEMIND.PY MEGA-PATCH v3
# ═══════════════════════════════════════════════════════════════

def patch_hivemind(hivemind_path: str = "hivemind.py", backup: bool = True):
    path = Path(hivemind_path)
    if not path.exists():
        print(f"❌ {hivemind_path} ei löydy!")
        return False

    src = path.read_text(encoding="utf-8")

    if "_translation_used" in src and "_detected_lang" in src:
        print("ℹ️  hivemind.py sisältää jo v3 mega-patchin!")
        return True

    if backup:
        backup_name = f"hivemind_backup_{datetime.now():%Y%m%d_%H%M}.py"
        shutil.copy2(path, backup_name)
        print(f"💾 Backup: {backup_name}")

    errors = []

    # ── PATCH 1: Import ──────────────────────────────────────
    old = "from memory.shared_memory import SharedMemory"
    new = """from memory.shared_memory import SharedMemory

# ═══ Translation Proxy — Voikko + sanakirja FI↔EN ═══
try:
    from translation_proxy import TranslationProxy, detect_language, is_finnish
    _TRANSLATION_AVAILABLE = True
except ImportError:
    _TRANSLATION_AVAILABLE = False
    def detect_language(t): return "fi"
    def is_finnish(t): return True"""

    if old in src:
        src = src.replace(old, new, 1)
        print("  ✅ [1/10] Import")
    else:
        errors.append("[1] Import: 'from memory.shared_memory' ei löydy")

    # ── PATCH 2: EN-promptit ─────────────────────────────────
    en_prompts = "\n# ═══ EN System Prompts ═══\nAGENT_EN_PROMPTS = " + repr(AGENT_EN_PROMPTS) + "\n"

    for class_name in ["class HiveMind:", "class WaggleDance:"]:
        marker = "\n" + class_name
        if marker in src:
            src = src.replace(marker, en_prompts + marker, 1)
            print(f"  ✅ [2/10] EN-promptit ({class_name})")
            break
    else:
        errors.append("[2] EN-promptit: class-määrittelyä ei löydy")

    # ── PATCH 3: __init__ ────────────────────────────────────
    old = "        self.running = False\n        self._heartbeat_count = 0"
    new = """        self.running = False
        self._heartbeat_count = 0
        self.translation_proxy = None
        self.language_mode = "auto"  # "auto", "fi", "en" """

    if old in src:
        src = src.replace(old, new, 1)
        print("  ✅ [3/10] __init__")
    else:
        errors.append("[3] __init__: running+heartbeat ei löydy")

    # ── PATCH 4: start() — proxy alustus ─────────────────────
    old = "        self.running = True\n        self.started_at = datetime.now()"
    new = """        # ═══ Translation Proxy ═══
        if _TRANSLATION_AVAILABLE:
            try:
                self.translation_proxy = TranslationProxy()
                _tp = self.translation_proxy
                _v = "✅" if _tp.voikko.available else "❌"
                print(f"  ✅ Translation Proxy (Voikko={_v}, Dict={len(_tp.dict_fi_en)}, Lang=auto)")
            except Exception as e:
                print(f"  ⚠️  Translation Proxy: {e}")
                self.translation_proxy = None
        else:
            print("  ℹ️  Translation Proxy ei saatavilla")
            self.translation_proxy = None

        self.running = True
        self.started_at = datetime.now()"""

    if old in src:
        src = src.replace(old, new, 1)
        print("  ✅ [4/10] start()")
    else:
        errors.append("[4] start(): running+started_at ei löydy")

    # ── PATCH 5: stop() ──────────────────────────────────────
    old_stop = '        print("  WaggleDance sammutettu.")'
    new_stop = """        if self.translation_proxy:
            self.translation_proxy.close()
            print("  ✅ Translation Proxy suljettu")
        print("  WaggleDance sammutettu.")"""

    if old_stop in src:
        src = src.replace(old_stop, new_stop, 1)
        print("  ✅ [5/10] stop()")
    else:
        errors.append("[5] stop(): 'WaggleDance sammutettu.' ei löydy")

    # ── PATCH 6: chat() — language parametri ─────────────────
    old = "    async def chat(self, message: str) -> str:"
    new = '    async def chat(self, message: str, language: str = "auto") -> str:'
    if old in src:
        src = src.replace(old, new, 1)
        print("  ✅ [6/10] chat() language param")
    else:
        errors.append("[6] chat() signature")

    old = "            return await self._do_chat(message)"
    new = "            return await self._do_chat(message, language=language)"
    if old in src:
        src = src.replace(old, new, 1)
    else:
        errors.append("[6b] _do_chat kutsu")

    # ── PATCH 7: _do_chat() — käännöslogiikka ────────────────
    old_dochat = '''    async def _do_chat(self, message: str) -> str:
        """Varsinainen chat-logiikka (eriytetty prioriteettigatesta)."""
        await self.memory.store_memory(
            content=f"Käyttäjä sanoi: {message}",
            agent_id="user",
            memory_type="observation",
            importance=0.6
        )

        context = await self.memory.get_full_context(message)
        msg_lower = message.lower()'''

    new_dochat = '''    async def _do_chat(self, message: str, language: str = "auto") -> str:
        """Varsinainen chat-logiikka. Tukee FI↔EN käännöstä: auto/fi/en."""
        _original_message = message
        self._translation_used = False
        self._fi_en_result = None
        self._detected_lang = language

        # ═══ Kielentunnistus ═══
        if language == "auto":
            self._detected_lang = detect_language(message) if _TRANSLATION_AVAILABLE else "fi"

        # ═══ FI→EN käännös (~2ms) ═══
        if self._detected_lang == "fi" and self.translation_proxy:
            self._fi_en_result = self.translation_proxy.fi_to_en(message)
            if self._fi_en_result.coverage >= 0.5 and self._fi_en_result.method != "passthrough":
                self._translation_used = True
                _en_message = self._fi_en_result.text
                if self.monitor:
                    await self.monitor.system(
                        f"🔄 FI→EN ({self._fi_en_result.method}, "
                        f"{self._fi_en_result.latency_ms:.1f}ms, "
                        f"{self._fi_en_result.coverage:.0%}): {_en_message[:80]}")
            else:
                _en_message = message
        else:
            _en_message = message

        # Viesti agentille
        self._routed_message = _en_message if (self._translation_used or self._detected_lang == "en") else message
        self._use_en_prompts = self._translation_used or self._detected_lang == "en"

        await self.memory.store_memory(
            content=f"Käyttäjä sanoi: {message}",
            agent_id="user",
            memory_type="observation",
            importance=0.6
        )

        context = await self.memory.get_full_context(_original_message)
        msg_lower = _original_message.lower()  # Reititys aina FI-sanoilla'''

    if old_dochat in src:
        src = src.replace(old_dochat, new_dochat, 1)
        print("  ✅ [7/10] _do_chat() käännöslogiikka")
    else:
        errors.append("[7] _do_chat(): signature+alku ei täsmää")

    # ── PATCH 7b: delegate kutsut → _routed_message ──────────
    old_dc = "                delegate_to, message, context, msg_lower"
    new_dc = "                delegate_to, self._routed_message, context, msg_lower"
    count = src.count(old_dc)
    if count > 0:
        src = src.replace(old_dc, new_dc)
        print(f"  ✅ [7b] delegate kutsut ({count}x)")
    else:
        errors.append("[7b] delegate kutsut ei löydy")

    # ── PATCH 7c: Master fallback — EN prompt + EN→FI ────────
    old_master = '''            # Fallback: Master (Swarm Queen)
            with self._enriched_prompt(self.master_agent, knowledge_max_chars=2000):
                response = await self.master_agent.think(message, context)
            await self._notify_ws("chat_response", {
                "message": message, "response": response
            })
            return response'''

    new_master = '''            # Fallback: Master (Swarm Queen)
            _orig_master_sys = None
            if self._use_en_prompts and "hivemind" in AGENT_EN_PROMPTS:
                _orig_master_sys = self.master_agent.system_prompt
                from datetime import datetime as _dt
                self.master_agent.system_prompt = f"Date: {_dt.now():%Y-%m-%d %H:%M}. " + AGENT_EN_PROMPTS["hivemind"]
            try:
                with self._enriched_prompt(self.master_agent, knowledge_max_chars=2000):
                    response = await self.master_agent.think(self._routed_message, context)
            finally:
                if _orig_master_sys is not None:
                    self.master_agent.system_prompt = _orig_master_sys
            if self._translation_used and self.translation_proxy:
                _en_fi = self.translation_proxy.en_to_fi(response)
                if _en_fi.method != "passthrough":
                    response = _en_fi.text
            await self._notify_ws("chat_response", {
                "message": message, "response": response,
                "language": self._detected_lang, "translated": self._translation_used
            })
            return response'''

    if old_master in src:
        src = src.replace(old_master, new_master, 1)
        print("  ✅ [7c] Master: EN-prompt + EN→FI")
    else:
        errors.append("[7c] Master fallback ei täsmää")

    # ── PATCH 8a: _delegate_to_agent — EN-prompt ─────────────
    old_enrich = '''        # FIX-1: Yksi context manager hoitaa kaiken injektoinnin ja palautuksen
        with self._enriched_prompt(agent, inject_date=True,
                                    inject_knowledge=True,
                                    knowledge_max_chars=2000):
            try:'''

    new_enrich = '''        # ═══ EN-prompt jos käännös aktiivinen ═══
        _orig_agent_sys = None
        if getattr(self, '_use_en_prompts', False):
            _atype = getattr(agent, 'agent_type', getattr(agent, 'type', ''))
            if _atype in AGENT_EN_PROMPTS:
                _orig_agent_sys = agent.system_prompt
                from datetime import datetime as _dt
                agent.system_prompt = f"Date: {_dt.now():%Y-%m-%d %H:%M}. " + AGENT_EN_PROMPTS[_atype]

        # FIX-1: Yksi context manager hoitaa kaiken injektoinnin ja palautuksen
        with self._enriched_prompt(agent, inject_date=True,
                                    inject_knowledge=True,
                                    knowledge_max_chars=2000):
            try:'''

    if old_enrich in src:
        src = src.replace(old_enrich, new_enrich, 1)
        print("  ✅ [8a] _delegate: EN-prompt")
    else:
        errors.append("[8a] _delegate: _enriched_prompt lohko ei täsmää")

    # ── PATCH 8b: _delegate — prompt restore + EN→FI ─────────
    old_ret = '''        await self._notify_ws("delegated", {
            "agent": agent.name, "type": delegate_to, "response": response
        })
        return f"[{agent.name}] {response}"'''

    new_ret = '''        # Palauta FI-prompt
        if _orig_agent_sys is not None:
            agent.system_prompt = _orig_agent_sys

        # ═══ EN→FI käännös ═══
        if getattr(self, '_translation_used', False) and self.translation_proxy:
            _en_fi = self.translation_proxy.en_to_fi(response)
            if _en_fi.method != "passthrough":
                if self.monitor:
                    _src_ms = getattr(self._fi_en_result, 'latency_ms', 0) if self._fi_en_result else 0
                    await self.monitor.system(
                        f"🔄 EN→FI ({_en_fi.method}, {_en_fi.latency_ms:.1f}ms, "
                        f"total: {_src_ms + _en_fi.latency_ms:.1f}ms)")
                response = _en_fi.text

        await self._notify_ws("delegated", {
            "agent": agent.name, "type": delegate_to, "response": response,
            "language": getattr(self, '_detected_lang', 'fi'),
            "translated": getattr(self, '_translation_used', False)
        })
        return f"[{agent.name}] {response}"'''

    if old_ret in src:
        src = src.replace(old_ret, new_ret, 1)
        print("  ✅ [8b] _delegate: EN→FI + restore")
    else:
        errors.append("[8b] _delegate: return-lohko ei täsmää")

    # ── PATCH 9: get_status() ────────────────────────────────
    old_k = '"knowledge": self.knowledge.list_all_knowledge() if self.knowledge else {}'
    if old_k in src:
        new_k = old_k + """,
            "translation_proxy": {
                "available": self.translation_proxy is not None,
                "language_mode": self.language_mode,
                "voikko": self.translation_proxy.voikko.available if self.translation_proxy else False,
                "dict_size": len(self.translation_proxy.dict_fi_en) if self.translation_proxy else 0,
                "stats": self.translation_proxy.get_stats() if self.translation_proxy else {},
            }"""
        src = src.replace(old_k, new_k, 1)
        print("  ✅ [9/10] Dashboard-tilastot")
    else:
        print("  ⚠️  [9/10] knowledge-rivi ei löydy (ohitetaan)")

    # ── PATCH 10: set_language() ─────────────────────────────
    helper = '''
    # ── Kieliasetukset ──────────────────────────────────────────

    def set_language(self, mode: str = "auto"):
        """Aseta kielitila: 'auto', 'fi', 'en'."""
        if mode in ("auto", "fi", "en"):
            self.language_mode = mode
            print(f"🌐 Kielitila: {mode}")
        else:
            print(f"⚠️  Tuntematon kielitila: {mode}")

    def get_language_status(self) -> dict:
        """Palauta käännösjärjestelmän tila."""
        return {
            "mode": self.language_mode,
            "proxy_available": self.translation_proxy is not None,
            "voikko": self.translation_proxy.voikko.available if self.translation_proxy else False,
            "dict_size": len(self.translation_proxy.dict_fi_en) if self.translation_proxy else 0,
            "en_prompts": list(AGENT_EN_PROMPTS.keys()),
            "stats": self.translation_proxy.get_stats() if self.translation_proxy else {},
        }

'''
    for marker in ["    # ── Heartbeat", "    async def _heartbeat_loop"]:
        if marker in src:
            src = src.replace(marker, helper + marker, 1)
            print("  ✅ [10/10] set_language()")
            break
    else:
        src += helper
        print("  ✅ [10/10] set_language() (loppuun)")

    # ── Virheraportit ────────────────────────────────────────
    if errors:
        print(f"\n⚠️  {len(errors)} patchia epäonnistui:")
        for e in errors:
            print(f"    ❌ {e}")

    # ── Syntax check ─────────────────────────────────────────
    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"\n❌ SYNTAKSIVIRHE rivillä {e.lineno}: {e.msg}")
        print(f"   Palauta backup!")
        return False

    path.write_text(src, encoding="utf-8")
    print(f"\n🟢 Mega-patch v3 valmis! ({src.count(chr(10))+1} riviä)")
    return True


# ═══════════════════════════════════════════════════════════════
# VERIFIOINTI
# ═══════════════════════════════════════════════════════════════

def verify_all(hivemind_path: str = "hivemind.py",
               proxy_path: str = "translation_proxy.py"):
    print("\n🔍 VERIFIOINTI")
    print("=" * 60)
    all_ok = True

    p = Path(proxy_path)
    if p.exists():
        src = p.read_text(encoding="utf-8")
        print(f"\n  📄 {proxy_path}:")
        for name, marker in [("detect_language()", "def detect_language"),
                              ("is_finnish()", "def is_finnish"),
                              ("FI markers", "_FI_MARKERS")]:
            ok = marker in src
            print(f"    {'✅' if ok else '❌'} {name}")
            if not ok: all_ok = False

    h = Path(hivemind_path)
    if h.exists():
        src = h.read_text(encoding="utf-8")
        print(f"\n  📄 {hivemind_path}:")
        for name, marker in [
            ("Import: TranslationProxy", "from translation_proxy import TranslationProxy"),
            ("Import: detect_language", "detect_language"),
            ("EN promptit", "AGENT_EN_PROMPTS"),
            ("__init__: proxy", "self.translation_proxy = None"),
            ("__init__: language_mode", "self.language_mode"),
            ("start(): proxy init", "TranslationProxy()"),
            ("chat(): language param", 'language: str = "auto"'),
            ("_do_chat(): detect_language", "_detected_lang = detect_language"),
            ("_do_chat(): _use_en_prompts", "_use_en_prompts"),
            ("_delegate: AGENT_EN_PROMPTS", "AGENT_EN_PROMPTS["),
            ("_do_chat(): FI→EN", "self.translation_proxy.fi_to_en"),
            ("_delegate: EN→FI", "self.translation_proxy.en_to_fi"),
            ("_do_chat(): _routed_message", "_routed_message"),
            ("stop(): proxy.close()", "self.translation_proxy.close()"),
            ("status: translation_proxy", '"translation_proxy"'),
            ("set_language()", "def set_language"),
            ("get_language_status()", "def get_language_status"),
        ]:
            ok = marker in src
            print(f"    {'✅' if ok else '❌'} {name}")
            if not ok: all_ok = False

    print(f"\n  {'🟢 KAIKKI OK' if all_ok else '🔴 PUUTTEITA'}")
    return all_ok


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "verify":
        verify_all()
        sys.exit(0)

    print("🐝 WaggleDance Mega Translation Patch v3.0")
    print("=" * 60)

    print("\n📦 Osa 1: translation_proxy.py")
    patch_translation_proxy()

    print("\n📦 Osa 2: hivemind.py")
    success = patch_hivemind()

    if success:
        print("\n" + "=" * 60)
        verify_all()
        print("""
  KÄYNNISTÄ WAGGLEDANCE UUDELLEEN:
    ✅ Translation Proxy (Voikko=✅, Dict=412, Lang=auto)

  KIELIMOODIT:
    auto → FI/EN tunnistetaan, käännös tarvittaessa
    fi   → Pakota suomi, FI-promptit
    en   → Pakota englanti, EN-promptit, ei käännöstä
""")
