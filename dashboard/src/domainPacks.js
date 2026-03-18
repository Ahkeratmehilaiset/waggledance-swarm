/**
 * Domain Packs — profile-specific content for the WaggleDance dashboard.
 *
 * Each profile exports its content through a structured pack. To add a new
 * domain, create a new pack object following the same shape and add it to
 * the DOMAIN_PACKS export.
 *
 * Generic autonomy concepts (layers, badges, errors, architecture) live in
 * the main dashboard components. Domain packs only inject:
 *   - Hardware specs
 *   - Feature/setup guides (EN + FI)
 *   - Live heartbeat messages (EN + FI)
 *   - Demo heartbeat sequences (EN + FI)
 *   - Domain label and tagline (EN + FI)
 *   - Profile icon and color
 */

// ── Hardware Specs ────────────────────────────────────────────────────────────

const HW_SPECS = {
  gadget: { name: "Raspberry Pi 5 / ESP32-S3", cpu: "Cortex-A76 4C / Xtensa LX7", ram: "8 GB / 512 KB", gpu: "VideoCore VII / none", storage: "128 GB SD / 16 MB Flash", power: "5W / 0.5W", tier: "EDGE", price: "~80€ / ~8€", models: "qwen3:0.6b (RPi) / TinyML (ESP32)", inference: "CPU quantized / on-chip ML", calc: { llm_toks: 5, fact_pipeline: "18s", facts_hour_real: 12, facts_day: 288, facts_week: "2,016", facts_year: "105,120", chat_cold: "12,000ms", chat_warm: "800ms", chat_micro: "45ms", chat_evolution: "12s → 800ms → 45ms", round_table: "N/A", agents_max: 2, night_8h: "~96", micro_gen: "~8 vk" } },
  cottage: { name: "Intel NUC 13 Pro", cpu: "i7-1360P 12C/16T", ram: "32 GB DDR5", gpu: "Iris Xe (shared)", storage: "1 TB NVMe", power: "28W", tier: "LIGHT", price: "~650€", models: "qwen3:0.6b + nomic-embed + Opus-MT", inference: "CPU-only GGUF", calc: { llm_toks: 15, fact_pipeline: "6.8s", facts_hour_real: 65, facts_day: 1560, facts_week: "10,920", facts_year: "569,400", chat_cold: "3,820ms", chat_warm: "180ms", chat_micro: "12ms", chat_evolution: "3,820ms → 180ms → 12ms", round_table: "45s", agents_max: 8, night_8h: "~520", micro_gen: "~3 vk" } },
  home: { name: "Mac Mini Pro M4", cpu: "M4 Pro 14-core", ram: "48 GB unified", gpu: "20-core GPU 48GB", storage: "1 TB NVMe", power: "30W", tier: "PRO", price: "~2,200€", models: "phi4:14b + llama3.3:8b + whisper + Piper", inference: "Metal GPU (MLX)", calc: { llm_toks: 42, fact_pipeline: "2.4s", facts_hour_real: 220, facts_day: 5280, facts_week: "36,960", facts_year: "1,927,200", chat_cold: "920ms", chat_warm: "42ms", chat_micro: "3ms", chat_evolution: "920ms → 42ms → 3ms", round_table: "12s", agents_max: 25, night_8h: "~1,760", micro_gen: "~8 pv" } },
  factory: { name: "NVIDIA DGX B200", cpu: "2× Xeon Platinum 8570, 112C/224T", ram: "4,096 GB DDR5", gpu: "8× B200 1,536 GB HBM3e, 72K TFLOPS FP16", storage: "30 TB NVMe RAID", power: "14.4kW", tier: "ENTERPRISE", price: "~475,000€", models: "llama3.3:70b + vision + 50 micro", inference: "8-way tensor parallel, NVLink 5 1.8TB/s/GPU", calc: { llm_toks: 380, fact_pipeline: "0.05s", facts_hour_real: 2800, facts_day: 67200, facts_week: "470,400", facts_year: "24,528,000", chat_cold: "15ms", chat_warm: "5ms", chat_micro: "0.08ms", chat_evolution: "15ms → 5ms → 0.08ms", round_table: "0.5s", agents_max: 50, night_8h: "~22,400", micro_gen: "~36h" } },
  apiary: { name: "Intel NUC 13 Pro + ESP32 Mesh", cpu: "i7-1360P 12C/16T", ram: "32 GB DDR5", gpu: "Iris Xe (shared)", storage: "1 TB NVMe", power: "28W + 12× 0.5W", tier: "STANDARD", price: "~750€ (NUC + sensors)", models: "phi4-mini + nomic-embed + bee acoustic CNN", inference: "CPU GGUF + ESP32 TinyML", calc: { llm_toks: 15, fact_pipeline: "6.5s", facts_hour_real: 70, facts_day: 1680, facts_week: "11,760", facts_year: "613,200", chat_cold: "3,500ms", chat_warm: "160ms", chat_micro: "10ms", chat_evolution: "3,500ms → 160ms → 10ms", round_table: "40s", agents_max: 12, night_8h: "~560", micro_gen: "~3 vk" } },
};

// ── Domain Labels ─────────────────────────────────────────────────────────────

const DOMAIN_LABELS = {
  en: {
    gadget:  { label: "GADGET",  tag: "ESP32 • RPi • wearables • edge intelligence everywhere" },
    cottage: { label: "COTTAGE", tag: "sauna • forest • off-grid intelligence" },
    home:    { label: "HOME",    tag: "47 devices • 6 rooms • smart living" },
    factory: { label: "FACTORY", tag: "142 equipment • 24/7 semiconductor production" },
    apiary:  { label: "APIARY",  tag: "professional beekeeping • 50+ hives • full monitoring" },
  },
  fi: {
    gadget:  { label: "LAITE",  tag: "ESP32 • RPi • puettavat • reunaälyä kaikkialle" },
    cottage: { label: "MÖKKI",  tag: "sauna • metsä • off-grid-älykkyys" },
    home:    { label: "KOTI",   tag: "47 laitetta • 6 huonetta • älykäs asuminen" },
    factory: { label: "TEHDAS", tag: "142 laitetta • 24/7 puolijohdetuotanto" },
    apiary:  { label: "TARHA",  tag: "ammattimainen mehiläistarhaus • 50+ pesää • täysi valvonta" },
  },
};

// ── Profile Icons & Colors ────────────────────────────────────────────────────

const PROFILE_ICONS = { gadget: "📡", home: "🏠", cottage: "🏡", factory: "⚙️", apiary: "🐝" };
const PROFILE_COLORS = { gadget: "#22D3EE", home: "#6366F1", cottage: "#F59E0B", factory: "#EF4444", apiary: "#A3E635" };
const PROFILE_COLORS_RGB = { gadget: "34,211,238", home: "99,102,241", cottage: "245,158,11", factory: "239,68,68", apiary: "163,230,53" };

// ── Feature Guides (EN) ──────────────────────────────────────────────────────

const FEATS_EN = {
  gadget: [
    { title: "1. GET HARDWARE", desc: "What you need", guide: "Minimum: any ONE of these:\n• Raspberry Pi 5 (8GB) — €80\n• ESP32-S3 DevKit — €8\n• Old Android phone (Termux)\n• Any Linux SBC with WiFi\n\nRecommended starter kit:\n1× RPi 5 (brain) + 2× ESP32 (sensors)\nTotal: ~€110" },
    { title: "2. INSTALL", desc: "5-minute setup", guide: "On Raspberry Pi / Linux SBC:\npip install -r requirements.txt\npython main.py\n\nWaggleDance auto-detects your\nhardware and selects optimal models.\nNo configuration needed.\n\nDashboard: http://[device-ip]:5173" },
    { title: "3. ADD SENSORS", desc: "Connect ESP32 nodes", guide: "Flash ESP32 with sensor firmware:\n1. Set WiFi credentials\n2. Set MQTT broker IP (your RPi)\n3. Upload firmware\n4. Sensor auto-registers\n\nSupported: temp, humidity, weight,\nsound, motion, light, air quality.\nAll data → vector memory." },
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
    { title: "1. INSTALL", desc: "Set up at your cottage", guide: "Hardware: any PC or NUC.\nWorks offline after first setup.\n\npip install -r requirements.txt\npython main.py\n\nDashboard: http://[local-ip]:5173\nRuns on solar + 4G." },
    { title: "2. ENVIRONMENT SENSORS", desc: "ESP32 temp + moisture + air", guide: "Per-room sensor node (~€20):\n• ESP32-S3 + DS18B20 (temperature)\n• DHT22 (humidity)\n• Air quality sensor (VOC/CO2)\n\nFlash firmware, set WiFi, done.\nTemp, humidity, air quality every 60s.\nAnomaly alerts via Telegram." },
    { title: "3. WEATHER", desc: "FMI forecast — free, automatic", guide: "FMI Open Data API (no key needed):\nweather:\n  enabled: true\n  locations: ['Your City']\n\nGets: temperature, wind, rain,\n48h forecast every 30 minutes.\nAgents use weather in decisions." },
    { title: "4. CAMERAS", desc: "Wildlife + security", guide: "RPi 5 + Coral TPU (~€110):\nfrigate:\n  enabled: true\n  cameras:\n    yard: {description: 'Property area'}\n\nDetects: bears, moose, foxes,\npeople, vehicles. Day and night.\nTelegram alert + snapshot in <2s." },
    { title: "5. SPOT PRICE", desc: "Sauna + appliance timing", guide: "Electricity spot price — automatic:\nelectricity:\n  enabled: true\n\nFinds cheapest hours for:\n• Sauna heating\n• Water heater\n• Floor heating\n• Any scheduled load\n\nSaves 15-30% on electricity." },
    { title: "6. NEWS FEEDS", desc: "Weather alerts + local news", guide: "Critical RSS feeds:\nrss:\n  feeds:\n    - url: your-local-news.fi/rss\n      critical: true\n    - url: weather-alerts.fi/feed\n\nSevere weather alerts\ntrigger IMMEDIATE notification.\nAll articles indexed for learning." },
    { title: "HOW IT LEARNS", desc: "Your property expert", guide: "Day 1: Sensor baselines established.\nWeek 1: Learns your property patterns.\nMonth 1: Predicts heating needs.\nMonth 6: Anticipates all conditions.\n\nNight learning: reads relevant data,\ncross-validates, grows 24/7.\nYour AI property manager never sleeps." },
    { title: "ANOMALY DETECTION", desc: "Catch problems early", guide: "ESP32 sensors at key locations:\n\nDetects:\n• Unusual temperature drops (frost)\n• Moisture anomalies (leaks)\n• Power fluctuations\n• Unexpected changes in patterns\n\nAlerts before problems become\ncostly damage." },
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
  apiary: [
    { title: "1. INSTALL", desc: "Set up your apiary brain", guide: "Hardware: NUC or mini-PC at apiary.\nWorks offline after first setup.\n\npip install -r requirements.txt\npython main.py --profile apiary\n\nDashboard: http://[local-ip]:8000\nAll hive data stays on-site." },
    { title: "2. HIVE SENSORS", desc: "ESP32 per hive", guide: "Per hive sensor node (~€20):\n• ESP32-S3 + load cell (weight)\n• DS18B20 (temperature)\n• INMP441 mic (colony sound)\n• DHT22 (humidity)\n\nData every 60s via MQTT.\nAnomaly alerts via Telegram.\nSupports 50+ hives." },
    { title: "3. ACOUSTIC AI", desc: "Colony health by sound", guide: "FFT spectrum analysis:\n\nDetects:\n• Queen piping (swarming 48-72h)\n• Stress buzz (disturbance)\n• Queenless howl (300-500Hz)\n• Normal healthy hum (200-250Hz)\n\n7-day baseline per hive.\nAlerts before human detection." },
    { title: "4. DISEASE MODEL", desc: "Varroa + foulbrood AI", guide: "Axiom-based disease models:\n\nVarroa treatment optimizer:\n• Mite count per 100 bees\n• Temperature windows\n• Treatment type selection\n• Oxalic acid timing\n\nFoulbrood risk scoring:\n• RSS alerts from authorities\n• Distance-based risk model" },
    { title: "5. HONEY YIELD", desc: "Harvest prediction", guide: "Weight-based yield model:\n\nInputs: hive weight, flower bloom,\nweather, colony strength.\n\nOutputs:\n• Daily weight gain trend\n• Honey flow start/end dates\n• Projected harvest per hive\n• Super add/remove timing\n\nAccuracy: ±5% after 1 season." },
    { title: "6. SEASONAL TASKS", desc: "12-month calendar", guide: "AI-driven task calendar:\n\nSpring: colony inspection, feeding\nSummer: super management, splits\nAutumn: varroa treatment, feeding\nWinter: monitoring, planning\n\nEach task adapted to your hives\nand local weather conditions." },
    { title: "HOW IT LEARNS", desc: "Your apiary expert", guide: "Day 1: 3,147+ bee facts loaded.\nWeek 1: Learns your hive patterns.\nMonth 1: Predicts swarms + flows.\nSeason 1: Per-hive optimization.\n\nNight learning: reads research,\ncross-validates, grows 24/7.\nYour AI beekeeper never sleeps." },
    { title: "SWARM PREDICTION", desc: "Prevent before it happens", guide: "Multi-signal swarming model:\n\n• Weight plateau detection\n• Queen cell presence (acoustic)\n• Colony congestion (temp)\n• Time of year + weather\n\nAlerts 2-5 days before swarm.\nSuggested actions per hive.\nPrevents 80%+ of swarms." },
  ],
};

// ── Feature Guides (FI) ──────────────────────────────────────────────────────

const FEATS_FI = {
  gadget: [
    { title: "1. HANKI LAITTEISTO", desc: "Mitä tarvitset", guide: "Vähimmäisvaatimus: mikä tahansa YKSI:\n• Raspberry Pi 5 (8GB) — 80€\n• ESP32-S3 DevKit — 8€\n• Vanha Android-puhelin (Termux)\n• Mikä tahansa Linux-SBC WiFillä\n\nSuositeltu aloituspaketti:\n1× RPi 5 (aivot) + 2× ESP32 (sensorit)\nYhteensä: ~110€" },
    { title: "2. ASENNUS", desc: "5 minuutin käyttöönotto", guide: "Raspberry Pi / Linux SBC:\npip install -r requirements.txt\npython main.py\n\nWaggleDance tunnistaa laitteistosi\nautomaattisesti ja valitsee optimaaliset\nmallit. Ei konfigurointia tarvita.\n\nHallintapaneeli: http://[laite-ip]:5173" },
    { title: "3. LISÄÄ SENSORIT", desc: "Yhdistä ESP32-solmut", guide: "Flashaa ESP32 sensorifirmwarella:\n1. Aseta WiFi-tunnukset\n2. Aseta MQTT-brokerin IP (RPi:si)\n3. Lataa firmware\n4. Sensori rekisteröityy itse\n\nTuetut: lämpö, kosteus, paino,\nääni, liike, valo, ilmanlaatu.\nKaikki data → vektorimuisti." },
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
    { title: "2. YMPÄRISTÖSENSORIT", desc: "ESP32 lämpö + kosteus + ilma", guide: "Huonekohtainen sensorisolmu (~20€):\n• ESP32-S3 + DS18B20 (lämpötila)\n• DHT22 (kosteus)\n• Ilmanlaatuanturi (VOC/CO2)\n\nFlashaa firmware, aseta WiFi, valmis.\nLämpö, kosteus, ilmanlaatu 60s välein.\nPoikkeamahälytykset Telegramiin." },
    { title: "3. SÄÄ", desc: "IL-ennuste — ilmainen, automaattinen", guide: "IL:n avoin data-API (ei avainta):\nweather:\n  enabled: true\n  locations: ['Kaupunkisi']\n\nHakee: lämpötila, tuuli, sade,\n48h ennuste 30 minuutin välein.\nAgentit käyttävät säätä päätöksissä." },
    { title: "4. KAMERAT", desc: "Villieläimet + turvallisuus", guide: "RPi 5 + Coral TPU (~110€):\nfrigate:\n  enabled: true\n  cameras:\n    piha: {description: 'Tonttialue'}\n\nTunnistaa: karhut, hirvet, ketut,\nihmiset, ajoneuvot. Päivällä ja yöllä.\nTelegram-hälytys + kuva <2s." },
    { title: "5. PÖRSSISÄHKÖ", desc: "Saunan + laitteiden ajoitus", guide: "Pörssisähkö — automaattinen:\nelectricity:\n  enabled: true\n\nEtsii halvimmat tunnit:\n• Saunan lämmitys\n• Lämminvesivaraaja\n• Lattialämmitys\n• Mikä tahansa ajoitettu kuorma\n\nSäästää 15-30% sähköstä." },
    { title: "6. UUTISSYÖTTEET", desc: "Säähälytykset + paikallisuutiset", guide: "Kriittiset RSS-syötteet:\nrss:\n  feeds:\n    - url: paikallisuutiset.fi/rss\n      critical: true\n    - url: saahälytykset.fi/feed\n\nAnkarat säävaroitukset\nlaukaisevat VÄLITTÖMÄN ilmoituksen.\nKaikki artikkelit indeksoitu oppimista varten." },
    { title: "MITEN SE OPPII", desc: "Mökkiasiantuntijasi", guide: "Päivä 1: Sensorien perustasot asetettu.\nViikko 1: Oppii kiinteistösi kuviot.\nKuukausi 1: Ennustaa lämmitystarpeet.\nKuukausi 6: Ennakoi kaikki olosuhteet.\n\nYöoppiminen: lukee dataa,\nristiinvalidoi, kasvaa 24/7.\nAI-kiinteistövahtisi ei nuku koskaan." },
    { title: "POIKKEAMAVALVONTA", desc: "Havaitse ongelmat ajoissa", guide: "ESP32-sensorit avainpisteissä:\n\nTunnistaa:\n• Poikkeukselliset lämpötilan laskut (pakkanen)\n• Kosteuspoikkeamat (vuodot)\n• Sähkön vaihtelut\n• Odottamattomat muutokset kuvioissa\n\nHälyttää ennen kuin ongelmat\nmuuttuvat kalliiksi vahingoiksi." },
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
  apiary: [
    { title: "1. ASENNUS", desc: "Käyttöönotto tarhalla", guide: "Laitteisto: NUC tai mini-PC tarhalla.\nToimii offline ensimmäisen asennuksen jälkeen.\n\npip install -r requirements.txt\npython main.py --profile apiary\n\nHallintapaneeli: http://[paikallinen-ip]:8000\nKaikki pesädata pysyy paikallisena." },
    { title: "2. PESÄSENSORIT", desc: "ESP32 per pesä", guide: "Pesäkohtainen sensorisolmu (~20€):\n• ESP32-S3 + punnitusanturi (paino)\n• DS18B20 (lämpötila)\n• INMP441-mikrofoni (pesän ääni)\n• DHT22 (kosteus)\n\nData 60s välein MQTT:llä.\nPoikkeamahälytykset Telegramiin.\nTukee 50+ pesää." },
    { title: "3. AKUSTIIKKA-AI", desc: "Pesien terveys äänestä", guide: "FFT-spektrianalyysi:\n\nTunnistaa:\n• Emon toitotus (parveilu 48-72h)\n• Stressisuraus (häiriö)\n• Emoton valitus (300-500Hz)\n• Normaali terve surina (200-250Hz)\n\n7 päivän perustaso per pesä.\nHälyttää ennen ihmisen havainnointia." },
    { title: "4. TAUTIMALLI", desc: "Varroa + esikotelomätä AI", guide: "Aksioomapohjainen tautimalli:\n\nVarroa-hoito-optimoija:\n• Punkkimäärä / 100 mehiläistä\n• Lämpötilaikkuna\n• Hoitotyypin valinta\n• Oksaalihappoajoitus\n\nEsikotelomätäriski:\n• RSS-hälytykset viranomaisilta\n• Etäisyyspohjainen riskimalli" },
    { title: "5. SATOENNUSTE", desc: "Hunajasadon ennustus", guide: "Painopohjainen satomalli:\n\nSyötteet: pesän paino, kukinta,\nsää, yhdyskunnan vahvuus.\n\nTulokset:\n• Päivittäinen painotrendi\n• Satokierron alku/loppu\n• Ennustettu sato per pesä\n• Korotusten ajoitus\n\nTarkkuus: ±5% yhden kauden jälkeen." },
    { title: "6. KAUSIKALENTERI", desc: "12 kuukauden tehtävät", guide: "AI-ohjattu tehtäväkalenteri:\n\nKevät: pesien tarkistus, ruokinta\nKesä: korotusten hallinta, jaot\nSyksy: varroa-hoito, syysruokinta\nTalvi: valvonta, suunnittelu\n\nJokainen tehtävä sopeutettu\npesiisi ja paikalliseen säähän." },
    { title: "MITEN SE OPPII", desc: "Tarha-asiantuntijasi", guide: "Päivä 1: 3 147+ mehiläisfaktaa.\nViikko 1: Oppii pesäkuviosi.\nKuukausi 1: Ennustaa parveilu + sato.\nKausi 1: Pesäkohtainen optimointi.\n\nYöoppiminen: lukee tutkimuksia,\nristiinvalidoi, kasvaa 24/7.\nAI-mehiläishoitajasi ei nuku koskaan." },
    { title: "PARVEILUENNUSTE", desc: "Estä ennen kuin tapahtuu", guide: "Monisignaalinen parveilumalli:\n\n• Painon tasaantumisen tunnistus\n• Emopöhötön havaitseminen (akust.)\n• Yhdyskunnan ahtaus (lämpö)\n• Vuodenaika + sää\n\nHälyttää 2-5 päivää ennen.\nEhdotetut toimenpiteet per pesä.\nEstää 80%+ parveilusta." },
  ],
};

// ── Heartbeat Messages (live agent feeds) ─────────────────────────────────────

const HEARTBEATS_EN = {
  gadget: [
    { a: "Mesh Hub", m: "12 edge devices connected. 8 online, 2 sleep, 2 charging. MQTT: 12ms avg.", t: "status" },
    { a: "TinyML", m: "ESP32-07 classified anomalous vibration at 94% confidence. Forwarded to main brain.", t: "insight" },
    { a: "Battery", m: "Solar node ESP32-03: 78%. Estimated 18 days to next charge. 22h/day sleep.", t: "status" },
    { a: "OTA", m: "Firmware v2.4.1 → 8 nodes. All confirmed. New sound classifier included.", t: "action" },
    { a: "Fusion", m: "Anomaly: Sensor 23 weight+temp diverged from baseline. Flagged for review.", t: "insight" },
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
    { a: "Property AI", m: "Indoor: 18.2°C, 42% humidity. Water pressure stable. All zones normal.", t: "status" },
    { a: "Frost Guard", m: "Pipe temp 3.1°C. Forecast −8°C overnight. Heating circuit activated.", t: "insight" },
    { a: "Weather", m: "FMI: Thu −6°C at 04:00. Wind chill −12°C. Storm warning issued.", t: "insight" },
    { a: "Energy", m: "Now 2.4c. Tonight 23-02 at 0.8c. Sauna ~1.20€.", t: "action" },
    { a: "Round Table", m: "HEATING 5/5: Shift to cheap hours. APPROVED.", t: "consensus" },
    { a: "Enrichment", m: "Night: 47 weather pattern facts. KB: 47,340.", t: "learning" },
  ],
  factory: [
    { a: "Process", m: "Etch 7: CD 1.1σ. 487 since PM. 12 SPC in control.", t: "status" },
    { a: "Yield AI", m: "WF-2851: 98.9%. CD 22.3nm ±0.4. HIGH.", t: "insight" },
    { a: "Equipment", m: "Pump 12 bearing 3.2×. Failure 68h.", t: "insight" },
    { a: "Round Table", m: "MAINT: Yield→ch.8, PM 02-06. APPROVED.", t: "consensus" },
    { a: "Shift Mgr", m: "B→C 2h. 14 lots, 94.2% util.", t: "action" },
    { a: "Meta", m: "Yield +0.4%. RF↔particles stored.", t: "learning" },
  ],
  apiary: [
    { a: "Hive Monitor", m: "Hive 23: 34.5°C, 248Hz, 33.2kg (+0.4). Queen healthy. Foraging active.", t: "status" },
    { a: "Disease AI", m: "Varroa avg 1.8/100 across 48 hives. Below 3/100 threshold. Hive 31: recheck.", t: "insight" },
    { a: "Weather", m: "FMI: Thu 14°C, wind <3m/s. Optimal oxalic acid window 09-11.", t: "insight" },
    { a: "Yield AI", m: "Honey flow: 12 hives +0.6kg/day avg. Projected harvest: 580kg this cycle.", t: "action" },
    { a: "Round Table", m: "TREATMENT 5/5: Hives 28-35 Thursday. Weather optimal. APPROVED.", t: "consensus" },
    { a: "Night Learn", m: "Night: 23 facts from ScientificBeekeeping.com. Cross-validated. KB: 4,230.", t: "learning" },
  ],
};

const HEARTBEATS_FI = {
  gadget: [
    { a: "Mesh-keskus", m: "12 reunalaitetta yhdistetty. 8 verkossa, 2 unessa, 2 latauksessa. MQTT: 12ms.", t: "status" },
    { a: "TinyML", m: "ESP32-07 tunnisti poikkeavan tärinän 94% varmuudella. Välitetty pääaivoille.", t: "insight" },
    { a: "Akku", m: "Aurinkosolmu ESP32-03: 78%. Arvioitu 18 päivää seuraavaan lataukseen.", t: "status" },
    { a: "OTA", m: "Firmware v2.4.1 → 8 solmuun. Kaikki vahvistettu. Uusi ääniluokittelija mukana.", t: "action" },
    { a: "Fuusio", m: "Poikkeama: Sensori 23 paino+lämpö poikkeaa perustasosta. Merkitty tarkistettavaksi.", t: "insight" },
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
    { a: "Kiinteistö-AI", m: "Sisälämpö: 18.2°C, kosteus 42%. Vedenpaine vakaa. Kaikki vyöhykkeet normaalit.", t: "status" },
    { a: "Pakkasvarti", m: "Putkiston lämpö 3.1°C. Ennuste −8°C yöllä. Lämmityspiiri aktivoitu.", t: "insight" },
    { a: "Meteorologi", m: "IL: torstai −6°C klo 04. Tuulen jäähdytys −12°C. Myrskyvaroitus.", t: "insight" },
    { a: "Sähkö", m: "Nyt 2.4c. Yöllä 23-02 0.8c. Sauna ~1.20€.", t: "action" },
    { a: "Pyöreä Pöytä", m: "LÄMMITYS 5/5: Siirretään halvoille tunneille. HYVÄKSYTTY.", t: "consensus" },
    { a: "Rikastus", m: "Yö: 47 sääkuviofaktaa. Tietokanta: 47 340.", t: "learning" },
  ],
  factory: [
    { a: "Prosessi", m: "Etsaus 7: CD 1.1σ. 487 PM:stä. 12 SPC hallinnassa.", t: "status" },
    { a: "Saanto-AI", m: "WF-2851: 98.9% ennustettu. CD 22.3nm ±0.4. KORKEA.", t: "insight" },
    { a: "Laitteet", m: "Pumppu 12 laakeri 3.2×. Vika 68h. Seuraava huoltoikkuna.", t: "insight" },
    { a: "Pyöreä Pöytä", m: "HUOLTO: Saanto→k.8, PM 02-06. HYVÄKSYTTY.", t: "consensus" },
    { a: "Vuoropääll.", m: "B→C 2h. 14 erää, käyttöaste 94.2%.", t: "action" },
    { a: "Meta", m: "Saanto +0.4%. RF↔partikkelit tallennettu.", t: "learning" },
  ],
  apiary: [
    { a: "Pesävalvonta", m: "Pesä 23: 34.5°C, 248Hz, 33.2kg (+0.4). Emo terve. Aktiivinen saalistus.", t: "status" },
    { a: "Tauti-AI", m: "Varroa keskim. 1.8/100 48 pesässä. Alle kynnyksen 3/100. Pesä 31: seuranta.", t: "insight" },
    { a: "Meteorologi", m: "IL: torstai 14°C, tuuli <3m/s. Optimaalinen oksaalihappoikkuna 09-11.", t: "insight" },
    { a: "Sato-AI", m: "Satokierto: 12 pesää +0.6kg/pv keskim. Ennustettu kokonaissato: 580kg.", t: "action" },
    { a: "Pyöreä Pöytä", m: "HOITO 5/5: Pesät 28-35 torstaina. Sää optimaalinen. HYVÄKSYTTY.", t: "consensus" },
    { a: "Yöoppi", m: "Yö: 23 faktaa ScientificBeekeeping.com:sta. Ristiinvalidoitu. TB: 4 230.", t: "learning" },
  ],
};

// ── Demo Heartbeat Sequences ──────────────────────────────────────────────────

const DEMO_HB_EN = {
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
    { a: "Property AI", m: "12 ESP32 sensors found. Each zone now has a nervous system. Reading first baselines...", t: "status" },
    { a: "Weather AI", m: "FMI satellite data streaming. 48h hyperlocal forecast loaded. Your property's weather station is live.", t: "status" },
    { a: "Frost Guard", m: "Pipe temperature monitoring active. Freeze prediction model loaded. Heating circuits mapped.", t: "learning" },
    { a: "Energy AI", m: "Spot price 2.1c/kWh now. Sauna scheduled for tonight's 0.8c window. Water heater follows at 01:00.", t: "action" },
    { a: "Property AI", m: "Zone 3: temp 18.2°C, humidity 42%, air quality good. All readings within normal range.", t: "insight" },
    { a: "News AI", m: "RSS connected: local weather alerts, property news. Severe weather triggers immediate notification.", t: "learning" },
    { a: "Security AI", m: "Camera online: driveway. Motion detected: moose crossing at 03:12. Logged. No threat.", t: "insight" },
    { a: "Frost Guard", m: "Temperature dropping. Pipe zone 2: 4.1°C. Threshold: 3.0°C. Pre-emptive heating scheduled.", t: "action" },
    { a: "Energy AI", m: "Weekly energy report: 48 kWh consumed, 62% during cheap hours. Savings: €8.40 vs flat rate.", t: "insight" },
    { a: "Round Table", m: "5 agents debated: shift heating to 02:00-05:00 cheap window. Weather + pipes align. Decision: GO.", t: "consensus" },
    { a: "Anomaly AI", m: "Water pressure drop detected: 2.4 bar → 2.1 bar over 6h. Possible leak. Alert sent.", t: "action" },
    { a: "Night Brain", m: "2 AM: learning from weather history — 23 new frost pattern facts. Cross-validated with local data.", t: "learning" },
    { a: "Property AI", m: "COTTAGE INTELLIGENCE ONLINE: 12 sensors as one system. Monitors conditions, saves energy, prevents damage.", t: "status" },
    { a: "Property AI", m: "This is not a demo — autonomous learning is already running. Your property gets smarter every day.", t: "status" },
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
  apiary: [
    { a: "Apiary Brain", m: "Scanning ESP32 mesh... 48 hive sensors found. Weight, temperature, sound, humidity per hive. Building baselines.", t: "status" },
    { a: "Disease AI", m: "3,147 bee disease facts indexed. Varroa models loaded. Foulbrood RSS monitoring active. Your AI vet is ready.", t: "learning" },
    { a: "Acoustic AI", m: "FFT analysis online. 48 microphones listening. Queen piping, stress buzz, queenless howl — all detectable.", t: "status" },
    { a: "Weather AI", m: "FMI satellite data streaming. 48h forecast loaded. Treatment windows calculated automatically.", t: "status" },
    { a: "Yield AI", m: "Weight sensors calibrated. Historical yield data loaded. Per-hive harvest prediction: ±5% accuracy.", t: "learning" },
    { a: "Hive 12", m: "Weight 33.9kg, temp 35.1°C, sound 248Hz, humidity 62%. Queen confirmed healthy. No human opened the hive.", t: "insight" },
    { a: "Disease AI", m: "ALERT: Ruokavirasto RSS — foulbrood outbreak in Häme. Distance: 140km. Risk: LOW. Monitoring 24/7.", t: "action" },
    { a: "Flora AI", m: "Phenology model: dandelion bloom in 3 days. Main honey flow in 18 days. Supers should go on day 15.", t: "insight" },
    { a: "Acoustic AI", m: "Hive 31 queen piping at 450Hz — swarming in 48-72h. Alert sent. Split recommended. No beekeeper would hear this.", t: "insight" },
    { a: "Swarm AI", m: "Multi-signal analysis: weight plateau + temp rise + acoustic shift in Hive 7. Swarm probability: 78%. Action plan sent.", t: "action" },
    { a: "Round Table", m: "6 agents debated: varroa treatment Hives 28-35, Thursday 09:00. Weather + mite counts align. Decision: GO.", t: "consensus" },
    { a: "Yield AI", m: "Honey flow confirmed: 12 hives averaging +0.6kg/day. Projected total harvest: 580kg. Best season in 3 years.", t: "learning" },
    { a: "Night Brain", m: "2 AM: learning from 14 research papers on integrated pest management. 47 new facts cross-validated.", t: "learning" },
    { a: "Apiary Brain", m: "APIARY INTELLIGENCE ONLINE: 48 hives as one organism. Reads research, tracks weather, listens to every colony.", t: "status" },
    { a: "Apiary Brain", m: "This is not a demo — autonomous learning is already running. 4,230 facts and counting. Your bees have an AI.", t: "status" },
  ],
};

const DEMO_HB_FI = {
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
    { a: "Mökki-AI", m: "12 ESP32-sensoria löydetty. Jokaisella vyöhykkeellä on nyt hermojärjestelmä. Luetaan perustasot...", t: "status" },
    { a: "Sää-AI", m: "IL:n satelliittidata virtaa. 48h paikallisennuste ladattu. Mökkisi sääasema on online.", t: "status" },
    { a: "Pakkasvahti", m: "Putkien lämpötilan seuranta aktiivinen. Jäätymisennustemalli ladattu. Lämmityspiirit kartoitettu.", t: "learning" },
    { a: "Energia-AI", m: "Spot-hinta 2.1c/kWh nyt. Sauna ajoitettu illan 0.8c ikkunaan. Lämminvesivaraaja klo 01:00.", t: "action" },
    { a: "Mökki-AI", m: "Vyöhyke 3: lämpö 18.2°C, kosteus 42%, ilmanlaatu hyvä. Kaikki lukemat normaalirajoissa.", t: "insight" },
    { a: "Uutis-AI", m: "RSS yhdistetty: paikalliset säävaroitukset, kiinteistöuutiset. Ankara sää laukaisee välittömän ilmoituksen.", t: "learning" },
    { a: "Turva-AI", m: "Kamera online: pihatilanne. Liike havaittu: hirvi ylitti klo 03:12. Kirjattu. Ei uhkaa.", t: "insight" },
    { a: "Pakkasvahti", m: "Lämpötila laskee. Putkivyöhyke 2: 4.1°C. Kynnys: 3.0°C. Ennaltaehkäisevä lämmitys ajoitettu.", t: "action" },
    { a: "Energia-AI", m: "Viikon energiaraportti: 48 kWh kulutettu, 62% halvoilla tunneilla. Säästö: 8.40€ vs tasahinta.", t: "insight" },
    { a: "Pyöreä Pöytä", m: "5 agenttia väitteli: lämmitys 02:00-05:00 halvimpaan ikkunaan. Sää + putket linjassa. Päätös: TOTEUTETAAN.", t: "consensus" },
    { a: "Poikkeama-AI", m: "Vedenpaineen lasku havaittu: 2.4 bar → 2.1 bar 6 tunnissa. Mahdollinen vuoto. Hälytys lähetetty.", t: "action" },
    { a: "Yö-aivot", m: "Klo 02: opitaan säähistoriasta — 23 uutta pakkaskuviofaktaa. Ristiinvalidoitu paikallisella datalla.", t: "learning" },
    { a: "Mökki-AI", m: "MÖKKIÄLYKKYYS ONLINE: 12 sensoria yhtenä järjestelmänä. Valvoo olosuhteita, säästää energiaa, estää vahinkoja.", t: "status" },
    { a: "Mökki-AI", m: "Tämä ei ole demo — autonominen oppiminen on jo käynnissä. Mökkisi viisastuu joka päivä.", t: "status" },
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
  apiary: [
    { a: "Tarha-aivot", m: "Skannataan ESP32-mesh... 48 pesäsensoria löydetty. Paino, lämpö, ääni, kosteus per pesä. Rakennetaan perustasot.", t: "status" },
    { a: "Tauti-AI", m: "3 147 mehiläistautifaktaa indeksoitu. Varroa-mallit ladattu. Esikotelomätä-RSS aktiivinen. AI-eläinlääkärisi on valmis.", t: "learning" },
    { a: "Akustiikka-AI", m: "FFT-analyysi online. 48 mikrofonia kuuntelee. Emon toitotus, stressisuraus, emoton valitus — kaikki tunnistettavissa.", t: "status" },
    { a: "Sää-AI", m: "IL:n satelliittidata virtaa. 48h ennuste ladattu. Hoitoikkunat lasketaan automaattisesti.", t: "status" },
    { a: "Sato-AI", m: "Painosensorit kalibroitu. Historiallinen satodata ladattu. Pesäkohtainen satoennuste: ±5% tarkkuus.", t: "learning" },
    { a: "Pesä 12", m: "Paino 33.9kg, lämpö 35.1°C, ääni 248Hz, kosteus 62%. Emo terve. Kukaan ei avannut pesää.", t: "insight" },
    { a: "Tauti-AI", m: "HÄLYTYS: Ruokavirasto RSS — esikotelomätäpurkaus Hämeessä. Etäisyys: 140km. Riski: MATALA. Seurataan 24/7.", t: "action" },
    { a: "Kasvi-AI", m: "Fenologiamalli: voikukka kukkii 3 päivän päästä. Pääsatokierto 18 päivässä. Korotukset päivänä 15.", t: "insight" },
    { a: "Akustiikka-AI", m: "Pesä 31 emon toitotus 450Hz — parveilua 48-72h. Hälytys lähetetty. Jako suositeltu. Kukaan ei kuulisi tätä.", t: "insight" },
    { a: "Parveilu-AI", m: "Monisignaali: painon tasaantuminen + lämmön nousu + akustinen muutos pesässä 7. Parveilutodennäköisyys: 78%.", t: "action" },
    { a: "Pyöreä Pöytä", m: "6 agenttia väitteli: varroa-hoito pesät 28-35, torstai 09:00. Sää + punkkimäärät linjassa. Päätös: TOTEUTETAAN.", t: "consensus" },
    { a: "Sato-AI", m: "Satokierto vahvistettu: 12 pesää keskim. +0.6kg/pv. Ennustettu kokonaissato: 580kg. Paras kausi 3 vuoteen.", t: "learning" },
    { a: "Yö-aivot", m: "Klo 02: opitaan 14 tutkimusjulkaisusta integroidusta tuholaistorjunnasta. 47 uutta faktaa ristiinvalidoitu.", t: "learning" },
    { a: "Tarha-aivot", m: "TARHA-ÄLYKKYYS ONLINE: 48 pesää yhtenä organismina. Lukee tutkimuksia, seuraa säätä, kuuntelee jokaista yhdyskuntaa.", t: "status" },
    { a: "Tarha-aivot", m: "Tämä ei ole demo — autonominen oppiminen on jo käynnissä. 4 230 faktaa ja kasvaa. Mehiläisilläsi on AI.", t: "status" },
  ],
}

// ── Prediction Models per Profile ─────────────────────────────────────────────

const PREDICTION_MODELS = {
  gadget:  "battery_discharge",
  cottage: "pipe_freezing",
  home:    "heat_pump_cop",
  factory: "oee_decomposition",
  apiary:  "hive_weight_trend",
};

// ── Quick Actions per Profile ─────────────────────────────────────────────────

const QUICK_ACTIONS = {
  en: {
    gadget:  ["How long will battery last at this usage?", "Is there signal or sensor drift?", "When should this device be serviced?", "How does usage mode affect battery life?"],
    cottage: ["How much has electricity cost so far?", "Is there frost risk tonight?", "What happened at the property in the last 24h?", "Should heating be shifted to cheaper hours?"],
    home:    ["What's the cheapest time to heat today?", "Is energy usage normal this week?", "Is anything unusual in the house?", "What did the system learn this week?"],
    factory: ["Why did OEE drop today?", "Which signals show drift?", "What should the next shift know?", "What's the biggest recurring issue this week?"],
    apiary:  ["How are the hives doing today?", "Is there swarm risk this week?", "When is the optimal treatment window?", "What's the projected honey yield?"],
  },
  fi: {
    gadget:  ["Kuinka kauan akku kestää tällä käytöllä?", "Onko signaalissa tai sensorissa driftia?", "Milloin laite kannattaa huoltaa?", "Miten käyttötila vaikuttaa akun kestoon?"],
    cottage: ["Paljonko sähkö on maksanut tähän mennessä?", "Onko jäätymisriskiä ensi yönä?", "Mitä mökillä tapahtui viimeisen 24h aikana?", "Kannattaako lämmitys siirtää halvoille tunneille?"],
    home:    ["Milloin lämmitys on halvinta tänään?", "Onko energiankulutus normaalia tällä viikolla?", "Onko talossa jotain poikkeavaa?", "Mitä järjestelmä oppi tällä viikolla?"],
    factory: ["Miksi OEE laski tänään?", "Mitkä signaalit näyttävät driftia?", "Mitä seuraavan vuoron pitää tietää?", "Mikä on viikon suurin toistuva häiriö?"],
    apiary:  ["Miten pesät voivat tänään?", "Onko parveiluriskiä tällä viikolla?", "Milloin on optimaalinen hoitoikkuna?", "Mikä on ennustettu hunajasato?"],
  },
};

// ── Purpose Descriptions per Profile ──────────────────────────────────────────

const PURPOSE = {
  en: {
    gadget:  "Monitors battery, signal, sensors and device state. Detects drift, predicts issues.",
    cottage: "Monitors conditions, costs, frost risk and anomalies. Generates summaries and forecasts without cloud.",
    home:    "Optimizes energy, comfort and safety. Learns your home's rhythm, detects anomalies, shows impact in euros.",
    factory: "Explains production anomalies, monitors OEE/SPC signals, helps prioritize actions.",
    apiary:  "Monitors hive health, predicts swarms and honey flow. Optimizes treatments and seasonal tasks.",
  },
  fi: {
    gadget:  "Valvoo akkua, signaalia, sensoreita ja laitteen tilaa. Havaitsee driftin ja ennustaa ongelmia.",
    cottage: "Valvoo olosuhteita, kustannuksia, jäätymisriskejä ja poikkeamia. Tekee yhteenvedot ja ennusteet ilman pilveä.",
    home:    "Optimoi energian, mukavuuden ja turvallisuuden. Oppii kodin rytmin, tunnistaa poikkeamat ja näyttää vaikutukset euroina.",
    factory: "Selittää tuotannon poikkeamat, seuraa OEE/SPC-signaaleja ja auttaa priorisoimaan oikeat toimenpiteet.",
    apiary:  "Valvoo pesien terveyttä, ennustaa parveilun ja satokierrot. Optimoi hoidot ja kausiluonteiset tehtävät.",
  },
};

// ── Exports ───────────────────────────────────────────────────────────────────

export const DOMAIN_IDS = ["gadget", "cottage", "home", "factory", "apiary"];

export {
  HW_SPECS,
  DOMAIN_LABELS,
  PROFILE_ICONS,
  PROFILE_COLORS,
  PROFILE_COLORS_RGB,
  FEATS_EN,
  FEATS_FI,
  HEARTBEATS_EN,
  HEARTBEATS_FI,
  DEMO_HB_EN,
  DEMO_HB_FI,
  PREDICTION_MODELS,
  QUICK_ACTIONS,
  PURPOSE,
};
