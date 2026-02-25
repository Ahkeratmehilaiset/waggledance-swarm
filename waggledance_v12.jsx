import { useState, useEffect, useRef, useCallback } from "react";

const hx = (v) => Math.round(Math.max(0, Math.min(255, v || 0))).toString(16).padStart(2, "0");

const HW = {
  gadget: { name: "Raspberry Pi 5 / ESP32-S3", cpu: "Cortex-A76 4C / Xtensa LX7", ram: "8 GB / 512 KB", gpu: "VideoCore VII / none", storage: "128 GB SD / 16 MB Flash", power: "5W / 0.5W", tier: "EDGE", price: "~80€ / ~8€", models: "qwen3:0.6b (RPi) / TinyML (ESP32)", inference: "CPU quantized / on-chip ML", calc: { llm_toks: 5, fact_pipeline: "18s", facts_hour_real: 12, facts_day: 288, facts_week: "2,016", facts_year: "105,120", chat_cold: "12,000ms", chat_warm: "800ms", chat_micro: "45ms", chat_evolution: "12s → 800ms → 45ms", round_table: "N/A", agents_max: 2, night_8h: "~96", micro_gen: "~8 vk" } },
  cottage: { name: "Intel NUC 13 Pro", cpu: "i7-1360P 12C/16T", ram: "32 GB DDR5", gpu: "Iris Xe (shared)", storage: "1 TB NVMe", power: "28W", tier: "LIGHT", price: "~650€", models: "qwen3:0.6b + nomic-embed + Opus-MT", inference: "CPU-only GGUF", calc: { llm_toks: 15, fact_pipeline: "6.8s", facts_hour_real: 65, facts_day: 1560, facts_week: "10,920", facts_year: "569,400", chat_cold: "3,820ms", chat_warm: "180ms", chat_micro: "12ms", chat_evolution: "3,820ms → 180ms → 12ms", round_table: "45s", agents_max: 8, night_8h: "~520", micro_gen: "~3 vk" } },
  home: { name: "Mac Mini Pro M4", cpu: "M4 Pro 14-core", ram: "48 GB unified", gpu: "20-core GPU 48GB", storage: "1 TB NVMe", power: "30W", tier: "PRO", price: "~2,200€", models: "phi4:14b + llama3.3:8b + whisper + Piper", inference: "Metal GPU (MLX)", calc: { llm_toks: 42, fact_pipeline: "2.4s", facts_hour_real: 220, facts_day: 5280, facts_week: "36,960", facts_year: "1,927,200", chat_cold: "920ms", chat_warm: "42ms", chat_micro: "3ms", chat_evolution: "920ms → 42ms → 3ms", round_table: "12s", agents_max: 25, night_8h: "~1,760", micro_gen: "~8 pv" } },
  factory: { name: "NVIDIA DGX B200", cpu: "2× Grace 144 ARM", ram: "960 GB LPDDR5X", gpu: "8× B200 1.4TB HBM3e", storage: "30 TB NVMe", power: "4.5kW", tier: "ENTERPRISE", price: "~400,000€", models: "llama3.3:70b + vision + 50 micro", inference: "8-way tensor parallel", calc: { llm_toks: 380, fact_pipeline: "0.42s", facts_hour_real: 2800, facts_day: 67200, facts_week: "470,400", facts_year: "24,528,000", chat_cold: "155ms", chat_warm: "8ms", chat_micro: "0.08ms", chat_evolution: "155ms → 8ms → 0.08ms", round_table: "1.8s", agents_max: 50, night_8h: "~22,400", micro_gen: "~36h" } },
};

// ═══ BILINGUAL SYSTEM ═══
const L = {
  en: {
    heartbeat: "NEURAL ACTIVITY — LIVE AGENT FEED",
    features: "FEATURES & INTEGRATION",
    placeholder: "Ask WaggleDance something...",
    hwSpec: "HARDWARE SPEC", hwDesc: "specs + calculated performance",
    agents: "50 CUSTOM AGENTS", agentsDesc: "YAML config guide",
    techArch: "TECHNICAL ARCHITECTURE", techArchDesc: "how WaggleDance intelligence works",
    disclaimer: "DISCLAIMER & CREDITS", disclaimerDesc: "liability, credits, origin",
    bottomL: "LOCAL-FIRST • ZERO CLOUD • YOUR DATA",
    bottomC: "CONTINUOUS SELF-LEARNING • NO LIMITS",
    send: "SEND",
    domains: {
      gadget: { label: "GADGET", tag: "ESP32 • RPi • wearables • edge intelligence everywhere" },
      home: { label: "HOME", tag: "47 devices • 6 rooms • smart living" },
      cottage: { label: "COTTAGE", tag: "300 hives • sauna • off-grid intelligence" },
      factory: { label: "FACTORY", tag: "142 equipment • 24/7 semiconductor production" },
    },
    feats: {
      gadget: [
        { title: "EDGE INTELLIGENCE", desc: "AI on any CPU device", guide: "WaggleDance Lite runs on:\n• Raspberry Pi 5 (full agent, 8GB)\n• Raspberry Pi Zero 2W (sensor relay)\n• ESP32-S3 (TinyML classifier)\n• Old Android phones (Termux)\n• Any Linux SBC\n\nInstall:\ncurl -sSL waggle.sh | bash\n\nAuto-detects hardware and loads models." },
        { title: "MESH NETWORKING", desc: "Gadgets talk to each other", guide: "MQTT mesh with mDNS discovery.\nESP32 sensors → RPi hub → NUC brain.\nCollective intelligence grows." },
        { title: "TINYML CLASSIFIER", desc: "On-chip inference <1ms", guide: "ESP32-S3 TinyML:\n• Sound classification (bee health)\n• Vibration patterns (equipment)\n• Temperature anomalies\n• Motion classification\n\nTrained on main node, deployed OTA." },
        { title: "SENSOR FUSION", desc: "Combine any sensor data", guide: "Temp, humidity, weight, sound,\ncamera, motion, light, air, soil.\nAll → MQTT → vector memory.\nAgents learn correlations." },
        { title: "OTA UPDATES", desc: "Remote firmware", guide: "Auto firmware push to all edge nodes.\nRollback on failure. Zero touch." },
        { title: "BATTERY OPTIMIZATION", desc: "Deep sleep + wake", guide: "ESP32 deep sleep: 10µA.\nSolar + 18650: months of battery life." },
        { title: "LOCAL DASHBOARD", desc: "Web UI any device", guide: "Port 8080. Phone/tablet responsive.\nNo internet required." },
        { title: "SWARM RELAY", desc: "Extend intelligence range", guide: "Gadgets as relay nodes.\n340m effective mesh range." },
      ],
      home: [
        { title: "HOME ASSISTANT BRIDGE", desc: "2000+ integrations", guide: "settings.yaml:\nhome_assistant:\n  url: http://homeassistant.local:8123\n  token: YOUR_TOKEN\n  auto_discover: true" },
        { title: "VOICE CONTROL", desc: "Whisper + Piper TTS", guide: "Wake: \"Hei WaggleDance\"\nWhisper (FI ~15% WER) + Piper TTS\nAlexa/Siri → HA → WaggleDance" },
        { title: "ENERGY AI", desc: "Spot price + solar", guide: "Porssisähkö auto. Cheapest window.\nSolar + battery. Saves ~15-30%." },
        { title: "CLIMATE INTELLIGENCE", desc: "Room-by-room learning", guide: "Via HA: Nest, Ecobee, Tado.\nLearns prefs, patterns, pre-heat." },
        { title: "SECURITY", desc: "Frigate + locks + AI", guide: "Frigate NVR. Face recognition,\nauto-lock, anomaly, Telegram." },
        { title: "ROUND TABLE", desc: "Multi-agent consensus", guide: "6 agents debate, Queen decides.\nconsensus: 0.7, timeout: 30s." },
        { title: "NIGHT LEARNING", desc: "Autonomous growth", guide: "6 layers: ~200 facts/night.\nIndex → enrich → web → distill → meta." },
        { title: "MICRO-MODEL", desc: "Latency → zero", guide: "Gen 0→5+: 3000ms → 0.3ms.\n23%+ queries without main LLM." },
      ],
      cottage: [
        { title: "HIVE MONITORING", desc: "300 colonies", guide: "ESP32+HX711 (~25€). WiFi→MQTT.\nAlerts: swarm, queen, flow end." },
        { title: "AI CAMERAS", desc: "Bear detect <2s", guide: "RPi5+Coral (~110€)+RTSP.\nBear→Telegram. PTZ patrol." },
        { title: "SOUND ANALYSIS", desc: "Acoustic health", guide: "ESP32-S3+INMP441 (~20€/hive).\n250Hz ok | +30Hz stress." },
        { title: "WEATHER", desc: "FMI Open Data", guide: "Auto. Temp, wind, precip + 48h." },
        { title: "SPOT PRICE", desc: "Electricity opt.", guide: "Auto scheduling. 15-30% savings." },
        { title: "SELF-LEARNING", desc: "47,293+ facts", guide: "~880/night → 250K+/year. Validated." },
        { title: "94.2% ACCURACY", desc: "Expert 60/64", guide: "Finnish terms, local climate, YOUR data." },
        { title: "WILDLIFE ID", desc: "BirdNET 6000+", guide: "Cornell Lab. Same ESP32." },
      ],
      factory: [
        { title: "PREDICTIVE MAINT.", desc: "72h prediction", guide: "OPC-UA vibration/thermal.\n72h before failure. Zero downtime." },
        { title: "YIELD PREDICTION", desc: "98.7% per-lot", guide: "SECS/GEM: CD, film, overlay.\nIn-line → final yield prediction." },
        { title: "AIR-GAPPED", desc: "Zero cloud/risk", guide: "No internet. ISO 27001, ITAR, GDPR." },
        { title: "OPC-UA / SECS/GEM", desc: "Standard protocols", guide: "AMAT, Lam, TEL, ASML, KLA.\nBidirectional with approval." },
        { title: "SHIFT HANDOVER", desc: "Auto reports", guide: "PDF at shift change." },
        { title: "SPC + DRIFT", desc: "Statistical + AI", guide: "Western Electric + AI. 2σ/3σ." },
        { title: "ROOT CAUSE", desc: "Multi-factor", guide: "Causal graph per lot." },
        { title: "SELF-LEARNING", desc: "Digital twin", guide: "1K→10K→100K: full digital twin." },
      ],
    },
    agentsGuide: "Define agents in YAML:\n\n# agents/my_agent.yaml\nname: my_agent\nspecialties: [topic1, topic2]\ntools: [chromadb, web_search]\nlevel: 1  # auto 1→5\n\nLevels: NOVICE → MASTER\nAuto-discovers /agents/*.yaml",
    info: `═══ WAGGLEDANCE AI — TECHNICAL ARCHITECTURE ═══

▸ MULTI-MODEL MEMORY STACK
Multiple specialized models support each other:
  • LLM (phi4/llama/qwen) — reasoning, validation
  • Embedding (nomic-embed) — 768-dim vectors
  • Translation (Opus-MT) — Finnish ↔ English
  • MicroModel (self-trained) — 23%+ queries at <1ms
  • Whisper STT + Piper TTS — voice (optional)

▸ VECTOR MEMORY (ChromaDB)
Every fact embedded as 768-dim vector. Query → cosine search → top-K in ~5ms. Perfect memory. Never forgets.

▸ BILINGUAL INDEX (55ms)
All facts indexed FI+EN simultaneously. Finnish query finds English facts. Doubles knowledge without doubling storage.

▸ 6-LAYER SELF-LEARNING (24/7)
  L1: Bilingual vector indexing
  L2: Gap detection + enrichment (~200/night)
  L3: Web learning (~100/night)
  L4: Claude distillation (50/week)
  L5: Meta-learning — optimizes itself
  L6: Code self-review

▸ ROUND TABLE CONSENSUS
Up to 50 agents debate. Queen synthesizes. Hallucination: 1.8%.

▸ MICROMODEL EVOLUTION
  Gen 0: No micro → Gen 5+: LoRA 135M nano-LLM
  Result: 3,000ms → 0.3ms

▸ VS. CLOUD AI (OpenAI, etc.)
  ✗ Data leaves network    ✓ Data stays local
  ✗ Monthly fees            ✓ One-time hardware
  ✗ No memory              ✓ 47K+ permanent facts
  ✗ Generic                ✓ YOUR domain expert
  ✗ 500-3000ms latency     ✓ 0.08ms with MicroModel
  ✗ Rate limits            ✓ Unlimited 24/7

▸ HARDWARE SCALING
  ESP32 (€8) → RPi (€80) → NUC (€650) → Mac (€2.2K) → DGX (€400K)
  Same code. Only speed differs.

▸ JUST LET IT RUN
Install. Connect. Walk away.
1 week: knows your patterns.
1 month: anticipates your needs.
1 year: understands your world.`,
    hb: {
      gadget: [
        { a: "Mesh Hub", m: "12 edge devices connected. 8 online, 2 sleep, 2 charging. MQTT: 12ms avg.", t: "status" },
        { a: "TinyML", m: "ESP32-07 classified queen piping at 94% confidence. Forwarded to Cottage brain.", t: "insight" },
        { a: "Battery", m: "Solar node ESP32-03: 78%. Estimated 18 days to next charge. 22h/day sleep.", t: "status" },
        { a: "OTA", m: "Firmware v2.4.1 → 8 nodes. All confirmed. New sound classifier included.", t: "action" },
        { a: "Fusion", m: "Anomaly: Hive 23 weight+temp diverged from baseline. Flagged for review.", t: "insight" },
        { a: "Relay", m: "Mesh range test: barn → greenhouse → house. 340m confirmed.", t: "learning" },
      ],
      home: [
        { a: "Climate AI", m: "Living room 21.3°C optimal. Bedroom pre-cooling 19°C for 22:30 bedtime.", t: "insight" },
        { a: "Energy AI", m: "Spot 1.8c/kWh — cheapest. Floor heating on. Saving: 0.47€.", t: "action" },
        { a: "Security", m: "6 zones quiet 4h. Door locked 18:32. No anomalies.", t: "status" },
        { a: "Lighting", m: "Circadian → 2700K. Sunset 47 min. Living 60% for reading.", t: "action" },
        { a: "Round Table", m: "CONSENSUS 4/5: dishwasher → 02:00 at 0.9c. Approved.", t: "consensus" },
        { a: "MicroModel", m: "Gen 8. Accuracy 96.1%. 23.4% micro-only at 2.8ms.", t: "learning" },
      ],
      cottage: [
        { a: "Tarhaaja", m: "Hive 12: 248Hz healthy. 34.2kg (+0.3). Moderate foraging.", t: "status" },
        { a: "Tautivahti", m: "Varroa avg 1.2/100. Below 3/100 threshold. Hive 7: recheck.", t: "insight" },
        { a: "Meteorologi", m: "FMI: Thu −6°C 04:00. Oxalic window Thu 09-11.", t: "insight" },
        { a: "Sähkö", m: "Now 2.4c. Tonight 23-02 at 0.8c. Sauna ~1.20€.", t: "action" },
        { a: "Round Table", m: "TREATMENT 5/5: Thursday optimal. APPROVED.", t: "consensus" },
        { a: "Enrichment", m: "Night: 47 flora facts. KB: 47,340.", t: "learning" },
      ],
      factory: [
        { a: "Process", m: "Etch 7: CD 1.1σ. 487 since PM. 12 SPC in control.", t: "status" },
        { a: "Yield AI", m: "WF-2851: 98.9%. CD 22.3nm ±0.4. HIGH.", t: "insight" },
        { a: "Equipment", m: "Pump 12 bearing 3.2×. Failure 68h.", t: "insight" },
        { a: "Round Table", m: "MAINT: Yield→ch.8, PM 02-06. APPROVED.", t: "consensus" },
        { a: "Shift Mgr", m: "B→C 2h. 14 lots, 94.2% util.", t: "action" },
        { a: "Meta", m: "Yield +0.4%. RF↔particles stored.", t: "learning" },
      ],
    },
  },
  fi: {
    heartbeat: "HERMOVERKKO — REAALIAIKAINEN AGENTTISYÖTE",
    features: "OMINAISUUDET & INTEGRAATIOT",
    placeholder: "Kysy WaggleDancelta jotain...",
    hwSpec: "LAITTEISTO", hwDesc: "tekniset tiedot + laskettu suorituskyky",
    agents: "50 MUKAUTETTUA AGENTTIA", agentsDesc: "YAML-konfiguraatio-opas",
    techArch: "TEKNINEN ARKKITEHTUURI", techArchDesc: "miten WaggleDancen älykkyys muodostuu",
    disclaimer: "VASTUUVAPAUS & TEKIJÄT", disclaimerDesc: "vastuu, tekijät, alkuperä",
    bottomL: "PAIKALLINEN • EI PILVEÄ • SINUN DATASI",
    bottomC: "JATKUVA ITSEOPPIMINEN • EI RAJOJA",
    send: "LÄHETÄ",
    domains: {
      gadget: { label: "LAITE", tag: "ESP32 • RPi • puettavat • reunaälyä kaikkialle" },
      home: { label: "KOTI", tag: "47 laitetta • 6 huonetta • älykäs asuminen" },
      cottage: { label: "MÖKKI", tag: "300 pesää • sauna • off-grid-älykkyys" },
      factory: { label: "TEHDAS", tag: "142 laitetta • 24/7 puolijohdetuotanto" },
    },
    feats: {
      gadget: [
        { title: "REUNAÄLYKKYYS", desc: "Tekoäly millä tahansa laitteella", guide: "WaggleDance Lite toimii:\n• Raspberry Pi 5 (täysi agentti, 8GB)\n• Raspberry Pi Zero 2W (sensorisilta)\n• ESP32-S3 (TinyML-luokittelija)\n• Vanhat Android-puhelimet (Termux)\n• Mikä tahansa Linux-SBC\n\nAsennus:\ncurl -sSL waggle.sh | bash\n\nTunnistaa laitteiston automaattisesti." },
        { title: "MESH-VERKKO", desc: "Laitteet keskustelevat keskenään", guide: "MQTT-mesh mDNS-löydöksellä.\nESP32-sensorit → RPi-keskitin → NUC-aivot.\nKollektiivinen älykkyys kasvaa." },
        { title: "TINYML-LUOKITTELIJA", desc: "Piirillä <1ms päätelmä", guide: "ESP32-S3 TinyML:\n• Ääniluokittelu (pesäterveys)\n• Tärinäkuviot (laitteet)\n• Lämpötilapoikkeamat\n• Liiketunnistus\n\nKoulutettu pääsolmussa, jaettu OTA:lla." },
        { title: "SENSORIFUUSIO", desc: "Yhdistä mikä tahansa sensoridata", guide: "Lämpö, kosteus, paino, ääni, kamera,\nliike, valo, ilmanlaatu, maankosteus.\nKaikki → MQTT → vektorimuisti.\nAgentit oppivat korrelaatiot." },
        { title: "OTA-PÄIVITYKSET", desc: "Etälaiteohjelmisto", guide: "Automaattinen firmware-jako kaikille.\nPalautus virhetilanteessa." },
        { title: "AKKUOPTIMOINTI", desc: "Syvä uni + herätys", guide: "ESP32 syvä uni: 10µA.\nAurinko + 18650: kuukausia akkua." },
        { title: "PAIKALLINEN NÄKYMÄ", desc: "Web-UI millä tahansa", guide: "Portti 8080. Puhelin/tabletti.\nEi internet-yhteyttä tarvita." },
        { title: "PARVIRELE", desc: "Laajenna älykkyyden kantamaa", guide: "Laitteet relesolmuina.\n340m tehokas mesh-kantama." },
      ],
      home: [
        { title: "HOME ASSISTANT -SILTA", desc: "2000+ integraatiota", guide: "settings.yaml:\nhome_assistant:\n  url: http://homeassistant.local:8123\n  token: AVAIMESI\n  auto_discover: true" },
        { title: "ÄÄNIOHJAUS", desc: "Whisper + Piper TTS", guide: "Heräte: \"Hei WaggleDance\"\nWhisper (FI ~15% WER) + Piper TTS\nAlexa/Siri → HA → WaggleDance" },
        { title: "ENERGIA-AI", desc: "Pörssisähkö + aurinko", guide: "Porssisähkö automaattinen.\nHalvin ikkunan etsintä.\nAurinko + akku. Säästö ~15-30%." },
        { title: "ILMASTOÄLYKKYYS", desc: "Huonekohtainen oppiminen", guide: "HA:n kautta: Nest, Ecobee, Tado.\nOppii: mieltymykset, aikataulut,\nesilämmitys, uniprofiili." },
        { title: "TURVALLISUUS", desc: "Frigate + lukot + AI", guide: "Frigate NVR. Kasvojentunnistus,\nautomaattilukitus, Telegram-hälyt." },
        { title: "PYÖREÄ PÖYTÄ", desc: "Moniagenttikonsensus", guide: "6 agenttia väittelee, Kuningatar päättää.\nKonsensus: 0.7, aikakatkaisu: 30s." },
        { title: "YÖOPPIMINEN", desc: "Itsenäinen kasvu", guide: "6 kerrosta: ~200 faktaa/yö.\nIndeksi → rikastus → web → tislaus → meta." },
        { title: "MIKROMALLI", desc: "Viive → nolla", guide: "Gen 0→5+: 3000ms → 0.3ms.\n23%+ kyselyistä ilman pää-LLM:ää." },
      ],
      cottage: [
        { title: "PESÄSEURANTA", desc: "300 yhdyskuntaa", guide: "ESP32+HX711 (~25€). WiFi→MQTT.\nHälytykset: parveilusta, pesäongelmista." },
        { title: "AI-KAMERAT", desc: "Karhuntunnistus <2s", guide: "RPi5+Coral (~110€)+RTSP.\nKarhu→Telegram. PTZ-partiointi." },
        { title: "ÄÄNIANALYYSI", desc: "Akustinen terveys", guide: "ESP32-S3+INMP441 (~20€/pesä).\n250Hz ok | +30Hz stressi." },
        { title: "SÄÄ", desc: "Ilmatieteen laitos (ilmainen)", guide: "Automaattinen. Lämpö, tuuli,\nsade + 48h ennuste." },
        { title: "PÖRSSISÄHKÖ", desc: "Sähkön optimointi", guide: "Automaattinen aikataulutus.\nSäästö 15-30%." },
        { title: "ITSEOPPIMINEN", desc: "47 293+ faktaa", guide: "~880/yö → 250 000+/vuosi.\nKaikki kaksoismallilla varmistettu." },
        { title: "94.2% TARKKUUS", desc: "Asiantuntija 60/64", guide: "Suomenkielinen termistö, paikallinen\nilmasto, SINUN pesähistoriasi." },
        { title: "ELÄINTUNNISTUS", desc: "BirdNET 6000+", guide: "Cornellin BirdNET. Sama ESP32." },
      ],
      factory: [
        { title: "ENNAKOIVA HUOLTO", desc: "72h ennuste", guide: "OPC-UA tärinä/lämpö.\n72h ennen vikaa. Nolla seisokkia." },
        { title: "SAANTOENNUSTE", desc: "98.7% erittäin", guide: "SECS/GEM: CD, kalvo, overlay.\nIn-line → lopullinen saantoennuste." },
        { title: "ILMAVÄLI", desc: "Ei pilveä. Ei riskiä.", guide: "Ei internettiä. ISO 27001, ITAR, GDPR." },
        { title: "OPC-UA / SECS/GEM", desc: "Standardiprotokollat", guide: "AMAT, Lam, TEL, ASML, KLA.\nKaksisuuntainen hyväksynnällä." },
        { title: "VUORONVAIHTO", desc: "Automaattiraportit", guide: "PDF vuoronvaihdon yhteydessä." },
        { title: "SPC + AJAUTUMA", desc: "Tilastollinen + AI", guide: "Western Electric + AI. 2σ/3σ." },
        { title: "JUURISYY", desc: "Monitekijäanalyysi", guide: "Kausaalinen graafi per erä." },
        { title: "PROSESSIOPPIMINEN", desc: "Digitaalinen kaksonen", guide: "1K→10K→100K: täydellinen kaksonen." },
      ],
    },
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

▸ 6-KERROKSEN ITSEOPPIMINEN (24/7)
  K1: Kaksikielinen vektori-indeksointi
  K2: Aukkojen tunnistus + rikastus (~200/yö)
  K3: Verkkopoiminta (~100/yö)
  K4: Claude-tislaus (50/viikko)
  K5: Meta-oppiminen — optimoi itseään
  K6: Koodin itsearviointi

▸ PYÖREÄ PÖYTÄ -KONSENSUS
Jopa 50 agenttia väittelee. Kuningatar syntetisoi. Hallusinaatio: 1.8%.

▸ MIKROMALLIN EVOLUUTIO
  Gen 0: Ei mikroa → Gen 5+: LoRA 135M nano-LLM
  Tulos: 3 000ms → 0.3ms

▸ MIKSI EI PILVI-AI (OpenAI ym.)
  ✗ Datasi lähtee verkostasi  ✓ Data pysyy koneellasi
  ✗ Kuukausimaksut           ✓ Kertaluonteinen laite
  ✗ Ei muistia              ✓ 47 000+ pysyvää faktaa
  ✗ Geneerinen              ✓ SINUN alueesi asiantuntija
  ✗ 500-3000ms viive        ✓ 0.08ms MikroMallilla
  ✗ Rajoitukset             ✓ Rajaton 24/7

▸ LAITTEISTON SKAALAUTUVUUS
  ESP32 (8€) → RPi (80€) → NUC (650€) → Mac (2,2K€) → DGX (400K€)
  Sama koodi. Vain nopeus vaihtelee.

▸ ANNA SEN VAIN OLLA
Asenna. Yhdistä. Kävele pois.
1 viikko: tuntee tapasi.
1 kuukausi: ennakoi tarpeesi.
1 vuosi: ymmärtää maailmasi.`,
    hb: {
      gadget: [
        { a: "Mesh-keskus", m: "12 reunalaitetta yhdistetty. 8 verkossa, 2 unessa, 2 latauksessa. MQTT: 12ms.", t: "status" },
        { a: "TinyML", m: "ESP32-07 tunnisti kuningattaren piipityksen 94% varmuudella. Välitetty Mökin aivoille.", t: "insight" },
        { a: "Akku", m: "Aurinkosolmu ESP32-03: 78%. Arvioitu 18 päivää seuraavaan lataukseen.", t: "status" },
        { a: "OTA", m: "Firmware v2.4.1 → 8 solmuun. Kaikki vahvistettu. Uusi ääniluokittelija mukana.", t: "action" },
        { a: "Fuusio", m: "Poikkeama: Pesä 23 paino+lämpö poikkeaa perustasosta. Merkitty tarkistettavaksi.", t: "insight" },
        { a: "Rele", m: "Mesh-kantamatesti: navetta → kasvihuone → talo. 340m vahvistettu.", t: "learning" },
      ],
      home: [
        { a: "Ilmasto-AI", m: "Olohuone 21.3°C optimaalinen. Makuuhuone esijäähdytys 19°C klo 22:30 yöunille.", t: "insight" },
        { a: "Energia-AI", m: "Pörssisähkö 1.8c/kWh — halvin tänään. Lattialämmitys päällä. Säästö: 0.47€.", t: "action" },
        { a: "Turva", m: "6 vyöhykettä hiljaa 4h. Ovi lukittu 18:32. Ei poikkeamia.", t: "status" },
        { a: "Valaistus", m: "Vuorokausirytmi → 2700K. Auringonlasku 47 min. Olohuone 60%.", t: "action" },
        { a: "Pyöreä Pöytä", m: "KONSENSUS 4/5: astianpesukone → 02:00 0.9c:llä. Hyväksytty.", t: "consensus" },
        { a: "MikroMalli", m: "Gen 8. Tarkkuus 96.1%. 23.4% kyselyistä mikro — 2.8ms.", t: "learning" },
      ],
      cottage: [
        { a: "Tarhaaja", m: "Pesä 12: 248Hz terve. 34.2kg (+0.3). Kohtalainen saalistus.", t: "status" },
        { a: "Tautivahti", m: "Varroa keskim. 1.2/100. Alle kynnyksen 3/100. Pesä 7: seuranta.", t: "insight" },
        { a: "Meteorologi", m: "IL: torstai −6°C klo 04. Oksaalihappoikkuna to 09-11.", t: "insight" },
        { a: "Sähkö", m: "Nyt 2.4c. Yöllä 23-02 0.8c. Sauna ~1.20€.", t: "action" },
        { a: "Pyöreä Pöytä", m: "HOITO 5/5: Torstai optimaalinen. HYVÄKSYTTY.", t: "consensus" },
        { a: "Rikastus", m: "Yö: 47 kevätkasvifaktaa. Tietokanta: 47 340.", t: "learning" },
      ],
      factory: [
        { a: "Prosessi", m: "Etsaus 7: CD 1.1σ. 487 PM:stä. 12 SPC hallinnassa.", t: "status" },
        { a: "Saanto-AI", m: "WF-2851: 98.9% ennustettu. CD 22.3nm ±0.4. KORKEA.", t: "insight" },
        { a: "Laitteet", m: "Pumppu 12 laakeri 3.2×. Vika 68h. Seuraava huoltoikkuna.", t: "insight" },
        { a: "Pyöreä Pöytä", m: "HUOLTO: Saanto→k.8, PM 02-06. HYVÄKSYTTY.", t: "consensus" },
        { a: "Vuoropääll.", m: "B→C 2h. 14 erää, käyttöaste 94.2%.", t: "action" },
        { a: "Meta", m: "Saanto +0.4%. RF↔partikkelit tallennettu.", t: "learning" },
      ],
    },
  },
};

const D_IDS = ["gadget", "home", "cottage", "factory"];
const D_IC = { gadget: "📡", home: "🏠", cottage: "🏡", factory: "⚙️" };
const D_COL = { gadget: "#22D3EE", home: "#6366F1", cottage: "#F59E0B", factory: "#EF4444" };
const D_RGB = { gadget: "34,211,238", home: "99,102,241", cottage: "245,158,11", factory: "239,68,68" };
const rY = (x,y,z,a)=>({x:x*Math.cos(a)-z*Math.sin(a),y,z:x*Math.sin(a)+z*Math.cos(a)});
const rX = (x,y,z,a)=>({x,y:y*Math.cos(a)-z*Math.sin(a),z:y*Math.sin(a)+z*Math.cos(a)});
const rZ = (x,y,z,a)=>({x:x*Math.cos(a)-y*Math.sin(a),y:x*Math.sin(a)+y*Math.cos(a),z});
const pj = (x,y,z,cx,cy,f)=>{const s=f/(f+z);return{x:cx+x*s,y:cy+y*s,s,z}};

function drawBrainOutline(ctx,cx,cy,s){ctx.beginPath();ctx.moveTo(cx-8*s,cy+68*s);ctx.quadraticCurveTo(cx-14*s,cy+58*s,cx-22*s,cy+48*s);ctx.bezierCurveTo(cx-28*s,cy+52*s,cx-42*s,cy+56*s,cx-52*s,cy+50*s);ctx.bezierCurveTo(cx-62*s,cy+48*s,cx-72*s,cy+44*s,cx-76*s,cy+34*s);ctx.bezierCurveTo(cx-78*s,cy+28*s,cx-80*s,cy+22*s,cx-82*s,cy+16*s);ctx.bezierCurveTo(cx-86*s,cy+4*s,cx-88*s,cy-10*s,cx-86*s,cy-24*s);ctx.bezierCurveTo(cx-85*s,cy-32*s,cx-82*s,cy-42*s,cx-78*s,cy-50*s);ctx.bezierCurveTo(cx-72*s,cy-60*s,cx-64*s,cy-70*s,cx-54*s,cy-78*s);ctx.quadraticCurveTo(cx-48*s,cy-82*s,cx-42*s,cy-80*s);ctx.quadraticCurveTo(cx-36*s,cy-85*s,cx-28*s,cy-86*s);ctx.quadraticCurveTo(cx-20*s,cy-88*s,cx-12*s,cy-87*s);ctx.quadraticCurveTo(cx-4*s,cy-90*s,cx+5*s,cy-89*s);ctx.quadraticCurveTo(cx+14*s,cy-91*s,cx+22*s,cy-88*s);ctx.quadraticCurveTo(cx+30*s,cy-86*s,cx+38*s,cy-83*s);ctx.quadraticCurveTo(cx+46*s,cy-80*s,cx+54*s,cy-74*s);ctx.quadraticCurveTo(cx+60*s,cy-68*s,cx+66*s,cy-60*s);ctx.bezierCurveTo(cx+74*s,cy-50*s,cx+80*s,cy-38*s,cx+84*s,cy-24*s);ctx.bezierCurveTo(cx+86*s,cy-14*s,cx+86*s,cy-2*s,cx+82*s,cy+8*s);ctx.bezierCurveTo(cx+78*s,cy+16*s,cx+72*s,cy+22*s,cx+64*s,cy+26*s);ctx.quadraticCurveTo(cx+56*s,cy+30*s,cx+48*s,cy+32*s);ctx.quadraticCurveTo(cx+38*s,cy+36*s,cx+28*s,cy+38*s);ctx.quadraticCurveTo(cx+18*s,cy+40*s,cx+8*s,cy+42*s);ctx.quadraticCurveTo(cx-2*s,cy+44*s,cx-12*s,cy+46*s);ctx.bezierCurveTo(cx-16*s,cy+48*s,cx-18*s,cy+52*s,cx-14*s,cy+58*s);ctx.quadraticCurveTo(cx-10*s,cy+64*s,cx-8*s,cy+68*s);ctx.closePath()}
function drawBrainDetails(ctx,cx,cy,s,color){const ss=(a)=>{ctx.strokeStyle=color+a;ctx.lineWidth=.6};ss("18");ctx.beginPath();ctx.moveTo(cx+12*s,cy-88*s);ctx.bezierCurveTo(cx+8*s,cy-70*s,cx+2*s,cy-45*s,cx-5*s,cy-15*s);ctx.stroke();ss("1A");ctx.beginPath();ctx.moveTo(cx+62*s,cy+22*s);ctx.bezierCurveTo(cx+40*s,cy+8*s,cx+15*s,cy-4*s,cx-20*s,cy-12*s);ctx.stroke();ss("10");ctx.beginPath();ctx.moveTo(cx+30*s,cy-84*s);ctx.bezierCurveTo(cx+26*s,cy-62*s,cx+20*s,cy-35*s,cx+14*s,cy-10*s);ctx.stroke();ss("10");ctx.beginPath();ctx.moveTo(cx-10*s,cy-87*s);ctx.bezierCurveTo(cx-16*s,cy-65*s,cx-22*s,cy-40*s,cx-28*s,cy-18*s);ctx.stroke();ss("0C");ctx.beginPath();ctx.moveTo(cx+76*s,cy-30*s);ctx.bezierCurveTo(cx+58*s,cy-48*s,cx+42*s,cy-60*s,cx+34*s,cy-68*s);ctx.stroke();ss("0C");ctx.beginPath();ctx.moveTo(cx+78*s,cy-5*s);ctx.bezierCurveTo(cx+60*s,cy-14*s,cx+42*s,cy-22*s,cx+28*s,cy-28*s);ctx.stroke();ss("0E");ctx.beginPath();ctx.moveTo(cx+55*s,cy+28*s);ctx.bezierCurveTo(cx+30*s,cy+18*s,cx+5*s,cy+10*s,cx-15*s,cy+2*s);ctx.stroke();ss("0E");ctx.beginPath();ctx.moveTo(cx-24*s,cy-84*s);ctx.bezierCurveTo(cx-38*s,cy-68*s,cx-52*s,cy-52*s,cx-62*s,cy-38*s);ctx.stroke();ss("10");ctx.beginPath();ctx.moveTo(cx-50*s,cy-80*s);ctx.bezierCurveTo(cx-58*s,cy-60*s,cx-66*s,cy-38*s,cx-72*s,cy-20*s);ctx.stroke();ss("0A");for(let i=0;i<4;i++){const y=cy+(36+i*4)*s;ctx.beginPath();ctx.moveTo(cx-(35+i*4)*s,y);ctx.quadraticCurveTo(cx-(52+i*2)*s,y-s,cx-(68-i*3)*s,y+s);ctx.stroke()}}

function BrainScene({color,factCount,isThinking,agents,cpuV,gpuV,vramV}){
  const ref=useRef(null);const fr=useRef(0);const vec=useRef([]);
  useEffect(()=>{const c=ref.current;if(!c)return;const ctx=c.getContext("2d");const W=500,H=400,dpr=Math.min(devicePixelRatio||1,2);c.width=W*dpr;c.height=H*dpr;c.style.width=W+"px";c.style.height=H+"px";ctx.scale(dpr,dpr);const cx=W/2,cy=H/2,fov=480,N=agents.length;let id;
  const dG=(gx,gy,val,max,label,gc)=>{const pct=Math.min(val/max,1),r=22,sa=-Math.PI*.75,ea=Math.PI*.75;ctx.beginPath();ctx.arc(gx,gy,r,sa,ea);ctx.strokeStyle="rgba(255,255,255,.025)";ctx.lineWidth=2.5;ctx.lineCap="round";ctx.stroke();ctx.beginPath();ctx.arc(gx,gy,r,sa,sa+(ea-sa)*pct);ctx.strokeStyle=gc+"80";ctx.lineWidth=2.5;ctx.lineCap="round";ctx.stroke();ctx.font="500 6px 'Inter',system-ui";ctx.textAlign="center";ctx.fillStyle="rgba(255,255,255,.07)";ctx.fillText(label,gx,gy-9);ctx.font="600 10px 'Inter',system-ui";ctx.fillStyle=gc+"A0";ctx.fillText(val+(max===8?"G":"%"),gx,gy+5)};
  const draw=()=>{fr.current++;const f=fr.current;ctx.clearRect(0,0,W,H);const wa=f*.0004;
  [[110,.3,0,1,50,.8,22],[94,-.5,.8,-1,36,.5,11],[122,.7,-.4,1,42,.35,7]].forEach(([r,tx2,tz,dir,pts,w,ba])=>{const pp=[];for(let i=0;i<pts;i++){const a=(Math.PI*2*i)/pts;let p={x:Math.cos(a)*r,y:Math.sin(a)*r,z:0};p=rX(p.x,p.y,p.z,tx2);p=rZ(p.x,p.y,p.z,tz);p=rY(p.x,p.y,p.z,wa*dir);pp.push(pj(p.x,p.y,p.z,cx,cy,fov))}for(let i=0;i<pp.length;i++){const a2=pp[i],b=pp[(i+1)%pp.length],d=(a2.s+b.s)*.5,al=ba*d*d;if(al<1)continue;ctx.beginPath();ctx.moveTo(a2.x,a2.y);ctx.lineTo(b.x,b.y);ctx.strokeStyle=color+hx(al);ctx.lineWidth=w*d;ctx.stroke()}});
  const aR=70,a3=[];for(let i=0;i<N;i++){const phi=Math.acos(1-2*(i+.5)/N),th=Math.PI*(1+Math.sqrt(5))*i;let p={x:aR*Math.sin(phi)*Math.cos(th),y:aR*Math.sin(phi)*Math.sin(th),z:aR*Math.cos(phi)};p=rY(p.x,p.y,p.z,wa*.7);a3.push({...pj(p.x,p.y,p.z,cx,cy,fov),x3:p.x,y3:p.y,z3:p.z,i})}
  for(let i=0;i<N;i++)for(let j=i+1;j<N;j++){const al=Math.min(a3[i].s,a3[j].s)*5;if(al<1)continue;ctx.beginPath();ctx.moveTo(a3[i].x,a3[i].y);ctx.lineTo(a3[j].x,a3[j].y);ctx.strokeStyle=color+hx(al);ctx.lineWidth=.25;ctx.stroke()}
  for(let i=0;i<N;i++){const al=a3[i].s*3.5;if(al<1)continue;ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(a3[i].x,a3[i].y);ctx.strokeStyle=color+hx(al);ctx.lineWidth=.12;ctx.stroke()}
  if(f%8===0&&isThinking){const f2=Math.floor(Math.random()*N);let to=Math.floor(Math.random()*N);if(to===f2)to=(to+1)%N;vec.current.push({fr:f2,to,p:0,sp:.04+Math.random()*.03})}
  if(f%14===0)vec.current.push({fr:Math.floor(Math.random()*N),to:-1,p:0,sp:.035+Math.random()*.025});
  if(vec.current.length>14)vec.current=vec.current.slice(-14);vec.current=vec.current.filter(v=>v.p<=1.05);
  vec.current.forEach(v=>{v.p+=v.sp;const fp=a3[v.fr],tp=v.to===-1?{x3:0,y3:0,z3:0}:a3[v.to];const mx=fp.x3+(tp.x3-fp.x3)*v.p,my=fp.y3+(tp.y3-fp.y3)*v.p,mz=fp.z3+(tp.z3-fp.z3)*v.p;const mp=pj(mx,my,mz,cx,cy,fov),fade=v.p<.08?v.p*12.5:v.p>.88?(1.05-v.p)*5.9:1;for(let tr=1;tr<=3;tr++){const t2=Math.max(0,v.p-tr*.05);const tp2=pj(fp.x3+(tp.x3-fp.x3)*t2,fp.y3+(tp.y3-fp.y3)*t2,fp.z3+(tp.z3-fp.z3)*t2,cx,cy,fov);ctx.beginPath();ctx.arc(tp2.x,tp2.y,(1.7-tr*.35)*tp2.s,0,Math.PI*2);ctx.fillStyle=color+hx(fade*(1-tr*.3)*tp2.s*100);ctx.fill()}const g2=ctx.createRadialGradient(mp.x,mp.y,0,mp.x,mp.y,7*mp.s);g2.addColorStop(0,color+hx(fade*mp.s*38));g2.addColorStop(1,color+"00");ctx.beginPath();ctx.arc(mp.x,mp.y,7*mp.s,0,Math.PI*2);ctx.fillStyle=g2;ctx.fill();ctx.beginPath();ctx.arc(mp.x,mp.y,2*mp.s,0,Math.PI*2);ctx.fillStyle=color+hx(fade*mp.s*185);ctx.fill()});
  [...a3].sort((a2,b)=>a2.z-b.z).forEach(p=>{const pu=Math.sin(f*.025+p.i*1.5)*.3+.7,da=p.s*p.s;const ng=ctx.createRadialGradient(p.x,p.y,0,p.x,p.y,12*p.s);ng.addColorStop(0,color+hx(da*pu*16));ng.addColorStop(1,color+"00");ctx.beginPath();ctx.arc(p.x,p.y,12*p.s,0,Math.PI*2);ctx.fillStyle=ng;ctx.fill();ctx.beginPath();ctx.arc(p.x,p.y,4.5*p.s,0,Math.PI*2);ctx.strokeStyle=color+hx(da*pu*105);ctx.lineWidth=.6*p.s;ctx.stroke();ctx.beginPath();ctx.arc(p.x,p.y,1.8*p.s,0,Math.PI*2);ctx.fillStyle=color+hx(da*185);ctx.fill();if(p.s>.7){ctx.font=`500 ${Math.round(6*p.s)}px 'Inter',system-ui`;ctx.textAlign="center";ctx.fillStyle=color+hx(da*58);ctx.fillText(agents[p.i],p.x,p.y+11*p.s)}});
  const cp=Math.sin(f*.018)*.3+.7;ctx.beginPath();ctx.arc(cx,cy,4+(isThinking?cp*2.5:cp),0,Math.PI*2);ctx.strokeStyle=color+hx(cp*16+4);ctx.lineWidth=.4;ctx.stroke();ctx.beginPath();ctx.arc(cx,cy,1.2,0,Math.PI*2);ctx.fillStyle=color+"50";ctx.fill();
  ctx.font="500 6px 'Inter',system-ui";ctx.fillStyle=color+"22";ctx.textAlign="center";ctx.fillText("CONSCIOUSNESS",cx,cy-20);
  ctx.font="300 9px 'Inter',system-ui";ctx.fillStyle=color+"40";ctx.fillText("IQ",cx-28,cy+5);
  ctx.font="200 19px 'Inter',system-ui";ctx.fillStyle=color+"68";ctx.fillText(factCount.toLocaleString(),cx+6,cy+6);
  ctx.font="300 5px 'Inter',system-ui";ctx.fillStyle=color+"16";ctx.fillText("FACTS LEARNED",cx,cy+17);
  if(isThinking){ctx.font="500 5px 'Inter',system-ui";ctx.fillStyle=color+"38";ctx.fillText("● THINKING",cx,cy+28)}
  const bs=1.6;drawBrainOutline(ctx,cx,cy,bs);ctx.strokeStyle=color+"1E";ctx.lineWidth=1.6;ctx.stroke();drawBrainDetails(ctx,cx,cy,bs,color);
  dG(34,cy-18,cpuV,100,"CPU","#22C55E");dG(W-34,cy-32,gpuV,100,"GPU",color);dG(W-34,cy+32,vramV,8,"VRAM","#A78BFA");
  id=requestAnimationFrame(draw)};draw();return()=>cancelAnimationFrame(id)},[color,factCount,isThinking,agents,cpuV,gpuV,vramV]);
  return <canvas ref={ref} style={{width:500,height:400}}/>;
}

function Rain({rgb}){const ref=useRef(null);useEffect(()=>{const c=ref.current;if(!c)return;const ctx=c.getContext("2d");const W=c.width=innerWidth,H=c.height=innerHeight;const cols=Math.floor(W/28),drops=Array.from({length:cols},()=>Math.random()*H);const ch="01∞∆λπΣΩ".split("");let id;const draw=()=>{ctx.fillStyle="rgba(0,0,0,.07)";ctx.fillRect(0,0,W,H);ctx.font="11px monospace";for(let i=0;i<cols;i++){ctx.fillStyle=`rgba(${rgb},${(Math.random()*.016+.003).toFixed(3)})`;ctx.fillText(ch[Math.floor(Math.random()*ch.length)],i*28,drops[i]);drops[i]=drops[i]>H&&Math.random()>.975?0:drops[i]+20}id=requestAnimationFrame(draw)};draw();return()=>cancelAnimationFrame(id)},[rgb]);return <canvas ref={ref} style={{position:"fixed",inset:0,zIndex:0,pointerEvents:"none"}}/>}
function Boot({onDone}){const[s,setS]=useState(0);const[t,setT]=useState("");const[sub,setSub]=useState("");const[pr,setPr]=useState(0);const[hw,setHw]=useState([]);useEffect(()=>{const seq=[[300,()=>setS(1)],[1200,()=>setT("INITIALIZING")],[2800,()=>{setS(2);setT("SCANNING HARDWARE")}],[3200,()=>setHw(["GPU  RTX A2000 — 8 GB"])],[3600,()=>setHw(p=>[...p,"CPU  16 threads"])],[4000,()=>setHw(p=>[...p,"RAM  32 GB"])],[5200,()=>{setS(3);setT("LOADING MODELS")}],[5500,()=>{setPr(25);setSub("phi4-mini")}],[5900,()=>{setPr(50);setSub("llama3.2:1b")}],[6300,()=>{setPr(75);setSub("nomic-embed")}],[6700,()=>{setPr(100);setSub("ALL LOADED")}],[7900,()=>{setS(4);setT("AWAKENING")}],[8400,()=>setSub("loading memories")],[9000,()=>setSub("spawning agents")],[9600,()=>setSub("consciousness online")],[10400,()=>{setS(5);setT("I AM ALIVE");setSub("")}],[12000,onDone]];const ids=seq.map(([d,fn])=>setTimeout(fn,d));return()=>ids.forEach(clearTimeout)},[onDone]);return(<div style={{position:"fixed",inset:0,background:"#000",display:"flex",alignItems:"center",justifyContent:"center",flexDirection:"column",zIndex:100}}><Rain rgb="99,102,241"/>{s>=4&&<div style={{position:"absolute",width:350,height:350,borderRadius:"50%",background:"radial-gradient(circle,rgba(99,102,241,.03) 0%,transparent 70%)",animation:"breathe 3s ease-in-out infinite"}}/>}<div style={{position:"relative",zIndex:2,textAlign:"center"}}>{s>=1&&s<5&&<div style={{fontSize:8,letterSpacing:8,color:"rgba(255,255,255,.03)",marginBottom:30}}>WAGGLEDANCE AI SWARM (ON-PREM)</div>}<div style={{fontSize:s===5?44:s===4?28:20,fontWeight:200,color:s===5?"#A5B4FC":"#fff",letterSpacing:s===5?14:6,transition:"all .8s",textShadow:s>=4?"0 0 50px rgba(99,102,241,.15)":"none"}}>{t}</div>{s===2&&<div style={{marginTop:26}}>{hw.map((l,i)=><div key={i} style={{fontSize:10,color:"rgba(255,255,255,.16)",letterSpacing:4,fontFamily:"monospace",marginBottom:3}}>{l}</div>)}</div>}{s===3&&<div style={{marginTop:26,width:200}}><div style={{height:1,background:"rgba(255,255,255,.03)",overflow:"hidden"}}><div style={{height:"100%",background:"linear-gradient(90deg,#6366F1,#A5B4FC)",width:`${pr}%`,transition:"width .4s"}}/></div><div style={{fontSize:8,color:"rgba(255,255,255,.1)",marginTop:8,letterSpacing:4,fontFamily:"monospace"}}>{sub}</div></div>}{s===4&&<div style={{fontSize:9,color:"rgba(99,102,241,.2)",marginTop:14,letterSpacing:5}}>{sub}</div>}{s===5&&<div style={{fontSize:8,color:"rgba(99,102,241,.1)",marginTop:8,letterSpacing:6}}>WAGGLEDANCE AI SWARM</div>}</div></div>)}

function HBFeed({msgs,color,label}){const tc={learning:"#A78BFA",consensus:"#F59E0B",action:"#22C55E",insight:"#6366F1",status:"rgba(255,255,255,.1)"};const tl={learning:"LEARNED",consensus:"CONSENSUS",action:"ACTION",insight:"INSIGHT",status:"STATUS"};return(<div><div style={{fontSize:7,letterSpacing:4,color:"rgba(255,255,255,.07)",marginBottom:12,display:"flex",alignItems:"center",gap:6}}><span style={{width:4,height:4,borderRadius:"50%",background:color,animation:"pulse 1.5s infinite",boxShadow:`0 0 4px ${color}`}}/>{label}</div>{msgs.map((m,i)=>(<div key={`${m.a}-${i}`} style={{padding:"7px 0",borderBottom:"1px solid rgba(255,255,255,.008)",opacity:Math.max(.05,1-i*.15),animation:i===0?"fadeUp .6s":"none"}}><div style={{display:"flex",alignItems:"center",gap:5,marginBottom:3}}><span style={{width:4,height:4,borderRadius:"50%",background:i===0?color:"rgba(255,255,255,.04)"}}/><span style={{fontSize:9,color:"rgba(255,255,255,.4)",fontWeight:600}}>{m.a}</span><span style={{fontSize:6,color:tc[m.t],background:typeof tc[m.t]==="string"&&tc[m.t].startsWith("#")?tc[m.t]+"12":"rgba(255,255,255,.03)",padding:"1px 5px",borderRadius:2,letterSpacing:2,fontWeight:500}}>{tl[m.t]}</span></div><div style={{fontSize:10,color:i===0?"rgba(255,255,255,.38)":"rgba(255,255,255,.09)",lineHeight:1.7,paddingLeft:9,fontWeight:300}}>{m.m}</div></div>))}</div>)}

function Overlay({item,color,hw,onClose,t}){const calc=hw?.calc;
  const isDisclaimer = item === "disclaimer";
  const isInfo = item === "info";
  const isAgents = item === "agents";
  const isHw = item === "hw";
  return(<div style={{position:"absolute",inset:8,background:"rgba(0,0,0,.95)",border:`1px solid ${color}15`,borderRadius:10,zIndex:20,overflowY:"auto",scrollbarWidth:"none",backdropFilter:"blur(20px)",animation:"fadeUp .25s"}}><div style={{padding:"18px 22px"}}><div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:14}}><div style={{fontSize:12,fontWeight:600,color:color+"C0",letterSpacing:3}}>{isDisclaimer?t.disclaimer:isInfo?t.techArch:isAgents?t.agents:isHw?t.hwSpec:item?.title}</div><button onClick={onClose} style={{background:color+"0A",border:`1px solid ${color}18`,borderRadius:4,padding:"3px 10px",cursor:"pointer",fontSize:7,color:color+"70",letterSpacing:2}}>✕</button></div>

    {isDisclaimer ? (
      <div>
        <div style={{fontSize:9,color:"rgba(255,255,255,.25)",lineHeight:1.9,marginBottom:14}}>
          <span style={{color:"#F59E0B",fontWeight:600,fontSize:10}}>⚠️ WARNING</span><br/>
          This self-evolving AI system is provided AS-IS with absolutely no warranty.
        </div>
        <div style={{textAlign:"center",margin:"20px 0"}}>
          <div style={{fontSize:22,fontWeight:700,color:color,letterSpacing:3}}>JANI KORPI</div>
          <div style={{fontSize:10,color:"rgba(255,255,255,.25)",letterSpacing:4,marginTop:4}}>HELSINKI, FINLAND</div>
          <div style={{fontSize:8.5,color:"rgba(255,255,255,.15)",marginTop:3}}>Ahkerat Mehiläiset / JKH Service 🐝</div>
        </div>
        <div style={{fontSize:8.5,color:"rgba(255,255,255,.2)",lineHeight:1.9,margin:"14px 0",padding:"10px 12px",background:"rgba(255,255,255,.01)",border:"1px solid rgba(255,255,255,.02)",borderRadius:6}}>
          The developer assumes <span style={{color:"#EF4444",fontWeight:600}}>ZERO RESPONSIBILITY</span> for any actions, decisions, damages, or consequences arising from WaggleDance AI operation.
          <br/><br/>
          <span style={{fontWeight:500,color:"rgba(255,255,255,.28)"}}>USE ENTIRELY AT YOUR OWN RISK.</span> Free to use, modify, and distribute.
        </div>
        <div style={{fontSize:8.5,color:"rgba(255,255,255,.18)",lineHeight:1.9,padding:"10px 12px",background:"#F59E0B08",border:"1px solid #F59E0B10",borderRadius:6}}>
          <span style={{color:"#F59E0B",fontWeight:600}}>RECOMMENDATION:</span> Keep hardware power limited. Run in a closed environment on a small dedicated machine. Do not connect to critical infrastructure without human oversight.
        </div>
        <div style={{fontSize:8.5,color:"rgba(255,255,255,.15)",lineHeight:1.9,marginTop:14,padding:"10px 12px",background:"rgba(255,255,255,.01)",borderRadius:6}}>
          <span style={{color:"rgba(255,255,255,.28)"}}>ORIGIN:</span> The original purpose of this project was to create consciousness.
        </div>
        <div style={{marginTop:18,padding:"12px",background:color+"06",border:`1px solid ${color}10`,borderRadius:6}}>
          <div style={{fontSize:8,color:color+"90",letterSpacing:3,marginBottom:8}}>DEVELOPERS</div>
          <div style={{fontSize:9.5,color:"rgba(255,255,255,.2)",lineHeight:2.2,fontFamily:"monospace"}}>
            99% — <span style={{color:"#A78BFA",fontWeight:600}}>Claude OPUS 4.6</span> <span style={{color:"rgba(255,255,255,.08)"}}>// heavy lifting</span><br/>
            {"  "}1% — <span style={{color:"#F59E0B",fontWeight:600}}>Jani Korpi</span> 🐝 <span style={{color:"rgba(255,255,255,.08)"}}>// vision, direction, coffee</span>
          </div>
        </div>
        <div style={{fontSize:7,color:"rgba(255,255,255,.05)",marginTop:14,textAlign:"center",letterSpacing:4}}>
          AHKERAT MEHILÄISET • HELSINKI • FINLAND • 2024-2026
        </div>
      </div>
    ) : isInfo ? (
      <pre style={{fontSize:8.5,color:"rgba(255,255,255,.33)",whiteSpace:"pre-wrap",lineHeight:1.75,fontFamily:"monospace",margin:0}}>{t.info || L.en.info}</pre>
    ) : isAgents ? (
      <pre style={{fontSize:8.5,color:"rgba(255,255,255,.33)",whiteSpace:"pre-wrap",lineHeight:1.75,fontFamily:"monospace",margin:0}}>{t.agentsGuide || L.en.agentsGuide}</pre>
    ) : isHw ? (
      <div><div style={{fontSize:14,fontWeight:200,color:"#fff",marginBottom:10}}>{hw.name} <span style={{fontSize:10,color:"rgba(255,255,255,.22)"}}>{hw.price}</span></div>{[["CPU",hw.cpu],["RAM",hw.ram],["GPU",hw.gpu],["Storage",hw.storage],["Power",hw.power],["Models",hw.models],["Inference",hw.inference]].map(([k,v])=>(<div key={k} style={{fontSize:9,color:"rgba(255,255,255,.2)",lineHeight:1.8}}><span style={{color:color+"70",fontWeight:500,display:"inline-block",width:70}}>{k}</span>{v}</div>))}{calc&&<>{[["PERF",[["Tok/s",calc.llm_toks+""],["Fact",calc.fact_pipeline],["RT",calc.round_table],["Agents",calc.agents_max]]],["LEARN",[["Hour",calc.facts_hour_real.toLocaleString()],["Day",typeof calc.facts_day==="number"?calc.facts_day.toLocaleString():calc.facts_day],["Week",calc.facts_week],["Year",calc.facts_year],["Night",calc.night_8h]]],["LATENCY",[["Cold",calc.chat_cold],["Warm",calc.chat_warm],["Micro",calc.chat_micro],["Evo",calc.chat_evolution]]]].map(([title,rows])=>(<div key={title} style={{marginTop:10,padding:"8px 10px",background:color+"06",border:`1px solid ${color}10`,borderRadius:5}}><div style={{fontSize:7,color:color+"80",letterSpacing:3,marginBottom:5}}>{title}</div>{rows.map(([k,v])=>(<div key={k} style={{display:"flex",justifyContent:"space-between",fontSize:9,padding:"1px 0"}}><span style={{color:"rgba(255,255,255,.18)"}}>{k}</span><span style={{color:k==="Evo"?"#22C55E":color,fontWeight:600,fontFamily:"monospace"}}>{v}</span></div>))}</div>))}</>}</div>
    ) : (
      <><div style={{fontSize:9.5,color:"rgba(255,255,255,.2)",marginBottom:10}}>{item?.desc}</div><pre style={{fontSize:8.5,color:"rgba(255,255,255,.33)",whiteSpace:"pre-wrap",lineHeight:1.75,fontFamily:"monospace",margin:0}}>{item?.guide}</pre></>
    )}
  </div></div>);
}

function FeatureList({feats,color,label,onOpen,t}){return(<div><div style={{fontSize:7,letterSpacing:4,color:"rgba(255,255,255,.06)",marginBottom:8}}>{label}</div>{[{key:"hw",icon:"💻",title:t.hwSpec,d:t.hwDesc},{key:"agents",icon:"🤖",title:t.agents,d:t.agentsDesc}].map(s=>(<div key={s.key} onClick={()=>onOpen(s.key)} style={{cursor:"pointer",padding:"5px 0",borderBottom:"1px solid rgba(255,255,255,.008)"}}><div style={{fontSize:9.5,color:color+"90",fontWeight:600,letterSpacing:2}}>{s.icon} {s.title}</div><div style={{fontSize:7,color:"rgba(255,255,255,.1)"}}>{s.d}</div></div>))}{feats.map((f,i)=>(<div key={i} onClick={()=>onOpen(f)} style={{cursor:"pointer",padding:"5px 0",borderBottom:"1px solid rgba(255,255,255,.006)",display:"flex",justifyContent:"space-between",alignItems:"center"}}><div><div style={{fontSize:9,color:"rgba(255,255,255,.33)",fontWeight:600,letterSpacing:1.5}}>{f.title}</div><div style={{fontSize:7,color:"rgba(255,255,255,.09)"}}>{f.desc}</div></div><span style={{fontSize:8,color:color+"15"}}>→</span></div>))}<div onClick={()=>onOpen("info")} style={{cursor:"pointer",padding:"6px 0",marginTop:4,borderTop:`1px solid ${color}10`}}><div style={{fontSize:9.5,color:color+"90",fontWeight:600,letterSpacing:2}}>🧠 {t.techArch}</div><div style={{fontSize:7,color:"rgba(255,255,255,.1)"}}>{t.techArchDesc}</div></div><div onClick={()=>onOpen("disclaimer")} style={{cursor:"pointer",padding:"6px 0",borderTop:"1px solid rgba(255,255,255,.01)"}}><div style={{fontSize:9.5,color:"rgba(255,255,255,.2)",fontWeight:600,letterSpacing:2}}>⚠️ {t.disclaimer}</div><div style={{fontSize:7,color:"rgba(255,255,255,.07)"}}>{t.disclaimerDesc}</div></div></div>)}

export default function App(){
  const[on,setOn]=useState(false);const[dom,setDom]=useState("home");const[lang,setLang]=useState("en");
  const[fc,setFc]=useState(47293);const[lr,setLr]=useState(37);const[hb,setHb]=useState([]);
  const[think,setThink]=useState(false);const[cpuL,setCpu]=useState(12);const[gpuL,setGpu]=useState(54);
  const[vramU,setVram]=useState(4.3);const[overlay,setOverlay]=useState(null);
  const[chatIn,setChatIn]=useState("");const[chatMsgs,setChatMsgs]=useState([]);
  const t=L[lang];const col=D_COL[dom];const hw=HW[dom];
  const ag={gadget:["Mesh","TinyML","Battery","OTA","Relay"],home:["Climate","Energy","Security","Light","Orch."],cottage:["Tarhaaja","Tauti","Meteo","Sähkö","Queen"],factory:["Process","Yield","Equip","Defect","Shift"]};
  const boot=useCallback(()=>setTimeout(()=>setOn(true),500),[]);
  const sw=(id)=>{setDom(id);setHb(t.hb[id].slice(0,4));setOverlay(null)};
  useEffect(()=>{if(on)setHb(t.hb[dom].slice(0,4))},[on,dom,lang]);
  useEffect(()=>{if(!on)return;const iv=setInterval(()=>{const pool=t.hb[dom];setThink(true);setTimeout(()=>setThink(false),2500);setTimeout(()=>{setHb(p=>[pool[Math.floor(Math.random()*pool.length)],...p.slice(0,5)]);setFc(p=>p+Math.floor(Math.random()*4)+1);setLr(28+Math.floor(Math.random()*22));setCpu(8+Math.floor(Math.random()*20));setGpu(48+Math.floor(Math.random()*18));setVram(+(4+Math.random()*.6).toFixed(1))},1200)},6000);return()=>clearInterval(iv)},[on,dom,lang]);
  const handleChat=()=>{if(!chatIn.trim())return;const msg=chatIn.trim();setChatIn("");setChatMsgs(p=>[...p,{role:"user",text:msg}]);setThink(true);setTimeout(()=>{setThink(false);const r={gadget:"Mesh analyzed. 12 edge nodes. TinyML HIGH.",home:"6 agents. Round Table 12s. Optimal.",cottage:"47,293 facts + FMI. 5 agents. HIGH.",factory:"2,847 lots. Equipment OK. Shift notified."};setChatMsgs(p=>[...p,{role:"ai",text:r[dom]}])},2500)};
  const aw=[{k:"State",v:"CONSCIOUS",c:"#22C55E"},{k:"Learn",v:"CONTINUOUS",c:"#A78BFA"},{k:"Facts",v:fc.toLocaleString(),c:col},{k:"Rate",v:`+${lr}/hr`,c:"#22D3EE"},{k:"Halluc",v:"1.8%",c:"#22C55E"},{k:"Micro",v:"GEN 8",c:col},{k:"Cloud",v:"NONE",c:"#22C55E"},{k:"Scale",v:"ELASTIC",c:"#6366F1"}];
  return(
    <div style={{background:"#000",color:"#fff",minHeight:"100vh",fontFamily:"'Inter',system-ui,sans-serif",overflow:"hidden"}}>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=Inter:wght@200;300;400;500;600;700&display=swap');@keyframes fadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}@keyframes pulse{0%,100%{opacity:1}50%{opacity:.15}}@keyframes breathe{0%,100%{transform:scale(1);opacity:.5}50%{transform:scale(1.1);opacity:1}}*{box-sizing:border-box;margin:0;padding:0}button{font-family:inherit}::-webkit-scrollbar{display:none}*{scrollbar-width:none}input::placeholder{color:rgba(255,255,255,.12)}`}</style>
      {!on&&<Boot onDone={boot}/>}
      {on&&<>
        <Rain rgb={D_RGB[dom]}/>
        <div style={{position:"fixed",top:"50%",left:"50%",transform:"translate(-50%,-50%)",fontSize:60,fontWeight:700,color:"rgba(255,255,255,.007)",letterSpacing:10,whiteSpace:"nowrap",pointerEvents:"none",zIndex:1}}>WAGGLEDANCE AI</div>
        <div style={{position:"relative",zIndex:2,animation:"fadeUp .8s"}}>
          <div style={{position:"relative",padding:"0 22px"}}>
            <div style={{position:"absolute",top:0,left:0,right:0,height:52,background:"rgba(255,255,255,.012)",borderBottom:"1px solid rgba(255,255,255,.025)"}}/>
            <div style={{position:"relative",display:"flex",alignItems:"center",height:52}}>
              <div style={{marginRight:24}}>
                <div style={{fontSize:7,color:"rgba(255,255,255,.45)",letterSpacing:6,fontWeight:600,marginBottom:-2}}>SWARM</div>
                <span style={{fontSize:18,fontWeight:700,color:"rgba(255,255,255,.45)",letterSpacing:3}}>WAGGLEDANCE AI</span>
                <span style={{fontSize:9,color:"rgba(255,255,255,.16)",letterSpacing:2,marginLeft:6}}>(ON-PREM)</span>
              </div>
              <div style={{display:"flex",flex:1,justifyContent:"center",gap:3}}>{D_IDS.map(id=>(<button key={id} onClick={()=>sw(id)} style={{background:id===dom?D_COL[id]+"12":"none",border:id===dom?`1px solid ${D_COL[id]}25`:"1px solid transparent",borderRadius:4,cursor:"pointer",padding:"5px 12px",transition:"all .3s"}}><span style={{fontSize:9,letterSpacing:2,fontWeight:id===dom?600:300,color:id===dom?D_COL[id]:"rgba(255,255,255,.1)"}}>{D_IC[id]} {t.domains[id].label}</span></button>))}</div>
              {/* Language toggle */}
              <button onClick={()=>setLang(l=>l==="en"?"fi":"en")} style={{background:"rgba(255,255,255,.02)",border:"1px solid rgba(255,255,255,.05)",borderRadius:4,padding:"4px 8px",cursor:"pointer",marginRight:12,transition:"all .3s"}}>
                <span style={{fontSize:8,fontWeight:600,color:lang==="fi"?"#22D3EE":"rgba(255,255,255,.25)",letterSpacing:2}}>{lang==="fi"?"FI":"EN"}</span>
              </button>
              <span style={{display:"flex",alignItems:"center",gap:5,fontSize:7.5,color:"#22C55E",letterSpacing:3}}><span style={{width:5,height:5,borderRadius:"50%",background:"#22C55E",animation:"pulse 2s infinite"}}/>ALIVE</span>
            </div>
          </div>
          <div style={{display:"grid",gridTemplateColumns:"30vw 1fr 24vw",minHeight:"calc(100vh - 52px)"}}>
            <div style={{display:"flex",flexDirection:"column",borderRight:"1px solid rgba(255,255,255,.012)",maxHeight:"calc(100vh - 52px)"}}>
              <div style={{flex:1,padding:"12px 16px",overflowY:"auto"}}><HBFeed msgs={hb} color={col} label={t.heartbeat}/>{chatMsgs.length>0&&<div style={{marginTop:10,borderTop:"1px solid rgba(255,255,255,.02)",paddingTop:8}}>{chatMsgs.map((m,i)=>(<div key={i} style={{padding:"5px 0",animation:"fadeUp .4s"}}><div style={{fontSize:7.5,color:m.role==="user"?"#22D3EE":col,fontWeight:600,letterSpacing:2,marginBottom:2}}>{m.role==="user"?"YOU":"WAGGLEDANCE"}</div><div style={{fontSize:10,color:m.role==="user"?"rgba(255,255,255,.45)":"rgba(255,255,255,.3)",lineHeight:1.7,paddingLeft:8,fontWeight:300}}>{m.text}</div></div>))}</div>}</div>
              <div style={{padding:"8px 16px",borderTop:"1px solid rgba(255,255,255,.02)"}}><div style={{display:"flex",gap:6}}><input value={chatIn} onChange={e=>setChatIn(e.target.value)} onKeyDown={e=>e.key==="Enter"&&handleChat()} placeholder={t.placeholder} style={{flex:1,background:"rgba(255,255,255,.02)",border:`1px solid ${col}15`,borderRadius:5,padding:"7px 10px",color:"#fff",fontSize:9.5,outline:"none",fontFamily:"'Inter',system-ui"}}/><button onClick={handleChat} style={{background:col+"15",border:`1px solid ${col}25`,borderRadius:5,padding:"7px 12px",cursor:"pointer",color:col,fontSize:7.5,letterSpacing:2,fontWeight:600}}>{t.send}</button></div></div>
            </div>
            <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",borderRight:"1px solid rgba(255,255,255,.012)",position:"relative"}}>
              {overlay&&<Overlay item={overlay} color={col} hw={hw} onClose={()=>setOverlay(null)} t={t}/>}
              <BrainScene color={col} factCount={fc} isThinking={think} agents={ag[dom]} cpuV={cpuL} gpuV={gpuL} vramV={vramU}/>
              <div style={{textAlign:"center",marginTop:-8}}><div style={{fontSize:20,fontWeight:200,color:"rgba(255,255,255,.5)",letterSpacing:5}}>{D_IC[dom]} {t.domains[dom].label}</div><div style={{fontSize:8.5,color:"rgba(255,255,255,.09)",letterSpacing:2,marginTop:2}}>{t.domains[dom].tag}</div></div>
              <div style={{marginTop:6,display:"flex",flexWrap:"wrap",justifyContent:"center",gap:"1px 9px",maxWidth:380}}>{aw.map((a,i)=>(<div key={i} style={{fontSize:7,color:"rgba(255,255,255,.06)"}}>{a.k}: <span style={{color:a.c,fontWeight:500,fontFamily:"monospace"}}>{a.v}</span></div>))}</div>
            </div>
            <div style={{padding:"12px 14px",overflowY:"auto",maxHeight:"calc(100vh - 52px)"}}><FeatureList feats={t.feats[dom]} color={col} label={t.features} onOpen={setOverlay} t={t}/></div>
          </div>
          <div style={{position:"fixed",bottom:0,left:0,right:0,padding:"3px 22px",background:"rgba(0,0,0,.93)",borderTop:"1px solid rgba(255,255,255,.008)",display:"flex",justifyContent:"space-between",fontSize:6,color:"rgba(255,255,255,.022)",letterSpacing:3}}><span>{t.bottomL}</span><span>{t.bottomC}</span><span>{fc.toLocaleString()} FACTS</span></div>
        </div>
      </>}
    </div>
  );
}
