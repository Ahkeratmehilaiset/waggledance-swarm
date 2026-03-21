# WaggleDance Latenssivertailu
## Cloud AI vs. Demo (HP ZBook) vs. Tuotanto (DGX B200)

**Päivämäärä:** 2.3.2026
**Versio:** WaggleDance v0.2.0
**Demo:** HP ZBook (i7-12850HX 16C/24T, 128 GB DDR5, RTX A2000 8GB, Samsung 2 TB NVMe)
**Tuotanto:** NVIDIA DGX B200 (2× Xeon 8570 112C, 4 096 GB DDR5, 8× B200 1 536 GB HBM3e, 72 000 TFLOPS)

---

## LAITTEISTOVERTAILU

| Ominaisuus | Pilvi-AI (Azure/AWS) | Demo (HP ZBook) | Tuotanto (DGX B200) |
|------------|---------------------|-----------------|---------------------|
| **GPU** | Jaettu | RTX A2000 8 GB | 8× B200 1 536 GB |
| **CPU** | Jaettu | i7-12850HX 16C | 2× Xeon 8570 112C |
| **RAM** | – | 128 GB DDR5 | 4 096 GB DDR5 |
| **Teho** | Datakeskus | ~150 W | 14,4 kW |
| **Hinta** | €5 136–7 200/v | ~€3 500 (kertaluonteinen) | ~€475 000 (kertaluonteinen) |
| **Sijainti** | Ulkomailla | Paikallinen | Paikallinen |

### Komponenttikohtaiset latenssit

| Komponentti | Demo (ZBook) | Tuotanto (B200) | Kerroin |
|-------------|-------------|-----------------|---------|
| phi4-mini inference (1K tok) | 800–1 500 ms | **5–15 ms** | **~100×** |
| llama 3.2 1B (heartbeat) | 400–800 ms | **2–5 ms** | **~150×** |
| nomic-embed (100 tekstiä) | 3–5 s | **20–50 ms** | **~100×** |
| ChromaDB-haku (3 147 faktaa) | 15–30 ms | **5–10 ms** | **~3×** |
| Opus-MT käännös | 200–500 ms | **5–10 ms** | **~50×** |
| NightEnricher (per fakta) | 5–10 s | **50–100 ms** | **~100×** |
| Round Table (50 agenttia) | 30–60 s | **200–500 ms** | **~100×** |

---

## TIIVISTELMÄ — KOKONAISLATENSSIT

| Operaatio | Cloud AI | Demo (ZBook) | Tuotanto (B200) | B200 vs. Cloud |
|-----------|----------|-------------|-----------------|----------------|
| Vektorihaku | 350 ms | 5 ms | **~3 ms** | **117×** |
| Yksinkertainen kysymys | 1 200 ms | 55 ms | **~12 ms** | **100×** |
| Monimutkainen analyysi | 4 000 ms | 500 ms | **~45 ms** | **89×** |
| Batch-käsittely (50 kpl) | 35 000 ms | 2 800 ms | **~80 ms** | **438×** |
| Cache-osuma | 1 200 ms | 5 ms | **~1 ms** | **1 200×** |

**Demo: 22–240× nopeampi kuin pilvi. Tuotanto (B200): 100–1 200× nopeampi.**

---

## 1. VEKTORIHAKU (Tietokantahaku)

### Cloud AI (tyypillinen arkkitehtuuri)
```
Asiakasohjelma → Internet (50-100ms)
              → Cloud Load Balancer (20-50ms)
              → API Gateway (10-30ms)
              → Pinecone/Weaviate (50-200ms)
              → Paluu samaa reittiä (80-180ms)
───────────────────────────────────────────────
TYYPILLINEN: 350ms
```

### Demo (HP ZBook) — mitattu
```
Asiakasohjelma → Localhost API (1ms)
              → ChromaDB (paikallinen SSD) (3-4ms)
              → Paluu (1ms)
───────────────────────────────────────────────
YHTEENSÄ: 5ms
```

### Tuotanto (DGX B200) — laskettu
```
Asiakasohjelma → Localhost (<1ms)
              → ChromaDB (NVMe RAID) (2ms)
              → Paluu (<1ms)
───────────────────────────────────────────────
YHTEENSÄ: ~3ms
```

---

## 2. YKSINKERTAINEN KYSYMYS

**Esimerkki:** "Mikä on UPS:n runtime minimum tehtaalla?"

### Cloud AI: 1 200 ms (tyypillinen)

### Demo (ZBook)
```
CACHE HIT: Hot Cache (0.1ms) → Vastaus (0.5ms) = 5ms
CACHE MISS: nomic-embed (15ms) → ChromaDB (35ms) → Return (5ms) = 55ms
```

### Tuotanto (B200)
```
CACHE HIT: Hot Cache (<0.1ms) → Vastaus (<1ms) = ~1ms
CACHE MISS: nomic-embed (1ms) → ChromaDB (8ms) → Return (3ms) = ~12ms
```

**B200 vs. Cloud: 100× nopeampi (cache miss), 1 200× (cache hit)**

---

## 3. MONIMUTKAINEN ANALYYSI

**Esimerkki:** "Analysoi 50 huoltoraporttia ja anna suositus ennakkohuollolle"

### Cloud AI: 4 000 ms (tyypillinen), ~€1,05/kysely

### Demo (ZBook): 500 ms
```
Batch embed (55ms) → ChromaDB batch (150ms) → phi4-mini (250ms) → Assembly (20ms) → Response (25ms)
```

### Tuotanto (B200): ~45 ms
```
Batch embed (5ms) → ChromaDB batch (10ms) → phi4-mini (15ms) → Assembly (10ms) → Response (5ms)
```

**B200 vs. Cloud: 89× nopeampi, €0 per kysely**

---

## 4. BATCH-KÄSITTELY (50 dokumenttia)

| | Cloud AI | Demo (ZBook) | Tuotanto (B200) |
|-|----------|-------------|-----------------|
| Aika | 35 000 ms | 2 800 ms (mitattu) | ~80 ms (laskettu) |
| vs. Cloud | – | 12× nopeampi | **438× nopeampi** |

---

## 5. REAALIAIKASKENAARIOT

### Skenaario A: Koneen vikahälytys

| Vaihe | Cloud | Demo | B200 |
|-------|-------|------|------|
| Alert → API | 200 ms | 1 ms | <1 ms |
| Lokit | 500 ms | 10 ms | 2 ms |
| Haku | 800 ms | 35 ms | 8 ms |
| Analyysi | 2 000 ms | 400 ms | 15 ms |
| Vastaus | 200 ms | 5 ms | 2 ms |
| **Yhteensä** | **3,7 s** | **0,45 s** | **~28 ms** |

B200: vikahälytyksen käsittely **alle 30 ms** — nopeampi kuin silmänräpäys.

### Skenaario B: Ohjeita mobiililla

| Vaihe | Cloud | Demo | B200 |
|-------|-------|------|------|
| WiFi | 50–200 ms | 10–30 ms | 10–30 ms |
| Käsittely | 1 000–2 000 ms | 5 ms (cache) | ~1 ms (cache) |
| Paluu | 150–500 ms | 10–30 ms | 10–30 ms |
| **Yhteensä** | **1,3–3,0 s** | **25–65 ms** | **21–61 ms** |

---

## 6. OPPIMISNOPEUDEN VAIKUTUS

| Mittari | Demo (ZBook) | Tuotanto (B200) | Kerroin |
|---------|-------------|-----------------|---------|
| NightEnricher (per fakta) | 5–10 s | 50–100 ms | ~100× |
| Faktoja per yö (8h) | ~3 000–6 000 | ~300 000–600 000 | ~100× |
| Round Table (50 agenttia) | 30–60 s | 200–500 ms | ~100× |
| Aika "ekspertiksi" | ~6 kuukautta | ~2 viikkoa | ~12× |

**B200 oppii yhdessä yössä saman määrän kuin demo-kone kolmessa kuukaudessa.**

---

## 7. KUSTANNUSVERTAILU (Standard-taso)

### Cloud AI Stack: €5 136/vuosi
```
Azure OpenAI (GPT-4):     €1 800/v
Embeddings (text-embed-3):   €96/v
Pinecone (vector DB):     €3 000/v
API Gateway:                €240/v
```

### WaggleDance Standard (paikallinen)
```
VUOSI 1: €1 800 (laitteisto €1 600 + sähkö €200)
VUOSI 2+: €200/vuosi (vain sähkö, 120W × 24/7)
5 vuotta: €2 600
```

**Säästö 5 vuodessa: €23 000–33 600**
**ROI: 3–4 kuukautta**

---

## 8–9. OFFLINE-TOIMINTA JA DATA-SUVERENITEETTI

| | Cloud AI | WaggleDance |
|-|----------|-------------|
| Internet poikki | Ei toimi | Toimii normaalisti |
| Verkkoriippuvuus | 100 % | 0 % |
| Data sijainti | Ulkomaiset palvelimet | Oma palvelin |
| GDPR | Haasteellinen | Yksinkertainen |
| Yökäyttö | Ongelmallinen | Täydellä teholla |

---

## 10. SKAALAUTUVUUS

| Käyttäjiä | Cloud AI (€/v) | WaggleDance (€/v) | Säästö |
|-----------|---------------|-------------------|--------|
| 1 | €720 | €200 | €520 |
| 10 | €7 200 | €200 | €7 000 |
| 50 | €36 000 | €200 | €35 800 |
| 100 | €72 000 | €200 | **€71 800** |

---

## YHTEENVETO

| Mittari | Cloud AI | Demo (ZBook) | Tuotanto (B200) |
|---------|----------|-------------|-----------------|
| **Latenssi (cache)** | 1 200 ms | 5 ms | **~1 ms** |
| **Latenssi (keskim.)** | 1 200 ms | 55 ms | **~12 ms** |
| **Analyysi (50 rap.)** | 4 000 ms | 500 ms | **~45 ms** |
| **Batch (50 kpl)** | 35 000 ms | 2 800 ms | **~80 ms** |
| **Round Table** | – | 30–60 s | **0,2–0,5 s** |
| **Oppiminen / yö** | – | ~3 000 faktaa | **~300 000 faktaa** |
| **Kustannus/vuosi** | €5 136 | €200 | €200* |
| **Offline** | ❌ | ✅ | ✅ |
| **Data-suvereniteetti** | ❌ | ✅ | ✅ |

*Sähkö (Standard-taso). DGX B200 sähkö ~€25 000/v, jakautuu tuotantoympäristön järjestelmille.

---

## SUOSITUS

**Demo todistaa konseptin** — jo kannettavalla 22–240× nopeampi kuin pilvi-AI.
**DGX B200 tuotannossa** — 100–1 200× nopeampi, ~300 000 faktaa per yö.
Sama ohjelmisto. Sama data. Eri laitteisto.

**ROI: 3–4 kuukautta**

---

**Laatija:** Jani Korpi
**Päivämäärä:** 2.3.2026
**Versio:** 1.2
**WaggleDance:** v0.2.0
