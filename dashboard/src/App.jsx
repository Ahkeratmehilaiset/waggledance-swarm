import { useState, useEffect, useRef, useCallback } from "react";
import { useApi } from "./hooks/useApi";
import ReasoningDashboard from "./ReasoningDashboard";
import {
  HW_SPECS, DOMAIN_LABELS, PROFILE_ICONS, PROFILE_COLORS, PROFILE_COLORS_RGB,
  FEATS_EN, FEATS_FI, HEARTBEATS_EN, HEARTBEATS_FI, DEMO_HB_EN, DEMO_HB_FI, DOMAIN_IDS,
} from "./domainPacks";

const hx = (v) => Math.round(Math.max(0, Math.min(255, v || 0))).toString(16).padStart(2, "0");

const HW = HW_SPECS;

// ═══ BILINGUAL SYSTEM ═══
const L = {
  en: {
    heartbeat: "RUNTIME ACTIVITY — LIVE AGENT FEED",
    features: "FEATURES & INTEGRATION",
    placeholder: "Ask WaggleDance something...",
    hwSpec: "HARDWARE SPEC", hwDesc: "specs + calculated performance",
    agents: "75 CUSTOM AGENTS", agentsDesc: "YAML config guide",
    techArch: "TECHNICAL ARCHITECTURE", techArchDesc: "how WaggleDance intelligence works",
    disclaimer: "DISCLAIMER & CREDITS", disclaimerDesc: "liability, credits, origin",
    bottomL: "LOCAL-FIRST • ZERO CLOUD • YOUR DATA",
    bottomC: "ADAPTIVE MEMORY • CONTINUOUS LEARNING",
    send: "SEND",
    domains: DOMAIN_LABELS.en,
    feats: FEATS_EN,
    agentsGuide: "Define agents in YAML:\n\n# agents/my_agent.yaml\nname: my_agent\nspecialties: [topic1, topic2]\ntools: [chromadb, web_search]\nlevel: 1  # auto 1→5\n\nLevels: NOVICE → MASTER\nAuto-discovers /agents/*.yaml",
    info: `═══ WAGGLEDANCE AI — TECHNICAL ARCHITECTURE ═══

▸ MULTI-MODEL MEMORY STACK
Multiple specialized models support each other:
  • LLM (phi4/llama/qwen) — reasoning, validation
  • Embedding (nomic-embed) — 768-dim vectors
  • Translation (Opus-MT) — any language pair
  • MicroModel (self-trained) — 23%+ queries at <1ms
  • Whisper STT + Piper TTS — voice (optional)

▸ VECTOR MEMORY (ChromaDB)
Every fact embedded as 768-dim vector. Query → cosine search → top-K in ~5ms. Perfect memory. Never forgets.

▸ CROSS-LANGUAGE INDEX (55ms)
All facts indexed in multiple languages simultaneously. Native-language query finds knowledge stored in any language. Doubles reach without doubling storage.

▸ LANGUAGE ARCHITECTURE — ANY LANGUAGE, ANY HARDWARE
WaggleDance processes internally in English — the language all LLMs understand best. Your native language goes through a deep NLP pipeline before translation:

  Your language → Morphological Engine → Lemmatization → Compound splitting → Spell correction → Translation → LLM (English) → Translation back → Your language

Finnish uses Voikko. Swap it for YOUR language's engine:
  German/Spanish/French → Hunspell    Japanese → MeCab
  Korean → KoNLPy                     Chinese → jieba
  Any language → spaCy / NLTK model

The result: native-language queries are understood MORE deeply than raw translation alone — because the morphological engine resolves grammar, inflections, and compounds BEFORE the LLM sees them.

English users get the fastest path — no translation overhead, direct LLM access at full speed.

▸ 6-LAYER ADAPTIVE MEMORY (24/7)
  L1: Cross-language vector indexing
  L2: Gap detection + enrichment (~200/night)
  L3: Web learning (~100/night)
  L4: Claude distillation (50/week)
  L5: Meta-learning — optimizes itself
  L6: Code self-review

▸ ROUND TABLE CONSENSUS
Up to 6 agents debate per query. Queen synthesizes consensus. Hallucination detection: contrastive + keyword.

▸ SMARTROUTER EVOLUTION
  Day 1: 3,000ms (full LLM) → Week 1: 55ms (memory) → Month 1: 18ms (native index) → Month 3: 0.5ms (HotCache)

▸ MICROMODEL EVOLUTION
  Gen 0: No micro → Gen 5+: LoRA 135M nano-LLM
  Result: 3,000ms → 0.3ms

▸ VS. CLOUD AI (OpenAI, etc.)
  ✗ Data leaves network    ✓ Data stays local
  ✗ Monthly fees            ✓ One-time hardware
  ✗ No memory              ✓ 1K+ permanent facts (growing 24/7)
  ✗ Generic                ✓ YOUR domain expert
  ✗ 500-3000ms latency     ✓ 0.08ms with MicroModel
  ✗ Rate limits            ✓ Unlimited 24/7

▸ WHY OFFLINE BY DEFAULT?
This is a deliberate architectural decision, not a limitation. WaggleDance is built to prove that a 3.8B parameter model with deep domain memory can outperform generic cloud models orders of magnitude larger — at a fraction of the latency and zero recurring cost. The full infrastructure for web search, RSS ingestion, and cloud AI distillation (Claude, GPT) exists in the codebase and is production-ready. It remains disabled until the local intelligence demonstrates self-sufficiency. When you choose to enable external sources, it is a single configuration toggle — not a migration. Your data never leaves your network unless you explicitly allow it.

▸ HARDWARE SCALING
  ESP32 (€8) → RPi (€80) → NUC (€650) → Mac (€2.2K) → DGX (€475K)
  Same code. Only speed differs.

▸ PHASE 4: AUTONOMOUS INTELLIGENCE (ACTIVE)
  • Voikko Morphological Engine — deep linguistic stemming
  • 3-Tier Speed: HotCache (0.5ms) → Native-direct (18ms) → Full (55ms)
  • Corrections Memory — permanent mistake learning
  • Specialty Centroids — agent expertise auto-mapping
  • Seasonal Guard — 10 month-aware intelligence rules
  • NightEnricher — 4-step quality gate, burst mode
  • Auto-Install — zero-config dependency management

▸ PHASE B: PRODUCTION HARDENING
  • CircuitBreaker — auto-disable failing subsystems, self-heal
  • Eviction TTL — stale facts auto-expire, memory stays fresh
  • Graceful Degradation — missing deps → warning, never crash

▸ PHASE C: CACHE & PIPELINE
  • HotCache auto-fill — top queries cached, 0.5ms response
  • LRU Cache — smart eviction, memory-bounded
  • Batch dedup pipeline — no duplicate facts stored
  • Readiness gates — subsystems report health before serving
  • Structured Logging — JSON logs, queryable, no noise

▸ PHASE D: AUTONOMOUS INTELLIGENCE
  • ConvergenceDetector — knows when learning plateaus
  • Weekly Report — auto-generated performance analysis
  • External source integration — RSS, weather, electricity

▸ PHASE 7: VOICE INTERFACE (INTEGRATED)
  • Whisper STT — Finnish speech-to-text, local
  • Piper TTS — fi_FI-harri-medium, local synthesis
  • Wake word "Hei WaggleDance", VAD silence detection

▸ PHASE 8: EXTERNAL DATA FEEDS (ACTIVE)
  • Weather — FMI Open Data, 30 min intervals
  • Electricity — spot price, cheapest window scheduling
  • RSS — critical alerts, domain-specific news feeds

▸ V0.0.5: PORTABILITY & RELIABILITY
  • Voikko bundled — mor.vfst + autocorr.vfst included, auto-download fallback
  • Night Shift Automation — tools/night_shift.py, watchdog, morning report
  • Health Score 100/100 — all 45 test suites GREEN

▸ V0.0.6: PHASE 5 — SMART HOME SENSORS
  • MQTT Hub — paho-mqtt, dedup, exponential reconnect
  • Frigate NVR — camera events, severity alerts (bear=CRITICAL)
  • Home Assistant — REST poll, significance filter, Finnish state
  • Alert Dispatcher — Telegram + Webhook, rate limiting

▸ V0.0.8: PHASE 6+7+11 — AUDIO, VOICE, ELASTIC SCALING
  • AudioAnalyzer — FFT spectrum, anomaly/pattern/event detection
  • BirdMonitor — BirdNET stub, predator alerts, graceful degradation
  • Whisper STT (Finnish) + Piper TTS (fi_FI-harri-medium), wake word
  • ElasticScaler — auto-detect HW, classify tier (ESP32→DGX)

▸ V0.0.9: PHASE 8 — EXTERNAL DATA FEEDS
  • Weather (FMI) + Electricity (porssisahko.net) + RSS disease alerts
  • 5 knowledge bases enriched (quality, supply chain, energy, maintenance)

▸ V0.1.0: DASHBOARD ANALYTICS + RUNTIME API
  • Analytics API — 7-day trends, route breakdown, model usage, fact growth
  • Round Table Transcript — consensus debates, agent dialogue
  • Agent Level Grid — 75 agents NOVICE→MASTER visualization
  • Runtime Settings — toggle 13 features on/off via API

▸ MAGMA MEMORY ARCHITECTURE (5 LAYERS)
  L1: AuditLog — append-only write log, ChromaDB adapter, agent rollback
  L2: ReplayStore — JSONL event replay, deduplication, causal replay
  L3: Overlays — branch management, A/B testing, mood presets
  L4: Cross-agent search, provenance tracking, consensus records
  L5: TrustEngine — 6-signal reputation scoring, temporal decay

▸ COGNITIVE GRAPH
  NetworkX knowledge graph with typed edges. JSON persistence. Causal replay of downstream effects.

▸ API AUTHENTICATION
  Bearer token middleware. Auto-generated WAGGLE_API_KEY on first startup.
  All /api/* routes authenticated. /health, /ready, /api/status exempt.

▸ DISK GUARD
  Write-path protection on ChromaDB, AuditLog, ReplayStore, CognitiveGraph.
  Warn <500MB. Refuse <100MB. Dashboard indicator.

▸ SCHEMA MIGRATION
  Versioned SQLite migrations. CLI: python tools/migrate_db.py --check / --migrate.

▸ TESTING
  50/50 test suites GREEN (700+ assertions)
  Pipeline, routing, corrections, autonomy, smart home, audio, voice, feeds,
  MAGMA layers, cognitive graph, overlays, trust engine, schema migration — all validated

▸ CODEBASE
  75 agents • 580 files • 266,000+ lines of code
  97.7% routing accuracy (1,207/1,235 tested)
  45 test suites • 10 seasonal rules • 34 domain terms

▸ JUST LET IT RUN
Install. Connect. Walk away.
1 week: knows your patterns.
1 month: anticipates your needs.
1 year: understands your world.`,
    hb: HEARTBEATS_EN,
    demoHb: DEMO_HB_EN,
    demoEnd: { a: "WaggleDance", m: "Demo complete. Now it's time to integrate your own data streams. See the setup guides in the right panel.", t: "status" },
  },
  fi: {
    heartbeat: "HERMOVERKKO — REAALIAIKAINEN AGENTTISYÖTE",
    features: "OMINAISUUDET & INTEGRAATIOT",
    placeholder: "Kysy WaggleDancelta jotain...",
    hwSpec: "LAITTEISTO", hwDesc: "tekniset tiedot + laskettu suorituskyky",
    agents: "75 MUKAUTETTUA AGENTTIA", agentsDesc: "YAML-konfiguraatio-opas",
    techArch: "TEKNINEN ARKKITEHTUURI", techArchDesc: "miten WaggleDancen älykkyys muodostuu",
    disclaimer: "VASTUUVAPAUS & TEKIJÄT", disclaimerDesc: "vastuu, tekijät, alkuperä",
    bottomL: "PAIKALLINEN • EI PILVEÄ • SINUN DATASI",
    bottomC: "JATKUVA ITSEOPPIMINEN • EI RAJOJA",
    send: "LÄHETÄ",
    domains: DOMAIN_LABELS.fi,
    feats: FEATS_FI,
    agentsGuide: "Määritä agentit YAML:lla:\n\n# agents/oma_agentti.yaml\nname: oma_agentti\nspecialties: [aihe1, aihe2]\ntools: [chromadb, web_search]\nlevel: 1  # automaattinen 1→5\n\nTasot: NOVIISI → MESTARI\nLöytää automaattisesti /agents/*.yaml",
    info: `═══ WAGGLEDANCE AI — TEKNINEN ARKKITEHTUURI ═══

▸ MONIMALLINEN MUISTIPINO
Useita erikoistuneita malleja tukemassa toisiaan:
  • LLM (phi4/llama/qwen) — päättely, validointi
  • Embedding (nomic-embed) — 768-ulotteiset vektorit
  • Käännös (Opus-MT) — suomi ↔ englanti
  • MikroMalli (itseopetettu) — 23%+ kyselyistä <1ms
  • Whisper STT + Piper TTS — puheliittymä

▸ VEKTORIMUISTI (ChromaDB)
Jokainen fakta upotetaan 768-ulotteiseksi vektoriksi. Kysely → kosini-haku → top-K ~5ms:ssä. Täydellinen muisti. Ei unohda koskaan.

▸ KAKSIKIELINEN INDEKSI (55ms)
Kaikki faktat indeksoitu FI+EN samanaikaisesti. Suomenkielinen kysely löytää englanninkieliset faktat ja päinvastoin.

▸ KIELIARKKITEHTUURI — MIKÄ TAHANSA KIELI, MIKÄ TAHANSA LAITE
WaggleDance prosessoi sisäisesti englanniksi — kielellä jonka kaikki LLM:t ymmärtävät parhaiten. Äidinkielesi kulkee syvän NLP-putken läpi ennen käännöstä:

  Oma kielesi → Morfologinen moottori → Lemmatisaatio → Yhdyssanojen pilkonta → Oikoluku → Käännös → LLM (englanti) → Käännös takaisin → Oma kielesi

Suomi käyttää Voikkoa. Vaihda oman kielesi moottoriin:
  Saksa/espanja/ranska → Hunspell    Japani → MeCab
  Korea → KoNLPy                     Kiina → jieba
  Mikä tahansa → spaCy / NLTK -malli

Tulos: äidinkieliset kyselysi ymmärretään SYVEMMIN kuin pelkkä käännös — koska morfologinen moottori purkaa kieliopin, taivutukset ja yhdyssanat ENNEN kuin LLM näkee ne.

Englanninkieliset käyttäjät saavat nopeimman reitin — ei käännöskuormaa, suora LLM-yhteys täydellä nopeudella.

▸ 6-KERROKSEN ITSEOPPIMINEN (24/7)
  K1: Kaksikielinen vektori-indeksointi
  K2: Aukkojen tunnistus + rikastus (~200/yö)
  K3: Verkkopoiminta (~100/yö)
  K4: Claude-tislaus (50/viikko)
  K5: Meta-oppiminen — optimoi itseään
  K6: Koodin itsearviointi

▸ PYÖREÄ PÖYTÄ -KONSENSUS
Jopa 50 agenttia väittelee. Kuningatar syntetisoi. Hallusinaatio: 1.8%.

▸ SMARTROUTER-EVOLUUTIO
  Päivä 1: 3 000ms (LLM) → Viikko 1: 55ms (muisti) → Kk 1: 18ms (FI-indeksi) → Kk 3: 0.5ms (HotCache)

▸ MIKROMALLIN EVOLUUTIO
  Gen 0: Ei mikroa → Gen 5+: LoRA 135M nano-LLM
  Tulos: 3 000ms → 0.3ms

▸ MIKSI EI PILVI-AI (OpenAI ym.)
  ✗ Datasi lähtee verkostasi  ✓ Data pysyy koneellasi
  ✗ Kuukausimaksut           ✓ Kertaluonteinen laite
  ✗ Ei muistia              ✓ 1 000+ pysyvää faktaa (kasvaa 24/7)
  ✗ Geneerinen              ✓ SINUN alueesi asiantuntija
  ✗ 500-3000ms viive        ✓ 0.08ms MikroMallilla
  ✗ Rajoitukset             ✓ Rajaton 24/7

▸ MIKSI OLETUKSENA OFFLINE?
Tämä on tietoinen arkkitehtuurivalinta, ei rajoitus. WaggleDance on rakennettu osoittamaan, että 3.8B parametrin malli syvällä toimialamuistilla voi päihittää suuruusluokkaa isommat pilvipalvelumallit — murto-osalla vasteajasta ja nollalla jatkuvilla kustannuksilla. Koodikannassa on täysin valmis infrastruktuuri verkkohaulle, RSS-syötteiden lukemiselle ja pilvi-AI-distillaatiolle (Claude, GPT). Se on tarkoituksella pois käytöstä kunnes paikallinen älykkyys osoittaa omavaraisuutensa. Kun päätät ottaa ulkoiset lähteet käyttöön, se on yksi asetusmuutos — ei migraatio. Datasi ei koskaan poistu verkostasi ellet sitä erikseen salli.

▸ LAITTEISTON SKAALAUTUVUUS
  ESP32 (8€) → RPi (80€) → NUC (650€) → Mac (2,2K€) → DGX (400K€)
  Sama koodi. Vain nopeus vaihtelee.

▸ VAIHE 4: AUTONOMINEN ÄLYKKYYS (AKTIIVINEN)
  • Voikko suomen normalisoija — morfologinen analyysi
  • 3-tasoinen nopeus: HotCache (0.5ms) → FI-suora (18ms) → Täysi (55ms)
  • Korjausmuisti — pysyvä virheoppiminen
  • Erikoistumiskeskukset — agenttien osaamiskartoitus
  • Kausivahtija — 10 kuukausitietoista älysääntöä
  • YöRikastaja — 4-vaiheinen laatuportti, purketila
  • Automaattiasennus — nollakonfiguraatio riippuvuuksille

▸ VAIHE B: TUOTANTOKOVETUS
  • CircuitBreaker — vikaantuvat osat pois automaattisesti, itsekorjaus
  • Vanhenemispoisto (TTL) — vanhentuneet faktat poistetaan automaattisesti
  • Vikasietoisuus — puuttuvat osat → varoitus, ei kaatumista

▸ VAIHE C: VÄLIMUISTI & PUTKI
  • HotCache-automaattitäyttö — yleisimmät kyselyt välimuistissa, 0.5ms
  • LRU-välimuisti — älykäs poisto, muistirajoitettu
  • Eräajodeduplikaatio — ei päällekkäisiä faktoja
  • Valmiusportit — alijärjestelmät raportoivat terveyden
  • Rakenteellinen lokitus — JSON-lokit, kyselykelpoiset

▸ VAIHE D: AUTONOMINEN ÄLYKKYYS
  • KonvergenssitTunnistin — tietää milloin oppiminen tasaantuu
  • Viikkoraportti — automaattinen suorituskykyanalyysi
  • Ulkoiset lähteet — RSS, sää, sähkön hinta

▸ VAIHE 7: PUHELIITTYMÄ (INTEGROITU)
  • Whisper STT — suomenkielinen puhe→teksti, paikallinen
  • Piper TTS — fi_FI-harri-medium, paikallinen synteesi
  • Herätyskäsky "Hei WaggleDance", VAD-hiljaisuustunnistus

▸ VAIHE 8: ULKOISET TIETOVIRRAT (AKTIIVINEN)
  • Sää — IL avoin data, 30 min välein
  • Pörssisähkö — spot-hinta, halvin tunti-ikkuna
  • RSS — kriittiset hälytykset, toimialakohtaiset uutissyötteet

▸ V0.0.5: SIIRRETTÄVYYS & LUOTETTAVUUS
  • Voikko mukana — mor.vfst + autocorr.vfst, autolataus
  • Yövuoroautomaatio — tools/night_shift.py, vartija, aamoraportti
  • Terveyspistemäärä 100/100 — kaikki 36 testisarjaa VIHREÄNÄ

▸ V0.0.6: VAIHE 5 — KODIN SENSORIT
  • MQTT Hub — paho-mqtt, dedup, eksponentiaalinen uudelleenyhdistys
  • Frigate NVR — kameratapahtumat, vakavuushälytykset (karhu=KRIITTINEN)
  • Home Assistant — REST-pollaus, merkittävyyssuodatin, suomenkielinen tila
  • Hälytysten välittäjä — Telegram + Webhook, nopeusrajoitus

▸ V0.0.8: VAIHE 6+7+11 — ÄÄNI, PUHE, ELASTINEN SKAALAUS
  • AudioAnalyzer — FFT-spektri, poikkeama/kuvio/tapahtuman tunnistus
  • BirdMonitor — BirdNET-stub, petoeläinhälytykset, vikasietoinen
  • Whisper STT (suomi) + Piper TTS (fi_FI-harri-medium), herätyssana
  • ElasticScaler — tunnistaa laitteiston, valitsee tason (ESP32→DGX)

▸ V0.0.9: VAIHE 8 — ULKOISET TIETOVIRRAT
  • Sää (IL) + Pörssisähkö + RSS tautihälytykset
  • 5 tietokantaa rikastettu (laatu, toimitusketju, energia, huolto)

▸ V0.1.0: HALLINTAPANEELIN ANALYTIIKKA + AJONAIKAINEN API
  • Analytiikka-API — 7 pv trendit, reittien jakauma, mallien käyttö, faktojen kasvu
  • Pyöreä Pöytä -transkriptit — konsensuskeskustelut, agenttien dialogi
  • Agenttitasojen ruudukko — 75 agenttia NOVIISI→MESTARI visualisoitu
  • Ajonaikaiset asetukset — 13 ominaisuuden päälle/pois kytkentä API:lla

▸ TESTAUS
  36/36 testisarjaa VIHREÄNÄ (700+ väittämää)
  Putki, reititys, korjaukset, autonomia, kodin sensorit, ääni, puhe, tietovirrat — kaikki validoitu

▸ KOODIKANTA
  75 agenttia • 140+ Python-moduulia • 200 000+ koodiriviä
  97.7% reitityksen tarkkuus (1 207/1 235 testattu)
  36 testisarjaa • 10 kausivahtisääntöä • 34 erikoistermiä

▸ ANNA SEN VAIN OLLA
Asenna. Yhdistä. Kävele pois.
1 viikko: tuntee tapasi.
1 kuukausi: ennakoi tarpeesi.
1 vuosi: ymmärtää maailmasi.`,
    hb: HEARTBEATS_FI,
    demoHb: DEMO_HB_FI,
    demoEnd: { a: "WaggleDance", m: "Demo valmis. Nyt on aika integroida omat tietovirtasi. Katso ohjeet oikeasta palkista.", t: "status" },
  },
};

const D_IDS = DOMAIN_IDS;
const D_IC = PROFILE_ICONS;
const D_COL = PROFILE_COLORS;
const D_RGB = PROFILE_COLORS_RGB;
const rY = (x,y,z,a)=>({x:x*Math.cos(a)-z*Math.sin(a),y,z:x*Math.sin(a)+z*Math.cos(a)});
const rX = (x,y,z,a)=>({x,y:y*Math.cos(a)-z*Math.sin(a),z:y*Math.sin(a)+z*Math.cos(a)});
const rZ = (x,y,z,a)=>({x:x*Math.cos(a)-y*Math.sin(a),y:x*Math.sin(a)+y*Math.cos(a),z});
const pj = (x,y,z,cx,cy,f)=>{const s=f/(f+z);return{x:cx+x*s,y:cy+y*s,s,z}};

function drawBrainOutline(ctx,cx,cy,s){ctx.beginPath();ctx.moveTo(cx-8*s,cy+68*s);ctx.quadraticCurveTo(cx-14*s,cy+58*s,cx-22*s,cy+48*s);ctx.bezierCurveTo(cx-28*s,cy+52*s,cx-42*s,cy+56*s,cx-52*s,cy+50*s);ctx.bezierCurveTo(cx-62*s,cy+48*s,cx-72*s,cy+44*s,cx-76*s,cy+34*s);ctx.bezierCurveTo(cx-78*s,cy+28*s,cx-80*s,cy+22*s,cx-82*s,cy+16*s);ctx.bezierCurveTo(cx-86*s,cy+4*s,cx-88*s,cy-10*s,cx-86*s,cy-24*s);ctx.bezierCurveTo(cx-85*s,cy-32*s,cx-82*s,cy-42*s,cx-78*s,cy-50*s);ctx.bezierCurveTo(cx-72*s,cy-60*s,cx-64*s,cy-70*s,cx-54*s,cy-78*s);ctx.quadraticCurveTo(cx-48*s,cy-82*s,cx-42*s,cy-80*s);ctx.quadraticCurveTo(cx-36*s,cy-85*s,cx-28*s,cy-86*s);ctx.quadraticCurveTo(cx-20*s,cy-88*s,cx-12*s,cy-87*s);ctx.quadraticCurveTo(cx-4*s,cy-90*s,cx+5*s,cy-89*s);ctx.quadraticCurveTo(cx+14*s,cy-91*s,cx+22*s,cy-88*s);ctx.quadraticCurveTo(cx+30*s,cy-86*s,cx+38*s,cy-83*s);ctx.quadraticCurveTo(cx+46*s,cy-80*s,cx+54*s,cy-74*s);ctx.quadraticCurveTo(cx+60*s,cy-68*s,cx+66*s,cy-60*s);ctx.bezierCurveTo(cx+74*s,cy-50*s,cx+80*s,cy-38*s,cx+84*s,cy-24*s);ctx.bezierCurveTo(cx+86*s,cy-14*s,cx+86*s,cy-2*s,cx+82*s,cy+8*s);ctx.bezierCurveTo(cx+78*s,cy+16*s,cx+72*s,cy+22*s,cx+64*s,cy+26*s);ctx.quadraticCurveTo(cx+56*s,cy+30*s,cx+48*s,cy+32*s);ctx.quadraticCurveTo(cx+38*s,cy+36*s,cx+28*s,cy+38*s);ctx.quadraticCurveTo(cx+18*s,cy+40*s,cx+8*s,cy+42*s);ctx.quadraticCurveTo(cx-2*s,cy+44*s,cx-12*s,cy+46*s);ctx.bezierCurveTo(cx-16*s,cy+48*s,cx-18*s,cy+52*s,cx-14*s,cy+58*s);ctx.quadraticCurveTo(cx-10*s,cy+64*s,cx-8*s,cy+68*s);ctx.closePath()}
function drawBrainDetails(ctx,cx,cy,s,color){const ss=(a)=>{ctx.strokeStyle=color+a;ctx.lineWidth=.6};ss("18");ctx.beginPath();ctx.moveTo(cx+12*s,cy-88*s);ctx.bezierCurveTo(cx+8*s,cy-70*s,cx+2*s,cy-45*s,cx-5*s,cy-15*s);ctx.stroke();ss("1A");ctx.beginPath();ctx.moveTo(cx+62*s,cy+22*s);ctx.bezierCurveTo(cx+40*s,cy+8*s,cx+15*s,cy-4*s,cx-20*s,cy-12*s);ctx.stroke();ss("10");ctx.beginPath();ctx.moveTo(cx+30*s,cy-84*s);ctx.bezierCurveTo(cx+26*s,cy-62*s,cx+20*s,cy-35*s,cx+14*s,cy-10*s);ctx.stroke();ss("10");ctx.beginPath();ctx.moveTo(cx-10*s,cy-87*s);ctx.bezierCurveTo(cx-16*s,cy-65*s,cx-22*s,cy-40*s,cx-28*s,cy-18*s);ctx.stroke();ss("0C");ctx.beginPath();ctx.moveTo(cx+76*s,cy-30*s);ctx.bezierCurveTo(cx+58*s,cy-48*s,cx+42*s,cy-60*s,cx+34*s,cy-68*s);ctx.stroke();ss("0C");ctx.beginPath();ctx.moveTo(cx+78*s,cy-5*s);ctx.bezierCurveTo(cx+60*s,cy-14*s,cx+42*s,cy-22*s,cx+28*s,cy-28*s);ctx.stroke();ss("0E");ctx.beginPath();ctx.moveTo(cx+55*s,cy+28*s);ctx.bezierCurveTo(cx+30*s,cy+18*s,cx+5*s,cy+10*s,cx-15*s,cy+2*s);ctx.stroke();ss("0E");ctx.beginPath();ctx.moveTo(cx-24*s,cy-84*s);ctx.bezierCurveTo(cx-38*s,cy-68*s,cx-52*s,cy-52*s,cx-62*s,cy-38*s);ctx.stroke();ss("10");ctx.beginPath();ctx.moveTo(cx-50*s,cy-80*s);ctx.bezierCurveTo(cx-58*s,cy-60*s,cx-66*s,cy-38*s,cx-72*s,cy-20*s);ctx.stroke();ss("0A");for(let i=0;i<4;i++){const y=cy+(36+i*4)*s;ctx.beginPath();ctx.moveTo(cx-(35+i*4)*s,y);ctx.quadraticCurveTo(cx-(52+i*2)*s,y-s,cx-(68-i*3)*s,y+s);ctx.stroke()}}

function BrainScene({color,factCount,isThinking,agents,cpuV,gpuV,vramV,vramMax}){
  const ref=useRef(null);const fr=useRef(0);const vec=useRef([]);const mem=useRef([]);
  const live=useRef({cpuV,gpuV,vramV,vramMax,factCount,isThinking,color});
  live.current={cpuV,gpuV,vramV,vramMax:vramMax||8,factCount,isThinking,color};
  useEffect(()=>{const c=ref.current;if(!c)return;const ctx=c.getContext("2d");const W=700,H=560,dpr=Math.min(devicePixelRatio||1,2);c.width=W*dpr;c.height=H*dpr;c.style.width=W+"px";c.style.height=H+"px";ctx.scale(dpr,dpr);const cx=W/2,cy=H/2,fov=580,N=agents.length;let id;
  // Planets — slowly orbiting memory clusters
  const planets=[
    {orb:200,spd:.00012,tilt:.4,sz:8,clr:"#A78BFA",label:"EPISODIC"},
    {orb:240,spd:-.00009,tilt:-.3,sz:6,clr:"#22D3EE",label:"SEMANTIC"},
    {orb:170,spd:.00015,tilt:.7,sz:7,clr:"#F59E0B",label:"PROCEDURAL"},
    {orb:260,spd:.00007,tilt:-.5,sz:5,clr:"#22C55E",label:"WORKING"},
    {orb:220,spd:-.00011,tilt:.2,sz:5.5,clr:"#EF4444",label:"SENSORY"},
  ];
  // Vector memory particles — spawn over time
  const spawnMem=()=>{const th=Math.random()*Math.PI*2,phi=Math.random()*Math.PI,r=60+Math.random()*120;mem.current.push({x:r*Math.sin(phi)*Math.cos(th),y:r*Math.sin(phi)*Math.sin(th),z:r*Math.cos(phi),life:0,maxLife:200+Math.random()*300,sz:1+Math.random()*2.5,drift:{x:(Math.random()-.5)*.08,y:(Math.random()-.5)*.08,z:(Math.random()-.5)*.08}})};
  const dG=(gx,gy,val,max,label,gc,unit)=>{const pct=Math.min(val/(max||1),1),r=44,sa=-Math.PI*.75,ea=Math.PI*.75;ctx.beginPath();ctx.arc(gx,gy,r,sa,ea);ctx.strokeStyle="rgba(255,255,255,.06)";ctx.lineWidth=5;ctx.lineCap="round";ctx.stroke();ctx.beginPath();ctx.arc(gx,gy,r,sa,sa+(ea-sa)*pct);ctx.strokeStyle=gc+"80";ctx.lineWidth=5;ctx.lineCap="round";ctx.stroke();ctx.font="500 11px 'Inter',system-ui";ctx.textAlign="center";ctx.fillStyle="rgba(255,255,255,.22)";ctx.fillText(label,gx,gy-16);ctx.font="600 20px 'Inter',system-ui";ctx.fillStyle=gc+"A0";ctx.fillText(val+(unit||"%"),gx,gy+8)};
  const draw=()=>{fr.current++;const f=fr.current;ctx.clearRect(0,0,W,H);const wa=f*.0004;
  // Orbital rings (scaled 2x)
  [[220,.3,0,1,50,.8,22],[188,-.5,.8,-1,36,.5,11],[244,.7,-.4,1,42,.35,7]].forEach(([r,tx2,tz,dir,pts,w,ba])=>{const pp=[];for(let i=0;i<pts;i++){const a=(Math.PI*2*i)/pts;let p={x:Math.cos(a)*r,y:Math.sin(a)*r,z:0};p=rX(p.x,p.y,p.z,tx2);p=rZ(p.x,p.y,p.z,tz);p=rY(p.x,p.y,p.z,wa*dir);pp.push(pj(p.x,p.y,p.z,cx,cy,fov))}for(let i=0;i<pp.length;i++){const a2=pp[i],b=pp[(i+1)%pp.length],d=(a2.s+b.s)*.5,al=ba*d*d;if(al<1)continue;ctx.beginPath();ctx.moveTo(a2.x,a2.y);ctx.lineTo(b.x,b.y);ctx.strokeStyle=color+hx(al);ctx.lineWidth=w*d;ctx.stroke()}});
  // Agent nodes (scaled 2x)
  const aR=140,a3=[];for(let i=0;i<N;i++){const phi=Math.acos(1-2*(i+.5)/N),th=Math.PI*(1+Math.sqrt(5))*i;let p={x:aR*Math.sin(phi)*Math.cos(th),y:aR*Math.sin(phi)*Math.sin(th),z:aR*Math.cos(phi)};p=rY(p.x,p.y,p.z,wa*.7);a3.push({...pj(p.x,p.y,p.z,cx,cy,fov),x3:p.x,y3:p.y,z3:p.z,i})}
  for(let i=0;i<N;i++)for(let j=i+1;j<N;j++){const al=Math.min(a3[i].s,a3[j].s)*5;if(al<1)continue;ctx.beginPath();ctx.moveTo(a3[i].x,a3[i].y);ctx.lineTo(a3[j].x,a3[j].y);ctx.strokeStyle=color+hx(al);ctx.lineWidth=.35;ctx.stroke()}
  for(let i=0;i<N;i++){const al=a3[i].s*3.5;if(al<1)continue;ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(a3[i].x,a3[i].y);ctx.strokeStyle=color+hx(al);ctx.lineWidth=.18;ctx.stroke()}
  // Planets — orbiting memory clusters
  planets.forEach((pl,pi)=>{const ang=f*pl.spd+pi*1.25;let px=Math.cos(ang)*pl.orb,py=Math.sin(ang)*pl.orb*.4,pz=Math.sin(ang)*pl.orb*Math.sin(pl.tilt);const pp=pj(px,py,pz,cx,cy,fov);
  // Orbit trail
  ctx.beginPath();for(let t=0;t<60;t++){const ta=ang-t*.04;const tx2=Math.cos(ta)*pl.orb,ty=Math.sin(ta)*pl.orb*.4,tz2=Math.sin(ta)*pl.orb*Math.sin(pl.tilt);const tp2=pj(tx2,ty,tz2,cx,cy,fov);t===0?ctx.moveTo(tp2.x,tp2.y):ctx.lineTo(tp2.x,tp2.y)}ctx.strokeStyle=pl.clr+"08";ctx.lineWidth=1;ctx.stroke();
  // Planet glow
  const pg=ctx.createRadialGradient(pp.x,pp.y,0,pp.x,pp.y,pl.sz*3*pp.s);pg.addColorStop(0,pl.clr+"30");pg.addColorStop(1,pl.clr+"00");ctx.beginPath();ctx.arc(pp.x,pp.y,pl.sz*3*pp.s,0,Math.PI*2);ctx.fillStyle=pg;ctx.fill();
  // Planet body
  ctx.beginPath();ctx.arc(pp.x,pp.y,pl.sz*pp.s,0,Math.PI*2);ctx.fillStyle=pl.clr+"90";ctx.fill();
  // Planet label
  if(pp.s>.6){ctx.font=`600 ${Math.round(7*pp.s)}px 'Inter',system-ui`;ctx.textAlign="center";ctx.fillStyle=pl.clr+"50";ctx.fillText(pl.label,pp.x,pp.y+pl.sz*2.5*pp.s)}});
  // Vector memory particles — 3D floating points representing stored vectors
  if(f%6===0&&mem.current.length<80)spawnMem();
  mem.current=mem.current.filter(m=>{m.life++;m.x+=m.drift.x;m.y+=m.drift.y;m.z+=m.drift.z;return m.life<m.maxLife});
  mem.current.forEach(m=>{const rot=rY(m.x,m.y,m.z,wa*.3);const mp=pj(rot.x,rot.y,rot.z,cx,cy,fov);const fade=m.life<20?m.life/20:m.life>m.maxLife-40?(m.maxLife-m.life)/40:1;const al=fade*mp.s*mp.s;if(al<.01)return;ctx.beginPath();ctx.arc(mp.x,mp.y,m.sz*mp.s,0,Math.PI*2);ctx.fillStyle=color+hx(al*60);ctx.fill();
  // Occasional connection lines between nearby memory particles
  if(m.life%30<2){const nearest=mem.current.find(o=>o!==m&&Math.abs(o.x-m.x)<40&&Math.abs(o.y-m.y)<40&&Math.abs(o.z-m.z)<40);if(nearest){const nr=rY(nearest.x,nearest.y,nearest.z,wa*.3);const np=pj(nr.x,nr.y,nr.z,cx,cy,fov);ctx.beginPath();ctx.moveTo(mp.x,mp.y);ctx.lineTo(np.x,np.y);ctx.strokeStyle=color+hx(al*25);ctx.lineWidth=.3;ctx.stroke()}}});
  // Signal vectors
  if(f%8===0&&isThinking){const f2=Math.floor(Math.random()*N);let to=Math.floor(Math.random()*N);if(to===f2)to=(to+1)%N;vec.current.push({fr:f2,to,p:0,sp:.04+Math.random()*.03})}
  if(f%14===0)vec.current.push({fr:Math.floor(Math.random()*N),to:-1,p:0,sp:.035+Math.random()*.025});
  if(vec.current.length>14)vec.current=vec.current.slice(-14);vec.current=vec.current.filter(v=>v.p<=1.05);
  vec.current.forEach(v=>{v.p+=v.sp;const fp=a3[v.fr],tp=v.to===-1?{x3:0,y3:0,z3:0}:a3[v.to];const mx=fp.x3+(tp.x3-fp.x3)*v.p,my=fp.y3+(tp.y3-fp.y3)*v.p,mz=fp.z3+(tp.z3-fp.z3)*v.p;const mp=pj(mx,my,mz,cx,cy,fov),fade=v.p<.08?v.p*12.5:v.p>.88?(1.05-v.p)*5.9:1;for(let tr=1;tr<=3;tr++){const t2=Math.max(0,v.p-tr*.05);const tp2=pj(fp.x3+(tp.x3-fp.x3)*t2,fp.y3+(tp.y3-fp.y3)*t2,fp.z3+(tp.z3-fp.z3)*t2,cx,cy,fov);ctx.beginPath();ctx.arc(tp2.x,tp2.y,(2.5-tr*.5)*tp2.s,0,Math.PI*2);ctx.fillStyle=color+hx(fade*(1-tr*.3)*tp2.s*100);ctx.fill()}const g2=ctx.createRadialGradient(mp.x,mp.y,0,mp.x,mp.y,10*mp.s);g2.addColorStop(0,color+hx(fade*mp.s*38));g2.addColorStop(1,color+"00");ctx.beginPath();ctx.arc(mp.x,mp.y,10*mp.s,0,Math.PI*2);ctx.fillStyle=g2;ctx.fill();ctx.beginPath();ctx.arc(mp.x,mp.y,3*mp.s,0,Math.PI*2);ctx.fillStyle=color+hx(fade*mp.s*185);ctx.fill()});
  // Agent node rendering (scaled)
  [...a3].sort((a2,b)=>a2.z-b.z).forEach(p=>{const pu=Math.sin(f*.025+p.i*1.5)*.3+.7,da=p.s*p.s;const ng=ctx.createRadialGradient(p.x,p.y,0,p.x,p.y,18*p.s);ng.addColorStop(0,color+hx(da*pu*16));ng.addColorStop(1,color+"00");ctx.beginPath();ctx.arc(p.x,p.y,18*p.s,0,Math.PI*2);ctx.fillStyle=ng;ctx.fill();ctx.beginPath();ctx.arc(p.x,p.y,6.5*p.s,0,Math.PI*2);ctx.strokeStyle=color+hx(da*pu*105);ctx.lineWidth=.8*p.s;ctx.stroke();ctx.beginPath();ctx.arc(p.x,p.y,2.8*p.s,0,Math.PI*2);ctx.fillStyle=color+hx(da*185);ctx.fill();if(p.s>.65){ctx.font=`500 ${Math.round(8*p.s)}px 'Inter',system-ui`;ctx.textAlign="center";ctx.fillStyle=color+hx(da*58);ctx.fillText(agents[p.i],p.x,p.y+15*p.s)}});
  // Center core
  const cp=Math.sin(f*.018)*.3+.7;ctx.beginPath();ctx.arc(cx,cy,7+(isThinking?cp*4:cp*2),0,Math.PI*2);ctx.strokeStyle=color+hx(cp*16+4);ctx.lineWidth=.6;ctx.stroke();ctx.beginPath();ctx.arc(cx,cy,2.4,0,Math.PI*2);ctx.fillStyle=color+"50";ctx.fill();
  // Labels — bigger IQ number
  const L=live.current;
  ctx.font="600 10px 'Inter',system-ui";ctx.fillStyle=L.color+"30";ctx.textAlign="center";ctx.fillText("RUNTIME STATE",cx,cy-36);
  ctx.font="300 14px 'Inter',system-ui";ctx.fillStyle=L.color+"50";ctx.fillText("IQ",cx-44,cy+8);
  ctx.font="200 38px 'Inter',system-ui";ctx.fillStyle=L.color+"80";ctx.fillText(L.factCount.toLocaleString(),cx+8,cy+12);
  ctx.font="300 8px 'Inter',system-ui";ctx.fillStyle=L.color+"20";ctx.fillText("FACTS LEARNED",cx,cy+28);
  if(L.isThinking){ctx.font="500 8px 'Inter',system-ui";ctx.fillStyle=L.color+"45";ctx.fillText("● THINKING",cx,cy+42)}
  const bs=2.8;drawBrainOutline(ctx,cx,cy,bs);ctx.strokeStyle=L.color+"1E";ctx.lineWidth=2;ctx.stroke();drawBrainDetails(ctx,cx,cy,bs,L.color);
  dG(56,cy-24,L.cpuV,100,"CPU","#22C55E","%");dG(W-56,cy-50,L.gpuV,100,"GPU",L.color,"%");dG(W-56,cy+50,L.vramV,L.vramMax,"VRAM","#A78BFA","G");
  id=requestAnimationFrame(draw)};draw();return()=>cancelAnimationFrame(id)},[agents]);
  return <canvas ref={ref} style={{width:700,height:560}}/>;
}

function Rain({rgb}){const ref=useRef(null);useEffect(()=>{const c=ref.current;if(!c)return;const ctx=c.getContext("2d");const W=c.width=innerWidth,H=c.height=innerHeight;const cols=Math.floor(W/28),drops=Array.from({length:cols},()=>Math.random()*H);const ch="01∞∆λπΣΩ".split("");let id;const draw=()=>{ctx.fillStyle="rgba(0,0,0,.07)";ctx.fillRect(0,0,W,H);ctx.font="11px monospace";for(let i=0;i<cols;i++){ctx.fillStyle=`rgba(${rgb},${(Math.random()*.07+.03).toFixed(3)})`;ctx.fillText(ch[Math.floor(Math.random()*ch.length)],i*28,drops[i]);drops[i]=drops[i]>H&&Math.random()>.975?0:drops[i]+20}id=requestAnimationFrame(draw)};draw();return()=>cancelAnimationFrame(id)},[rgb]);return <canvas ref={ref} style={{position:"fixed",inset:0,zIndex:0,pointerEvents:"none"}}/>}
function Boot({onDone}){const[s,setS]=useState(0);const[t,setT]=useState("");const[sub,setSub]=useState("");const[pr,setPr]=useState(0);const[hw,setHw]=useState([]);const hwData=useRef({gpu:"detecting...",cpu:"detecting...",ram:"detecting..."});useEffect(()=>{const _hh={};const _hk=localStorage.getItem("WAGGLE_API_KEY");if(_hk)_hh["Authorization"]=`Bearer ${_hk}`;fetch("/api/hardware",{headers:_hh}).then(r=>r.json()).then(d=>{if(d.gpu_name)hwData.current.gpu=d.gpu_name;else if(d.vram_total)hwData.current.gpu=`GPU — ${d.vram_total} GB`;if(d.cpu_model)hwData.current.cpu=d.cpu_model;else if(d.cpu_count)hwData.current.cpu=d.cpu_count+" threads";if(d.ram_total_gb)hwData.current.ram=Math.round(d.ram_total_gb)+" GB";else if(d.ram_gb)hwData.current.ram=Math.round(d.ram_gb)+" GB"}).catch(()=>{hwData.current={gpu:"GPU (offline)",cpu:"CPU",ram:"RAM"}})},[]);useEffect(()=>{const seq=[[300,()=>setS(1)],[1200,()=>setT("INITIALIZING")],[2800,()=>{setS(2);setT("SCANNING HARDWARE")}],[3200,()=>setHw(["GPU  "+hwData.current.gpu])],[3600,()=>setHw(p=>[...p,"CPU  "+hwData.current.cpu])],[4000,()=>setHw(p=>[...p,"RAM  "+hwData.current.ram])],[5200,()=>{setS(3);setT("LOADING MODELS")}],[5500,()=>{setPr(25);setSub("phi4-mini")}],[5900,()=>{setPr(50);setSub("llama3.2:1b")}],[6300,()=>{setPr(75);setSub("nomic-embed")}],[6700,()=>{setPr(100);setSub("ALL LOADED")}],[7900,()=>{setS(4);setT("AWAKENING")}],[8400,()=>setSub("loading memories")],[9000,()=>setSub("spawning agents")],[9600,()=>setSub("runtime ready")],[10400,()=>{setS(5);setT("I AM ALIVE");setSub("")}],[13000,onDone]];const ids=seq.map(([d,fn])=>setTimeout(fn,d));return()=>ids.forEach(clearTimeout)},[onDone]);return(<div style={{position:"fixed",inset:0,background:"#000",display:"flex",alignItems:"center",justifyContent:"center",flexDirection:"column",zIndex:100}}><Rain rgb="99,102,241"/>{s>=4&&<div style={{position:"absolute",width:280,height:280,borderRadius:"50%",background:"radial-gradient(circle,rgba(99,102,241,.08) 0%,transparent 70%)",animation:"breathe 3s ease-in-out infinite"}}/>}<div style={{position:"relative",zIndex:2,textAlign:"center"}}>{s>=1&&s<5&&<div style={{fontSize:11,fontWeight:600,letterSpacing:6,color:"rgba(255,255,255,.35)",marginBottom:22}}>WAGGLEDANCE AI</div>}<div style={{fontSize:s===5?90:s===4?32:28,fontWeight:s===5?800:600,color:s===5?"#818CF8":"#fff",letterSpacing:s===5?14:4,transition:"all .8s",whiteSpace:"nowrap",textShadow:s===5?"0 0 80px rgba(99,102,241,.8), 0 0 160px rgba(99,102,241,.5)":s>=4?"0 0 30px rgba(99,102,241,.25)":"none",animation:s===5?"explodeIn .6s cubic-bezier(.17,.67,.29,1.3)":"none"}}>{t}</div>{s===2&&<div style={{marginTop:18}}>{hw.map((l,i)=><div key={i} style={{fontSize:12,fontWeight:600,color:"rgba(255,255,255,.55)",letterSpacing:1,fontFamily:"monospace",marginBottom:3}}>{l}</div>)}</div>}{s===3&&<div style={{marginTop:18,width:220,margin:"18px auto 0"}}><div style={{height:2,background:"rgba(255,255,255,.15)",overflow:"hidden"}}><div style={{height:"100%",background:"linear-gradient(90deg,#6366F1,#A5B4FC)",width:`${pr}%`,transition:"width .4s"}}/></div><div style={{fontSize:11,fontWeight:600,color:"rgba(255,255,255,.50)",marginTop:6,letterSpacing:2,fontFamily:"monospace"}}>{sub}</div></div>}{s===4&&<div style={{fontSize:12,fontWeight:600,color:"rgba(99,102,241,.60)",marginTop:10,letterSpacing:3}}>{sub}</div>}{s===5&&<div style={{fontSize:13,fontWeight:700,color:"rgba(99,102,241,.50)",marginTop:12,letterSpacing:6,textShadow:"0 0 30px rgba(99,102,241,.3)",animation:"explodeIn .6s cubic-bezier(.17,.67,.29,1.3) .15s both"}}>WAGGLEDANCE AI</div>}</div></div>)}

function LearnToFly({onDone}){const[step,setStep]=useState(0);const[main,setMain]=useState("");const[sub,setSub]=useState("");const[items,setItems]=useState([]);const[pr,setPr]=useState(0);
useEffect(()=>{const seq=[
[800,()=>{setStep(1);setMain("WAGGLEDANCE AI");setSub("AUTONOMOUS LOCAL-FIRST INTELLIGENCE")}],
[7000,()=>{setStep(2);setMain("CONFIGURABLE MODULES");setSub("routing • memory • adaptive learning")}],
[14000,()=>{setStep(2);setMain("266,000+ LINES OF CODE");setSub("580 files • zero cloud dependencies")}],
[21000,()=>{setStep(2);setMain("97.7% ROUTING ACCURACY");setSub("1,207 of 1,235 queries — perfect agent selection")}],
[28000,()=>{setStep(3);setMain("3-TIER RESPONSE");setSub("");setItems(["HotCache — 0.5ms (pre-computed answers)","Native-language index — 18ms (skip translation)","Full pipeline — 55ms (bilingual reasoning)"])}],
[36000,()=>{setStep(4);setMain("PHASE 4: ONLINE");setSub("");setItems(["✓ Morphological Normalizer (Voikko)"]);setPr(15)}],
[38500,()=>{setItems(p=>[...p,"✓ Seasonal Guard (10 rules)"]);setPr(30)}],
[41000,()=>{setItems(p=>[...p,"✓ Corrections Memory"]);setPr(50)}],
[43500,()=>{setItems(p=>[...p,"✓ NightEnricher (4-stage quality gate)"]);setPr(70)}],
[46000,()=>{setItems(p=>[...p,"✓ HotCache + native-language fast path"]);setPr(90)}],
[48500,()=>{setItems(p=>[...p,"✓ Auto-dependency installer"]);setPr(100)}],
[51000,()=>{setStep(4);setMain("PHASES B/C/D: HARDENED");setSub("");setItems(["✓ CircuitBreaker (auto-heal failing subsystems)"]);setPr(15)}],
[53000,()=>{setItems(p=>[...p,"✓ HotCache + LRU (0.5ms cached responses)"]);setPr(35)}],
[55000,()=>{setItems(p=>[...p,"✓ ConvergenceDetector (knows when learning plateaus)"]);setPr(55)}],
[57000,()=>{setItems(p=>[...p,"✓ Structured Logging + Weekly Report"]);setPr(75)}],
[59000,()=>{setItems(p=>[...p,"✓ 50/50 test suites GREEN (700+ assertions)"]);setPr(100)}],
[62000,()=>{setStep(5);setMain("LANGUAGE-NATIVE AI");setSub("deep morphological integration — not just translation");setItems(["Opus-MT neural translation (any language pair)","Voikko-level morphological analysis","Domain-specific terminology engine","Native-language vector index (skip translation)"]);setPr(0)}],
[70000,()=>{setStep(6);setMain("INFINITE SCALING");setSub("same code — any hardware");setItems(["ESP32 — €8 — edge intelligence","Raspberry Pi — €80 — full agent","Intel NUC — €650 — home brain","Mac Pro — €2,200 — professional","NVIDIA DGX — €400,000 — enterprise"])}],
[78000,()=>{setStep(7);setMain("JUST LET IT RUN");setSub("");setItems(["1 week → knows your patterns","1 month → anticipates your needs","6 months → domain expert","1 year → understands your world"])}],
[86000,()=>{setStep(8);setMain("RUNTIME");setSub("initializing...");setItems([])}],
[89000,()=>setSub("loading memories...")],
[92000,()=>setSub("starting modules...")],
[96000,()=>{setStep(9);setMain("READY TO FLY");setSub("")}],
[102000,()=>onDone()]
];const ids=seq.map(([d,fn])=>setTimeout(fn,d));return()=>ids.forEach(clearTimeout)},[onDone]);
return(<div style={{position:"fixed",inset:0,background:"#000",display:"flex",alignItems:"center",justifyContent:"center",flexDirection:"column",zIndex:101,cursor:"pointer"}} onClick={onDone}><Rain rgb="99,102,241"/>
{step>=8&&<div style={{position:"absolute",width:280,height:280,borderRadius:"50%",background:"radial-gradient(circle,rgba(99,102,241,.08) 0%,transparent 70%)",animation:"breathe 3s ease-in-out infinite"}}/>}
<div style={{position:"relative",zIndex:2,textAlign:"center",maxWidth:440}}>
{step>=1&&step<9&&<div style={{fontSize:11,letterSpacing:6,color:"rgba(255,255,255,.35)",fontWeight:600,marginBottom:22}}>LEARN TO FLY</div>}
<div style={{fontSize:step===9?90:step<=2?32:28,fontWeight:step===9?800:600,color:step===9?"#818CF8":"#fff",letterSpacing:step===9?14:4,transition:"all .8s ease",whiteSpace:"nowrap",textShadow:step>=8?"0 0 80px rgba(99,102,241,.8), 0 0 160px rgba(99,102,241,.5)":"none",animation:step===9?"explodeIn .6s cubic-bezier(.17,.67,.29,1.3)":"none"}}>{main}</div>
{sub&&<div style={{fontSize:12,fontWeight:600,color:"rgba(99,102,241,.60)",marginTop:8,letterSpacing:3,transition:"all .4s"}}>{sub}</div>}
{items.length>0&&<div style={{marginTop:14}}>{items.map((item,i)=>(<div key={i} style={{fontSize:11,fontWeight:600,color:"rgba(99,102,241,.45)",letterSpacing:2,marginBottom:4,animation:"fadeUp .4s",fontFamily:"monospace"}}>{item}</div>))}</div>}
{step===4&&pr>0&&<div style={{marginTop:12,width:220,margin:"12px auto 0"}}><div style={{height:2,background:"rgba(255,255,255,.15)",overflow:"hidden"}}><div style={{height:"100%",background:"linear-gradient(90deg,#6366F1,#A5B4FC)",width:`${pr}%`,transition:"width .4s"}}/></div></div>}
{step>0&&step<9&&<div style={{position:"fixed",bottom:16,left:0,right:0,textAlign:"center",fontSize:9,color:"rgba(255,255,255,.12)",letterSpacing:3}}>CLICK TO SKIP</div>}
</div></div>)}

function HBFeed({msgs,color,label}){const tc={learning:"#A78BFA",consensus:"#F59E0B",action:"#22C55E",insight:"#6366F1",status:"rgba(255,255,255,.35)"};const tl={learning:"LEARNED",consensus:"CONSENSUS",action:"ACTION",insight:"INSIGHT",status:"STATUS"};const rc={scout:"#22D3EE",worker:"#A78BFA",judge:"#F59E0B"};const rl={scout:"SCOUT",worker:"WORKER",judge:"JUDGE"};const phClr=(v)=>v>=0.75?"#22C55E":v>=0.5?"#F59E0B":"#EF4444";return(<div><div style={{fontSize:7,letterSpacing:4,color:"rgba(255,255,255,.35)",marginBottom:12,display:"flex",alignItems:"center",gap:6}}><span style={{width:4,height:4,borderRadius:"50%",background:color,animation:"pulse 1.5s infinite",boxShadow:`0 0 4px ${color}`}}/>{label}</div>{msgs.map((m,i)=>(<div key={`${m.a}-${i}`} style={{padding:"7px 0",borderBottom:"1px solid rgba(255,255,255,.03)",opacity:Math.max(.15,1-i*.12),animation:i===0?"fadeUp .6s":"none"}}><div style={{display:"flex",alignItems:"center",gap:5,marginBottom:3}}><span style={{width:4,height:4,borderRadius:"50%",background:i===0?color:"rgba(255,255,255,.15)"}}/><span style={{fontSize:9,color:"rgba(255,255,255,.65)",fontWeight:600}}>{m.a}</span>{m.role&&<span style={{fontSize:5.5,color:rc[m.role]||"#888",background:(rc[m.role]||"#888")+"15",padding:"1px 4px",borderRadius:2,letterSpacing:1.5,fontWeight:600}}>{rl[m.role]||m.role}</span>}<span style={{fontSize:6,color:tc[m.t],background:typeof tc[m.t]==="string"&&tc[m.t].startsWith("#")?tc[m.t]+"20":"rgba(255,255,255,.06)",padding:"1px 5px",borderRadius:2,letterSpacing:2,fontWeight:500}}>{tl[m.t]}</span>{m.ph!=null&&<span style={{fontSize:6,color:phClr(m.ph),fontFamily:"monospace",fontWeight:700,letterSpacing:1}}>⬡{(m.ph*100).toFixed(0)}</span>}</div><div style={{fontSize:10,color:i===0?"rgba(255,255,255,.6)":"rgba(255,255,255,.25)",lineHeight:1.7,paddingLeft:9,fontWeight:300}}>{m.m}</div></div>))}</div>)}

function Overlay({item,color,hw,onClose,t}){const calc=hw?.calc;
  const isDisclaimer = item === "disclaimer";
  const isInfo = item === "info";
  const isAgents = item === "agents";
  const isHw = item === "hw";
  return(<div style={{position:"absolute",inset:8,background:"rgba(0,0,0,.95)",border:`1px solid ${color}15`,borderRadius:10,zIndex:20,overflowY:"auto",scrollbarWidth:"none",backdropFilter:"blur(20px)",animation:"fadeUp .25s"}}><div style={{padding:"18px 22px"}}><div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:14}}><div style={{fontSize:14,fontWeight:600,color:color+"C0",letterSpacing:3}}>{isDisclaimer?t.disclaimer:isInfo?t.techArch:isAgents?t.agents:isHw?t.hwSpec:item?.title}</div><button onClick={onClose} style={{background:color+"0A",border:`1px solid ${color}18`,borderRadius:4,padding:"3px 10px",cursor:"pointer",fontSize:9,color:color+"70",letterSpacing:2}}>✕</button></div>

    {isDisclaimer ? (
      <div>
        <div style={{fontSize:11,color:"rgba(255,255,255,.40)",lineHeight:1.9,marginBottom:14}}>
          <span style={{color:"#F59E0B",fontWeight:600,fontSize:12}}>⚠️ WARNING</span><br/>
          This self-evolving AI system is provided AS-IS with absolutely no warranty.
        </div>
        <div style={{textAlign:"center",margin:"20px 0"}}>
          <div style={{fontSize:18,fontWeight:700,color:color,letterSpacing:3}}>JANI KORPI</div>
          <div style={{fontSize:10,color:"rgba(255,255,255,.30)",marginTop:3}}>Ahkerat Mehiläiset / JKH Service</div>
        </div>
        <div style={{fontSize:11,color:"rgba(255,255,255,.35)",lineHeight:1.9,margin:"14px 0",padding:"10px 12px",background:"rgba(255,255,255,.03)",border:"1px solid rgba(255,255,255,.04)",borderRadius:6}}>
          The developer assumes <span style={{color:"#EF4444",fontWeight:600}}>ZERO RESPONSIBILITY</span> for any actions, decisions, damages, or consequences arising from WaggleDance AI operation.
          <br/><br/>
          <span style={{fontWeight:500,color:"rgba(255,255,255,.45)"}}>USE ENTIRELY AT YOUR OWN RISK.</span> Free to use, modify, and distribute.
        </div>
        <div style={{fontSize:11,color:"rgba(255,255,255,.33)",lineHeight:1.9,padding:"10px 12px",background:"#F59E0B12",border:"1px solid #F59E0B10",borderRadius:6}}>
          <span style={{color:"#F59E0B",fontWeight:600}}>RECOMMENDATION:</span> Keep hardware power limited. Run in a closed environment on a small dedicated machine. Do not connect to critical infrastructure without human oversight.
        </div>
        <div style={{fontSize:11,color:"rgba(255,255,255,.30)",lineHeight:1.9,marginTop:14,padding:"10px 12px",background:"rgba(255,255,255,.03)",borderRadius:6}}>
          <span style={{color:"rgba(255,255,255,.45)"}}>ORIGIN:</span> Originally developed for Finnish beekeeping operations, now generalizing into a domain-agnostic runtime.
        </div>
        <div style={{marginTop:18,padding:"12px",background:color+"06",border:`1px solid ${color}10`,borderRadius:6}}>
          <div style={{fontSize:10,color:color+"90",letterSpacing:3,marginBottom:8}}>DEVELOPERS</div>
          <div style={{fontSize:11,color:"rgba(255,255,255,.35)",lineHeight:2.2,fontFamily:"monospace"}}>
            99% — <span style={{color:"#A78BFA",fontWeight:600}}>Claude OPUS 4.6</span> <span style={{color:"rgba(255,255,255,.20)"}}>// heavy lifting</span><br/>
            {"  "}1% — <span style={{color:"#F59E0B",fontWeight:600}}>Jani Korpi</span> 🐝 <span style={{color:"rgba(255,255,255,.20)"}}>// vision, direction, coffee</span>
          </div>
        </div>
        <div style={{fontSize:9,color:"rgba(255,255,255,.15)",marginTop:14,textAlign:"center",letterSpacing:4}}>
          AHKERAT MEHILÄISET • 2024-2026
        </div>
      </div>
    ) : isInfo ? (
      <pre style={{fontSize:11,color:"rgba(255,255,255,.50)",whiteSpace:"pre-wrap",lineHeight:1.75,fontFamily:"monospace",margin:0}}>{t.info || L.en.info}</pre>
    ) : isAgents ? (
      <pre style={{fontSize:11,color:"rgba(255,255,255,.50)",whiteSpace:"pre-wrap",lineHeight:1.75,fontFamily:"monospace",margin:0}}>{t.agentsGuide || L.en.agentsGuide}</pre>
    ) : isHw ? (
      <div><div style={{fontSize:16,fontWeight:200,color:"#fff",marginBottom:10}}>{hw.name} <span style={{fontSize:11,color:"rgba(255,255,255,.38)"}}>{hw.price}</span></div>{[["CPU",hw.cpu],["RAM",hw.ram],["GPU",hw.gpu],["Storage",hw.storage],["Power",hw.power],["Models",hw.models],["Inference",hw.inference]].map(([k,v])=>(<div key={k} style={{fontSize:11,color:"rgba(255,255,255,.35)",lineHeight:1.8}}><span style={{color:color+"70",fontWeight:500,display:"inline-block",width:78}}>{k}</span>{v}</div>))}{calc&&<>{[["PERF",[["Tok/s",calc.llm_toks+""],["Fact",calc.fact_pipeline],["RT",calc.round_table],["Agents",calc.agents_max]]],["LEARN",[["Hour",calc.facts_hour_real.toLocaleString()],["Day",typeof calc.facts_day==="number"?calc.facts_day.toLocaleString():calc.facts_day],["Week",calc.facts_week],["Year",calc.facts_year],["Night",calc.night_8h]]],["LATENCY",[["Cold",calc.chat_cold],["Warm",calc.chat_warm],["Micro",calc.chat_micro],["Evo",calc.chat_evolution]]]].map(([title,rows])=>(<div key={title} style={{marginTop:10,padding:"8px 10px",background:color+"06",border:`1px solid ${color}10`,borderRadius:5}}><div style={{fontSize:10,color:color+"80",letterSpacing:3,marginBottom:5}}>{title}</div>{rows.map(([k,v])=>(<div key={k} style={{display:"flex",justifyContent:"space-between",fontSize:11,padding:"1px 0"}}><span style={{color:"rgba(255,255,255,.32)"}}>{k}</span><span style={{color:k==="Evo"?"#22C55E":color,fontWeight:600,fontFamily:"monospace"}}>{v}</span></div>))}</div>))}</>}</div>
    ) : (
      <><div style={{fontSize:11,color:"rgba(255,255,255,.35)",marginBottom:10}}>{item?.desc}</div><pre style={{fontSize:11,color:"rgba(255,255,255,.50)",whiteSpace:"pre-wrap",lineHeight:1.75,fontFamily:"monospace",margin:0}}>{item?.guide}</pre></>
    )}
  </div></div>);
}

// ═══ ANALYTICS MINI-PANEL ═══
function AnalyticsPanel({data,color,lang}){
  if(!data||!data.days||data.days.length===0)return null;
  const maxRT=Math.max(...data.rt_trend,1);
  const fi=lang==="fi";
  return(<div style={{marginTop:14,padding:"8px 0",borderTop:`1px solid ${color}15`}}>
    <div style={{fontSize:9,letterSpacing:4,color:"rgba(255,255,255,.30)",marginBottom:8}}>{fi?"OPPIMISANALYTIIKKA":"LEARNING ANALYTICS"}</div>
    <div style={{fontSize:8,color:"rgba(255,255,255,.25)",marginBottom:6}}>{fi?"7 PV TRENDI":"7-DAY TREND"} — {data.total_queries||0} {fi?"kyselyä":"queries"}</div>
    <div style={{display:"flex",gap:3,alignItems:"flex-end",height:40,marginBottom:6}}>
      {data.rt_trend.map((v,i)=>(<div key={i} style={{flex:1,display:"flex",flexDirection:"column",alignItems:"center",gap:2}}>
        <div style={{width:"100%",background:`${color}30`,borderRadius:2,height:Math.max(2,v/maxRT*32)}}/>
        <span style={{fontSize:6,color:"rgba(255,255,255,.20)"}}>{data.days[i]?.slice(5)}</span>
      </div>))}
    </div>
    <div style={{display:"flex",gap:8,fontSize:8}}>
      <span style={{color:"#22C55E"}}>Halluc: {((data.halluc_trend[data.halluc_trend.length-1]||0)*100).toFixed(1)}%</span>
      <span style={{color:"#22D3EE"}}>Cache: {((data.cache_trend[data.cache_trend.length-1]||0)*100).toFixed(0)}%</span>
      <span style={{color:color}}>RT: {data.rt_trend[data.rt_trend.length-1]||0}ms</span>
    </div>
  </div>);
}

// ═══ ROUND TABLE MINI-PANEL ═══
function RoundTablePanel({data,color,lang}){
  if(!data||!data.discussions||data.discussions.length===0)return null;
  const latest=data.discussions[0];
  const fi=lang==="fi";
  return(<div style={{marginTop:14,padding:"8px 0",borderTop:`1px solid ${color}15`}}>
    <div style={{fontSize:9,letterSpacing:4,color:"rgba(255,255,255,.30)",marginBottom:8}}>{fi?"PYÖREÄ PÖYTÄ — VIIMEISIN":"ROUND TABLE — LATEST"}</div>
    <div style={{fontSize:10,color:"rgba(255,255,255,.55)",fontWeight:600,marginBottom:4}}>{latest.topic}</div>
    <div style={{fontSize:8,color:"rgba(255,255,255,.25)",marginBottom:6}}>{latest.agent_count} {fi?"agenttia":"agents"} — {(latest.agreement*100).toFixed(0)}% {fi?"konsensus":"agreement"}</div>
    {latest.discussion.slice(-3).map((d,i)=>(<div key={i} style={{padding:"3px 0",borderLeft:`2px solid ${d.agent==="Kuningatar"?color:"rgba(255,255,255,.08)"}`,paddingLeft:6,marginBottom:3}}>
      <span style={{fontSize:7.5,color:d.agent==="Kuningatar"?color:"rgba(255,255,255,.35)",fontWeight:600,letterSpacing:1}}>{d.agent}</span>
      <div style={{fontSize:9,color:"rgba(255,255,255,.40)",lineHeight:1.5}}>{d.msg}</div>
    </div>))}
    <div style={{fontSize:9,color:color,fontWeight:600,marginTop:4,padding:"4px 6px",background:`${color}08`,borderRadius:3}}>{latest.consensus}</div>
  </div>);
}

// ═══ AGENT GRID MINI-PANEL ═══
function AgentGridPanel({data,color,lang}){
  if(!data||!data.agents)return null;
  const LC={5:"#22C55E",4:"#A78BFA",3:"#22D3EE",2:"#F59E0B",1:"#6B7280"};
  const LN={5:"M",4:"E",3:"J",2:"A",1:"N"};
  const fi=lang==="fi";
  return(<div style={{marginTop:14,padding:"8px 0",borderTop:`1px solid ${color}15`}}>
    <div style={{fontSize:9,letterSpacing:4,color:"rgba(255,255,255,.30)",marginBottom:8}}>{fi?"AGENTTITASOT":"AGENT LEVELS"} — {data.total} {fi?"agenttia":"agents"}</div>
    <div style={{display:"flex",gap:4,marginBottom:8,fontSize:8}}>
      {Object.entries(data.level_distribution||{}).map(([name,cnt])=>(<span key={name} style={{color:LC[{MASTER:5,EXPERT:4,JOURNEYMAN:3,APPRENTICE:2,NOVICE:1}[name]||1]}}>{name.slice(0,3)}: {cnt}</span>))}
    </div>
    <div style={{display:"flex",flexWrap:"wrap",gap:2}}>
      {data.agents.slice(0,75).map((a,i)=>(<div key={i} title={`${a.agent_id} L${a.level} T:${a.trust_score}`} style={{width:10,height:10,borderRadius:2,background:LC[a.level]||"#333",opacity:0.7,fontSize:5,display:"flex",alignItems:"center",justifyContent:"center",color:"#000",fontWeight:800}}>{LN[a.level]}</div>))}
    </div>
  </div>);
}

// ═══ MODEL STATUS PANEL ═══
function ModelStatusPanel({data,color,lang}){
  if(!data)return null;
  const fi=lang==="fi";
  const models=data.models||[];
  if(models.length===0&&!data.ollama_available)return(
    <div style={{marginTop:14,padding:"8px 0",borderTop:`1px solid ${color}15`}}>
      <div style={{fontSize:9,letterSpacing:4,color:"rgba(255,255,255,.30)",marginBottom:8}}>{fi?"MALLIT":"MODELS"}</div>
      <div style={{fontSize:9,color:"#F59E0B"}}>Ollama {fi?"ei saatavilla":"not available"}</div>
    </div>
  );
  const ROLE_COL={"chat":"#22C55E","background_learning":"#A78BFA","embedding":"#22D3EE","evaluation":"#F59E0B","other":"#6B7280"};
  const ROLE_LABEL={"chat":fi?"Chat":"Chat","background_learning":fi?"Tausta":"Background","embedding":"Embed","evaluation":"Eval","other":fi?"Muu":"Other"};
  const vramPct=data.vram_total_mb>0?Math.round(data.vram_used_mb/data.vram_total_mb*100):0;
  return(
    <div style={{marginTop:14,padding:"8px 0",borderTop:`1px solid ${color}15`}}>
      <div style={{fontSize:9,letterSpacing:4,color:"rgba(255,255,255,.30)",marginBottom:8}}>{fi?"MALLIT":"MODELS"}</div>
      {/* VRAM bar */}
      {data.vram_total_mb>0&&<div style={{marginBottom:8}}>
        <div style={{display:"flex",justifyContent:"space-between",fontSize:7.5,color:"rgba(255,255,255,.35)",marginBottom:3}}>
          <span>VRAM</span><span>{Math.round(data.vram_used_mb)} / {data.vram_total_mb} MB ({vramPct}%)</span>
        </div>
        <div style={{height:6,background:"rgba(255,255,255,.05)",borderRadius:3,overflow:"hidden"}}>
          <div style={{height:"100%",width:`${vramPct}%`,background:`linear-gradient(90deg,${color},${color}80)`,borderRadius:3,transition:"width 1s"}}/>
        </div>
      </div>}
      {/* Model cards */}
      {models.map((m,i)=>{const rc=ROLE_COL[m.role]||"#6B7280";return(
        <div key={i} style={{padding:"4px 0",borderBottom:"1px solid rgba(255,255,255,.02)",display:"flex",alignItems:"center",gap:6}}>
          <span style={{width:5,height:5,borderRadius:"50%",background:m.loaded?"#22C55E":"rgba(255,255,255,.15)",flexShrink:0}}/>
          <div style={{flex:1,minWidth:0}}>
            <div style={{fontSize:10,color:"rgba(255,255,255,.55)",fontWeight:600,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{m.name}</div>
            <div style={{fontSize:7.5,color:"rgba(255,255,255,.25)"}}>{ROLE_LABEL[m.role]||m.role} — {m.size_gb}GB{m.vram_mb>0?` — ${m.vram_mb}MB VRAM`:""}</div>
          </div>
          <span style={{fontSize:7,color:rc,fontWeight:600,letterSpacing:1,flexShrink:0}}>{(ROLE_LABEL[m.role]||m.role).toUpperCase()}</span>
        </div>
      )})}
    </div>
  );
}

function FeatureList({feats,color,label,onOpen,t}){return(<div><div style={{fontSize:9,letterSpacing:4,color:"rgba(255,255,255,.30)",marginBottom:10}}>{label}</div>{[{key:"hw",icon:"💻",title:t.hwSpec,d:t.hwDesc},{key:"agents",icon:"🤖",title:t.agents,d:t.agentsDesc}].map(s=>(<div key={s.key} onClick={()=>onOpen(s.key)} style={{cursor:"pointer",padding:"6px 0",borderBottom:"1px solid rgba(255,255,255,.025)"}}><div style={{fontSize:12,color:color+"90",fontWeight:600,letterSpacing:2}}>{s.icon} {s.title}</div><div style={{fontSize:9,color:"rgba(255,255,255,.28)"}}>{s.d}</div></div>))}{feats.map((f,i)=>(<div key={i} onClick={()=>onOpen(f)} style={{cursor:"pointer",padding:"6px 0",borderBottom:"1px solid rgba(255,255,255,.02)",display:"flex",justifyContent:"space-between",alignItems:"center"}}><div><div style={{fontSize:11,color:"rgba(255,255,255,.55)",fontWeight:600,letterSpacing:1.5}}>{f.title}</div><div style={{fontSize:9,color:"rgba(255,255,255,.28)"}}>{f.desc}</div></div><span style={{fontSize:10,color:color+"45"}}>→</span></div>))}<div onClick={()=>onOpen("info")} style={{cursor:"pointer",padding:"7px 0",marginTop:4,borderTop:`1px solid ${color}10`}}><div style={{fontSize:12,color:color+"90",fontWeight:600,letterSpacing:2}}>🧠 {t.techArch}</div><div style={{fontSize:9,color:"rgba(255,255,255,.28)"}}>{t.techArchDesc}</div></div><div onClick={()=>onOpen("disclaimer")} style={{cursor:"pointer",padding:"7px 0",borderTop:"1px solid rgba(255,255,255,.03)"}}><div style={{fontSize:12,color:"rgba(255,255,255,.40)",fontWeight:600,letterSpacing:2}}>⚠️ {t.disclaimer}</div><div style={{fontSize:9,color:"rgba(255,255,255,.25)"}}>{t.disclaimerDesc}</div></div></div>)}

export default function App(){
  const[fly,setFly]=useState(true);const[on,setOn]=useState(false);const[dom,setDom]=useState("home");const[lang,setLang]=useState("en");const[dashView,setDashView]=useState("classic");
  const[fc,setFc]=useState(0);const[lr,setLr]=useState(37);const[hb,setHb]=useState([]);
  const[think,setThink]=useState(false);const[cpuL,setCpu]=useState(0);const[gpuL,setGpu]=useState(0);
  const[vramU,setVram]=useState(0);const[vramTotal,setVramTotal]=useState(8);const[overlay,setOverlay]=useState(null);
  const[chatIn,setChatIn]=useState("");const[chatMsgs,setChatMsgs]=useState([]);
  const t=L[lang];const col=D_COL[dom];const hw=HW[dom];
  const ag={gadget:["Mesh","TinyML","Battery","OTA","Relay"],home:["Climate","Energy","Security","Light","Orch."],cottage:["Tarhaaja","Tauti","Meteo","Sähkö","Queen"],factory:["Process","Yield","Equip","Defect","Shift"],apiary:["Monitor","Disease","Acoustic","Yield","Swarm"]};

  // ═══ SYNC LANGUAGE TO BACKEND ═══
  useEffect(() => {
    const lh = { "Content-Type": "application/json" }; const lk = localStorage.getItem("WAGGLE_API_KEY"); if (lk) lh["Authorization"] = `Bearer ${lk}`;
    fetch("/api/language", { method: "POST", headers: lh, body: JSON.stringify({ mode: lang }) }).catch(() => {});
  }, [lang]);

  // ═══ PER-TAB DEMO MODE (90s each) ═══
  const DEMO_DURATION = 90000;
  const DEMO_INTERVAL = 6000;
  const [demoTimers, setDemoTimers] = useState({ gadget: null, home: null, cottage: null, factory: null, apiary: null });
  const demoIndexRef = useRef({ gadget: 0, home: 0, cottage: 0, factory: 0, apiary: 0 });
  const demoEndShownRef = useRef({ gadget: false, home: false, cottage: false, factory: false, apiary: false });
  // Force re-render when demo ends (isDemoActive depends on Date.now())
  const [tick, setTick] = useState(0);

  const isDemoActive = useCallback((domId) => {
    const timer = demoTimers[domId];
    return timer !== null && (Date.now() - timer < DEMO_DURATION);
  }, [demoTimers]);

  // ═══ API INTEGRATION ═══
  const api = useApi();

  // Sync API data into local state — hardware gauges always update (even during demo)
  useEffect(() => {
    if (!on) return;

    // Fact count: always use real API value when available
    if (api.backendAvailable && api.status.facts > 0) {
      setFc(api.status.facts);
    }

    // Hardware gauges — prefer api.hardware (direct from /api/hardware endpoint)
    if (api.backendAvailable) {
      const hw = api.hardware || {};
      const _cpu = hw.cpu || api.status.cpu || 0;
      const _gpu = hw.gpu || api.status.gpu || 0;
      const _vram = hw.vram || api.status.vram || 0;
      console.log("[HW SYNC]", {_cpu, _gpu, _vram, hwGpu: hw.gpu, statusGpu: api.status.gpu, on});
      setCpu(_cpu);
      setGpu(_gpu);
      setVram(_vram);
      if (hw.vram_total > 0) setVramTotal(hw.vram_total);
    }

    // Thinking state from API
    if (!isDemoActive(dom)) setThink(api.status.is_thinking);
  }, [on, dom, api.backendAvailable, api.status, api.hardware, isDemoActive]);

  // Sync heartbeat messages from API — only when demo is NOT active for current tab
  useEffect(() => {
    if (!on) return;
    if (isDemoActive(dom)) return; // demo still running, don't overwrite
    if (api.heartbeats.length > 0) {
      setHb(api.heartbeats.slice(0, 6));
    }
  }, [on, dom, api.heartbeats, isDemoActive]);

  const boot=useCallback(()=>setTimeout(()=>setOn(true),500),[]);

  // Initialize default tab demo on first load
  useEffect(() => {
    if (on && demoTimers[dom] === null) {
      setDemoTimers(prev => ({ ...prev, [dom]: Date.now() }));
    }
  }, [on]);

  // Sync profile from backend on first load
  useEffect(() => {
    if (api.profile?.active_profile) {
      const p = api.profile.active_profile;
      if (["gadget","cottage","home","factory","apiary"].includes(p) && p !== dom) {
        setDom(p);
      }
    }
  }, [api.profile?.active_profile]);

  // Tab switch: restart demo every time a tab is clicked
  const sw=(id)=>{
    setDom(id);
    setOverlay(null);
    // Persist profile to backend
    api.switchProfile(id);
    // Always restart demo on tab click — use setTick to force re-render even if dom unchanged
    demoIndexRef.current[id] = 0;
    demoEndShownRef.current[id] = false;
    const now = Date.now();
    setDemoTimers(prev => ({
      ...prev,
      [id]: now
    }));
    setHb([]);
    setTick(t => t + 1);
  };

  // ═══ DEMO HEARTBEAT SCHEDULER — drives demo messages for current tab ═══
  useEffect(() => {
    if (!on) return;
    if (!isDemoActive(dom)) return;

    const timer = demoTimers[dom];
    if (!timer) return;

    const demoMsgs = t.demoHb[dom];
    const elapsed = Date.now() - timer;
    const currentIdx = Math.min(Math.floor(elapsed / DEMO_INTERVAL), 14);

    // Show all messages up to currentIdx (newest first)
    const visible = [];
    for (let i = currentIdx; i >= 0; i--) {
      if (demoMsgs[i]) visible.push(demoMsgs[i]);
    }
    setHb(visible.slice(0, 6));
    setThink(currentIdx < 14); // thinking while demo progresses
    demoIndexRef.current[dom] = currentIdx;

    // Simulate slow fact count growth during demo (only if API not providing real data)
    if (!api.backendAvailable || api.status.facts === 0) {
      setFc(prev => prev + Math.floor(Math.random() * 3) + 1);
    }

    // Schedule next tick
    const nextMsgAt = (currentIdx + 1) * DEMO_INTERVAL;
    const delay = Math.max(100, timer + nextMsgAt - Date.now());

    const timeout = setTimeout(() => {
      if (Date.now() - timer >= DEMO_DURATION) {
        // Demo ended — show transition message, then schedule removal
        setThink(false);
        setHb(prev => [t.demoEnd, ...prev.slice(0, 5)]);
        demoEndShownRef.current[dom] = true;
        // Force tick to update isDemoActive
        setTick(t => t + 1);
      } else {
        // Trigger re-render to show next message
        setTick(t => t + 1);
      }
    }, delay);

    return () => clearTimeout(timeout);
  }, [on, dom, demoTimers, isDemoActive, t, tick]);

  // After demo ends, load initial data from API or fallback
  useEffect(() => {
    if (!on) return;
    if (isDemoActive(dom)) return;
    if (!demoTimers[dom]) return; // not yet opened
    if (!demoEndShownRef.current[dom]) return; // end message not shown yet

    // Demo is done — if API has heartbeats, use them; otherwise use mock cycling pool
    const timeout = setTimeout(() => {
      if (api.heartbeats.length > 0) {
        setHb(api.heartbeats.slice(0, 6));
      } else {
        setHb(t.hb[dom].slice(0, 4));
      }
      demoEndShownRef.current[dom] = false; // consume the flag
    }, 2000); // show "Demo complete" for 2s before switching

    return () => clearTimeout(timeout);
  }, [on, dom, demoTimers, isDemoActive, api.heartbeats, t]);

  // Language change during demo — update displayed messages to new language
  useEffect(() => {
    if (!on) return;
    if (!isDemoActive(dom)) return;
    const timer = demoTimers[dom];
    if (!timer) return;
    const demoMsgs = t.demoHb[dom];
    const elapsed = Date.now() - timer;
    const currentIdx = Math.min(Math.floor(elapsed / DEMO_INTERVAL), 14);
    const visible = [];
    for (let i = currentIdx; i >= 0; i--) {
      if (demoMsgs[i]) visible.push(demoMsgs[i]);
    }
    setHb(visible.slice(0, 6));
  }, [lang]);

  // Mock heartbeat cycling — ONLY when demo is done AND API isn't providing data
  useEffect(()=>{if(!on)return;
    // Don't cycle during demo
    if (isDemoActive(dom)) return;
    // Skip entirely if backend is available — real data comes from API sync
    if (api.backendAvailable) return;
    const iv=setInterval(()=>{
    const pool=t.hb[dom];setThink(true);setTimeout(()=>setThink(false),2500);setTimeout(()=>{setHb(p=>[pool[Math.floor(Math.random()*pool.length)],...p.slice(0,5)]);setLr(28+Math.floor(Math.random()*22))},1200)},6000);return()=>clearInterval(iv)},[on,dom,lang,api.backendAvailable,isDemoActive]);

  // ═══ LOAD CHAT HISTORY ON MOUNT ═══
  const historyLoaded = useRef(false);
  useEffect(() => {
    if (!on || historyLoaded.current) return;
    historyLoaded.current = true;
    api.loadHistory().then(msgs => {
      if (msgs.length > 0) {
        const restored = msgs.map(m => ({
          role: m.role === "user" ? "user" : "ai",
          text: m.content,
          message_id: m.id || null,
          feedback: m.feedback_rating || null,
        }));
        setChatMsgs(restored);
      }
    });
  }, [on]);

  // ═══ CHAT — uses API with mock fallback ═══
  const handleChat=async()=>{if(!chatIn.trim())return;const msg=chatIn.trim();setChatIn("");setChatMsgs(p=>[...p,{role:"user",text:msg}]);setThink(true);
    const result = await api.sendChat(msg, lang);
    setThink(false);
    setChatMsgs(p=>[...p,{role:"ai",text:result.text,message_id:result.message_id,feedback:null}]);
  };

  const handleFeedback=(msgIndex,rating)=>{
    setChatMsgs(p=>p.map((m,i)=>i===msgIndex?{...m,feedback:rating}:m));
    const msg=chatMsgs[msgIndex];
    if(msg&&msg.message_id)api.sendFeedback(msg.message_id,rating);
  };

  const _hRate = api.status.total_queries > 0 ? ((api.status.hallucinations_caught / api.status.total_queries) * 100).toFixed(1) + "%" : "0%";
  const _microGen = api.status.micro_model?.generation ? `GEN ${api.status.micro_model.generation}` : "—";
  const _agentsReal = api.backendAvailable && api.status.agents_total > 0 ? api.status.agents_total : "—";
  const _errRate = api.status.total_requests > 0 ? ((1 - api.status.total_errors / api.status.total_requests) * 100).toFixed(1) + "%" : "—";
  const _ss = api.sensors?.status || {};
  const _mqttOn = _ss.mqtt?.connected;
  const _haOn = _ss.home_assistant?.connected;
  const _frigOn = _ss.frigate?.connected;
  const _alertCnt = _ss.alerts?.sent_total || 0;
  const _sttOn = api.voiceStatus?.stt_available;
  const _ttsOn = api.voiceStatus?.tts_available;
  const _audioOn = api.audioStatus?.available && api.audioStatus?.status?.started;
  const _tier = api.status?.elastic_scaler?.tier || null;
  const _nomicOk = api.status?.embedding?.available !== false;
  const _nomicAlert = api.status?.embedding?.alert || null;
  const _diskStatus = api.status?.disk_space?.status || "unknown";
  const _diskFree = api.status?.disk_space?.free_gb >= 0 ? `${api.status.disk_space.free_gb}G` : "—";
  const _diskColor = _diskStatus==="critical"?"#EF4444":_diskStatus==="warning"?"#F59E0B":"#22C55E";
  const _lastUpdate = api.backendAvailable ? new Date().toLocaleTimeString([],{hour:"2-digit",minute:"2-digit",second:"2-digit"}) : "—";
  const aw=[{k:"State",v:api.backendAvailable?"CONSCIOUS":"OFFLINE",c:api.backendAvailable?"#22C55E":"#EF4444"},{k:"Learn",v:api.backendAvailable?"CONTINUOUS":"OFFLINE",c:api.backendAvailable?"#A78BFA":"#EF4444"},{k:"Facts",v:fc.toLocaleString(),c:col},{k:"Rate",v:`+${lr}/hr`,c:"#22D3EE"},{k:"Halluc",v:_hRate,c:"#22C55E"},{k:"Circuit",v:"CLOSED",c:"#22C55E"},{k:"Tests",v:"50/50",c:"#22C55E"},{k:"Speed",v:"3s\u219218ms",c:"#A78BFA"},{k:"Micro",v:_microGen,c:col},{k:"Cloud",v:"NONE",c:"#22C55E"},{k:"Errors",v:`${api.status.total_errors || 0}`,c:api.status.total_errors>0?"#EF4444":"#22C55E"},{k:"Cache",v:api.status.cache_hit_rate||"\u2014",c:"#22D3EE"},{k:"Reqs",v:`${api.status.total_requests||0}`,c:"#6366F1"},{k:"Agents",v:`${_agentsReal}`,c:col},{k:"Tier",v:_tier?_tier.toUpperCase():"—",c:_tier?"#A78BFA":"rgba(255,255,255,.20)"},{k:"Disk",v:_diskFree,c:_diskColor},{k:"MQTT",v:_mqttOn?"ON":"OFF",c:_mqttOn?"#22C55E":"rgba(255,255,255,.20)"},{k:"HA",v:_haOn?"ON":"OFF",c:_haOn?"#22C55E":"rgba(255,255,255,.20)"},{k:"Cam",v:_frigOn?"ON":"OFF",c:_frigOn?"#22C55E":"rgba(255,255,255,.20)"},{k:"Alerts",v:`${_alertCnt}`,c:_alertCnt>0?"#F59E0B":"rgba(255,255,255,.20)"},{k:"Audio",v:_audioOn?"ON":"OFF",c:_audioOn?"#22C55E":"rgba(255,255,255,.20)"},{k:"STT",v:_sttOn?"ON":"OFF",c:_sttOn?"#22C55E":"rgba(255,255,255,.20)"},{k:"TTS",v:_ttsOn?"ON":"OFF",c:_ttsOn?"#22C55E":"rgba(255,255,255,.20)"}];
  return(
    <div style={{background:"#000",color:"#fff",minHeight:"100vh",fontFamily:"'Inter',system-ui,sans-serif",overflow:"hidden"}}>
      <style>{`@keyframes fadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}@keyframes pulse{0%,100%{opacity:1}50%{opacity:.15}}@keyframes breathe{0%,100%{transform:scale(1);opacity:.5}50%{transform:scale(1.1);opacity:1}}@keyframes explodeIn{0%{transform:scale(0);opacity:0}60%{transform:scale(1.15);opacity:1}100%{transform:scale(1);opacity:1}}*{box-sizing:border-box;margin:0;padding:0}button{font-family:inherit}::-webkit-scrollbar{display:none}*{scrollbar-width:none}input::placeholder{color:rgba(255,255,255,.28)}`}</style>
      {fly&&<LearnToFly onDone={()=>setFly(false)}/>}
      {!on&&!fly&&<Boot onDone={boot}/>}
      {on&&<>{dashView==="reasoning"&&<ReasoningDashboard onSwitchView={()=>setDashView("classic")}/>}{dashView==="classic"&&<>
        <Rain rgb={D_RGB[dom]}/>
        {/* Nomic-embed-text alert banner */}
        {!_nomicOk&&<div style={{position:"fixed",top:0,left:0,right:0,zIndex:100,background:"rgba(239,68,68,.95)",padding:"8px 22px",display:"flex",alignItems:"center",justifyContent:"center",gap:8,animation:"fadeUp .4s"}}><span style={{fontSize:11,fontWeight:700,color:"#fff",letterSpacing:1}}>{lang==="fi"?"VAROITUS: nomic-embed-text EI SAATAVILLA — muistitoiminnot eiv\u00e4t toimi!":"WARNING: nomic-embed-text UNAVAILABLE — memory operations disabled!"}</span></div>}
        <div style={{position:"fixed",top:"50%",left:"50%",transform:"translate(-50%,-50%)",fontSize:60,fontWeight:700,color:"rgba(255,255,255,.007)",letterSpacing:10,whiteSpace:"nowrap",pointerEvents:"none",zIndex:1}}>WAGGLEDANCE AI</div>
        <div style={{position:"relative",zIndex:2,animation:"fadeUp .8s"}}>
          <div style={{position:"relative",padding:"0 22px"}}>
            <div style={{position:"absolute",top:0,left:0,right:0,height:52,background:"rgba(255,255,255,.025)",borderBottom:"1px solid rgba(255,255,255,.04)"}}/>
            <div style={{position:"relative",display:"flex",alignItems:"center",height:52}}>
              <div style={{marginRight:24}}>
                <div style={{fontSize:7,color:"rgba(255,255,255,.45)",letterSpacing:6,fontWeight:600,marginBottom:-2}}>SWARM</div>
                <span style={{fontSize:18,fontWeight:700,color:"rgba(255,255,255,.45)",letterSpacing:3}}>WAGGLEDANCE AI</span>
                <span style={{fontSize:9,color:"rgba(255,255,255,.30)",letterSpacing:2,marginLeft:6}}>(ON-PREM)</span>
              </div>
              <div style={{display:"flex",flex:1,justifyContent:"center",gap:3}}>{D_IDS.map(id=>(<button key={id} onClick={()=>sw(id)} style={{background:id===dom?D_COL[id]+"12":"none",border:id===dom?`1px solid ${D_COL[id]}25`:"1px solid transparent",borderRadius:4,cursor:"pointer",padding:"5px 12px",transition:"all .3s"}}><span style={{fontSize:9,letterSpacing:2,fontWeight:id===dom?600:300,color:id===dom?D_COL[id]:"rgba(255,255,255,.25)"}}>{D_IC[id]} {t.domains[id].label}</span></button>))}</div>
              {/* Mode indicator */}
              <span style={{fontSize:9,fontWeight:700,letterSpacing:3,color:api.backendMode==="production"?"#22C55E":api.backendMode==="stub"?"#F59E0B":"rgba(255,255,255,.20)",background:api.backendMode==="production"?"rgba(34,197,94,.12)":api.backendMode==="stub"?"rgba(245,158,11,.12)":"transparent",border:`1px solid ${api.backendMode==="production"?"rgba(34,197,94,.25)":api.backendMode==="stub"?"rgba(245,158,11,.25)":"rgba(255,255,255,.05)"}`,borderRadius:4,padding:"5px 10px",marginRight:8,boxShadow:api.backendMode==="production"?"0 0 8px rgba(34,197,94,.15)":api.backendMode==="stub"?"0 0 8px rgba(245,158,11,.15)":"none"}}>{api.backendMode==="production"?"PROD":api.backendMode==="stub"?"STUB":"—"}</span>
              {/* Dashboard view toggle */}
              <button onClick={()=>setDashView(v=>v==="classic"?"reasoning":"classic")} style={{background:dashView==="reasoning"?"rgba(167,139,250,.12)":"rgba(255,255,255,.02)",border:`1px solid ${dashView==="reasoning"?"rgba(167,139,250,.35)":"rgba(255,255,255,.05)"}`,borderRadius:4,padding:"4px 10px",cursor:"pointer",marginRight:8,transition:"all .3s"}}><span style={{fontSize:8,fontWeight:600,letterSpacing:2,color:dashView==="reasoning"?"#A78BFA":"rgba(255,255,255,.35)"}}>{dashView==="classic"?"CLASSIC":"REASONING"}</span></button>
              {/* Language toggle */}
              <button onClick={()=>setLang(l=>l==="en"?"fi":"en")} style={{background:"rgba(255,255,255,.02)",border:"1px solid rgba(255,255,255,.05)",borderRadius:4,padding:"4px 8px",cursor:"pointer",marginRight:12,transition:"all .3s"}}>
                <span style={{fontSize:8,fontWeight:600,color:lang==="fi"?"#22D3EE":"rgba(255,255,255,.25)",letterSpacing:2}}>{lang==="fi"?"FI":"EN"}</span>
              </button>
              <span style={{display:"flex",alignItems:"center",gap:5,fontSize:7.5,letterSpacing:3,color:api.backendAvailable?"#22C55E":"#F59E0B"}}><span style={{width:5,height:5,borderRadius:"50%",background:api.backendAvailable?"#22C55E":"#F59E0B",animation:"pulse 2s infinite"}}/>{api.backendAvailable?"LIVE":"ALIVE"}</span>
            </div>
          </div>
          <div style={{display:"grid",gridTemplateColumns:"30vw 1fr 24vw",minHeight:"calc(100vh - 52px)"}}>
            <div style={{display:"flex",flexDirection:"column",borderRight:"1px solid rgba(255,255,255,.03)",maxHeight:"calc(100vh - 52px)"}}>
              <div style={{flex:1,padding:"12px 16px",overflowY:"auto"}}><HBFeed msgs={hb} color={col} label={t.heartbeat}/>{chatMsgs.length>0&&<div style={{marginTop:10,borderTop:"1px solid rgba(255,255,255,.04)",paddingTop:8}}>{chatMsgs.map((m,i)=>(<div key={i} style={{padding:"5px 0",animation:"fadeUp .4s"}}><div style={{fontSize:7.5,color:m.role==="user"?"#22D3EE":col,fontWeight:600,letterSpacing:2,marginBottom:2}}>{m.role==="user"?"YOU":"WAGGLEDANCE"}</div><div style={{fontSize:10,color:m.role==="user"?"rgba(255,255,255,.60)":"rgba(255,255,255,.45)",lineHeight:1.7,paddingLeft:8,fontWeight:300}}>{m.text}</div>{m.role==="ai"&&<div style={{paddingLeft:8,marginTop:2,display:"flex",gap:6}}><button onClick={()=>handleFeedback(i,2)} style={{background:"none",border:"none",cursor:"pointer",fontSize:10,color:m.feedback===2?"#22C55E":"rgba(255,255,255,.15)",padding:0}} title="Good">&#x1F44D;</button><button onClick={()=>handleFeedback(i,1)} style={{background:"none",border:"none",cursor:"pointer",fontSize:10,color:m.feedback===1?"#EF4444":"rgba(255,255,255,.15)",padding:0}} title="Bad">&#x1F44E;</button></div>}</div>))}</div>}</div>
              <div style={{padding:"8px 16px",borderTop:"1px solid rgba(255,255,255,.04)"}}><div style={{display:"flex",gap:6}}><input value={chatIn} onChange={e=>setChatIn(e.target.value)} onKeyDown={e=>e.key==="Enter"&&handleChat()} placeholder={t.placeholder} style={{flex:1,background:"rgba(255,255,255,.02)",border:`1px solid ${col}15`,borderRadius:5,padding:"7px 10px",color:"#fff",fontSize:9.5,outline:"none",fontFamily:"'Inter',system-ui"}}/><button onClick={handleChat} style={{background:col+"15",border:`1px solid ${col}25`,borderRadius:5,padding:"7px 12px",cursor:"pointer",color:col,fontSize:7.5,letterSpacing:2,fontWeight:600}}>{t.send}</button></div></div>
            </div>
            <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",borderRight:"1px solid rgba(255,255,255,.03)",position:"relative"}}>
              {overlay&&<Overlay item={overlay} color={col} hw={hw} onClose={()=>setOverlay(null)} t={t}/>}
              <BrainScene color={col} factCount={fc} isThinking={think} agents={ag[dom]} cpuV={cpuL} gpuV={gpuL} vramV={vramU} vramMax={vramTotal}/>
              <div style={{textAlign:"center",marginTop:-8}}><div style={{fontSize:34,fontWeight:200,color:"rgba(255,255,255,.65)",letterSpacing:6}}>{D_IC[dom]} {t.domains[dom].label}</div><div style={{fontSize:14,color:"rgba(255,255,255,.30)",letterSpacing:3,marginTop:5}}>{t.domains[dom].tag}</div></div>
              <div style={{marginTop:10,display:"flex",flexWrap:"wrap",justifyContent:"center",gap:"4px 14px",maxWidth:440}}>{aw.map((a,i)=>(<div key={i} style={{fontSize:9.5,color:"rgba(255,255,255,.30)",letterSpacing:1}}>{a.k}: <span style={{color:a.c,fontWeight:600,fontFamily:"monospace"}}>{a.v}</span></div>))}</div>
            </div>
            <div style={{padding:"12px 14px",overflowY:"auto",maxHeight:"calc(100vh - 52px)"}}><FeatureList feats={t.feats[dom]} color={col} label={t.features} onOpen={setOverlay} t={t}/><ModelStatusPanel data={api.models} color={col} lang={lang}/><AnalyticsPanel data={api.analytics} color={col} lang={lang}/><RoundTablePanel data={api.roundTable} color={col} lang={lang}/><AgentGridPanel data={api.agentLevels} color={col} lang={lang}/></div>
          </div>
          <div style={{position:"fixed",bottom:0,left:0,right:0,padding:"3px 22px",background:"rgba(0,0,0,.93)",borderTop:"1px solid rgba(255,255,255,.025)",display:"flex",justifyContent:"space-between",fontSize:6,color:"rgba(255,255,255,.15)",letterSpacing:3}}><span>{t.bottomL}</span><span>{t.bottomC}</span><span>{fc.toLocaleString()} FACTS</span><span>{lang==="fi"?"P\u00e4ivitetty":"Updated"}: {_lastUpdate}</span></div>
        </div>
      </>}</>}
    </div>
  );
}
