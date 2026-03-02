# WaggleDance Vuosikustannukset - Korjattu Laskelma

## VIRHE AIEMMASSA LASKELMASSA

Sanoin: "$175/vuosi" - **TÄMÄ OLI LIIAN ALHAINEN**

Oikea laskelma:

---

## SÄHKÖNKULUTUS

### Laitteiston teho:

```
RTX A2000 GPU:        70W  (TDP, idle ~15W, load ~70W)
CPU (24-core):        65W  (idle ~30W, load ~100W)
RAM (128GB):          20W
SSD, emo, tuulettimet: 15W
─────────────────────────────────────────────
IDLE:   ~80W
TYYPILLINEN: ~120W (kevyt AI-työ)
MAKSIMI: ~200W (täysi kuorma)
```

**Keskimääräinen kulutus 24/7 käytössä:**
- Heartbeat-taskit: kevyt kuorma (llama3.2:1b)
- Chat-hetket: täysi kuorma (phi4-mini)
- Yöaika: kevyt oppiminen
- **ARVIO: 120W keskimäärin**

---

## SÄHKÖLASKENTA

### Vuosikulutus:
```
120W × 24h × 365 päivää = 1,051 kWh/vuosi
```

### Hinta eri sähkösopimuksilla:

| Sähkön hinta | €/kWh | Vuosikustannus |
|--------------|-------|----------------|
| Pörssisähkö (halpa) | €0.10 | **€105/vuosi** |
| Pörssisähkö (keski) | €0.15 | **€158/vuosi** |
| Kiinteä (normaali) | €0.20 | **€210/vuosi** |
| Kiinteä (kallis) | €0.25 | **€263/vuosi** |

**TYYPILLINEN SUOMESSA: €150-250/vuosi**

---

## MUUT KUSTANNUKSET?

### Ylläpito:
```
✅ Ei ohjelmistolisensssejä (kaikki avointa/ilmaista)
✅ Ei pilvipalvelumaksuja
✅ Ei API-kutsumaksuja
✅ Ei konsulttimaksuja (autonomous)
✅ Ei päivitysmaksuja
─────────────────────────────────────────────
YLLÄPITO: €0/vuosi
```

### Laitteiston poistot:
```
RTX A2000: €1,500 ÷ 5 vuotta = €300/vuosi
SSD 2TB:   €100 ÷ 5 vuotta = €20/vuosi
─────────────────────────────────────────────
POISTOT: €320/vuosi (kirjanpidollinen, ei käteiskulu)
```

---

## KORJATTU VERTAILU

### Cloud AI (vuosi):
```
ChatGPT Enterprise:        €7,200/v  (10 käyttäjää × €60/kk)
tai
Azure OpenAI + Pinecone:   €5,136/v  (API + vektoritietokanta)
```

### WaggleDance (vuosi):

| Kustannuserä | Vuosi 1 | Vuosi 2+ |
|--------------|---------|----------|
| **Laitteisto** | €1,600 | €0 |
| **Sähkö** | €200 | €200 |
| **Ylläpito** | €0 | €0 |
| **YHTEENSÄ (käteinen)** | **€1,800** | **€200** |
| **+ Poistot (kirjanpito)** | €320 | €320 |
| **YHTEENSÄ (kirjanpito)** | **€2,120** | **€520** |

---

## SÄÄSTÖLASKELMA (KORJATTU)

### Verrattuna ChatGPT Enterprise (€7,200/v):

| Vuosi | Cloud AI | WaggleDance | Säästö |
|-------|----------|-------------|--------|
| 1 | €7,200 | €1,800 | **€5,400** |
| 2 | €7,200 | €200 | **€7,000** |
| 3 | €7,200 | €200 | **€7,000** |
| 4 | €7,200 | €200 | **€7,000** |
| 5 | €7,200 | €200 | **€7,000** |
| **YHTEENSÄ 5v** | **€36,000** | **€2,400** | **€33,600** |

**SÄÄSTÖ: 93% (vs. ChatGPT Enterprise)**

---

### Verrattuna Azure AI Stack (€5,136/v):

| Vuosi | Cloud AI | WaggleDance | Säästö |
|-------|----------|-------------|--------|
| 1 | €5,136 | €1,800 | **€3,336** |
| 2 | €5,136 | €200 | **€4,936** |
| 3 | €5,136 | €200 | **€4,936** |
| 4 | €5,136 | €200 | **€4,936** |
| 5 | €5,136 | €200 | **€4,936** |
| **YHTEENSÄ 5v** | **€25,680** | **€2,400** | **€23,280** |

**SÄÄSTÖ: 91% (vs. Azure AI)**

---

## ROI (TAKAISINMAKSUAIKA)

### Verrattuna ChatGPT Enterprise:
```
€1,800 investointi ÷ (€7,200 - €200) säästö/v = 0.26 vuotta
```
**ROI: 3 kuukautta** ✅

### Verrattuna Azure AI:
```
€1,800 investointi ÷ (€5,136 - €200) säästö/v = 0.36 vuotta
```
**ROI: 4.3 kuukautta** ✅

---

## MIKSI NÄIN HALPA?

### 1. Ei pilvipalveluja
```
Cloud AI:
  - API-kutsut: €0.03-0.06 per 1000 tokenia
  - Embeddings: €0.13 per miljona tokenia  
  - Vektoritietokanta: €250/kk (Pinecone)
  - API Gateway: €20/kk

WaggleDance:
  - Kaikki paikallista → €0
```

### 2. Ei käyttöpohjaista laskutusta
```
Cloud AI:
  - Jokainen kysely maksaa
  - Jokainen embedding maksaa
  - Jokainen haku maksaa
  - Maksut skaalautuvat käytön mukaan

WaggleDance:
  - Vakio sähkölasku riippumatta käytöstä
  - 1 kysely tai 10,000 kyselyä = sama hinta
```

### 3. Ei lisenssimaksuja
```
Cloud AI:
  - €60/kk per käyttäjä (Enterprise)
  - Minimitilaus usein 5-10 käyttäjää

WaggleDance:
  - Rajaton määrä käyttäjiä
  - Ei lisenssejä
```

### 4. Tehokas laitteisto
```
RTX A2000:
  - 70W TDP (vs. datacenter GPU 300-500W)
  - Riittää 23 agentille + chat + oppiminen
  - Ei yli-investointia
```

---

## SÄHKÖN OPTIMOINTI

### Jos haluat vähentää kustannuksia:

**1. Pörssisähkö + Night Mode**
```
Päivä (kallis):    Vain chat (idle ~80W)
Yö (halpa):        Täysi oppiminen (load ~150W)
───────────────────────────────────────────
SÄÄSTÖ: ~30% sähkölaskusta
€200/v → €140/v
```

**2. Kesäkuukaudet**
```
Ei lämmitystä tarvita (GPU lämmittää)
Voisi säästää lämmityskustannuksissa talvella
BONUS: "Ilmainen" lämpö
```

**3. Sleep Mode**
```
Jos ei käytetä viikonloppuisin:
Sammuta 48h/viikko
───────────────────────────────────────────
SÄÄSTÖ: 29% ajasta
€200/v → €142/v
```

---

## YHTEENVETO (KORJATTU)

### Vuosikustannus:
```
VUOSI 1:  €1,800 (€1,600 laitteisto + €200 sähkö)
VUOSI 2+: €200 (vain sähkö)
```

### Säästö vs. Cloud AI:
```
ChatGPT Enterprise: €5,400/v → €7,000/v  (93% säästö)
Azure AI Stack:     €3,336/v → €4,936/v  (91% säästö)
```

### ROI:
```
ChatGPT Enterprise: 3 kuukautta
Azure AI Stack:     4.3 kuukautta
```

---

## PAHOITTELU

**Aiempi virhe:** Sanoin "$175/vuosi" käyttämällä liian alhaista tehoa (100W) ja dollareita.

**Oikea luku:** 
- **€200/vuosi sähkö** (120W keskimäärin, €0.20/kWh)
- **€1,800 vuosi 1** (sisältää laitteiston)
- **€200 vuosi 2+** (vain sähkö)

**Säästö silti valtava: 91-93% verrattuna Cloud AI:hin**

