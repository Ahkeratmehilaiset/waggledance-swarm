"""
DASHBOARD PATCH - Oracle + Search + Knowledge
==============================================

OHJEET:
1. Korvaa dashboard.py rivit 105-140 (vanhat oracle endpointit) tällä koodilla
2. Lisää Oracle-paneelin HTML spawner-napin jälkeen
3. Lisää JS-funktiot script-tagiin

Alla on koodi osissa.
"""

# ══════════════════════════════════════════════════════════════
# OSA 1: API ENDPOINTIT
# Korvaa vanhat /api/oracle/* endpointit (rivit 105-140)
# ══════════════════════════════════════════════════════════════

ENDPOINTS_CODE = '''
    @app.post("/api/oracle/query")
    async def oracle_query(data: dict):
        """Agentti kysyy Oraakkelilta - haku verkosta."""
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

        # Hae verkosta
        result = await oracle.search_and_learn(question)
        return {"success": True, "query": question, "result": result}

    @app.post("/api/oracle/search")
    async def oracle_search(data: dict):
        """Manuaalinen verkkohaku dashboardista."""
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
        """Syvä tutkimus aiheesta (useita hakuja)."""
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
        """Usea agentti yhdistää tokenit."""
        return {"info": "Pool-toiminto päivitetty. Käytä /api/oracle/search."}
'''


# ══════════════════════════════════════════════════════════════
# OSA 2: HTML - Oracle-paneeli
# Lisää tämä dashboard HTML:ään, esim. Token Economy -paneelin jälkeen
# ══════════════════════════════════════════════════════════════

ORACLE_HTML = '''
                <!-- 🔮 Oracle Panel -->
                <div class="card" id="oracle-panel">
                    <h2>🔮 Oracle — Tiedonhankinta</h2>

                    <!-- Verkkohaku -->
                    <div style="margin-bottom:16px">
                        <h3 style="margin:0 0 8px 0;font-size:14px;color:#90caf9">🔍 Verkkohaku</h3>
                        <div style="display:flex;gap:8px">
                            <input type="text" id="oracle-search-input"
                                   placeholder="Hakusana..."
                                   style="flex:1;padding:8px 12px;background:#1a1a2e;border:1px solid #333;color:#eee;border-radius:6px;font-size:14px"
                                   onkeypress="if(event.key==='Enter')oracleSearch()">
                            <button onclick="oracleSearch()"
                                    style="padding:8px 16px;background:#7c4dff;color:white;border:none;border-radius:6px;cursor:pointer;font-size:14px">
                                🔍 Hae
                            </button>
                            <button onclick="oracleResearch()"
                                    style="padding:8px 16px;background:#00bfa5;color:white;border:none;border-radius:6px;cursor:pointer;font-size:14px"
                                    title="Syvä tutkimus (useita hakuja)">
                                📊 Tutki
                            </button>
                        </div>
                        <div id="oracle-search-result" style="margin-top:8px;padding:8px;background:#0d1117;border-radius:6px;font-size:13px;color:#aaa;display:none;max-height:200px;overflow-y:auto;white-space:pre-wrap"></div>
                    </div>

                    <!-- Claude-konsultaatio -->
                    <div style="border-top:1px solid #333;padding-top:12px">
                        <h3 style="margin:0 0 8px 0;font-size:14px;color:#ffd54f">
                            🔮 Claude-konsultaatio
                            <span id="oracle-badge" style="background:#7c4dff;color:white;border-radius:12px;padding:2px 8px;font-size:11px;margin-left:8px;display:none">0 odottaa</span>
                        </h3>

                        <div style="display:flex;gap:8px;margin-bottom:8px">
                            <button onclick="oracleLoadQuestions()"
                                    style="padding:6px 12px;background:#1a1a2e;border:1px solid #444;color:#eee;border-radius:6px;cursor:pointer;font-size:13px">
                                📥 Lataa kysymykset
                            </button>
                            <button onclick="oracleCopyQuestions()"
                                    style="padding:6px 12px;background:#ff6f00;color:white;border:none;border-radius:6px;cursor:pointer;font-size:13px">
                                📋 Kopioi Claudelle
                            </button>
                        </div>

                        <textarea id="oracle-questions"
                                  style="width:100%;height:120px;background:#0d1117;border:1px solid #333;color:#aaa;border-radius:6px;padding:8px;font-size:12px;font-family:monospace;resize:vertical"
                                  placeholder="Kysymykset latautuvat tähän..."
                                  readonly></textarea>

                        <h4 style="margin:12px 0 6px 0;font-size:13px;color:#81c784">📝 Pasteaa Clauden vastaus:</h4>
                        <textarea id="oracle-answer"
                                  style="width:100%;height:100px;background:#0d1117;border:1px solid #333;color:#eee;border-radius:6px;padding:8px;font-size:12px;font-family:monospace;resize:vertical"
                                  placeholder="Kopioi Clauden vastaus tähän..."></textarea>

                        <button onclick="oracleSubmitAnswer()"
                                style="margin-top:8px;padding:8px 20px;background:#4caf50;color:white;border:none;border-radius:6px;cursor:pointer;font-size:14px;width:100%">
                            📤 Lähetä vastaus agenteille
                        </button>
                    </div>

                    <div id="oracle-status" style="margin-top:8px;font-size:12px;color:#666"></div>
                </div>
'''


# ══════════════════════════════════════════════════════════════
# OSA 3: JavaScript-funktiot
# Lisää script-tagiin
# ══════════════════════════════════════════════════════════════

ORACLE_JS = '''
        // ── Oracle Functions ──────────────────────────────────

        async function oracleSearch() {
            const input = document.getElementById('oracle-search-input');
            const resultDiv = document.getElementById('oracle-search-result');
            const query = input.value.trim();
            if (!query) return;

            resultDiv.style.display = 'block';
            resultDiv.textContent = '🔍 Haetaan...';
            resultDiv.style.color = '#ffd54f';

            try {
                const resp = await fetch('/api/oracle/search', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({query})
                });
                const data = await resp.json();
                if (data.success) {
                    resultDiv.textContent = data.result;
                    resultDiv.style.color = '#81c784';
                } else {
                    resultDiv.textContent = '❌ ' + (data.error || 'Virhe');
                    resultDiv.style.color = '#ef5350';
                }
            } catch(e) {
                resultDiv.textContent = '❌ Yhteysvirhe: ' + e.message;
                resultDiv.style.color = '#ef5350';
            }
        }

        async function oracleResearch() {
            const input = document.getElementById('oracle-search-input');
            const resultDiv = document.getElementById('oracle-search-result');
            const topic = input.value.trim();
            if (!topic) return;

            resultDiv.style.display = 'block';
            resultDiv.textContent = '📊 Tutkitaan syvällisesti (useita hakuja)...';
            resultDiv.style.color = '#ffd54f';

            try {
                const resp = await fetch('/api/oracle/research', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({topic})
                });
                const data = await resp.json();
                if (data.success && data.summary) {
                    resultDiv.textContent = `📊 ${data.rounds} hakukierrosta, ${data.total_results} tulosta\\n\\n${data.summary}`;
                    resultDiv.style.color = '#81c784';
                } else {
                    resultDiv.textContent = '❌ ' + (data.error || 'Ei tuloksia');
                    resultDiv.style.color = '#ef5350';
                }
            } catch(e) {
                resultDiv.textContent = '❌ ' + e.message;
                resultDiv.style.color = '#ef5350';
            }
        }

        async function oracleLoadQuestions() {
            try {
                const resp = await fetch('/api/oracle/questions');
                const data = await resp.json();
                document.getElementById('oracle-questions').value = data.text || 'Ei kysymyksiä';
                const badge = document.getElementById('oracle-badge');
                if (data.pending > 0) {
                    badge.style.display = 'inline';
                    badge.textContent = data.pending + ' odottaa';
                } else {
                    badge.style.display = 'none';
                }
            } catch(e) {
                document.getElementById('oracle-questions').value = 'Virhe: ' + e.message;
            }
        }

        function oracleCopyQuestions() {
            const text = document.getElementById('oracle-questions').value;
            navigator.clipboard.writeText(text).then(() => {
                document.getElementById('oracle-status').textContent = '✅ Kopioitu leikepöydälle! Pasteaa Claudelle.';
                document.getElementById('oracle-status').style.color = '#81c784';
            });
        }

        async function oracleSubmitAnswer() {
            const answer = document.getElementById('oracle-answer').value.trim();
            if (!answer) return;

            const statusDiv = document.getElementById('oracle-status');
            statusDiv.textContent = '📤 Lähetetään...';
            statusDiv.style.color = '#ffd54f';

            try {
                const resp = await fetch('/api/oracle/answer', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({answer})
                });
                const data = await resp.json();
                if (data.success) {
                    statusDiv.textContent = `✅ Vastaus jaettu: ${data.distributed_to.join(', ')} (${data.questions_answered} kpl)`;
                    statusDiv.style.color = '#81c784';
                    document.getElementById('oracle-answer').value = '';
                    oracleLoadQuestions();
                } else {
                    statusDiv.textContent = '❌ ' + (data.error || 'Virhe');
                    statusDiv.style.color = '#ef5350';
                }
            } catch(e) {
                statusDiv.textContent = '❌ ' + e.message;
                statusDiv.style.color = '#ef5350';
            }
        }

        // Lataa Oracle-tilanne automaattisesti
        setInterval(oracleLoadQuestions, 60000);
        setTimeout(oracleLoadQuestions, 3000);
'''
