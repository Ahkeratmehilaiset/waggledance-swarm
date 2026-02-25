"""
Integroi consciousness.py v2 → hivemind.py

Muutokset:
1. Import + init (consciousness saa translation_proxy:n)
2. Chat pre-filter (math + muistihaku ENNEN LLM:ää)
3. Konteksti-injektio LLM:n promptiin
4. Hallusinaatiotarkistus + oppiminen vastauksen jälkeen
5. Dashboard-tilastot

Aja: python patch_consciousness_v2.py
"""
import ast

src = open('hivemind.py', encoding='utf-8').read()
changes = 0

# ═══════════════════════════════════════════════════════════════
# 1. IMPORT
# ═══════════════════════════════════════════════════════════════

if 'from consciousness import' not in src:
    old = "class HiveMind:"
    new = """# ═══ Tietoisuuskerros v2 ═══
try:
    from consciousness import Consciousness
    _CONSCIOUSNESS_AVAILABLE = True
except ImportError:
    _CONSCIOUSNESS_AVAILABLE = False

class HiveMind:"""
    if old in src:
        src = src.replace(old, new, 1)
        changes += 1
        print("  ✅ [1] Import")
    else:
        print("  ❌ [1] 'class HiveMind:' ei löydy")
else:
    print("  ⏭️  [1] Import jo olemassa")

# ═══════════════════════════════════════════════════════════════
# 2. INIT — self.consciousness attribuutti
# ═══════════════════════════════════════════════════════════════

if 'self.consciousness' not in src:
    old = "        self.translation_proxy = None"
    new = """        self.translation_proxy = None
        self.consciousness = None"""
    if old in src:
        src = src.replace(old, new, 1)
        changes += 1
        print("  ✅ [2] Init attribuutti")
    else:
        print("  ❌ [2] translation_proxy-riviä ei löydy")
else:
    print("  ⏭️  [2] Init jo olemassa")

# ═══════════════════════════════════════════════════════════════
# 3. STARTUP — Consciousness alustus Translation Proxy:n JÄLKEEN
# ═══════════════════════════════════════════════════════════════

if 'Consciousness(' not in src:
    # Etsi EN Validator -rivi ja lisää sen jälkeen
    lines = src.split('\n')
    insert_idx = None
    for i, line in enumerate(lines):
        if 'EN Validator' in line and 'print' in line:
            insert_idx = i + 1
            break

    if insert_idx:
        block = '''
        # ── Tietoisuuskerros v2 ──
        if _CONSCIOUSNESS_AVAILABLE:
            try:
                _ollama_url = self.config.get("ollama", {}).get("base_url", "http://localhost:11434")
                self.consciousness = Consciousness(
                    db_path="data/chroma_db",
                    ollama_url=_ollama_url,
                    translation_proxy=self.translation_proxy
                )
                print(f"  ✅ Tietoisuus v2 (muisti={self.consciousness.memory.count}, "
                      f"embed={self.consciousness.embed.available})")
            except Exception as e:
                print(f"  ⚠️  Tietoisuus: {e}")
                self.consciousness = None'''
        lines.insert(insert_idx, block)
        src = '\n'.join(lines)
        changes += 1
        print("  ✅ [3] Startup init")
    else:
        print("  ❌ [3] EN Validator -print-riviä ei löydy")
else:
    print("  ⏭️  [3] Startup jo olemassa")

# ═══════════════════════════════════════════════════════════════
# 4. CHAT PRE-FILTER — ENNEN FI→EN käännöstä
# ═══════════════════════════════════════════════════════════════

if 'before_llm' not in src:
    # Etsi chat-funktion FI→EN käännösosio
    # Haetaan "# ═══ FI→EN" tai vastaava kommentti
    search_targets = [
        "        # ═══ FI→EN käännös",
        "        # ═══ FI→EN",
        "        # FI→EN käännös",
    ]
    
    found = False
    for target in search_targets:
        if target in src:
            new_block = f"""        # ═══ Tietoisuus: pre-filter ═══
        if self.consciousness:
            _pre = self.consciousness.before_llm(message)
            if _pre.handled:
                if self.monitor:
                    await self.monitor.system(
                        f"🧠 {{_pre.method}}: {{_pre.answer[:80]}}")
                await self._notify_ws("chat_response", {{
                    "message": message, "response": _pre.answer,
                    "language": self._detected_lang,
                    "method": _pre.method
                }})
                return _pre.answer

{target}"""
            src = src.replace(target, new_block, 1)
            changes += 1
            found = True
            print("  ✅ [4] Chat pre-filter")
            break
    
    if not found:
        print("  ⚠️  [4] FI→EN kommenttia ei löydy — manuaalinen lisäys tarvitaan")
else:
    print("  ⏭️  [4] Pre-filter jo olemassa")

# ═══════════════════════════════════════════════════════════════
# 5. KONTEKSTI-INJEKTIO — lisää muistikonteksti chat-promptiin
# ═══════════════════════════════════════════════════════════════

if '_consciousness_context' not in src:
    # Etsi kohta jossa _en_message on valmis ja prompt rakennetaan
    # Haetaan master-kutsun system prompt -osio
    lines = src.split('\n')
    found_ctx = False
    
    for i, line in enumerate(lines):
        # Etsi kohta jossa AGENT_EN_PROMPTS["hivemind"] asetetaan chatissa
        if 'AGENT_EN_PROMPTS["hivemind"]' in line and 'system_prompt' in line:
            # Lisää konteksti tähän promptiin
            indent = '                '
            ctx_block = f"""
{indent}# Tietoisuus: muistikonteksti
{indent}_consciousness_context = ""
{indent}if self.consciousness:
{indent}    _ctx_msg = _en_message if self._translation_used else message
{indent}    _consciousness_context = self.consciousness.get_context(_ctx_msg)
{indent}    if _consciousness_context:
{indent}        _consciousness_context = "\\n" + _consciousness_context + "\\n"
"""
            lines.insert(i, ctx_block)
            
            # Nyt pitää injektoida konteksti promptiin
            # Etsitään sama rivi uudelleen (siirtynyt eteenpäin)
            for j in range(i+1, min(i+20, len(lines))):
                if 'AGENT_EN_PROMPTS["hivemind"]' in lines[j] and 'system_prompt' in lines[j]:
                    # Lisää + _consciousness_context promptin perään
                    if '_consciousness_context' not in lines[j]:
                        lines[j] = lines[j].rstrip()
                        # Etsi rivin loppu ennen mahdollista lainausmerkkiä
                        if lines[j].endswith('"'):
                            lines[j] = lines[j][:-1] + '" + _consciousness_context'
                        else:
                            lines[j] = lines[j] + ' + _consciousness_context'
                    break
            
            src = '\n'.join(lines)
            changes += 1
            found_ctx = True
            print("  ✅ [5] Konteksti-injektio")
            break
    
    if not found_ctx:
        print("  ⚠️  [5] Konteksti-injektiota ei voitu lisätä automaattisesti")
else:
    print("  ⏭️  [5] Konteksti jo olemassa")

# ═══════════════════════════════════════════════════════════════
# 6. HALLUSINAATIOFILTERI + OPPIMINEN — vastauksen jälkeen
# ═══════════════════════════════════════════════════════════════

if 'check_hallucination' not in src:
    # Etsi chat_response notify_ws -kutsu
    # Tämä on vastauksen loppuosa jossa palautetaan response
    search_patterns = [
        # Pattern 1: tyypillinen notify + return
        '''            await self._notify_ws("chat_response", {
                "message": message, "response": response,
                "language": self._detected_lang, "translated": self._translation_used
            })
            return response''',
        # Pattern 2: lyhyempi versio
        '''            await self._notify_ws("chat_response",''',
    ]
    
    found_hall = False
    for pattern in search_patterns:
        if pattern in src:
            if pattern == search_patterns[0]:
                new_block = '''            # ═══ Tietoisuus: hallusinaatio + oppiminen ═══
            if self.consciousness:
                _hall = self.consciousness.check_hallucination(message, response)
                if _hall.is_suspicious and self.monitor:
                    await self.monitor.system(
                        f"⚠️ Hallusinaatio? {_hall.reason}")
                _quality = _hall.relevance if not _hall.is_suspicious else 0.3
                self.consciousness.learn_conversation(
                    message, response, quality_score=_quality)

            await self._notify_ws("chat_response", {
                "message": message, "response": response,
                "language": self._detected_lang, "translated": self._translation_used
            })
            return response'''
                src = src.replace(pattern, new_block, 1)
                changes += 1
                found_hall = True
                print("  ✅ [6] Hallusinaatio + oppiminen")
                break
    
    if not found_hall:
        print("  ⚠️  [6] chat_response -blokkia ei löydy täsmälleen")
else:
    print("  ⏭️  [6] Hallusinaatio jo olemassa")

# ═══════════════════════════════════════════════════════════════
# 7. HEARTBEAT LEARNING — insightit muistiin
# ═══════════════════════════════════════════════════════════════

if 'consciousness' not in src[src.find('agent_insight'):src.find('agent_insight')+3000] if 'agent_insight' in src else True:
    # Etsi agent_insight -kohta heartbeatissa
    if 'agent_insight' in src:
        idx = src.find('"type": "agent_insight"')
        if idx > 0:
            # Etsi tämän jälkeen seuraava rivi jossa on await tai muu toiminto
            next_lines = src[idx:idx+500].split('\n')
            # Lisää oppiminen insight-blokin sisälle
            # Etsitään sopiva paikka
            block_area = src[idx-200:idx+500]
            
            # Yksinkertaisempi: etsi "insight" muuttujan nimi ja lisää sen jälkeen
            # Etsitään _insight tai insight muuttuja
            insight_var_patterns = ['_insight_text', '_insight', 'insight_text', 'thought']
            
            # Lisätään oppiminen yksinkertaisesti: etsi notify_ws("agent_insight"
            notify_idx = src.find('await self._notify_ws("agent_insight"', idx-200)
            if notify_idx < 0:
                notify_idx = src.find('"type": "agent_insight"', idx)
            
            if notify_idx > 0:
                # Etsi rivin loppu
                line_end = src.find('\n', notify_idx)
                # Etsi seuraava rivin loppu (notify voi olla monta riviä)
                # Etsitään seuraava ")" joka päättää notify-kutsun
                paren_depth = 0
                scan_pos = notify_idx
                while scan_pos < len(src):
                    if src[scan_pos] == '(':
                        paren_depth += 1
                    elif src[scan_pos] == ')':
                        paren_depth -= 1
                        if paren_depth <= 0:
                            break
                    scan_pos += 1
                
                # Etsi rivin loppu tämän jälkeen
                insert_pos = src.find('\n', scan_pos)
                if insert_pos > 0:
                    # Tarkista indent
                    next_line_start = insert_pos + 1
                    remaining = src[next_line_start:next_line_start+80]
                    indent = len(remaining) - len(remaining.lstrip())
                    ind = ' ' * indent

                    learn_code = f"""
{ind}# Tietoisuus: tallenna heartbeat insight
{ind}if self.consciousness:
{ind}    try:
{ind}        _ht = thought if isinstance(thought, str) else str(thought)
{ind}        self.consciousness.learn(
{ind}            _ht, agent_id=getattr(agent, 'name', 'unknown'),
{ind}            source_type="heartbeat", confidence=0.5)
{ind}    except Exception:
{ind}        pass"""
                    src = src[:insert_pos] + learn_code + src[insert_pos:]
                    changes += 1
                    print("  ✅ [7] Heartbeat learning")
                else:
                    print("  ❌ [7] Insert position ei löydy")
            else:
                print("  ❌ [7] notify agent_insight ei löydy")
        else:
            print("  ❌ [7] agent_insight string ei löydy")
    else:
        print("  ⚠️  [7] agent_insight ei löydy hivemind.py:stä")
else:
    print("  ⏭️  [7] Heartbeat learning jo olemassa")

# ═══════════════════════════════════════════════════════════════
# 8. DASHBOARD — consciousness stats API:iin
# ═══════════════════════════════════════════════════════════════

if 'consciousness' not in src[src.find('/api/status'):src.find('/api/status')+500] if '/api/status' in src else True:
    # Etsi /api/status endpoint ja lisää consciousness stats
    if '/api/status' in src:
        # Etsi return/response -kohta statuksessa
        status_idx = src.find('/api/status')
        return_area = src[status_idx:status_idx+1000]
        
        # Etsitään "token_economy" tai vastaava kenttä ja lisätään consciousness
        if 'token_economy' in return_area:
            # Etsi tarkka kohta
            te_idx = src.find('"token_economy"', status_idx)
            if te_idx > 0:
                line_end = src.find('\n', te_idx)
                indent = '                '
                consciousness_stats = f'\n{indent}"consciousness": self.consciousness.stats if self.consciousness else {{}},\n'
                # Lisää ennen token_economy:a  
                src = src[:te_idx] + f'"consciousness": self.consciousness.stats if self.consciousness else {{}},\n{indent}' + src[te_idx:]
                changes += 1
                print("  ✅ [8] Dashboard stats")
            else:
                print("  ⚠️  [8] token_economy ei löydy status-endpointista")
        else:
            print("  ⚠️  [8] Status endpoint: token_economy ei löydy")
    else:
        print("  ⚠️  [8] /api/status endpoint ei löydy")
else:
    print("  ⏭️  [8] Dashboard stats jo olemassa")

# ═══════════════════════════════════════════════════════════════
# TALLENNUS
# ═══════════════════════════════════════════════════════════════

print(f"\n  Muutoksia: {changes}")

if changes > 0:
    try:
        ast.parse(src)
        open('hivemind.py', 'w', encoding='utf-8').write(src)
        print("  ✅ Tallennettu, syntax OK")
    except SyntaxError as e:
        print(f"  ❌ SYNTAX ERROR: {e}")
        # Tallenna debug-versio
        open('hivemind_debug.py', 'w', encoding='utf-8').write(src)
        print(f"  Debug versio: hivemind_debug.py")
else:
    print("  ⚠️  Ei muutoksia")
