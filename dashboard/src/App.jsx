import { useState, useEffect, useRef, useCallback } from "react";
import { useApi } from "./hooks/useApi";

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
      cottage: { label: "COTTAGE", tag: "300 hives • sauna • off-grid intelligence" },
      home: { label: "HOME", tag: "47 devices • 6 rooms • smart living" },
      factory: { label: "FACTORY", tag: "142 equipment • 24/7 semiconductor production" },
    },
    feats: {
      gadget: [
        { title: "1. GET HARDWARE", desc: "What you need", guide: "Minimum: any ONE of these:\n• Raspberry Pi 5 (8GB) — €80\n• ESP32-S3 DevKit — €8\n• Old Android phone (Termux)\n• Any Linux SBC with WiFi\n\nRecommended starter kit:\n1× RPi 5 (brain) + 2× ESP32 (sensors)\nTotal: ~€110" },
        { title: "2. INSTALL", desc: "5-minute setup", guide: "On Raspberry Pi / Linux SBC:\npip install -r requirements.txt\npython main.py\n\nWaggleDance auto-detects your\nhardware and selects optimal models.\nNo configuration needed.\n\nDashboard: http://[device-ip]:5173" },
        { title: "3. ADD SENSORS", desc: "Connect ESP32 nodes", guide: "Flash ESP32 with hive_audio.ino:\n1. Set WiFi credentials\n2. Set MQTT broker IP (your RPi)\n3. Upload firmware\n4. Sensor auto-registers\n\nSupported: temp, humidity, weight,\nsound, motion, light, air quality.\nAll data → vector memory." },
        { title: "4. MESH NETWORK", desc: "Devices find each other", guide: "MQTT + mDNS auto-discovery.\nESP32 sensors → RPi hub → brain.\n\nRange: 340m through relay nodes.\nLatency: 8-20ms across mesh.\n\nAdd a device → it joins the swarm.\nRemove one → swarm adapts." },
        { title: "5. TINYML", desc: "AI on the chip itself", guide: "48KB neural nets on ESP32:\n• Sound classification\n• Vibration anomaly detection\n• Motion patterns\n• Temperature prediction\n\nTrained on main node overnight.\nDeployed via OTA. No downtime." },
        { title: "6. POWER OPTIONS", desc: "Solar / battery / USB", guide: "ESP32 deep sleep: 10µA standby.\n\nOptions:\n• USB-C (continuous power)\n• 18650 battery (3-6 months)\n• Solar panel + battery (infinite)\n\nWake on event or schedule." },
        { title: "HOW IT LEARNS", desc: "Getting smarter daily", guide: "Day 1: Basic sensor readings.\nWeek 1: Patterns emerging.\nMonth 1: Predicts your routines.\nMonth 3: Anticipates anomalies.\n\nEach device specializes on its own.\nTogether they form one intelligence." },
        { title: "WHAT'S POSSIBLE", desc: "Use case ideas", guide: "• Doorbell + camera = face recognition\n• Soil sensor = auto irrigation\n• BLE beacons = asset tracking\n• Air sensor = ventilation control\n• Motion mesh = presence mapping\n• Sound = anomaly detection\n\nAll local. All private. All learning." },
      ],
      home: [
        { title: "1. INSTALL", desc: "5-minute setup", guide: "Requirements:\n• Any PC, NUC, or Mac Mini\n• 8GB+ RAM recommended\n\npip install -r requirements.txt\npython main.py\n\nDashboard: http://localhost:5173\nAuto-detects your hardware." },
        { title: "2. HOME ASSISTANT", desc: "Connect your smart home", guide: "In settings.yaml:\nhome_assistant:\n  url: http://homeassistant.local:8123\n  token: YOUR_LONG_LIVED_TOKEN\n  auto_discover: true\n\nWaggleDance discovers all devices\nautomatically. 2000+ integrations\nvia Home Assistant." },
        { title: "3. ENERGY SETUP", desc: "Start saving on electricity", guide: "Electricity spot price is automatic.\nWaggleDance reads hourly prices and\nschedules your heavy appliances\nto cheapest hours.\n\nTypical savings: 15-30% annually.\n\nWorks with: EV chargers, heat pumps,\nwater heaters, washing machines." },
        { title: "4. SECURITY", desc: "Cameras + face recognition", guide: "Add Frigate NVR for cameras:\nfrigate:\n  enabled: true\n  url: http://frigate:5000\n\nFeatures:\n• Face recognition (family vs stranger)\n• Auto-lock doors at night\n• Telegram alerts with snapshots\n• Night anomaly detection" },
        { title: "5. RSS & RECIPES", desc: "News + cooking intelligence", guide: "Add your favorite feeds:\nrss:\n  feeds:\n    - url: https://your-news.com/feed\n    - url: https://recipes.com/feed\n\nAI reads articles, learns recipes,\nsuggests meals based on what's\nin your fridge + store deals." },
        { title: "6. VOICE CONTROL", desc: "Talk to your home", guide: "Wake word: 'Hey WaggleDance'\n\nUses Whisper (speech-to-text)\n+ Piper (text-to-speech).\nBoth run locally on your hardware.\n\nAlso works via Alexa/Siri\nthrough Home Assistant bridge." },
        { title: "HOW IT LEARNS", desc: "Your home gets smarter", guide: "Week 1: Learns your schedule.\nWeek 2: Predicts heating needs.\nMonth 1: Knows your preferences.\nMonth 3: Anticipates everything.\n\n6 agents debate decisions.\nRound Table consensus ensures\nno single agent makes mistakes." },
        { title: "PRIVACY", desc: "Your data stays home", guide: "Zero cloud. Zero subscription.\nAll AI runs on your hardware.\n\nNo data leaves your network.\nNo microphone recordings stored.\nNo camera feeds uploaded.\n\nYou own everything. Forever." },
      ],
      cottage: [
        { title: "1. INSTALL", desc: "Set up at your cottage", guide: "Hardware: any PC or NUC.\nWorks offline after first setup.\n\npip install -r requirements.txt\npython main.py\n\nDashboard: http://[local-ip]:5173\nLeaves can run on solar + 4G." },
        { title: "2. HIVE SENSORS", desc: "ESP32 weight + temp + sound", guide: "Per hive sensor node (~€20):\n• ESP32-S3 + load cell (weight)\n• DS18B20 (temperature)\n• INMP441 mic (colony sound)\n\nFlash firmware, set WiFi, done.\nWeight, temp, sound every 60s.\nAnomaly alerts via Telegram." },
        { title: "3. WEATHER", desc: "FMI forecast — free, automatic", guide: "FMI Open Data API (no key needed):\nweather:\n  enabled: true\n  locations: ['Your City']\n\nGets: temperature, wind, rain,\n48h forecast every 30 minutes.\nAgents use weather in decisions." },
        { title: "4. CAMERAS", desc: "Wildlife + security", guide: "RPi 5 + Coral TPU (~€110):\nfrigate:\n  enabled: true\n  cameras:\n    yard: {description: 'Hive area'}\n\nDetects: bears, moose, foxes,\npeople, vehicles. Day and night.\nTelegram alert + snapshot in <2s." },
        { title: "5. SPOT PRICE", desc: "Sauna + extraction timing", guide: "Electricity spot price — automatic:\nelectricity:\n  enabled: true\n\nFinds cheapest hours for:\n• Sauna heating\n• Honey extraction\n• Water heater\n• Any scheduled load\n\nSaves 15-30% on electricity." },
        { title: "6. NEWS FEEDS", desc: "Disease alerts + bee news", guide: "Critical RSS feeds:\nrss:\n  feeds:\n    - url: ruokavirasto.fi/rss\n      critical: true\n    - url: mehilaishoitajat.fi/feed\n\nFoulbrood outbreak alerts\ntrigger IMMEDIATE notification.\nAll articles indexed for learning." },
        { title: "HOW IT LEARNS", desc: "Becomes your bee expert", guide: "Day 1: 1,348 base facts loaded.\nWeek 1: Learns your hive patterns.\nMonth 1: Predicts swarms + flows.\nMonth 6: Deeper than any textbook.\n\nNight learning: reads research,\ncross-validates, grows 24/7.\nYour AI beekeeper never sleeps." },
        { title: "ACOUSTIC ANALYSIS", desc: "Listen to your bees", guide: "ESP32 + INMP441 mic at entrance:\n\nDetects:\n• Queen piping (swarming soon)\n• Stress buzz (disturbance)\n• Queenless howl\n• Normal healthy hum\n\nAlerts before a human could\npossibly hear the difference." },
      ],
      factory: [
        { title: "1. INSTALL", desc: "Air-gapped deployment", guide: "Server or workstation. No internet.\n\npip install -r requirements.txt\npython main.py\n\nISO 27001 / ITAR / GDPR ready.\nAll data stays on your network.\nNo cloud. No external calls." },
        { title: "2. OPC-UA CONNECT", desc: "Read your equipment", guide: "In settings.yaml:\nopc_ua:\n  endpoints:\n    - url: opc.tcp://grind5:4840\n    - url: opc.tcp://pnt_2:4840\n    - url: opc.tcp://centrotherm:4840\n\nReads: RPM, pressure, temp, flow,\nvibration, thickness, uniformity.\nAll → vector memory for learning." },
        { title: "3. SPC + DRIFT", desc: "Statistical process control", guide: "AI-enhanced SPC monitoring:\n• Western Electric rules (auto)\n• 2σ/3σ drift detection\n• Trend analysis per parameter\n\nSees drift 14 wafers before\nstandard SPC charts trigger.\nPrevents excursions, not just\ndetects them." },
        { title: "4. PREDICTIVE PM", desc: "Fix before it breaks", guide: "Vibration + thermal trending:\n\nExample workflow:\n1. Grind5 bearing 0.42g → 0.67g\n2. AI: 'failure in 96 hours'\n3. Round Table: schedule PM tonight\n4. Tech Jari P. gets work order\n5. Fixed at 02:00, zero scrap\n\nPredicts 72h before human notice." },
        { title: "5. YIELD AI", desc: "Per-lot prediction 98.7%", guide: "Learns from every lot:\nCD, film thickness, overlay,\ndefect density, etch rate.\n\nIn-line prediction before\nfinal metrology completes.\n\nCorrelation engine finds\nhidden process links:\nRF ramp ↔ particles\nHumidity ↔ defect density" },
        { title: "6. SHIFT HANDOVER", desc: "Auto-generated briefs", guide: "Every shift change:\n\n1. AI summarizes all events\n2. Highlights equipment alerts\n3. Lists active lots + status\n4. Flags pending PMs\n5. 30-second brief for next shift\n\nTechnicians, leads, management\neach get their own view." },
        { title: "HOW IT LEARNS", desc: "Digital twin grows daily", guide: "Week 1: Baselines all equipment.\nMonth 1: Predicts common failures.\nMonth 3: Finds process correlations\n  no engineer has seen.\nMonth 6: Full digital twin.\n\nEvery lot teaches it.\nEvery shift it gets sharper.\nEvery PM validates its predictions." },
        { title: "ESCALATION FLOW", desc: "Alert → decide → fix → verify", guide: "Automatic escalation chain:\n\n1. Sensor anomaly detected\n2. Round Table: 4 agents evaluate\n3. Severity: INFO → WARN → CRIT\n4. WARN: tech gets Telegram alert\n5. CRIT: shift lead + management\n6. PM scheduled, verified, closed\n\nAll decisions logged + learned." },
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
    demoHb: {
      gadget: [
        { a: "Edge OS", m: "Booting WaggleDance on ESP32-S3... 512KB RAM, WiFi connected. Loading nano-agent runtime.", t: "status" },
        { a: "TinyML", m: "48KB neural network loaded. On-device inference: 12ms per classification. No cloud needed. Ever.", t: "learning" },
        { a: "Wearable AI", m: "Smartwatch connected via BLE. Heart rate, step count, sleep quality streaming. Personal health agent active.", t: "status" },
        { a: "Voice Agent", m: "Wake word 'Hey Waggle' loaded on 4MB flash. Always listening, always local. Zero data leaves this chip.", t: "action" },
        { a: "Vision AI", m: "ESP32-CAM running person detection at 5fps. Model: 320KB MobileNet. Recognizes 3 faces locally.", t: "insight" },
        { a: "Mesh Brain", m: "6 devices forming neural mesh. Each node: 1 specialty. Together: general intelligence. Latency: 8ms.", t: "status" },
        { a: "Predictive AI", m: "Learning your daily patterns... Coffee machine ON at 06:42, lights dim at 22:15. Predicting needs.", t: "insight" },
        { a: "Sensor Fusion", m: "Combining temperature + motion + light + sound. Context: 'person cooking dinner'. Accuracy: 94%.", t: "insight" },
        { a: "OTA Evolution", m: "Micro-model Gen 3 trained overnight on YOUR data. Deploying to all 6 nodes... Intelligence upgrade live.", t: "learning" },
        { a: "Energy Zero", m: "Solar harvesting: 340mW input, 180mW consumption. Net positive. This AI runs on sunlight alone.", t: "status" },
        { a: "Swarm Logic", m: "Doorbell ESP32 detected stranger → porch camera zoomed → light turned on. 3 devices, 1 decision, 45ms.", t: "consensus" },
        { a: "Edge Reason", m: "Old phone running as room AI: voice + camera + sensors. €0 hardware cost. Full agent intelligence.", t: "insight" },
        { a: "Digital Twin", m: "Your environment model: 14 sensors, 47 learned behaviors, 3 predictions active. Updating every 60s.", t: "learning" },
        { a: "Edge OS", m: "GADGET SWARM ONLINE: 6 devices, collective intelligence active. The future isn't in the cloud — it's in your pocket.", t: "consensus" },
        { a: "Edge OS", m: "This is not a demo — autonomous learning is already running. Every device is getting smarter, right now.", t: "status" },
      ],
      home: [
        { a: "Home Brain", m: "Scanning Home Assistant API... Found 47 devices, 6 rooms, 12 automations. Building your home's neural map.", t: "status" },
        { a: "Energy AI", m: "Electricity spot price API connected. Current: 3.2c/kWh. Your home will never pay peak price again.", t: "action" },
        { a: "News AI", m: "RSS feeds connected: YLE, Helsingin Sanomat, Iltalehti. Scanning headlines... 14 articles relevant to your interests.", t: "learning" },
        { a: "Security AI", m: "4 cameras online. Face recognition loaded. Your home now knows who belongs here — and who doesn't.", t: "status" },
        { a: "Recipe AI", m: "Fridge inventory scanned: chicken, cream, mushrooms. Suggested: creamy chicken pasta. Recipe loaded. Cook time: 25 min.", t: "insight" },
        { a: "Presence AI", m: "You leave at 07:45, return at 17:15. Home pre-heats, lights welcome you. It learned this on its own.", t: "learning" },
        { a: "Energy AI", m: "EV charging moved to 02:00-05:00 at 0.8c/kWh. Saved €4.20 tonight. €127/month projected savings.", t: "insight" },
        { a: "News AI", m: "Learning from today's tech news: 3 smart home trends indexed. Your AI reads 200 articles/day so you don't have to.", t: "learning" },
        { a: "Air Quality", m: "CO₂ rising to 920ppm in bedroom. Ventilation auto-boosted. You'll sleep 23% better. Science says so.", t: "action" },
        { a: "Round Table", m: "5 agents debated: pre-heat bathroom 06:30 or 06:45? Consensus: 06:30. Your alarm is at 07:00.", t: "consensus" },
        { a: "Recipe AI", m: "Weekly meal plan generated from fridge + store deals + nutrition goals. Shopping list: 12 items, €34. Optimized.", t: "insight" },
        { a: "Security AI", m: "Unknown person at door 22:47 — face not in database. Lights ON, camera recording, notification sent.", t: "action" },
        { a: "Predict AI", m: "Friday pattern detected: you order food at 18:30. Kitchen lights auto-dim. Living room TV preset loaded.", t: "learning" },
        { a: "Home Brain", m: "HOME INTELLIGENCE ONLINE: 47 devices as one brain. Your home doesn't just respond — it anticipates.", t: "status" },
        { a: "Home Brain", m: "This is not a demo — autonomous learning is already running. Your home learns something new every hour.", t: "status" },
      ],
      cottage: [
        { a: "Hive Mind", m: "12 ESP32 weight sensors found. Each hive now has a nervous system. Reading first baselines...", t: "status" },
        { a: "Disease AI", m: "3,147 bee disease facts indexed. Varroa, foulbrood, nosema — your AI vet never sleeps.", t: "learning" },
        { a: "News AI", m: "RSS: Mehiläishoitajat.fi — new article: 'Early spring varroa treatment results 2026'. Indexing key facts...", t: "learning" },
        { a: "Weather AI", m: "FMI satellite data streaming. 48h hyperlocal forecast loaded. Your bees' weather station is smarter than yours.", t: "status" },
        { a: "Energy AI", m: "Spot price 2.1c/kWh now. Sauna scheduled for tonight's 0.8c window. Honey extraction follows at 01:00.", t: "action" },
        { a: "Hive Mind", m: "Hive 12: weight 33.9kg, temp 35.1°C, sound 248Hz. Queen confirmed healthy. No human opened the hive.", t: "insight" },
        { a: "News AI", m: "ALERT: Ruokavirasto RSS — foulbrood outbreak reported in Häme region. Distance: 140km. Risk level: LOW. Monitoring.", t: "action" },
        { a: "Recipe AI", m: "Honey harvest recipe index updated: 8 new recipes learned. 'Honey-glazed salmon' matched your cooking history.", t: "learning" },
        { a: "Acoustic AI", m: "Hive 7 queen piping at 450Hz — swarming in 48-72h. Alert sent. No beekeeper on Earth could hear this.", t: "insight" },
        { a: "Flora AI", m: "Satellite + phenology: dandelion bloom in 3 days. Main honey flow in 18 days. Supers should go on day 15.", t: "insight" },
        { a: "Round Table", m: "5 agents debated: treatment Thursday 09:00-11:00. Weather + Disease + Hive data align. Decision: GO.", t: "consensus" },
        { a: "Harvest AI", m: "Weight trends: Hive 3 +0.8kg/day. Honey flow confirmed. Projected harvest: 42kg this super cycle.", t: "learning" },
        { a: "Night Brain", m: "2 AM: learning from ScientificBeekeeping.com — 23 new facts on oxalic acid sublimation. Cross-validated.", t: "learning" },
        { a: "Hive Mind", m: "COTTAGE INTELLIGENCE ONLINE: 12 hives as one organism. It reads the news, watches the weather, listens to your bees.", t: "status" },
        { a: "Hive Mind", m: "This is not a demo — autonomous learning is already running. 3,147 facts and counting. Your bees have an AI.", t: "status" },
      ],
      factory: [
        { a: "Factory Brain", m: "OPC-UA scan complete: Grind5, PNT_2, PNT_4, Centrotherm_02, 6 furnaces, 24 pumps. 142 nodes online.", t: "status" },
        { a: "Grind5", m: "Grind5 spindle RPM: 5,069 rpm. Pad pressure: 3.2 psi. Slurry flow: 220 ml/min. All within spec. Monitoring.", t: "status" },
        { a: "Yield AI", m: "2,847 historical lots ingested. Yield model trained. Predicts defects 14 wafers before metrology sees them.", t: "learning" },
        { a: "PNT_2", m: "⚠️ PNT_2 coat thickness drifting: 1.12µm → 1.18µm over 3 hours. Still in spec (1.20 limit). Alert to tech Mikko K.", t: "insight" },
        { a: "Shift AI", m: "B-shift lead Antti R: 14 active lots, utilization 91.3%. Dispatch optimized — Centrotherm_02 queue cleared.", t: "action" },
        { a: "Centrotherm_02", m: "Centrotherm_02 furnace tube 3: temperature uniformity ±0.3°C across 150 wafers. Oxide growth: 102nm target hit.", t: "insight" },
        { a: "Grind5", m: "⚠️ Grind5 vibration spike: 0.42g → 0.67g at 5,069 rpm. Bearing wear pattern detected. ETA to threshold: 96 hours.", t: "insight" },
        { a: "BigData AI", m: "Correlation found: PNT_4 humidity +2% → defect density +0.3/cm². Root cause in 4 hours. Process eng. notified.", t: "learning" },
        { a: "PNT_4", m: "PNT_4 resist dispense: 1.87ml ±0.02. Nozzle 2 shows 0.04ml deviation. PM scheduled before excursion. Tech: Jari P.", t: "action" },
        { a: "Round Table", m: "ESCALATION PREVENTED: Grind5 bearing + PNT_2 drift. 4 agents agree: PM window tonight 02:00. Zero scrap.", t: "consensus" },
        { a: "Mgmt Dashboard", m: "Live to management: OEE 94.2%, yield 98.7%, 0 excursions, 2 predictive PMs scheduled. Board report auto-generated.", t: "status" },
        { a: "Handover AI", m: "B→C shift handover: 'Grind5 bearing watch, PNT_2 stable after adjust, Centrotherm_02 running perfect.' 30-second brief.", t: "action" },
        { a: "BigData AI", m: "Week 1 insight: furnace ramp rate ↔ wafer bow. Adjusting Centrotherm_02 recipe. Projected yield gain: +0.4%.", t: "learning" },
        { a: "Factory Brain", m: "FACTORY AI ONLINE: 142 machines, 6 technicians, 3 shifts — one digital twin. Sees problems before they exist.", t: "status" },
        { a: "Factory Brain", m: "This is not a demo — autonomous learning is already running. Every lot teaches it. Every shift it gets sharper.", t: "status" },
      ],
    },
    demoEnd: { a: "WaggleDance", m: "Demo complete. Now it's time to integrate your own data streams. See the setup guides in the right panel.", t: "status" },
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
      cottage: { label: "MÖKKI", tag: "300 pesää • sauna • off-grid-älykkyys" },
      home: { label: "KOTI", tag: "47 laitetta • 6 huonetta • älykäs asuminen" },
      factory: { label: "TEHDAS", tag: "142 laitetta • 24/7 puolijohdetuotanto" },
    },
    feats: {
      gadget: [
        { title: "1. HANKI LAITTEISTO", desc: "Mitä tarvitset", guide: "Vähimmäisvaatimus: mikä tahansa YKSI:\n• Raspberry Pi 5 (8GB) — 80€\n• ESP32-S3 DevKit — 8€\n• Vanha Android-puhelin (Termux)\n• Mikä tahansa Linux-SBC WiFillä\n\nSuositeltu aloituspaketti:\n1× RPi 5 (aivot) + 2× ESP32 (sensorit)\nYhteensä: ~110€" },
        { title: "2. ASENNUS", desc: "5 minuutin käyttöönotto", guide: "Raspberry Pi / Linux SBC:\npip install -r requirements.txt\npython main.py\n\nWaggleDance tunnistaa laitteistosi\nautomaattisesti ja valitsee optimaaliset\nmallit. Ei konfigurointia tarvita.\n\nHallintapaneeli: http://[laite-ip]:5173" },
        { title: "3. LISÄÄ SENSORIT", desc: "Yhdistä ESP32-solmut", guide: "Flashaa ESP32 hive_audio.ino:lla:\n1. Aseta WiFi-tunnukset\n2. Aseta MQTT-brokerin IP (RPi:si)\n3. Lataa firmware\n4. Sensori rekisteröityy itse\n\nTuetut: lämpö, kosteus, paino,\nääni, liike, valo, ilmanlaatu.\nKaikki data → vektorimuisti." },
        { title: "4. MESH-VERKKO", desc: "Laitteet löytävät toisensa", guide: "MQTT + mDNS automaattilöydös.\nESP32-sensorit → RPi-keskitin → aivot.\n\nKantama: 340m relesolmujen kautta.\nViive: 8-20ms mesh-verkon yli.\n\nLisää laite → se liittyy parveen.\nPoista yksi → parvi mukautuu." },
        { title: "5. TINYML", desc: "AI itse piirillä", guide: "48KB neuroverkot ESP32:lla:\n• Ääniluokittelu\n• Tärinäpoikkeamien tunnistus\n• Liikekuviot\n• Lämpötilaennusteet\n\nKoulutettu pääsolmussa yön yli.\nJaetaan OTA:lla. Ei katkoja." },
        { title: "6. VIRTAVAIHTOEHDOT", desc: "Aurinko / akku / USB", guide: "ESP32 syvä uni: 10µA valmiustila.\n\nVaihtoehdot:\n• USB-C (jatkuva virta)\n• 18650-akku (3-6 kuukautta)\n• Aurinkopaneeli + akku (loputon)\n\nHerätys tapahtumasta tai ajastimesta." },
        { title: "MITEN SE OPPII", desc: "Viisastuu päivä päivältä", guide: "Päivä 1: Perus sensoridataa.\nViikko 1: Kuvioita nousee esiin.\nKuukausi 1: Ennustaa rutiinisi.\nKuukausi 3: Ennakoi poikkeamat.\n\nJokainen laite erikoistuu omallaan.\nYhdessä ne muodostavat yhden älyn." },
        { title: "MAHDOLLISUUDET", desc: "Käyttöideoita", guide: "• Ovikello + kamera = kasvojentunnistus\n• Maasensori = automaattikastelu\n• BLE-majakat = omaisuuden seuranta\n• Ilmasensori = ilmanvaihtoohjaus\n• Liikeverkko = läsnäolokartoitus\n• Ääni = poikkeamien tunnistus\n\nKaikki paikallista. Kaikki yksityistä." },
      ],
      home: [
        { title: "1. ASENNUS", desc: "5 minuutin käyttöönotto", guide: "Vaatimukset:\n• Mikä tahansa PC, NUC tai Mac Mini\n• 8GB+ RAM suositeltu\n\npip install -r requirements.txt\npython main.py\n\nHallintapaneeli: http://localhost:5173\nTunnistaa laitteistosi automaattisesti." },
        { title: "2. HOME ASSISTANT", desc: "Yhdistä älykotisi", guide: "Tiedostoon settings.yaml:\nhome_assistant:\n  url: http://homeassistant.local:8123\n  token: PITKÄ_AVAIMESI\n  auto_discover: true\n\nWaggleDance löytää kaikki laitteet\nautomaattisesti. 2000+ integraatiota\nHome Assistantin kautta." },
        { title: "3. ENERGIAN ASETUKSET", desc: "Ala säästää sähkössä", guide: "Pörssisähkö on automaattinen.\nWaggleDance lukee tuntihintoja ja\najoittaa raskaat laitteesi\nhalvimpiin tunteihin.\n\nTyypillinen säästö: 15-30% vuodessa.\n\nToimii: sähköautolaturit, lämpöpumput,\nlämminvesivaraajat, pesukoneet." },
        { title: "4. TURVALLISUUS", desc: "Kamerat + kasvojentunnistus", guide: "Lisää Frigate NVR kameroille:\nfrigate:\n  enabled: true\n  url: http://frigate:5000\n\nOminaisuudet:\n• Kasvojentunnistus (perhe vs vieras)\n• Automaattilukitus yöllä\n• Telegram-hälytykset kuvineen\n• Yöaikainen poikkeamavalvonta" },
        { title: "5. RSS & RESEPTIT", desc: "Uutiset + kokkiälykkyys", guide: "Lisää lempisyötteesi:\nrss:\n  feeds:\n    - url: https://uutis-sivusi.fi/feed\n    - url: https://reseptit.fi/feed\n\nAI lukee artikkelit, oppii reseptejä,\nehdottaa aterioita jääkaapin sisällön\n+ kaupan tarjousten perusteella." },
        { title: "6. ÄÄNIOHJAUS", desc: "Puhu kodillesi", guide: "Herätyssana: 'Hei WaggleDance'\n\nKäyttää Whisperiä (puhe→teksti)\n+ Piperiä (teksti→puhe).\nMolemmat toimivat paikallisesti.\n\nToimii myös Alexan/Sirin kautta\nHome Assistant -sillan avulla." },
        { title: "MITEN SE OPPII", desc: "Kotisi viisastuu", guide: "Viikko 1: Oppii aikataulusi.\nViikko 2: Ennustaa lämmitystarpeet.\nKuukausi 1: Tuntee mieltymyksesi.\nKuukausi 3: Ennakoi kaiken.\n\n6 agenttia väittelee päätöksistä.\nPyöreä Pöytä -konsensus varmistaa\nettei yksikään agentti tee virheitä." },
        { title: "YKSITYISYYS", desc: "Datasi pysyy kotona", guide: "Ei pilveä. Ei tilausta.\nKaikki AI toimii laitteellasi.\n\nDatasi ei lähde verkostasi.\nMikrofonitallennuksia ei säilytetä.\nKamerakuvaa ei ladata mihinkään.\n\nOmistat kaiken. Ikuisesti." },
      ],
      cottage: [
        { title: "1. ASENNUS", desc: "Käyttöönotto mökillä", guide: "Laitteisto: mikä tahansa PC tai NUC.\nToimii offline ensimmäisen asennuksen jälkeen.\n\npip install -r requirements.txt\npython main.py\n\nHallintapaneeli: http://[paikallinen-ip]:5173\nVoi toimia aurinkopaneelilla + 4G:llä." },
        { title: "2. PESÄSENSORIT", desc: "ESP32 paino + lämpö + ääni", guide: "Pesäkohtainen sensorisolmu (~20€):\n• ESP32-S3 + punnitusanturi (paino)\n• DS18B20 (lämpötila)\n• INMP441-mikrofoni (pesän ääni)\n\nFlashaa firmware, aseta WiFi, valmis.\nPaino, lämpö, ääni 60s välein.\nPoikkeamahälytykset Telegramiin." },
        { title: "3. SÄÄ", desc: "IL-ennuste — ilmainen, automaattinen", guide: "IL:n avoin data-API (ei avainta):\nweather:\n  enabled: true\n  locations: ['Kaupunkisi']\n\nHakee: lämpötila, tuuli, sade,\n48h ennuste 30 minuutin välein.\nAgentit käyttävät säätä päätöksissä." },
        { title: "4. KAMERAT", desc: "Villieläimet + turvallisuus", guide: "RPi 5 + Coral TPU (~110€):\nfrigate:\n  enabled: true\n  cameras:\n    piha: {description: 'Tarhanaapuri'}\n\nTunnistaa: karhut, hirvet, ketut,\nihmiset, ajoneuvot. Päivällä ja yöllä.\nTelegram-hälytys + kuva <2s." },
        { title: "5. PÖRSSISÄHKÖ", desc: "Saunan + linkouden ajoitus", guide: "Pörssisähkö — automaattinen:\nelectricity:\n  enabled: true\n\nEtsii halvimmat tunnit:\n• Saunan lämmitys\n• Hunajan linkous\n• Lämminvesivaraaja\n• Mikä tahansa ajoitettu kuorma\n\nSäästää 15-30% sähköstä." },
        { title: "6. UUTISSYÖTTEET", desc: "Tautihälytykset + mehiläisuutiset", guide: "Kriittiset RSS-syötteet:\nrss:\n  feeds:\n    - url: ruokavirasto.fi/rss\n      critical: true\n    - url: mehilaishoitajat.fi/feed\n\nEsikotelomätäpurkaukset\nlaukaisevat VÄLITTÖMÄN ilmoituksen.\nKaikki artikkelit indeksoitu oppimista varten." },
        { title: "MITEN SE OPPII", desc: "Mehiläisasiantuntijasi", guide: "Päivä 1: 1 348 perustietoa ladattu.\nViikko 1: Oppii pesäkuviosi.\nKuukausi 1: Ennustaa parveilu + sato.\nKuukausi 6: Syvempi kuin mikään oppikirja.\n\nYöoppiminen: lukee tutkimuksia,\nristiinvalidoi, kasvaa 24/7.\nAI-mehiläishoitajasi ei nuku koskaan." },
        { title: "AKUSTIIKKA-ANALYYSI", desc: "Kuuntele mehiläisiäsi", guide: "ESP32 + INMP441-mikrofoni sisäänkäyntiin:\n\nTunnistaa:\n• Emon toitotus (parveilu tulossa)\n• Stressisuraus (häiriö)\n• Emoton valitus\n• Normaali terve surina\n\nHälyttää ennen kuin ihminen\nvoisi mitenkään kuulla eroa." },
      ],
      factory: [
        { title: "1. ASENNUS", desc: "Ilmavälikäyttöönotto", guide: "Palvelin tai työasema. Ei internettiä.\n\npip install -r requirements.txt\npython main.py\n\nISO 27001 / ITAR / GDPR -valmis.\nKaikki data pysyy verkossasi.\nEi pilveä. Ei ulkoisia kutsuja." },
        { title: "2. OPC-UA YHDISTÄ", desc: "Lue laitteitasi", guide: "Tiedostoon settings.yaml:\nopc_ua:\n  endpoints:\n    - url: opc.tcp://grind5:4840\n    - url: opc.tcp://pnt_2:4840\n    - url: opc.tcp://centrotherm:4840\n\nLukee: RPM, paine, lämpö, virtaus,\ntärinä, paksuus, tasaisuus.\nKaikki → vektorimuisti oppimista varten." },
        { title: "3. SPC + AJAUTUMA", desc: "Tilastollinen prosessinohjaus", guide: "AI-tehostettu SPC-valvonta:\n• Western Electric -säännöt (autom.)\n• 2σ/3σ ajautumisen tunnistus\n• Trendianalyysi per parametri\n\nNäkee ajautuman 14 kiekkoa ennen\nkuin standardi SPC-kaavio reagoi.\nEstää poikkeamat, ei vain\nhavaitse niitä." },
        { title: "4. ENNAKOIVA HUOLTO", desc: "Korjaa ennen kuin hajoaa", guide: "Tärinä + lämpötrendi:\n\nEsimerkkityönkulku:\n1. Grind5 laakeri 0.42g → 0.67g\n2. AI: 'vika 96 tunnissa'\n3. Pyöreä Pöytä: ajoita PM yölle\n4. Teknikko Jari P. saa työmääräimen\n5. Korjattu klo 02, nolla romua\n\nEnnustaa 72h ennen kuin ihminen huomaa." },
        { title: "5. SAANTO-AI", desc: "Eräkohtainen ennuste 98.7%", guide: "Oppii jokaisesta erästä:\nCD, kalvopaksuus, kohdistus,\nvikadensiteetti, etsausnopeus.\n\nIn-line ennuste ennen kuin\nlopullinen metrologia valmistuu.\n\nKorrelaatiomoottori löytää\npiilotetut prosessiyhteydet:\nRF-ramppi ↔ partikkelit\nKosteus ↔ vikadensiteetti" },
        { title: "6. VUORONVAIHTO", desc: "Automaattiset katsaukset", guide: "Joka vuoronvaihdossa:\n\n1. AI tiivistää kaikki tapahtumat\n2. Korostaa laitteiden hälytykset\n3. Listaa aktiiviset erät + tilat\n4. Merkitsee avoimet PM:t\n5. 30 sekunnin tiedotus seuraavalle\n\nTeknikot, vuorovastaavat, johto —\nkullakin oma näkymänsä." },
        { title: "MITEN SE OPPII", desc: "Digitaalinen kaksonen kasvaa", guide: "Viikko 1: Perustasot kaikille laitteille.\nKuukausi 1: Ennustaa yleiset viat.\nKuukausi 3: Löytää prosessikorrelaatioita\n  joita insinööri ei ole nähnyt.\nKuukausi 6: Täysi digitaalinen kaksonen.\n\nJokainen erä opettaa sitä.\nJokainen vuoro se terävöityy.\nJokainen PM validoi sen ennusteet." },
        { title: "ESKALAATIOKETJU", desc: "Hälytys → päätös → korjaus → vahvistus", guide: "Automaattinen eskalaatioketju:\n\n1. Sensorianomalia havaittu\n2. Pyöreä Pöytä: 4 agenttia arvioi\n3. Vakavuus: INFO → VAROITUS → KRIIT.\n4. VAROITUS: teknikko saa Telegram-hälytyksen\n5. KRIIT.: vuorovastaava + johto\n6. PM ajoitettu, vahvistettu, suljettu\n\nKaikki päätökset kirjattu + opittu." },
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
    demoHb: {
      gadget: [
        { a: "Reuna-OS", m: "WaggleDance käynnistyy ESP32-S3:lla... 512KB RAM, WiFi yhdistetty. Ladataan nano-agenttiympäristö.", t: "status" },
        { a: "TinyML", m: "48KB neuroverkko ladattu. Päättely laitteella: 12ms. Ei pilveä. Ei koskaan.", t: "learning" },
        { a: "Puettava AI", m: "Älykello yhdistetty BLE:llä. Syke, askeleet, unenlaatu virtaavat. Terveysagentti aktiivinen.", t: "status" },
        { a: "Ääni-agentti", m: "Herätyskäsky 'Hei Waggle' ladattu 4MB flashille. Kuuntelee aina, paikallisesti. Datasi ei lähde mihinkään.", t: "action" },
        { a: "Näkö-AI", m: "ESP32-CAM tunnistaa henkilöitä 5fps. Malli: 320KB MobileNet. Tunnistaa 3 kasvoa paikallisesti.", t: "insight" },
        { a: "Mesh-aivot", m: "6 laitetta muodostaa hermoverkon. Jokainen solmu: 1 erikoisala. Yhdessä: yleisälykkyys. Viive: 8ms.", t: "status" },
        { a: "Ennuste-AI", m: "Päivärytmisi opittu... Kahvinkeitin PÄÄLLE 06:42, valot himmenevät 22:15. Ennustetaan tarpeitasi.", t: "insight" },
        { a: "Sensorifuusio", m: "Lämpö + liike + valo + ääni yhdistetty. Konteksti: 'henkilö laittaa ruokaa'. Tarkkuus: 94%.", t: "insight" },
        { a: "OTA-evoluutio", m: "Mikromalli Gen 3 koulutettu yöllä SINUN datallasi. Lähetetään 6 solmuun... Älykkyyspäivitys live.", t: "learning" },
        { a: "Energianolla", m: "Aurinkoenergia: 340mW sisään, 180mW kulutus. Nettopositiivinen. Tämä AI toimii pelkällä auringonvalolla.", t: "status" },
        { a: "Parvilogiikka", m: "Ovikello-ESP32 havaitsi tuntemattoman → pihakamera zoomasi → valo syttyi. 3 laitetta, 1 päätös, 45ms.", t: "consensus" },
        { a: "Reuna-järki", m: "Vanha puhelin toimii huone-AI:na: ääni + kamera + sensorit. 0€ laitteistokulu. Täysi agenttiälykkyys.", t: "insight" },
        { a: "Digikaksonen", m: "Ympäristömallisi: 14 sensoria, 47 opittua käytöstä, 3 ennustetta aktiivisena. Päivittyy 60s välein.", t: "learning" },
        { a: "Reuna-OS", m: "LAITE-PARVI ONLINE: 6 laitetta, kollektiivinen älykkyys aktiivinen. Tulevaisuus ei ole pilvessä — se on taskussasi.", t: "consensus" },
        { a: "Reuna-OS", m: "Tämä ei ole demo — autonominen oppiminen on jo käynnissä. Jokainen laite oppii juuri nyt.", t: "status" },
      ],
      home: [
        { a: "Koti-aivot", m: "Skannataan Home Assistant API... 47 laitetta, 6 huonetta, 12 automaatiota. Rakennetaan kotisi hermokartta.", t: "status" },
        { a: "Energia-AI", m: "Sähkön spot-hinta API yhdistetty. Nyt: 3.2c/kWh. Kotisi ei maksa enää koskaan huippuhintaa.", t: "action" },
        { a: "Uutis-AI", m: "RSS-syötteet yhdistetty: YLE, Helsingin Sanomat, Iltalehti. Skannataan otsikoita... 14 artikkelia kiinnostuksiisi.", t: "learning" },
        { a: "Turva-AI", m: "4 kameraa verkossa. Kasvojentunnistus ladattu. Kotisi tietää nyt kuka kuuluu tänne — ja kuka ei.", t: "status" },
        { a: "Resepti-AI", m: "Jääkaappi skannattu: kanaa, kermaa, sieniä. Ehdotus: kermainen kanpasta. Resepti ladattu. Valmistusaika: 25 min.", t: "insight" },
        { a: "Läsnäolo-AI", m: "Lähdet 07:45, palaat 17:15. Koti esilämmittää, valot toivottavat tervetulleeksi. Se oppi tämän itse.", t: "learning" },
        { a: "Energia-AI", m: "Sähköauton lataus siirretty 02:00-05:00 hintaan 0.8c/kWh. Säästö tänä yönä 4.20€. Ennuste: 127€/kk.", t: "insight" },
        { a: "Uutis-AI", m: "Opitaan päivän teknologiauutisista: 3 älykotitrendiä indeksoitu. AI:si lukee 200 artikkelia/päivä puolestasi.", t: "learning" },
        { a: "Ilmanlaatu", m: "CO₂ nousee 920ppm makuuhuoneessa. Ilmanvaihto tehostettu. Nukut 23% paremmin. Tiede sanoo niin.", t: "action" },
        { a: "Pyöreä Pöytä", m: "5 agenttia väitteli: esilämmitys 06:30 vai 06:45? Konsensus: 06:30. Herätyksesi on 07:00.", t: "consensus" },
        { a: "Resepti-AI", m: "Viikon ruokalista generoitu jääkaapin + tarjousten + ravintotavoitteiden perusteella. Ostoslista: 12 tuotetta, 34€.", t: "insight" },
        { a: "Turva-AI", m: "Tuntematon henkilö ovella 22:47 — kasvoja ei tietokannassa. Valot PÄÄLLE, kamera tallentaa, ilmoitus lähetetty.", t: "action" },
        { a: "Ennuste-AI", m: "Perjantaikuvio havaittu: tilaat ruokaa 18:30. Keittiön valot himmenevät. Olohuoneen TV-esiasetus ladattu.", t: "learning" },
        { a: "Koti-aivot", m: "KOTIÄLYKKYYS ONLINE: 47 laitetta yhtenä aivona. Kotisi ei vain reagoi — se ennakoi.", t: "status" },
        { a: "Koti-aivot", m: "Tämä ei ole demo — autonominen oppiminen on jo käynnissä. Kotisi oppii jotain uutta joka tunti.", t: "status" },
      ],
      cottage: [
        { a: "Pesä-aivot", m: "12 ESP32-painosensoria löydetty. Jokaisella pesällä on nyt hermojärjestelmä. Luetaan perustasot...", t: "status" },
        { a: "Tauti-AI", m: "3 147 mehiläistautifaktaa indeksoitu. Varroa, esikotelomätä, nosema — AI-eläinlääkärisi ei nuku koskaan.", t: "learning" },
        { a: "Uutis-AI", m: "RSS: Mehiläishoitajat.fi — uusi artikkeli: 'Kevään varroakäsittelytulokset 2026'. Indeksoidaan avaintiedot...", t: "learning" },
        { a: "Sää-AI", m: "IL:n satelliittidata virtaa. 48h paikallisennuste ladattu. Mehiläistesi sääasema on sinun omaasi älykkäämpi.", t: "status" },
        { a: "Energia-AI", m: "Spot-hinta 2.1c/kWh nyt. Sauna ajoitettu illan 0.8c ikkunaan. Linkous klo 01:00.", t: "action" },
        { a: "Pesä-aivot", m: "Pesä 12: paino 33.9kg, lämpö 35.1°C, ääni 248Hz. Emo terve. Kukaan ei avannut pesää.", t: "insight" },
        { a: "Uutis-AI", m: "HÄLYTYS: Ruokavirasto RSS — esikotelomätäpurkaus raportoitu Hämeessä. Etäisyys: 140km. Riski: MATALA. Seurataan.", t: "action" },
        { a: "Resepti-AI", m: "Hunajaresepti-indeksi päivitetty: 8 uutta reseptiä opittu. 'Hunajalohi' osui ruokahistoriaasi.", t: "learning" },
        { a: "Akustiikka-AI", m: "Pesä 7 emon toitotus 450Hz — parveilua 48-72h. Hälytys lähetetty. Yksikään hoitaja ei kuulisi tätä.", t: "insight" },
        { a: "Kasvi-AI", m: "Satelliitti + fenologia: voikukka kukkii 3 päivän päästä. Pääsatokierto 18 päivässä. Korotukset päivänä 15.", t: "insight" },
        { a: "Pyöreä Pöytä", m: "5 agenttia väitteli: hoito to 09:00-11:00. Sää + tauti + pesädata linjassa. Päätös: TOTEUTETAAN.", t: "consensus" },
        { a: "Sato-AI", m: "Painotrendit: pesä 3 +0.8kg/pv. Satokierto vahvistettu. Ennuste: 42kg tämän korotusjakson aikana.", t: "learning" },
        { a: "Yö-aivot", m: "Klo 02: opitaan ScientificBeekeeping.com:sta — 23 uutta faktaa oksaalihappohöyrystyksestä. Ristiinvalidoitu.", t: "learning" },
        { a: "Pesä-aivot", m: "MÖKKIÄLYKKYYS ONLINE: 12 pesää yhtenä organismina. Lukee uutiset, seuraa säätä, kuuntelee mehiläisiäsi.", t: "status" },
        { a: "Pesä-aivot", m: "Tämä ei ole demo — autonominen oppiminen on jo käynnissä. 3 147 faktaa ja kasvaa. Mehiläisilläsi on AI.", t: "status" },
      ],
      factory: [
        { a: "Tehdas-aivot", m: "OPC-UA skannaus valmis: Grind5, PNT_2, PNT_4, Centrotherm_02, 6 uunia, 24 pumppua. 142 solmua online.", t: "status" },
        { a: "Grind5", m: "Grind5 karan pyörimisnopeus: 5 069 rpm. Puristuspaine: 3.2 psi. Lietevirtaus: 220 ml/min. Kaikki speksissä.", t: "status" },
        { a: "Saanto-AI", m: "2 847 historiallista erää syötetty. Saantomalli koulutettu. Ennustaa viat 14 kiekkoa ennen metrologiaa.", t: "learning" },
        { a: "PNT_2", m: "⚠️ PNT_2 pinnoitepaksuus ajautuu: 1.12µm → 1.18µm 3 tunnissa. Vielä speksissä (raja 1.20). Hälytys teknikko Mikko K.", t: "insight" },
        { a: "Vuoro-AI", m: "B-vuoron vetäjä Antti R: 14 aktiivista erää, käyttöaste 91.3%. Ajojärjestys optimoitu — Centrotherm_02 jono tyhjennetty.", t: "action" },
        { a: "Centrotherm_02", m: "Centrotherm_02 uuniputki 3: lämpötasaisuus ±0.3°C 150 kiekon yli. Oksidikasvatus: 102nm tavoite saavutettu.", t: "insight" },
        { a: "Grind5", m: "⚠️ Grind5 värinäpiikki: 0.42g → 0.67g nopeudella 5 069 rpm. Laakerikuvio havaittu. Aikaa raja-arvoon: 96h.", t: "insight" },
        { a: "BigData-AI", m: "Korrelaatio löydetty: PNT_4 kosteus +2% → vikadensiteetti +0.3/cm². Juurisyy 4 tunnissa. Prosessi-insinööri tiedotettu.", t: "learning" },
        { a: "PNT_4", m: "PNT_4 resistiannostelu: 1.87ml ±0.02. Suutin 2 poikkeaa 0.04ml. PM ajoitettu ennen eskalaatiota. Teknikko: Jari P.", t: "action" },
        { a: "Pyöreä Pöytä", m: "ESKALAATIO ESTETTY: Grind5 laakeri + PNT_2 ajautuma. 4 agenttia yksimielisiä: PM-ikkuna tänä yönä 02:00. Nolla romua.", t: "consensus" },
        { a: "Johto", m: "Live johdolle: OEE 94.2%, saanto 98.7%, 0 poikkeamaa, 2 ennakoivaa huoltoa ajoitettu. Hallitusraportti automaattinen.", t: "status" },
        { a: "Luovutus-AI", m: "B→C vuoronvaihto: 'Grind5 laakeriseuranta, PNT_2 vakaa säädön jälkeen, Centrotherm_02 täydellinen.' 30s briiffi.", t: "action" },
        { a: "BigData-AI", m: "Viikon 1 oivallus: uunin ramppinopeus ↔ kiekkovääntymä. Säädetään Centrotherm_02 reseptiä. Saantoparannus: +0.4%.", t: "learning" },
        { a: "Tehdas-aivot", m: "TEHDAS-AI ONLINE: 142 konetta, 6 teknikkoa, 3 vuoroa — yksi digikaksonen. Näkee ongelmat ennen kuin ne syntyvät.", t: "status" },
        { a: "Tehdas-aivot", m: "Tämä ei ole demo — autonominen oppiminen on jo käynnissä. Jokainen erä opettaa. Joka vuoro se terävöityy.", t: "status" },
      ],
    },
    demoEnd: { a: "WaggleDance", m: "Demo valmis. Nyt on aika integroida omat tietovirtasi. Katso ohjeet oikeasta palkista.", t: "status" },
  },
};

const D_IDS = ["gadget", "cottage", "home", "factory"];
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
  const ref=useRef(null);const fr=useRef(0);const vec=useRef([]);const mem=useRef([]);
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
  const dG=(gx,gy,val,max,label,gc)=>{const pct=Math.min(val/max,1),r=44,sa=-Math.PI*.75,ea=Math.PI*.75;ctx.beginPath();ctx.arc(gx,gy,r,sa,ea);ctx.strokeStyle="rgba(255,255,255,.06)";ctx.lineWidth=5;ctx.lineCap="round";ctx.stroke();ctx.beginPath();ctx.arc(gx,gy,r,sa,sa+(ea-sa)*pct);ctx.strokeStyle=gc+"80";ctx.lineWidth=5;ctx.lineCap="round";ctx.stroke();ctx.font="500 11px 'Inter',system-ui";ctx.textAlign="center";ctx.fillStyle="rgba(255,255,255,.22)";ctx.fillText(label,gx,gy-16);ctx.font="600 20px 'Inter',system-ui";ctx.fillStyle=gc+"A0";ctx.fillText(val+(max===8?"G":"%"),gx,gy+8)};
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
  ctx.font="600 10px 'Inter',system-ui";ctx.fillStyle=color+"30";ctx.textAlign="center";ctx.fillText("CONSCIOUSNESS",cx,cy-36);
  ctx.font="300 14px 'Inter',system-ui";ctx.fillStyle=color+"50";ctx.fillText("IQ",cx-44,cy+8);
  ctx.font="200 38px 'Inter',system-ui";ctx.fillStyle=color+"80";ctx.fillText(factCount.toLocaleString(),cx+8,cy+12);
  ctx.font="300 8px 'Inter',system-ui";ctx.fillStyle=color+"20";ctx.fillText("FACTS LEARNED",cx,cy+28);
  if(isThinking){ctx.font="500 8px 'Inter',system-ui";ctx.fillStyle=color+"45";ctx.fillText("● THINKING",cx,cy+42)}
  const bs=2.8;drawBrainOutline(ctx,cx,cy,bs);ctx.strokeStyle=color+"1E";ctx.lineWidth=2;ctx.stroke();drawBrainDetails(ctx,cx,cy,bs,color);
  dG(56,cy-24,cpuV,100,"CPU","#22C55E");dG(W-56,cy-50,gpuV,100,"GPU",color);dG(W-56,cy+50,vramV,8,"VRAM","#A78BFA");
  id=requestAnimationFrame(draw)};draw();return()=>cancelAnimationFrame(id)},[color,factCount,isThinking,agents,cpuV,gpuV,vramV]);
  return <canvas ref={ref} style={{width:700,height:560}}/>;
}

function Rain({rgb}){const ref=useRef(null);useEffect(()=>{const c=ref.current;if(!c)return;const ctx=c.getContext("2d");const W=c.width=innerWidth,H=c.height=innerHeight;const cols=Math.floor(W/28),drops=Array.from({length:cols},()=>Math.random()*H);const ch="01∞∆λπΣΩ".split("");let id;const draw=()=>{ctx.fillStyle="rgba(0,0,0,.07)";ctx.fillRect(0,0,W,H);ctx.font="11px monospace";for(let i=0;i<cols;i++){ctx.fillStyle=`rgba(${rgb},${(Math.random()*.07+.03).toFixed(3)})`;ctx.fillText(ch[Math.floor(Math.random()*ch.length)],i*28,drops[i]);drops[i]=drops[i]>H&&Math.random()>.975?0:drops[i]+20}id=requestAnimationFrame(draw)};draw();return()=>cancelAnimationFrame(id)},[rgb]);return <canvas ref={ref} style={{position:"fixed",inset:0,zIndex:0,pointerEvents:"none"}}/>}
function Boot({onDone}){const[s,setS]=useState(0);const[t,setT]=useState("");const[sub,setSub]=useState("");const[pr,setPr]=useState(0);const[hw,setHw]=useState([]);const hwData=useRef({gpu:"RTX A2000 — 8 GB",cpu:"16 threads",ram:"32 GB"});useEffect(()=>{fetch("/api/hardware").then(r=>r.json()).then(d=>{if(d.gpu_name)hwData.current.gpu=d.gpu_name;if(d.cpu_model)hwData.current.cpu=d.cpu_model;if(d.ram_total_gb)hwData.current.ram=Math.round(d.ram_total_gb)+" GB"}).catch(()=>{})},[]);useEffect(()=>{const seq=[[300,()=>setS(1)],[1200,()=>setT("INITIALIZING")],[2800,()=>{setS(2);setT("SCANNING HARDWARE")}],[3200,()=>setHw(["GPU  "+hwData.current.gpu])],[3600,()=>setHw(p=>[...p,"CPU  "+hwData.current.cpu])],[4000,()=>setHw(p=>[...p,"RAM  "+hwData.current.ram])],[5200,()=>{setS(3);setT("LOADING MODELS")}],[5500,()=>{setPr(25);setSub("phi4-mini")}],[5900,()=>{setPr(50);setSub("llama3.2:1b")}],[6300,()=>{setPr(75);setSub("nomic-embed")}],[6700,()=>{setPr(100);setSub("ALL LOADED")}],[7900,()=>{setS(4);setT("AWAKENING")}],[8400,()=>setSub("loading memories")],[9000,()=>setSub("spawning agents")],[9600,()=>setSub("consciousness online")],[10400,()=>{setS(5);setT("I AM ALIVE");setSub("")}],[13000,onDone]];const ids=seq.map(([d,fn])=>setTimeout(fn,d));return()=>ids.forEach(clearTimeout)},[onDone]);return(<div style={{position:"fixed",inset:0,background:"#000",display:"flex",alignItems:"center",justifyContent:"center",flexDirection:"column",zIndex:100}}><Rain rgb="99,102,241"/>{s>=4&&<div style={{position:"absolute",width:350,height:350,borderRadius:"50%",background:"radial-gradient(circle,rgba(99,102,241,.08) 0%,transparent 70%)",animation:"breathe 3s ease-in-out infinite"}}/>}<div style={{position:"relative",zIndex:2,textAlign:"center"}}>{s>=1&&s<5&&<div style={{fontSize:15,fontWeight:600,letterSpacing:8,color:"rgba(255,255,255,.45)",marginBottom:30}}>WAGGLEDANCE AI SWARM (ON-PREM)</div>}<div style={{fontSize:s===5?140:s===4?44:36,fontWeight:s===5?800:600,color:s===5?"#818CF8":"#fff",letterSpacing:s===5?20:6,transition:"all .8s",textShadow:s===5?"0 0 120px rgba(99,102,241,.8), 0 0 240px rgba(99,102,241,.5), 0 0 400px rgba(99,102,241,.25)":s>=4?"0 0 50px rgba(99,102,241,.25)":"none",animation:s===5?"explodeIn .6s cubic-bezier(.17,.67,.29,1.3)":"none"}}>{t}</div>{s===2&&<div style={{marginTop:26}}>{hw.map((l,i)=><div key={i} style={{fontSize:17,fontWeight:600,color:"rgba(255,255,255,.65)",letterSpacing:1,fontFamily:"monospace",marginBottom:5}}>{l}</div>)}</div>}{s===3&&<div style={{marginTop:26,width:280,margin:"26px auto 0"}}><div style={{height:3,background:"rgba(255,255,255,.15)",overflow:"hidden"}}><div style={{height:"100%",background:"linear-gradient(90deg,#6366F1,#A5B4FC)",width:`${pr}%`,transition:"width .4s"}}/></div><div style={{fontSize:15,fontWeight:600,color:"rgba(255,255,255,.60)",marginTop:8,letterSpacing:3,fontFamily:"monospace"}}>{sub}</div></div>}{s===4&&<div style={{fontSize:16,fontWeight:600,color:"rgba(99,102,241,.70)",marginTop:14,letterSpacing:4}}>{sub}</div>}{s===5&&<div style={{fontSize:18,fontWeight:700,color:"rgba(99,102,241,.60)",marginTop:16,letterSpacing:10,textShadow:"0 0 40px rgba(99,102,241,.4)",animation:"explodeIn .6s cubic-bezier(.17,.67,.29,1.3) .15s both"}}>WAGGLEDANCE AI SWARM</div>}</div></div>)}

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
          <div style={{fontSize:22,fontWeight:700,color:color,letterSpacing:3}}>JANI KORPI</div>
          <div style={{fontSize:11,color:"rgba(255,255,255,.40)",letterSpacing:4,marginTop:4}}>HELSINKI, FINLAND</div>
          <div style={{fontSize:10,color:"rgba(255,255,255,.30)",marginTop:3}}>Ahkerat Mehiläiset / JKH Service 🐝</div>
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
          <span style={{color:"rgba(255,255,255,.45)"}}>ORIGIN:</span> The original purpose of this project was to create consciousness.
        </div>
        <div style={{marginTop:18,padding:"12px",background:color+"06",border:`1px solid ${color}10`,borderRadius:6}}>
          <div style={{fontSize:10,color:color+"90",letterSpacing:3,marginBottom:8}}>DEVELOPERS</div>
          <div style={{fontSize:11,color:"rgba(255,255,255,.35)",lineHeight:2.2,fontFamily:"monospace"}}>
            99% — <span style={{color:"#A78BFA",fontWeight:600}}>Claude OPUS 4.6</span> <span style={{color:"rgba(255,255,255,.20)"}}>// heavy lifting</span><br/>
            {"  "}1% — <span style={{color:"#F59E0B",fontWeight:600}}>Jani Korpi</span> 🐝 <span style={{color:"rgba(255,255,255,.20)"}}>// vision, direction, coffee</span>
          </div>
        </div>
        <div style={{fontSize:9,color:"rgba(255,255,255,.15)",marginTop:14,textAlign:"center",letterSpacing:4}}>
          AHKERAT MEHILÄISET • HELSINKI • FINLAND • 2024-2026
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

function FeatureList({feats,color,label,onOpen,t}){return(<div><div style={{fontSize:9,letterSpacing:4,color:"rgba(255,255,255,.30)",marginBottom:10}}>{label}</div>{[{key:"hw",icon:"💻",title:t.hwSpec,d:t.hwDesc},{key:"agents",icon:"🤖",title:t.agents,d:t.agentsDesc}].map(s=>(<div key={s.key} onClick={()=>onOpen(s.key)} style={{cursor:"pointer",padding:"6px 0",borderBottom:"1px solid rgba(255,255,255,.025)"}}><div style={{fontSize:12,color:color+"90",fontWeight:600,letterSpacing:2}}>{s.icon} {s.title}</div><div style={{fontSize:9,color:"rgba(255,255,255,.28)"}}>{s.d}</div></div>))}{feats.map((f,i)=>(<div key={i} onClick={()=>onOpen(f)} style={{cursor:"pointer",padding:"6px 0",borderBottom:"1px solid rgba(255,255,255,.02)",display:"flex",justifyContent:"space-between",alignItems:"center"}}><div><div style={{fontSize:11,color:"rgba(255,255,255,.55)",fontWeight:600,letterSpacing:1.5}}>{f.title}</div><div style={{fontSize:9,color:"rgba(255,255,255,.28)"}}>{f.desc}</div></div><span style={{fontSize:10,color:color+"45"}}>→</span></div>))}<div onClick={()=>onOpen("info")} style={{cursor:"pointer",padding:"7px 0",marginTop:4,borderTop:`1px solid ${color}10`}}><div style={{fontSize:12,color:color+"90",fontWeight:600,letterSpacing:2}}>🧠 {t.techArch}</div><div style={{fontSize:9,color:"rgba(255,255,255,.28)"}}>{t.techArchDesc}</div></div><div onClick={()=>onOpen("disclaimer")} style={{cursor:"pointer",padding:"7px 0",borderTop:"1px solid rgba(255,255,255,.03)"}}><div style={{fontSize:12,color:"rgba(255,255,255,.40)",fontWeight:600,letterSpacing:2}}>⚠️ {t.disclaimer}</div><div style={{fontSize:9,color:"rgba(255,255,255,.25)"}}>{t.disclaimerDesc}</div></div></div>)}

export default function App(){
  const[on,setOn]=useState(false);const[dom,setDom]=useState("cottage");const[lang,setLang]=useState("en");
  const[fc,setFc]=useState(47293);const[lr,setLr]=useState(37);const[hb,setHb]=useState([]);
  const[think,setThink]=useState(false);const[cpuL,setCpu]=useState(12);const[gpuL,setGpu]=useState(54);
  const[vramU,setVram]=useState(4.3);const[overlay,setOverlay]=useState(null);
  const[chatIn,setChatIn]=useState("");const[chatMsgs,setChatMsgs]=useState([]);
  const t=L[lang];const col=D_COL[dom];const hw=HW[dom];
  const ag={gadget:["Mesh","TinyML","Battery","OTA","Relay"],home:["Climate","Energy","Security","Light","Orch."],cottage:["Tarhaaja","Tauti","Meteo","Sähkö","Queen"],factory:["Process","Yield","Equip","Defect","Shift"]};

  // ═══ PER-TAB DEMO MODE (90s each) ═══
  const DEMO_DURATION = 90000;
  const DEMO_INTERVAL = 6000;
  const [demoTimers, setDemoTimers] = useState({ gadget: null, home: null, cottage: null, factory: null });
  const demoIndexRef = useRef({ gadget: 0, home: 0, cottage: 0, factory: 0 });
  const demoEndShownRef = useRef({ gadget: false, home: false, cottage: false, factory: false });
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

    // Fact count: use API value only if backend has real data AND demo is done
    if (!isDemoActive(dom) && api.backendAvailable && api.status.facts > 0) {
      setFc(api.status.facts);
    }

    // Hardware gauges from API (always, even during demo)
    if (api.status.cpu > 0) setCpu(api.status.cpu);
    if (api.status.gpu > 0) setGpu(api.status.gpu);
    if (api.status.vram > 0) setVram(api.status.vram);

    // Thinking state from API
    if (!isDemoActive(dom)) setThink(api.status.is_thinking);
  }, [on, dom, api.backendAvailable, api.status, isDemoActive]);

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
    if (on && demoTimers.cottage === null) {
      setDemoTimers(prev => ({ ...prev, cottage: Date.now() }));
    }
  }, [on]);

  // Tab switch: restart demo every time a tab is clicked
  const sw=(id)=>{
    setDom(id);
    setOverlay(null);
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

    // Simulate slow fact count growth during demo
    setFc(prev => prev + Math.floor(Math.random() * 3) + 1);

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
    const iv=setInterval(()=>{
    // Skip if API is providing heartbeats
    if (api.backendAvailable && api.heartbeats.length > 0) return;

    const pool=t.hb[dom];setThink(true);setTimeout(()=>setThink(false),2500);setTimeout(()=>{setHb(p=>[pool[Math.floor(Math.random()*pool.length)],...p.slice(0,5)]);setFc(p=>p+Math.floor(Math.random()*4)+1);setLr(28+Math.floor(Math.random()*22));setCpu(8+Math.floor(Math.random()*20));setGpu(48+Math.floor(Math.random()*18));setVram(+(4+Math.random()*.6).toFixed(1))},1200)},6000);return()=>clearInterval(iv)},[on,dom,lang,api.backendAvailable,api.heartbeats.length,isDemoActive]);

  // ═══ CHAT — uses API with mock fallback ═══
  const handleChat=async()=>{if(!chatIn.trim())return;const msg=chatIn.trim();setChatIn("");setChatMsgs(p=>[...p,{role:"user",text:msg}]);setThink(true);
    const response = await api.sendChat(msg, lang);
    setThink(false);
    setChatMsgs(p=>[...p,{role:"ai",text:response}]);
  };

  const aw=[{k:"State",v:"CONSCIOUS",c:"#22C55E"},{k:"Learn",v:"CONTINUOUS",c:"#A78BFA"},{k:"Facts",v:fc.toLocaleString(),c:col},{k:"Rate",v:`+${lr}/hr`,c:"#22D3EE"},{k:"Halluc",v:"1.8%",c:"#22C55E"},{k:"Micro",v:"GEN 8",c:col},{k:"Cloud",v:"NONE",c:"#22C55E"},{k:"Scale",v:"ELASTIC",c:"#6366F1"}];
  return(
    <div style={{background:"#000",color:"#fff",minHeight:"100vh",fontFamily:"'Inter',system-ui,sans-serif",overflow:"hidden"}}>
      <style>{`@keyframes fadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}@keyframes pulse{0%,100%{opacity:1}50%{opacity:.15}}@keyframes breathe{0%,100%{transform:scale(1);opacity:.5}50%{transform:scale(1.1);opacity:1}}@keyframes explodeIn{0%{transform:scale(0);opacity:0}60%{transform:scale(1.15);opacity:1}100%{transform:scale(1);opacity:1}}*{box-sizing:border-box;margin:0;padding:0}button{font-family:inherit}::-webkit-scrollbar{display:none}*{scrollbar-width:none}input::placeholder{color:rgba(255,255,255,.28)}`}</style>
      {!on&&<Boot onDone={boot}/>}
      {on&&<>
        <Rain rgb={D_RGB[dom]}/>
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
              {/* Language toggle */}
              <button onClick={()=>setLang(l=>l==="en"?"fi":"en")} style={{background:"rgba(255,255,255,.02)",border:"1px solid rgba(255,255,255,.05)",borderRadius:4,padding:"4px 8px",cursor:"pointer",marginRight:12,transition:"all .3s"}}>
                <span style={{fontSize:8,fontWeight:600,color:lang==="fi"?"#22D3EE":"rgba(255,255,255,.25)",letterSpacing:2}}>{lang==="fi"?"FI":"EN"}</span>
              </button>
              <span style={{display:"flex",alignItems:"center",gap:5,fontSize:7.5,letterSpacing:3,color:api.backendAvailable?"#22C55E":"#F59E0B"}}><span style={{width:5,height:5,borderRadius:"50%",background:api.backendAvailable?"#22C55E":"#F59E0B",animation:"pulse 2s infinite"}}/>{api.backendAvailable?"LIVE":"ALIVE"}</span>
            </div>
          </div>
          <div style={{display:"grid",gridTemplateColumns:"30vw 1fr 24vw",minHeight:"calc(100vh - 52px)"}}>
            <div style={{display:"flex",flexDirection:"column",borderRight:"1px solid rgba(255,255,255,.03)",maxHeight:"calc(100vh - 52px)"}}>
              <div style={{flex:1,padding:"12px 16px",overflowY:"auto"}}><HBFeed msgs={hb} color={col} label={t.heartbeat}/>{chatMsgs.length>0&&<div style={{marginTop:10,borderTop:"1px solid rgba(255,255,255,.04)",paddingTop:8}}>{chatMsgs.map((m,i)=>(<div key={i} style={{padding:"5px 0",animation:"fadeUp .4s"}}><div style={{fontSize:7.5,color:m.role==="user"?"#22D3EE":col,fontWeight:600,letterSpacing:2,marginBottom:2}}>{m.role==="user"?"YOU":"WAGGLEDANCE"}</div><div style={{fontSize:10,color:m.role==="user"?"rgba(255,255,255,.60)":"rgba(255,255,255,.45)",lineHeight:1.7,paddingLeft:8,fontWeight:300}}>{m.text}</div></div>))}</div>}</div>
              <div style={{padding:"8px 16px",borderTop:"1px solid rgba(255,255,255,.04)"}}><div style={{display:"flex",gap:6}}><input value={chatIn} onChange={e=>setChatIn(e.target.value)} onKeyDown={e=>e.key==="Enter"&&handleChat()} placeholder={t.placeholder} style={{flex:1,background:"rgba(255,255,255,.02)",border:`1px solid ${col}15`,borderRadius:5,padding:"7px 10px",color:"#fff",fontSize:9.5,outline:"none",fontFamily:"'Inter',system-ui"}}/><button onClick={handleChat} style={{background:col+"15",border:`1px solid ${col}25`,borderRadius:5,padding:"7px 12px",cursor:"pointer",color:col,fontSize:7.5,letterSpacing:2,fontWeight:600}}>{t.send}</button></div></div>
            </div>
            <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",borderRight:"1px solid rgba(255,255,255,.03)",position:"relative"}}>
              {overlay&&<Overlay item={overlay} color={col} hw={hw} onClose={()=>setOverlay(null)} t={t}/>}
              <BrainScene color={col} factCount={fc} isThinking={think} agents={ag[dom]} cpuV={cpuL} gpuV={gpuL} vramV={vramU}/>
              <div style={{textAlign:"center",marginTop:-8}}><div style={{fontSize:34,fontWeight:200,color:"rgba(255,255,255,.65)",letterSpacing:6}}>{D_IC[dom]} {t.domains[dom].label}</div><div style={{fontSize:14,color:"rgba(255,255,255,.30)",letterSpacing:3,marginTop:5}}>{t.domains[dom].tag}</div></div>
              <div style={{marginTop:10,display:"flex",flexWrap:"wrap",justifyContent:"center",gap:"4px 14px",maxWidth:440}}>{aw.map((a,i)=>(<div key={i} style={{fontSize:9.5,color:"rgba(255,255,255,.30)",letterSpacing:1}}>{a.k}: <span style={{color:a.c,fontWeight:600,fontFamily:"monospace"}}>{a.v}</span></div>))}</div>
            </div>
            <div style={{padding:"12px 14px",overflowY:"auto",maxHeight:"calc(100vh - 52px)"}}><FeatureList feats={t.feats[dom]} color={col} label={t.features} onOpen={setOverlay} t={t}/></div>
          </div>
          <div style={{position:"fixed",bottom:0,left:0,right:0,padding:"3px 22px",background:"rgba(0,0,0,.93)",borderTop:"1px solid rgba(255,255,255,.025)",display:"flex",justifyContent:"space-between",fontSize:6,color:"rgba(255,255,255,.15)",letterSpacing:3}}><span>{t.bottomL}</span><span>{t.bottomC}</span><span>{fc.toLocaleString()} FACTS</span></div>
        </div>
      </>}
    </div>
  );
}
