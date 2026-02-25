"""
Täsmälliset consciousness-patchit hivemind.py:hin.
Käyttää tarkkoja str.replace-osumia riveiltä 486-590.
"""
import ast

src = open('hivemind.py', encoding='utf-8').read()
changes = 0

# ═══ 1. IMPORT ═══
if 'from consciousness import' not in src:
    old = '    _CONSCIOUSNESS_AVAILABLE = True'
    new = '    from consciousness import Consciousness\n    _CONSCIOUSNESS_AVAILABLE = True'
    if old in src:
        src = src.replace(old, new, 1)
        changes += 1
        print("  OK [1] Import")
    else:
        print("  FAIL [1]")
else:
    print("  SKIP [1]")

# ═══ 2. PRE-FILTER — ennen rivin 495 FI→EN käännöstä ═══
if 'before_llm' not in src:
    old = '        # ═══ FI→EN käännös'
    new = '''        # ═══ Tietoisuus: pre-filter ═══
        if self.consciousness:
            _pre = self.consciousness.before_llm(message)
            if _pre.handled:
                if self.monitor:
                    await self.monitor.system(
                        f"🧠 {_pre.method}: {_pre.answer[:80]}")
                await self._notify_ws("chat_response", {
                    "message": message, "response": _pre.answer,
                    "language": self._detected_lang,
                    "method": _pre.method
                })
                return _pre.answer

        # ═══ FI→EN käännös'''
    if old in src:
        src = src.replace(old, new, 1)
        changes += 1
        print("  OK [2] Pre-filter")
    else:
        print("  FAIL [2] FI->EN kommentti ei loydy")
else:
    print("  SKIP [2]")

# ═══ 3. KONTEKSTI — rivin 569 system_prompt asetuksen edelle ═══
if '_consciousness_context' not in src:
    old = '                self.master_agent.system_prompt = f"Date: {_dt.now():%Y-%m-%d %H:%M}. " + AGENT_EN_PROMPTS["hivemind"]'
    new = '''                # Tietoisuus: muistikonteksti
                _consciousness_context = ""
                if self.consciousness:
                    _ctx_q = _en_message if self._translation_used else message
                    _consciousness_context = self.consciousness.get_context(_ctx_q)
                    if _consciousness_context:
                        _consciousness_context = "\\n" + _consciousness_context
                self.master_agent.system_prompt = f"Date: {_dt.now():%Y-%m-%d %H:%M}. " + AGENT_EN_PROMPTS["hivemind"] + _consciousness_context'''
    if old in src:
        src = src.replace(old, new, 1)
        changes += 1
        print("  OK [3] Konteksti")
    else:
        print("  FAIL [3] system_prompt rivi ei loydy")
else:
    print("  SKIP [3]")

# ═══ 4. HALLUSINAATIO + OPPIMINEN — ennen notify_ws ═══
if 'check_hallucination' not in src:
    old = '''            await self._notify_ws("chat_response", {
                "message": message, "response": response,
                "language": self._detected_lang, "translated": self._translation_used
            })
            return response'''
    new = '''            # Tietoisuus: hallusinaatio + oppiminen
            if self.consciousness:
                try:
                    _hall = self.consciousness.check_hallucination(message, response)
                    if _hall.is_suspicious and self.monitor:
                        await self.monitor.system(f"⚠️ Hallusinaatio? {_hall.reason}")
                    _quality = _hall.relevance if not _hall.is_suspicious else 0.3
                    self.consciousness.learn_conversation(message, response, quality_score=_quality)
                except Exception:
                    pass

            await self._notify_ws("chat_response", {
                "message": message, "response": response,
                "language": self._detected_lang, "translated": self._translation_used
            })
            return response'''
    if old in src:
        src = src.replace(old, new, 1)
        changes += 1
        print("  OK [4] Hallusinaatio + oppiminen")
    else:
        print("  FAIL [4] notify_ws blokki ei loydy")
else:
    print("  SKIP [4]")

# ═══ TALLENNUS ═══
print(f"\n  Muutoksia: {changes}")
if changes > 0:
    try:
        ast.parse(src)
        open('hivemind.py', 'w', encoding='utf-8').write(src)
        print("  SAVED - syntax OK")
    except SyntaxError as e:
        print(f"  SYNTAX ERROR: {e}")
        open('hivemind_debug3.py', 'w', encoding='utf-8').write(src)
        print("  Debug: hivemind_debug3.py")

# ═══ VERIFY ═══
print("\n  --- Verify ---")
v = open('hivemind.py', encoding='utf-8').read()
for n, p in zip(
    ['Import', 'Init', 'Startup', 'Pre-filter', 'Context', 'Hallucination'],
    ['from consciousness import', 'self.consciousness = None', 'Consciousness(',
     'before_llm', '_consciousness_context', 'check_hallucination']
):
    print(('  OK   ' if p in v else '  MISS ') + n)
