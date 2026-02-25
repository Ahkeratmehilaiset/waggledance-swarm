"""
OpenClaw Dashboard Patcher
==========================
Ajaa kerran: python patch_dashboard.py
Lisää Oracle-paneelin, verkkohaun ja uudet endpointit dashboardiin.
"""

import re
import shutil
from pathlib import Path
from datetime import datetime


DASHBOARD_PATH = Path("web/dashboard.py")

# ── Uudet API-endpointit (korvaa vanhat oracle-reitit) ──────

NEW_ORACLE_ENDPOINTS = '''
    @app.post("/api/oracle/query")
    async def oracle_query(data: dict):
        """Hae verkosta Oraclen kautta."""
        question = data.get("question", "")
        if not question:
            return {"error": "Kysymys puuttuu"}
        oracle = None
        if hivemind.spawner:
            oracles = hivemind.spawner.get_agents_by_type("oracle")
            if oracles:
                oracle = oracles[0]
        if not oracle:
            return {"error": "OracleAgent ei aktiivinen. Spawna ensin!"}
        result = await oracle.search_and_learn(question)
        return {"success": True, "query": question, "result": result}

    @app.post("/api/oracle/search")
    async def oracle_search(data: dict):
        """Manuaalinen verkkohaku."""
        query = data.get("query", "")
        if not query:
            return {"error": "Hakusana puuttuu"}
        result = await hivemind.oracle_search(query)
        return {"success": True, "query": query, "result": result}

    @app.get("/api/oracle/questions")
    async def oracle_questions():
        """Hae Oracle-kysymykset Claudelle."""
        text = hivemind.oracle_get_questions()
        oracle = hivemind.get_oracle()
        pending = 0
        if oracle and hasattr(oracle, 'get_pending_questions'):
            pending = len(oracle.get_pending_questions())
        return {"text": text, "pending": pending}

    @app.post("/api/oracle/answer")
    async def oracle_answer(data: dict):
        """Pasteaa Clauden vastaus."""
        answer = data.get("answer", "")
        if not answer:
            return {"error": "Vastaus puuttuu"}
        result = await hivemind.oracle_receive_answer(answer)
        return result

    @app.post("/api/oracle/research")
    async def oracle_research(data: dict):
        """Syvä tutkimus aiheesta."""
        topic = data.get("topic", "")
        if not topic:
            return {"error": "Aihe puuttuu"}
        oracle = hivemind.get_oracle()
        if not oracle:
            return {"error": "OracleAgent ei aktiivinen"}
        result = await oracle.research_topic(topic, depth=2)
        return result

    @app.post("/api/oracle/pool")
    async def oracle_pool(data: dict):
        """Yhteensopivuus — ohjaa hakuun."""
        return {"info": "Käytä /api/oracle/search"}
'''

# ── Oracle HTML-paneeli ──────────────────────────────────────

ORACLE_HTML_PANEL = '''
                <!-- 🔮 Oracle Panel -->
                <div class="card" id="oracle-panel">
                    <h2>🔮 Oracle — Tiedonhankinta</h2>
                    <div style="margin-bottom:16px">
                        <h3 style="margin:0 0 8px 0;font-size:14px;color:#90caf9">🔍 Verkkohaku</h3>
                        <div style="display:flex;gap:8px">
                            <input type="text" id="oracle-search-input" placeholder="Hakusana..." style="flex:1;padding:8px 12px;background:#1a1a2e;border:1px solid #333;color:#eee;border-radius:6px;font-size:14px" onkeypress="if(event.key===\'Enter\')oracleSearch()">
                            <button onclick="oracleSearch()" style="padding:8px 16px;background:#7c4dff;color:white;border:none;border-radius:6px;cursor:pointer;font-size:14px">🔍 Hae</button>
                            <button onclick="oracleResearch()" style="padding:8px 16px;background:#00bfa5;color:white;border:none;border-radius:6px;cursor:pointer;font-size:14px" title="Syvä tutkimus">📊 Tutki</button>
                        </div>
                        <div id="oracle-search-result" style="margin-top:8px;padding:8px;background:#0d1117;border-radius:6px;font-size:13px;color:#aaa;display:none;max-height:200px;overflow-y:auto;white-space:pre-wrap"></div>
                    </div>
                    <div style="border-top:1px solid #333;padding-top:12px">
                        <h3 style="margin:0 0 8px 0;font-size:14px;color:#ffd54f">🔮 Claude-konsultaatio <span id="oracle-badge" style="background:#7c4dff;color:white;border-radius:12px;padding:2px 8px;font-size:11px;margin-left:8px;display:none">0</span></h3>
                        <div style="display:flex;gap:8px;margin-bottom:8px">
                            <button onclick="oracleLoadQuestions()" style="padding:6px 12px;background:#1a1a2e;border:1px solid #444;color:#eee;border-radius:6px;cursor:pointer;font-size:13px">📥 Lataa</button>
                            <button onclick="oracleCopyQuestions()" style="padding:6px 12px;background:#ff6f00;color:white;border:none;border-radius:6px;cursor:pointer;font-size:13px">📋 Kopioi Claudelle</button>
                        </div>
                        <textarea id="oracle-questions" style="width:100%;height:100px;background:#0d1117;border:1px solid #333;color:#aaa;border-radius:6px;padding:8px;font-size:12px;font-family:monospace;resize:vertical" placeholder="Kysymykset latautuvat tähän..." readonly></textarea>
                        <h4 style="margin:12px 0 6px 0;font-size:13px;color:#81c784">📝 Clauden vastaus:</h4>
                        <textarea id="oracle-answer" style="width:100%;height:80px;background:#0d1117;border:1px solid #333;color:#eee;border-radius:6px;padding:8px;font-size:12px;font-family:monospace;resize:vertical" placeholder="Kopioi Clauden vastaus tähän..."></textarea>
                        <button onclick="oracleSubmitAnswer()" style="margin-top:8px;padding:8px 20px;background:#4caf50;color:white;border:none;border-radius:6px;cursor:pointer;font-size:14px;width:100%">📤 Lähetä agenteille</button>
                    </div>
                    <div id="oracle-status" style="margin-top:8px;font-size:12px;color:#666"></div>
                </div>
'''

# ── Oracle JavaScript ────────────────────────────────────────

ORACLE_JS = '''
        async function oracleSearch() {
            const input = document.getElementById('oracle-search-input');
            const rd = document.getElementById('oracle-search-result');
            const q = input.value.trim(); if (!q) return;
            rd.style.display='block'; rd.textContent='🔍 Haetaan...'; rd.style.color='#ffd54f';
            try {
                const r = await fetch('/api/oracle/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q})});
                const d = await r.json();
                rd.textContent = d.success ? d.result : '❌ '+(d.error||'Virhe');
                rd.style.color = d.success ? '#81c784' : '#ef5350';
            } catch(e) { rd.textContent='❌ '+e.message; rd.style.color='#ef5350'; }
        }
        async function oracleResearch() {
            const input = document.getElementById('oracle-search-input');
            const rd = document.getElementById('oracle-search-result');
            const t = input.value.trim(); if (!t) return;
            rd.style.display='block'; rd.textContent='📊 Tutkitaan...'; rd.style.color='#ffd54f';
            try {
                const r = await fetch('/api/oracle/research',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({topic:t})});
                const d = await r.json();
                rd.textContent = d.success ? '📊 '+d.rounds+' hakua, '+d.total_results+' tulosta\\n\\n'+d.summary : '❌ '+(d.error||'Ei tuloksia');
                rd.style.color = d.success ? '#81c784' : '#ef5350';
            } catch(e) { rd.textContent='❌ '+e.message; rd.style.color='#ef5350'; }
        }
        async function oracleLoadQuestions() {
            try {
                const r = await fetch('/api/oracle/questions'); const d = await r.json();
                document.getElementById('oracle-questions').value = d.text||'Ei kysymyksiä';
                const b = document.getElementById('oracle-badge');
                if (d.pending>0) { b.style.display='inline'; b.textContent=d.pending+' odottaa'; }
                else { b.style.display='none'; }
            } catch(e) { document.getElementById('oracle-questions').value='Virhe: '+e.message; }
        }
        function oracleCopyQuestions() {
            navigator.clipboard.writeText(document.getElementById('oracle-questions').value).then(()=>{
                const s=document.getElementById('oracle-status'); s.textContent='✅ Kopioitu!'; s.style.color='#81c784';
            });
        }
        async function oracleSubmitAnswer() {
            const answer = document.getElementById('oracle-answer').value.trim(); if (!answer) return;
            const s = document.getElementById('oracle-status'); s.textContent='📤 Lähetetään...'; s.style.color='#ffd54f';
            try {
                const r = await fetch('/api/oracle/answer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answer})});
                const d = await r.json();
                if (d.success) { s.textContent='✅ Jaettu: '+d.distributed_to.join(', '); s.style.color='#81c784'; document.getElementById('oracle-answer').value=''; oracleLoadQuestions(); }
                else { s.textContent='❌ '+(d.error||'Virhe'); s.style.color='#ef5350'; }
            } catch(e) { s.textContent='❌ '+e.message; s.style.color='#ef5350'; }
        }
        setInterval(oracleLoadQuestions, 60000);
        setTimeout(oracleLoadQuestions, 3000);
'''


def patch_dashboard():
    """Patchaa dashboard.py automaattisesti."""
    
    if not DASHBOARD_PATH.exists():
        print(f"❌ {DASHBOARD_PATH} ei löydy! Aja skripti openclaw-kansiosta.")
        return False
    
    content = DASHBOARD_PATH.read_text(encoding="utf-8")
    
    # Backup
    backup_path = DASHBOARD_PATH.with_suffix(f".py.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(DASHBOARD_PATH, backup_path)
    print(f"📦 Backup: {backup_path}")
    
    changes = 0
    
    # ── 1. Korvaa vanhat Oracle-endpointit ──────────────────
    # Etsi vanha oracle/query endpoint ja korvaa oracle/pool loppuun asti
    old_oracle_pattern = r'    @app\.post\("/api/oracle/query"\).*?return await oracle\.pool_query\(agent_ids, question\)'
    
    match = re.search(old_oracle_pattern, content, re.DOTALL)
    if match:
        content = content[:match.start()] + NEW_ORACLE_ENDPOINTS + content[match.end():]
        changes += 1
        print("✅ Oracle-endpointit korvattu")
    else:
        print("⚠️  Vanhoja Oracle-endpointteja ei löytynyt — lisätään uudet")
        # Lisää ennen /api/monitor/history
        monitor_pos = content.find('@app.get("/api/monitor/history")')
        if monitor_pos > 0:
            content = content[:monitor_pos] + NEW_ORACLE_ENDPOINTS + "\n\n    " + content[monitor_pos:]
            changes += 1
            print("✅ Oracle-endpointit lisätty ennen monitor-endpointteja")
    
    # ── 2. Lisää Oracle HTML-paneeli ────────────────────────
    if 'id="oracle-panel"' not in content:
        # Etsi Token Economy -paneelin jälkeen
        token_panel_end = content.find('Token-talous</h2>')
        if token_panel_end > 0:
            # Etsi seuraava </div> joka sulkee cardin
            # Etsitään Token Economy -cardin sulkeva </div>
            # Turvallisempi: lisätään Oracle-paneelin HTML ennen Token Economy -panelia
            token_card_start = content.rfind('<div class="card">', 0, token_panel_end)
            if token_card_start > 0:
                content = content[:token_card_start] + ORACLE_HTML_PANEL + "\n\n" + content[token_card_start:]
                changes += 1
                print("✅ Oracle-paneeli lisätty dashboardiin")
            else:
                print("⚠️  Token-paneelin alkua ei löytynyt, lisätään HTML toiseen kohtaan")
                # Fallback: etsi </main> tai </body>
                main_end = content.find('</main>')
                if main_end < 0:
                    main_end = content.find('</body>')
                if main_end > 0:
                    content = content[:main_end] + ORACLE_HTML_PANEL + "\n" + content[main_end:]
                    changes += 1
                    print("✅ Oracle-paneeli lisätty (fallback)")
        else:
            print("⚠️  Token-paneelia ei löytynyt HTML:stä")
    else:
        print("ℹ️  Oracle-paneeli on jo dashboardissa")
    
    # ── 3. Lisää Oracle JavaScript ──────────────────────────
    if 'oracleSearch' not in content:
        # Etsi viimeinen </script> tai script-tagin loppu
        # Etsitään refreshAll-funktion jälkeen sopiva kohta
        refresh_pos = content.find('setInterval(refreshAll')
        if refresh_pos > 0:
            # Lisää Oracle JS ennen refreshAll setIntervalia
            content = content[:refresh_pos] + ORACLE_JS + "\n\n        " + content[refresh_pos:]
            changes += 1
            print("✅ Oracle JavaScript lisätty")
        else:
            # Fallback: etsi </script>
            last_script = content.rfind('</script>')
            if last_script > 0:
                content = content[:last_script] + "\n" + ORACLE_JS + "\n" + content[last_script:]
                changes += 1
                print("✅ Oracle JavaScript lisätty (fallback)")
    else:
        print("ℹ️  Oracle JavaScript on jo dashboardissa")
    
    # ── Tallenna ────────────────────────────────────────────
    if changes > 0:
        DASHBOARD_PATH.write_text(content, encoding="utf-8")
        print(f"\n🎉 Dashboard patchattu! ({changes} muutosta)")
        print(f"   Backup: {backup_path}")
        return True
    else:
        print("\nℹ️  Ei muutoksia tarvittu.")
        return False


if __name__ == "__main__":
    patch_dashboard()
