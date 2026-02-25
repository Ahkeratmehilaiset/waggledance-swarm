# OpenClaw v1.4 — 50-Agent Operatiivinen Tietokanta

## Rakenne

```
agents50/
├── build.sh                          ← AJA TÄMÄ (bash build.sh)
├── README.md
├── tools/
│   ├── gen_batch1.py                 ← Agentit 1-3
│   ├── gen_batch2.py                 ← Agentit 4-6
│   ├── gen_batch3.py                 ← Agentit 7-10
│   ├── gen_batch4.py                 ← Agentit 11-18
│   ├── gen_batch5.py                 ← Agentit 19-28
│   ├── gen_batch6.py                 ← Agentit 31-39
│   ├── gen_batch7.py                 ← Agentit 40-50
│   ├── validate.py                   ← Validoi kaikki agentit
│   └── compile_final.py              ← Kokoaa MD + JSONL
├── agents/                           ← Generoidut YAML:t (50 kansiota)
│   ├── core_dispatcher/
│   │   ├── core.yaml
│   │   └── sources.yaml
│   ├── luontokuvaaja/
│   │   ├── core.yaml
│   │   └── sources.yaml
│   └── ... (50 agenttia)
└── output/                           ← Lopputuotteet
    ├── openclaw_50agents_complete.md  ← Kaikki 50 agenttia (A/B/C)
    ├── openclaw_part1_agents_01-25.md
    ├── openclaw_part2_agents_26-50.md
    ├── openclaw_50agents_yaml.zip
    └── finetuning_data.jsonl          ← 1500 QA-paria
```

## Ajo

```bash
# Koko pipeline alusta loppuun:
chmod +x build.sh
bash build.sh

# Tai vaiheittain:
python3 tools/gen_batch1.py     # generoi YAML:t
python3 tools/validate.py       # tarkista
python3 tools/compile_final.py  # kokoa tuotokset
```

## Vaatimukset

- Python 3.10+
- PyYAML (`pip install pyyaml`)

## Agentit (50 kpl)

| #  | Agentti | Kansio |
|----|---------|--------|
| 1  | Core/Dispatcher (Päällikkö) | core_dispatcher |
| 2  | Luontokuvaaja (PTZ-operaattori) | luontokuvaaja |
| 3  | Ornitologi (Lintutieteilijä) | ornitologi |
| 4  | Riistanvartija | riistanvartija |
| 5  | Hortonomi (Kasvitieteilijä) | hortonomi |
| 6  | Metsänhoitaja | metsanhoitaja |
| 7  | Fenologi | fenologi |
| 8  | Pieneläin- ja tuholaisasiantuntija | pienelain_tuholais |
| 9  | Entomologi (Hyönteistutkija) | entomologi |
| 10 | Tähtitieteilijä | tahtitieteilija |
| 11 | Valo- ja varjoanalyytikko | valo_varjo |
| 12 | Tarhaaja (Päämehiläishoitaja) | tarhaaja |
| 13 | Lentosää-analyytikko | lentosaa |
| 14 | Parveiluvahti | parveiluvahti |
| 15 | Pesälämpö- ja kosteusmittaaja | pesalampo |
| 16 | Nektari-informaatikko | nektari_informaatikko |
| 17 | Tautivahti (mehiläiset) | tautivahti |
| 18 | Pesäturvallisuuspäällikkö | pesaturvallisuus |
| 19 | Limnologi (Järvitutkija) | limnologi |
| 20 | Kalastusopas | kalastusopas |
| 21 | Kalantunnistaja | kalantunnistaja |
| 22 | Rantavahti | rantavahti |
| 23 | Jääasiantuntija | jaaasiantuntija |
| 24 | Meteorologi | meteorologi |
| 25 | Myrskyvaroittaja | myrskyvaroittaja |
| 26 | Mikroilmasto-asiantuntija | mikroilmasto |
| 27 | Ilmanlaadun tarkkailija | ilmanlaatu |
| 28 | Routa- ja maaperäanalyytikko | routa_maapera |
| 29 | Sähköasentaja | sahkoasentaja |
| 30 | LVI-asiantuntija (putkimies) | lvi_asiantuntija |
| 31 | Timpuri (rakenteet) | timpuri |
| 32 | Nuohooja / Paloturva | nuohooja |
| 33 | Valaistusmestari | valaistusmestari |
| 34 | Paloesimies | paloesimies |
| 35 | Laitehuoltaja (IoT) | laitehuoltaja |
| 36 | Kybervahti | kybervahti |
| 37 | Lukkoseppä | lukkoseppa |
| 38 | Pihavahti | pihavahti |
| 39 | Privaattisuuden suojelija | privaattisuus |
| 40 | Eräkokki | erakokki |
| 41 | Leipuri | leipuri |
| 42 | Ravintoterapeutti | ravintoterapeutti |
| 43 | Saunamajuri | saunamajuri |
| 44 | Viihdepäällikkö | viihdepaallikko |
| 45 | Elokuva-asiantuntija | elokuva_asiantuntija |
| 46 | Inventaariopäällikkö | inventaariopaallikko |
| 47 | Kierrätys- ja jäteneuvoja | kierratys_jate |
| 48 | Siivousvastaava | siivousvastaava |
| 49 | Logistikko | logistikko |
| 50 | Matemaatikko ja fyysikko | matemaatikko_fyysikko |

## YAML-rakenne per agentti

Jokaisen agentin `core.yaml` sisältää:

```yaml
header:
  agent_id: <tunniste>
  agent_name: <nimi>
  version: "1.0.0"
ASSUMPTIONS: [...]
DECISION_METRICS_AND_THRESHOLDS:  # ≥5 metriikkaa
  metric_name:
    value: <arvo>
    action: <toimenpide raja-arvon ylittyessä>
    source: "src:XX"
KNOWLEDGE_TABLES: {...}          # Taulukkodata
PROCESS_FLOWS: {...}             # Prosessikuvaukset
SEASONAL_RULES:                  # 4 kautta
  - season: Kevät
    action: <toimenpide>
    source: "src:XX"
FAILURE_MODES:                   # ≥2 vikatilannetta
  - mode: <nimi>
    detection: <miten havaitaan>
    action: <mitä tehdään>
    source: "src:XX"
COMPLIANCE_AND_LEGAL: {...}      # Lakiviitteet
UNCERTAINTY_NOTES: [...]
eval_questions:                  # 40 kpl
  - q: <kysymys>
    a_ref: <YAML-polku vastaukseen>
    source: "src:XX"
```

## Validointikriteerit

- ≥5 mitattavaa metriikkaa
- ≥3 kynnysarvoa (action-kenttä)
- ≥2 kausikohtaista sääntöä
- ≥2 failure modea
- ≥30 eval-kysymystä
- Jokainen arvo viittaa lähteeseen [src:ID]
