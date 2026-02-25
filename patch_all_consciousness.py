"""
Lisää kaikki puuttuvat consciousness-osat hivemind.py:hin.
Turvallinen: tarkistaa jokaisen osan ennen lisäystä.
"""
import ast

src = open('hivemind.py', encoding='utf-8').read()
changes = 0

# ═══ 1. IMPORT ═══
if 'from consciousness import' not in src:
    # Lisää ennen "class HiveMind:" TAI ennen Consciousness(-kutsua
    # Etsi sopiva paikka importeista
    if '_CONSCIOUSNESS_AVAILABLE' not in src:
        # Etsi viimeinen try/except import -blokki
        marker = "class HiveMind:" if "class HiveMind:" in src else None
        if not marker:
            # Ehkä jo korvattu
            for line in src.split('\n'):
                if line.strip().startswith('class HiveMind'):
                    marker = line.strip()
                    break
        
        if marker and marker in src:
            src = src.replace(marker, 
                "try:\n"
                "    from consciousness import Consciousness\n"
                "    _CONSCIOUSNESS_AVAILABLE = True\n"
                "except ImportError:\n"
                "    _CONSCIOUSNESS_AVAILABLE = False\n"
                "\n" + marker, 1)
            changes += 1
            print("  OK [1] Import")
        else:
            print("  FAIL [1] class HiveMind ei loydy")
    else:
        # _CONSCIOUSNESS_AVAILABLE on jo, mutta import puuttuu
        # Lisää import ennen sitä
        src = src.replace('_CONSCIOUSNESS_AVAILABLE = True',
            'from consciousness import Consciousness\n    _CONSCIOUSNESS_AVAILABLE = True', 1)
        changes += 1
        print("  OK [1] Import (lisatty olemassaolevaan)")
else:
    print("  SKIP [1] Import")

# ═══ 2. PRE-FILTER — chat-funktiossa ennen LLM-kutsua ═══
if 'before_llm' not in src:
    # Etsi chat-funktion alku: async def _handle_chat tai vastaava
    # Etsitään kohta jossa message tulee sisään ja FI->EN alkaa
    
    # Strategia: etsi "fi_to_en" chat-kontekstissa ja lisää ennen sitä
    lines = src.split('\n')
    insert_idx = None
    
    for i, line in enumerate(lines):
        # Etsi ensimmäinen fi_to_en joka on chat-flowssa (ei heartbeat)
        if 'fi_to_en' in line and 'force_opus' in line:
            # Etsi tämän yläpuolelta sopiva kohta (kommentti tai tyhjä rivi)
            for j in range(i-1, max(i-15, 0), -1):
                stripped = lines[j].strip()
                if stripped.startswith('#') and ('FI' in stripped or 'käännös' in stripped.lower() or 'translate' in stripped.lower()):
                    insert_idx = j
                    break
                if stripped == '' and j < i-1:
                    insert_idx = j + 1
                    break
            if not insert_idx:
                insert_idx = i  # Lisää suoraan ennen fi_to_en-riviä
            break
    
    if insert_idx:
        # Tarkista indent
        ref_line = lines[insert_idx]
        indent = len(ref_line) - len(ref_line.lstrip()) if ref_line.strip() else 8
        ind = ' ' * indent
        
        prefilter = (
            f"{ind}# --- Tietoisuus: pre-filter ---\n"
            f"{ind}if self.consciousness:\n"
            f"{ind}    _pre = self.consciousness.before_llm(message)\n"
            f"{ind}    if _pre.handled:\n"
            f"{ind}        if self.monitor:\n"
            f"{ind}            await self.monitor.system(\n"
            f"{ind}                f\"🧠 {{_pre.method}}: {{_pre.answer[:80]}}\")\n"
            f"{ind}        await self._notify_ws(\"chat_response\", {{\n"
            f"{ind}            \"message\": message, \"response\": _pre.answer,\n"
            f"{ind}            \"language\": getattr(self, '_detected_lang', 'fi'),\n"
            f"{ind}            \"method\": _pre.method\n"
            f"{ind}        }})\n"
            f"{ind}        return _pre.answer\n"
            f"\n"
        )
        lines.insert(insert_idx, prefilter)
        src = '\n'.join(lines)
        changes += 1
        print("  OK [2] Pre-filter")
    else:
        print("  FAIL [2] fi_to_en ei loydy chat-flowssa")
else:
    print("  SKIP [2] Pre-filter")

# ═══ 3. HALLUSINAATIO + OPPIMINEN — vastauksen jälkeen ═══
if 'check_hallucination' not in src:
    # Etsi chat_response notify_ws ja lisää ennen sitä
    target = 'await self._notify_ws("chat_response"'
    idx = src.find(target)
    
    if idx > 0:
        # Etsi rivin alku
        line_start = src.rfind('\n', 0, idx) + 1
        ref = src[line_start:idx]
        indent = len(ref)
        ind = ' ' * indent
        
        hall_block = (
            f"{ind}# --- Tietoisuus: hallusinaatio + oppiminen ---\n"
            f"{ind}if self.consciousness:\n"
            f"{ind}    try:\n"
            f"{ind}        _hall = self.consciousness.check_hallucination(message, response)\n"
            f"{ind}        if _hall.is_suspicious and self.monitor:\n"
            f"{ind}            await self.monitor.system(f\"⚠️ Hallusinaatio? {{_hall.reason}}\")\n"
            f"{ind}        _quality = _hall.relevance if not _hall.is_suspicious else 0.3\n"
            f"{ind}        self.consciousness.learn_conversation(message, response, quality_score=_quality)\n"
            f"{ind}    except Exception:\n"
            f"{ind}        pass\n"
            f"\n"
        )
        src = src[:line_start] + hall_block + src[line_start:]
        changes += 1
        print("  OK [3] Hallusinaatio + oppiminen")
    else:
        print("  FAIL [3] chat_response notify ei loydy")
else:
    print("  SKIP [3] Hallusinaatio")

# ═══ 4. KONTEKSTI — injektoi muistikonteksti master promptiin ═══
if '_consciousness_context' not in src:
    # Etsi AGENT_EN_PROMPTS["hivemind"] chat-kontekstissa
    target = 'AGENT_EN_PROMPTS["hivemind"]'
    idx = src.find(target)
    
    if idx > 0:
        line_start = src.rfind('\n', 0, idx) + 1
        ref = src[line_start:idx]
        indent = len(ref)
        ind = ' ' * indent
        
        ctx_block = (
            f"{ind}# --- Tietoisuus: muistikonteksti ---\n"
            f"{ind}_consciousness_context = \"\"\n"
            f"{ind}if self.consciousness:\n"
            f"{ind}    _consciousness_context = self.consciousness.get_context(\n"
            f"{ind}        _en_message if self._translation_used else message)\n"
            f"\n"
        )
        src = src[:line_start] + ctx_block + src[line_start:]
        changes += 1
        print("  OK [4] Konteksti-injektio (hakulohko)")
    else:
        print("  FAIL [4] AGENT_EN_PROMPTS[hivemind] ei loydy")
else:
    print("  SKIP [4] Konteksti")

# ═══ 5. HEARTBEAT LEARNING ═══
if 'consciousness.learn' not in src:
    # Etsi heartbeat-insightin tallennuskohta
    # Etsitään "thought" tai "💭" heartbeat-loopissa
    target = 'agent_insight'
    idx = src.find(target)
    
    if idx > 0:
        # Etsi tämän blokin loppupuoli ja lisää oppiminen
        # Etsitään seuraava await tai tyhjä rivi
        next_empty = src.find('\n\n', idx)
        if next_empty > idx:
            indent = '                    '
            learn_block = (
                f"\n{indent}# Tietoisuus: tallenna heartbeat insight\n"
                f"{indent}if self.consciousness:\n"
                f"{indent}    try:\n"
                f"{indent}        _ht = thought if isinstance(thought, str) else str(thought)\n"
                f"{indent}        self.consciousness.learn(\n"
                f"{indent}            _ht, agent_id=getattr(agent, 'name', 'unknown'),\n"
                f"{indent}            source_type='heartbeat', confidence=0.5)\n"
                f"{indent}    except Exception:\n"
                f"{indent}        pass\n"
            )
            src = src[:next_empty] + learn_block + src[next_empty:]
            changes += 1
            print("  OK [5] Heartbeat learning")
        else:
            print("  FAIL [5] agent_insight blokin loppu ei loydy")
    else:
        print("  SKIP [5] agent_insight ei loydy (ei kriittinen)")
else:
    print("  SKIP [5] Heartbeat learning")

# ═══ TALLENNUS ═══
print(f"\n  Muutoksia: {changes}")
if changes > 0:
    try:
        ast.parse(src)
        open('hivemind.py', 'w', encoding='utf-8').write(src)
        print("  SAVED - syntax OK")
    except SyntaxError as e:
        print(f"  SYNTAX ERROR: {e}")
        open('hivemind_debug2.py', 'w', encoding='utf-8').write(src)
        print("  Debug: hivemind_debug2.py")

# ═══ VERIFY ═══
print("\n  --- Verify ---")
src2 = open('hivemind.py', encoding='utf-8').read()
for n, p in zip(
    ['Import', 'Init', 'Startup', 'Pre-filter', 'Context', 'Hallucination', 'HB learn'],
    ['from consciousness import', 'self.consciousness = None', 'Consciousness(', 
     'before_llm', '_consciousness_context', 'check_hallucination', 'consciousness.learn']
):
    print(('  OK   ' if p in src2 else '  MISS ') + n)
