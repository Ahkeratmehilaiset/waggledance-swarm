#!/usr/bin/env python3
"""
WaggleDance — EN Validator Integration Patch v1.0
==================================================
Lisää EN-terminologian validointi hivemind.py:hin.

VAATIMUS: Mega-patch v3 pitää olla jo asennettu (TranslationProxy toimii).
Tämä on LISÄPATCH joka menee v3:n päälle.

Muutokset:
  1. Import: ENValidator
  2. __init__: self.en_validator
  3. start(): alusta ENValidator domain-termeillä
  4. _delegate_to_agent(): validoi EN-vastaus ennen EN→FI käännöstä
  5. Master fallback: sama validointi
  6. Heartbeat insight: validoi ennen muistiin tallennusta
  7. get_status(): en_validator tilastot

Käyttö:
  cd U:\\project
  python en_validator_patch.py

Tekijä: WaggleDance / JKH Service
"""

import ast
import shutil
from pathlib import Path
from datetime import datetime


def patch_hivemind(hivemind_path: str = "hivemind.py", backup: bool = True):
    path = Path(hivemind_path)
    if not path.exists():
        print(f"❌ {hivemind_path} ei löydy!")
        return False

    src = path.read_text(encoding="utf-8")

    # ═══ Tarkistukset ═══
    if "ENValidator" in src:
        print("ℹ️  EN Validator on jo integroitu!")
        return True

    if "_translation_used" not in src:
        print("❌ Mega-patch v3 ei ole asennettu! Aja waggledance_mega_patch.py ensin.")
        return False

    if backup:
        backup_name = f"hivemind_backup_{datetime.now():%Y%m%d_%H%M}.py"
        shutil.copy2(path, backup_name)
        print(f"💾 Backup: {backup_name}")

    errors = []

    # ══════════════════════════════════════════════════════════
    # PATCH 1: Import
    # ══════════════════════════════════════════════════════════
    old = """    _TRANSLATION_AVAILABLE = True
except ImportError:
    _TRANSLATION_AVAILABLE = False
    def detect_language(t): return "fi"
    def is_finnish(t): return True"""

    new = """    _TRANSLATION_AVAILABLE = True
except ImportError:
    _TRANSLATION_AVAILABLE = False
    def detect_language(t): return "fi"
    def is_finnish(t): return True

try:
    from en_validator import ENValidator
    _EN_VALIDATOR_AVAILABLE = True
except ImportError:
    _EN_VALIDATOR_AVAILABLE = False"""

    if old in src:
        src = src.replace(old, new, 1)
        print("  ✅ [1/7] Import ENValidator")
    else:
        errors.append("[1] Import: TranslationProxy lohko ei löydy")

    # ══════════════════════════════════════════════════════════
    # PATCH 2: __init__
    # ══════════════════════════════════════════════════════════
    old = '        self.translation_proxy = None\n        self.language_mode = "auto"'
    new = '        self.translation_proxy = None\n        self.en_validator = None\n        self.language_mode = "auto"'

    if old in src:
        src = src.replace(old, new, 1)
        print("  ✅ [2/7] __init__: self.en_validator")
    else:
        errors.append("[2] __init__: translation_proxy+language_mode ei löydy")

    # ══════════════════════════════════════════════════════════
    # PATCH 3: start() — alusta EN validator domain-termeillä
    # ══════════════════════════════════════════════════════════
    old = """        self.running = True
        self.started_at = datetime.now()"""

    new = """        # ═══ EN Validator (WordNet + domain synonyms) ═══
        if _EN_VALIDATOR_AVAILABLE:
            try:
                _domain = set(self.translation_proxy.dict_en_fi.keys()) if self.translation_proxy else set()
                self.en_validator = ENValidator(domain_terms=_domain)
                _wn = "✅" if self.en_validator.wordnet.available else "❌"
                print(f"  ✅ EN Validator (WordNet={_wn}, Synonyms={len(self.en_validator.domain_synonyms)})")
            except Exception as e:
                print(f"  ⚠️  EN Validator: {e}")
                self.en_validator = None
        else:
            print("  ℹ️  EN Validator ei saatavilla (pip install nltk)")
            self.en_validator = None

        self.running = True
        self.started_at = datetime.now()"""

    if old in src:
        src = src.replace(old, new, 1)
        print("  ✅ [3/7] start(): EN Validator alustus")
    else:
        errors.append("[3] start(): running+started_at ei löydy")

    # ══════════════════════════════════════════════════════════
    # PATCH 4: _delegate_to_agent — validoi EN ennen EN→FI
    # ══════════════════════════════════════════════════════════
    old = """        # ═══ EN→FI käännös ═══
        if getattr(self, '_translation_used', False) and self.translation_proxy:"""

    new = """        # ═══ EN Validator: standardisoi terminologia ═══
        if self.en_validator and getattr(self, '_use_en_prompts', False):
            _val = self.en_validator.validate(response)
            if _val.was_corrected:
                if self.monitor:
                    await self.monitor.system(
                        f"🔍 EN-fix ({_val.method}, {_val.latency_ms:.1f}ms, "
                        f"{_val.correction_count} korjausta): "
                        f"{_val.corrections[:3]}")
                response = _val.corrected

        # ═══ EN→FI käännös ═══
        if getattr(self, '_translation_used', False) and self.translation_proxy:"""

    if old in src:
        src = src.replace(old, new, 1)
        print("  ✅ [4/7] _delegate: EN validation ennen EN→FI")
    else:
        errors.append("[4] _delegate: EN→FI lohko ei löydy")

    # ══════════════════════════════════════════════════════════
    # PATCH 5: Master fallback — validoi EN ennen EN→FI
    # ══════════════════════════════════════════════════════════
    old = """            if self._translation_used and self.translation_proxy:
                _en_fi = self.translation_proxy.en_to_fi(response)
                if _en_fi.method != "passthrough":
                    response = _en_fi.text

            await self._notify_ws("chat_response", {"""

    new = """            # EN Validator master-vastaukselle
            if self.en_validator and self._use_en_prompts:
                _val = self.en_validator.validate(response)
                if _val.was_corrected:
                    response = _val.corrected

            if self._translation_used and self.translation_proxy:
                _en_fi = self.translation_proxy.en_to_fi(response)
                if _en_fi.method != "passthrough":
                    response = _en_fi.text

            await self._notify_ws("chat_response", {"""

    if old in src:
        src = src.replace(old, new, 1)
        print("  ✅ [5/7] Master: EN validation")
    else:
        errors.append("[5] Master: EN→FI lohko ei löydy")

    # ══════════════════════════════════════════════════════════
    # PATCH 6: Heartbeat insight — validoi ennen muistiin tallennusta
    # ══════════════════════════════════════════════════════════
    old = """            # KORJAUS K10: validoi ennen tallennusta
            if insight and self._is_valid_response(insight):
                await self.memory.store_memory(
                    content=f"[{agent.name}] {insight}","""

    new = """            # KORJAUS K10: validoi ennen tallennusta
            if insight and self._is_valid_response(insight):
                # ═══ EN Validator: standardisoi heartbeat-insight ═══
                if self.en_validator:
                    _val = self.en_validator.validate(insight)
                    if _val.was_corrected:
                        insight = _val.corrected
                await self.memory.store_memory(
                    content=f"[{agent.name}] {insight}","""

    if old in src:
        src = src.replace(old, new, 1)
        print("  ✅ [6/7] Heartbeat: EN validation ennen muistia")
    else:
        errors.append("[6] Heartbeat: K10 lohko ei löydy")

    # ══════════════════════════════════════════════════════════
    # PATCH 7: get_status() — EN validator tilastot
    # ══════════════════════════════════════════════════════════
    old = """                "stats": self.translation_proxy.get_stats() if self.translation_proxy else {},
            }"""

    new = """                "stats": self.translation_proxy.get_stats() if self.translation_proxy else {},
            },
            "en_validator": {
                "available": self.en_validator is not None,
                "wordnet": self.en_validator.wordnet.available if self.en_validator else False,
                "synonyms": len(self.en_validator.domain_synonyms) if self.en_validator else 0,
                "stats": self.en_validator.get_stats() if self.en_validator else {},
            }"""

    if old in src:
        src = src.replace(old, new, 1)
        print("  ✅ [7/7] Dashboard: EN validator tilastot")
    else:
        errors.append("[7] get_status(): translation_proxy lohko ei löydy")

    # ══════════════════════════════════════════════════════════
    # VIRHEET + SYNTAX
    # ══════════════════════════════════════════════════════════
    if errors:
        print(f"\n⚠️  {len(errors)} patchia epäonnistui:")
        for e in errors:
            print(f"    ❌ {e}")

    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"\n❌ SYNTAKSIVIRHE rivillä {e.lineno}: {e.msg}")
        return False

    path.write_text(src, encoding="utf-8")
    print(f"\n🟢 EN Validator patch valmis! ({src.count(chr(10))+1} riviä)")
    return True


# ═══════════════════════════════════════════════════════════════
# VERIFIOINTI
# ═══════════════════════════════════════════════════════════════

def verify(hivemind_path: str = "hivemind.py",
           validator_path: str = "en_validator.py"):
    print("\n🔍 VERIFIOINTI")
    print("=" * 60)
    all_ok = True

    v = Path(validator_path)
    if v.exists():
        src = v.read_text(encoding="utf-8")
        print(f"\n  📄 {validator_path}:")
        for name, marker in [
            ("ENValidator class", "class ENValidator"),
            ("DOMAIN_SYNONYMS", "DOMAIN_SYNONYMS"),
            ("WordNetLayer", "class WordNetLayer"),
            ("validate()", "def validate"),
        ]:
            ok = marker in src
            print(f"    {'✅' if ok else '❌'} {name}")
            if not ok: all_ok = False
    else:
        print(f"\n  ❌ {validator_path} ei löydy!")
        all_ok = False

    h = Path(hivemind_path)
    if h.exists():
        src = h.read_text(encoding="utf-8")
        print(f"\n  📄 {hivemind_path}:")
        for name, marker in [
            ("Import: ENValidator", "from en_validator import ENValidator"),
            ("__init__: en_validator", "self.en_validator = None"),
            ("start(): ENValidator()", "ENValidator(domain_terms="),
            ("_delegate: EN validation", "self.en_validator.validate(response)"),
            ("Master: EN validation", "EN Validator master"),
            ("Heartbeat: EN validation", "EN Validator: standardisoi heartbeat"),
            ("Dashboard: en_validator", '"en_validator"'),
            # V3 mega-patch (pitää olla)
            ("V3: TranslationProxy", "from translation_proxy import TranslationProxy"),
            ("V3: _translation_used", "_translation_used"),
            ("V3: _use_en_prompts", "_use_en_prompts"),
        ]:
            ok = marker in src
            print(f"    {'✅' if ok else '❌'} {name}")
            if not ok: all_ok = False

    print(f"\n  {'🟢 KAIKKI OK' if all_ok else '🔴 PUUTTEITA'}")
    return all_ok


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "verify":
        verify()
        sys.exit(0)

    print("🐝 WaggleDance EN Validator Patch v1.0")
    print("=" * 60)

    # Tarkista en_validator.py
    if not Path("en_validator.py").exists():
        print("❌ en_validator.py ei löydy! Kopioi se ensin project-kansioon.")
        sys.exit(1)

    success = patch_hivemind()

    if success:
        print("\n" + "=" * 60)
        verify()
        print("""
═══════════════════════════════════════════════════════════
  EN VALIDATOR INTEGROITU
═══════════════════════════════════════════════════════════

  Käynnistyksessä näkyy:
    ✅ Translation Proxy (Voikko=✅, Dict=412, Lang=auto)
    ✅ EN Validator (WordNet=✅/❌, Synonyms=107)

  WordNet-asennus (valinnainen, lisää lemmatisaation):
    pip install nltk
    python -c "import nltk; nltk.download('wordnet'); nltk.download('omw-1.4')"

  Putki nyt:
    [FI käyttäjä]
    FI viesti → FI→EN proxy (2ms) → EN Validator (0.1ms)
    → EN prompt → LLM → EN Validator (0.1ms) → EN→FI proxy (1ms)
    → FI vastaus

    [Heartbeat]
    Agent ajattelee EN → EN Validator (0.1ms) → SharedMemory
    → Standardisoidut termit muistissa

  Overhead: +0.2ms (merkityksetön)
""")
