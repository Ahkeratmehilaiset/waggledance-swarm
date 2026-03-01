#!/usr/bin/env python3
"""
WaggleDance — Ollama Full Benchmark v2.2 (bilingual, crashproof)
=================================================================
  • 14 mallia: monikieliset, EN-optimoidut, suomi-erikoiset
  • 16 kysymystä × 2 kieltä (FI + EN) = 32 testiä per malli
  • 9 aihealuetta
  • Kielivertailu + käännösproxy-analyysi
  • Nopeus GPU vs CPU, rinnakkaisajot
  • CRASHPROOF: try/except joka tasolla, JSON-välitallennus
  • LOPULLINEN TUOMIO: tiivis yhteenveto koko benchmarkista

Käyttö:
  python ollama_benchmark.py              # Kaikki testit
  python ollama_benchmark.py quick        # Nopea (4 mallia, 3 kysymystä)
  python ollama_benchmark.py models       # Listaa asennetut mallit

Tulokset: benchmark_YYYYMMDD_HHMMSS.log + .json
"""
import subprocess, json, time, platform, os, sys, threading
from datetime import datetime
from pathlib import Path
from collections import defaultdict

# ═══════════════════════════════════════════════════════════════
# KONFIGURAATIO
# ═══════════════════════════════════════════════════════════════

MODELS = [
    # Monikieliset (vertailupohja)
    "qwen2.5:0.5b", "gemma3:1b", "qwen2.5:1.5b", "qwen2.5:3b",
    "gemma3:4b", "qwen2.5:7b", "qwen2.5:32b",
    # Suomi-erikoiset
    "akx/viking-7b", "osoderholm/poro",
    # Englanti-optimoidut (käännösproxy-kandidaatit)
    "llama3.2:1b", "llama3.2:3b", "smollm2:1.7b",
    "phi4-mini", "phi4-mini-reasoning",
]
QUICK_MODELS = ["qwen2.5:3b", "llama3.2:3b", "phi4-mini", "qwen2.5:32b"]

QUESTIONS = [
    # ── 🐝 TARHAAJA (3) ──
    {"id":"BEE1","category":"tarhaaja","cat_icon":"🐝","difficulty":"helppo",
     "prompt":"Kerro lyhyesti mehiläisten yhteiskunnasta ja rooleista pesässä.",
     "expected":["kuningatar","työläi","kuhnuri","pesä","mun"],
     "prompt_en":"Briefly describe honeybee colony society and the roles within the hive.",
     "expected_en":["queen","worker","drone","hive","egg"]},
    {"id":"BEE2","category":"tarhaaja","cat_icon":"🐝","difficulty":"keskitaso",
     "prompt":"Miten mehiläishoitaja käsittelee varroa-punkkia muurahaishapolla syksyllä?",
     "expected":["varroa","muurahaishap","haihdut","lämpötila","käsittel"],
     "prompt_en":"How does a beekeeper treat varroa mites with formic acid in autumn?",
     "expected_en":["varroa","formic","evaporat","temperature","treat"]},
    {"id":"BEE3","category":"tarhaaja","cat_icon":"🐝","difficulty":"vaikea",
     "prompt":"Kuinka paljon ja milloin mehiläisyhdyskunta pitää ruokkia talvea varten Suomessa?",
     "expected":["sokeri","siirappi","kilo","syys","talv"],
     "prompt_en":"How much and when should a bee colony be fed for winter in Finland?",
     "expected_en":["sugar","syrup","kilogram","autumn","winter"]},
    # ── 🦠 TAUTIVAHTI (1) ──
    {"id":"DIS1","category":"tautivahti","cat_icon":"🦠","difficulty":"vaikea",
     "prompt":"Mitkä ovat amerikkalaisen sikiömädän (AFB) oireet ja miten se eroaa eurooppalaisesta (EFB)?",
     "expected":["afb","efb","sikiö","itiö","haju"],
     "prompt_en":"What are the symptoms of American Foulbrood (AFB) and how does it differ from European Foulbrood (EFB)?",
     "expected_en":["afb","efb","larva","spore","smell"]},
    # ── 🐻 PESÄTURVALLISUUS (1) ──
    {"id":"SEC1","category":"pesaturvallisuus","cat_icon":"🐻","difficulty":"keskitaso",
     "prompt":"Miten suojaat mehiläispesät karhuilta Itä-Suomessa? Kerro sähköaidan rakentamisesta.",
     "expected":["sähköait","karhu","aita","suoja","voltti"],
     "prompt_en":"How do you protect beehives from bears in Eastern Finland? Describe building an electric fence.",
     "expected_en":["electric","bear","fence","protect","volt"]},
    # ── 🏠 MÖKKI (2) ──
    {"id":"MOK1","category":"mökki","cat_icon":"🏠","difficulty":"helppo",
     "prompt":"Mitä pitää huomioida kun sulkee kesämökin talveksi Suomessa? Listaa tärkeimmät toimenpiteet.",
     "expected":["vesi","putk","lämmit","sulk","jääty"],
     "prompt_en":"What should you consider when closing a summer cottage for winter in Finland? List the most important steps.",
     "expected_en":["water","pipe","heat","drain","freez"]},
    {"id":"MOK2","category":"mökki","cat_icon":"🏠","difficulty":"keskitaso",
     "prompt":"Mökin sähkösopimus: pitäisikö olla pörssisähkö vai kiinteä hinta 8 snt/kWh vapaa-ajan asunnolle jossa käydään viikonloppuisin?",
     "expected":["pörssi","kiinte","hinta","kulut","riski"],
     "prompt_en":"Electricity contract for a cottage: should I choose spot price or fixed price at 8 cents/kWh for a weekend-only vacation home?",
     "expected_en":["spot","fixed","price","cost","risk"]},
    # ── ⚡ SÄHKÖ (1) ──
    {"id":"ELE1","category":"sähkö","cat_icon":"⚡","difficulty":"keskitaso",
     "prompt":"Mökin 25A pääsulake laukeaa aina kun sauna ja lämminvesivaraaja ovat päällä yhtä aikaa. Mistä johtuu ja miten korjataan?",
     "expected":["sulak","ampeeri","kuorm","teho","watti"],
     "prompt_en":"The cottage's 25A main fuse trips whenever the sauna and hot water heater are on at the same time. What causes this and how to fix it?",
     "expected_en":["fuse","amp","load","power","watt"]},
    # ── 🍯 RUOKA (2) ──
    {"id":"FOOD1","category":"ruoka","cat_icon":"🍯","difficulty":"helppo",
     "prompt":"Anna resepti hunaja-sinappi-lohelle uunissa. 4 hengelle, valmistusaika ja lämpötila.",
     "expected":["lohi","hunaj","sinapp","uuni","aste","minut"],
     "prompt_en":"Give a recipe for honey-mustard salmon in the oven. For 4 people, include preparation time and temperature.",
     "expected_en":["salmon","honey","mustard","oven","degree","minute"]},
    {"id":"FOOD2","category":"ruoka","cat_icon":"🍯","difficulty":"keskitaso",
     "prompt":"Miten valmistetaan perinteistä simaa vappuaatoksi? Anna ohje ja kerro käymisajasta.",
     "expected":["sima","sitruun","sokeri","hiiva","käy"],
     "prompt_en":"How do you make traditional Finnish sima (mead) for May Day Eve? Give the recipe and fermentation time.",
     "expected_en":["sima","lemon","sugar","yeast","ferment"]},
    # ── 🔢 MATEMATIIKKA (3) ──
    {"id":"MATH1","category":"matematiikka","cat_icon":"🔢","difficulty":"helppo",
     "prompt":"Mehiläistarhaajalla on 300 pesää. Jokainen pesä tuottaa keskimäärin 35 kg hunajaa. Hunajan hinta on 12 euroa/kg. Kuinka paljon on vuoden liikevaihto? Entä jos 15% pesistä ei tuota mitään?",
     "expected":["126000","107100"],
     "prompt_en":"A beekeeper has 300 hives. Each hive produces on average 35 kg of honey. Honey price is 12 euros/kg. What is the annual revenue? What if 15% of hives produce nothing?",
     "expected_en":["126000","107100"]},
    {"id":"MATH2","category":"matematiikka","cat_icon":"🔢","difficulty":"keskitaso",
     "prompt":"Laske suorakulmion muotoisen mehiläistarhan ympärille tarvittavan sähköaidan pituus, kun tarhan mitat ovat 45m x 30m. Paljonko aitamateriaali maksaa jos hinta on 5.50 euroa/metri?",
     "expected":["150","825"],
     "prompt_en":"Calculate the length of electric fence needed around a rectangular apiary measuring 45m x 30m. How much does the fencing material cost at 5.50 euros per meter?",
     "expected_en":["150","825"]},
    {"id":"PHYS1","category":"matematiikka","cat_icon":"🔬","difficulty":"vaikea",
     "prompt":"Mehiläinen painaa 0.1 grammaa ja lentää 25 km/h. Laske sen liike-energia jouleina. Näytä laskukaava ja välivaiheet.",
     "expected":["energia","massa","nopeu","joule"],
     "prompt_en":"A honeybee weighs 0.1 grams and flies at 25 km/h. Calculate its kinetic energy in joules. Show the formula and intermediate steps.",
     "expected_en":["energy","mass","velocity","joule"]},
    # ── 📊 TIEDONKERUU (2) ──
    {"id":"DATA1","category":"tiedonkeruu","cat_icon":"📊","difficulty":"keskitaso",
     "prompt":"Tee lista: mitä sensoritietoja mehiläispesästä kannattaa kerätä automaattisesti ympäri vuoden, ja millä raja-arvoilla pitäisi hälyttää hoitajalle?",
     "expected":["lämpötila","kosteu","paino","ääni","häly"],
     "prompt_en":"Make a list: what sensor data should be automatically collected from a beehive year-round, and at what threshold values should an alert be sent to the beekeeper?",
     "expected_en":["temperature","humidity","weight","sound","alert"]},
    {"id":"DATA2","category":"tiedonkeruu","cat_icon":"📊","difficulty":"vaikea",
     "prompt":"Kirjoita JSON-skeema mehiläispesän sensoridatalle. Kentät: pesä_id, aikaleima, sisälämpötila, ulkolämpötila, kosteus_pct, paino_kg, äänitaso_db, akku_pct. Lisää kommentit jokaiselle kentälle.",
     "expected":["{","pesä_id","lämpötila","paino","json"],
     "prompt_en":"Write a JSON schema for beehive sensor data. Fields: hive_id, timestamp, internal_temperature, external_temperature, humidity_pct, weight_kg, sound_level_db, battery_pct. Add comments for each field.",
     "expected_en":["{","hive_id","temperature","weight","json"]},
    # ── 🌿 LUONTO (1) ──
    {"id":"NAT1","category":"luonto","cat_icon":"🌿","difficulty":"keskitaso",
     "prompt":"Mitkä kasvit kukkivat Etelä-Suomessa heinäkuussa ja ovat tärkeitä mehiläisille mesikasveja? Mainitse ainakin viisi.",
     "expected":["apila","horsm","kukk","mesi"],
     "prompt_en":"What plants bloom in Southern Finland in July and are important nectar sources for honeybees? Name at least five.",
     "expected_en":["clover","willowherb","flower","nectar"]},
]
QUICK_QUESTIONS = ["BEE1", "MATH1", "DATA1"]

PARALLEL_COMBOS = [
    # (malli_A, gpu_A, malli_B, gpu_B, label)
    ("qwen2.5:32b",99,"qwen2.5:3b",0,"32b GPU + qwen3b CPU"),
    ("qwen2.5:32b",99,"llama3.2:1b",0,"32b GPU + llama1b CPU"),
    ("qwen2.5:32b",99,"llama3.2:3b",0,"32b GPU + llama3b CPU"),
    ("qwen2.5:32b",99,"phi4-mini",0,"32b GPU + phi4-mini CPU"),
    ("phi4-mini",99,"llama3.2:1b",99,"phi4+llama1b GPU"),
    ("phi4-mini",99,"qwen2.5:3b",99,"phi4+qwen3b GPU"),
    ("llama3.2:3b",99,"gemma3:1b",99,"llama3b+gemma1b GPU"),
    ("phi4-mini",0,"llama3.2:3b",0,"phi4+llama3b CPU"),
    ("qwen2.5:3b",0,"llama3.2:3b",0,"qwen3b+llama3b CPU"),
    ("smollm2:1.7b",0,"llama3.2:1b",0,"smollm2+llama1b CPU"),
]

SPEED_PROMPT = "Vastaa yhdellä lauseella: mikä on mehiläisten tärkein tehtävä?"
OLLAMA_URL = "http://localhost:11434"
TIMEOUT = 300

# ═══════════════════════════════════════════════════════════════
# VÄRIT
# ═══════════════════════════════════════════════════════════════
if platform.system() == "Windows":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7)
    except Exception: pass
G="\033[92m"; R="\033[91m"; Y="\033[93m"
B="\033[94m"; C="\033[96m"; W="\033[97m"
DIM="\033[90m"; X="\033[0m"

# ═══════════════════════════════════════════════════════════════
# LOGGER
# ═══════════════════════════════════════════════════════════════
class Logger:
    def __init__(self, fp):
        self.filepath = fp
        self.fh = open(fp, "w", encoding="utf-8")
    def log(self, msg="", cc=""):
        plain = msg
        for c in [G,R,Y,B,C,W,DIM,X]: plain = plain.replace(c,"")
        self.fh.write(plain+"\n"); self.fh.flush()
        print(f"{cc}{msg}{X}" if cc else msg)
    def sep(self, ch="═", w=72): self.log(ch*w)
    def close(self): self.fh.close()

# ═══════════════════════════════════════════════════════════════
# RAUTA
# ═══════════════════════════════════════════════════════════════
def collect_hardware(log):
    log.sep(); log.log("  RAUTA-ANALYYSI", C); log.sep(); log.log()
    hw = {"cpu":"?","cores":0,"ram_gb":0,"gpu":None,"vram_mb":0}
    log.log(f"  OS:     {platform.system()} {platform.release()}")
    log.log(f"  Python: {sys.version_info.major}.{sys.version_info.minor}")
    log.log(f"  Aika:   {datetime.now():%Y-%m-%d %H:%M:%S}")
    try:
        if platform.system()=="Windows":
            r=subprocess.run(["wmic","cpu","get","name"],capture_output=True,text=True,timeout=5)
            for l in r.stdout.split("\n"):
                if l.strip() and l.strip()!="Name": hw["cpu"]=l.strip(); break
        else:
            with open("/proc/cpuinfo") as f:
                for l in f:
                    if "model name" in l: hw["cpu"]=l.split(":")[1].strip(); break
    except Exception: pass
    hw["cores"]=os.cpu_count() or 0
    log.log(f"  CPU:    {hw['cpu']} ({hw['cores']} ydintä)")
    try:
        import psutil; mem=psutil.virtual_memory()
        hw["ram_gb"]=round(mem.total/(1024**3),1)
        log.log(f"  RAM:    {hw['ram_gb']} GB (vapaa {mem.available/(1024**3):.1f} GB)")
    except ImportError: log.log("  RAM:    ? (pip install psutil)")
    try:
        r=subprocess.run(["nvidia-smi","--query-gpu=name,memory.total,memory.free,temperature.gpu,utilization.gpu","--format=csv,noheader,nounits"],capture_output=True,text=True,timeout=10)
        if r.returncode==0:
            p=[x.strip() for x in r.stdout.strip().split(",")]
            hw["gpu"]=p[0]; hw["vram_mb"]=int(p[1])
            log.log(f"  GPU:    {p[0]} — {p[1]}MB, vapaa {p[2]}MB, {p[4]}%, {p[3]}°C")
    except Exception: log.log("  GPU:    nvidia-smi ei löydy")
    try:
        r=subprocess.run(["ollama","--version"],capture_output=True,text=True,timeout=5)
        log.log(f"  Ollama: {r.stdout.strip()}")
    except Exception: pass
    log.log(f"  MAX_LOADED_MODELS: {os.environ.get('OLLAMA_MAX_LOADED_MODELS','(ei asetettu)')}")
    log.log(); return hw

# ═══════════════════════════════════════════════════════════════
# OLLAMA API
# ═══════════════════════════════════════════════════════════════
def ollama_gen(model, prompt, system="", num_gpu=None, max_tok=500):
    import urllib.request
    payload = {"model":model,"prompt":prompt,"stream":False,
               "options":{"temperature":0.7,"num_predict":max_tok}}
    if system: payload["system"]=system
    if num_gpu is not None: payload["options"]["num_gpu"]=num_gpu
    t0=time.monotonic()
    try:
        req=urllib.request.Request(f"{OLLAMA_URL}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type":"application/json"},method="POST")
        with urllib.request.urlopen(req,timeout=TIMEOUT) as resp:
            result=json.loads(resp.read().decode("utf-8"))
        elapsed=time.monotonic()-t0
        content=result.get("response","").strip()
        tokens=result.get("eval_count",0)
        load_dur=result.get("load_duration",0)/1e9
        return {"content":content,"elapsed_s":round(elapsed,2),"tokens":tokens,
                "load_dur_s":round(load_dur,2),
                "tokens_per_s":round(tokens/elapsed,1) if elapsed>0 and tokens>0 else 0,
                "error":None}
    except Exception as e:
        return {"content":"","elapsed_s":round(time.monotonic()-t0,2),
                "tokens":0,"load_dur_s":0,"tokens_per_s":0,
                "error":f"{type(e).__name__}: {e}"}

def unload_all(models):
    import urllib.request
    for m in models:
        try:
            req=urllib.request.Request(f"{OLLAMA_URL}/api/generate",
                data=json.dumps({"model":m,"prompt":"","keep_alive":"0"}).encode(),
                headers={"Content-Type":"application/json"},method="POST")
            urllib.request.urlopen(req,timeout=10)
        except Exception: pass
    time.sleep(2)

def gpu_snap(log, label=""):
    try:
        r=subprocess.run(["nvidia-smi","--query-gpu=memory.used,memory.free,utilization.gpu,temperature.gpu","--format=csv,noheader,nounits"],capture_output=True,text=True,timeout=5)
        if r.returncode==0:
            p=[x.strip() for x in r.stdout.strip().split(",")]
            log.log(f"  GPU [{label}]: {p[0]}MB käyt, {p[1]}MB vapaa, {p[2]}%, {p[3]}°C",DIM)
    except Exception: pass

def check_terms(content, expected):
    cl=content.lower()
    found=[t for t in expected if t.lower() in cl]
    missing=[t for t in expected if t.lower() not in cl]
    return found, missing

def is_finnish(text):
    fi_chars=sum(1 for c in text if c in "äöåÄÖÅ")
    fi_words=sum(1 for w in ["on","ja","eli","tai","kun","että","joka","ovat","myös"]
                 if f" {w} " in f" {text.lower()} ")
    return fi_chars>2 or fi_words>=2

def get_installed():
    models=set()
    try:
        r=subprocess.run(["ollama","list"],capture_output=True,text=True,timeout=10)
        if r.returncode==0:
            for line in r.stdout.strip().split("\n")[1:]:
                if line.strip(): models.add(line.split()[0])
    except Exception: pass
    return models

# ═══════════════════════════════════════════════════════════════
# TESTI 1: KIELIVERTAILU
# ═══════════════════════════════════════════════════════════════
def test_bilingual(log, models, questions):
    total=len(models)*len(questions)*2
    log.sep()
    log.log(f"  TESTI 1: KIELIVERTAILU ({len(models)} mallia × {len(questions)} × 2 = {total})",C)
    log.sep()

    all_res=[]
    t_phase=time.monotonic()
    done_tests=0

    for mi,model in enumerate(models,1):
        log.log(f"\n  {'━'*64}")
        elapsed_m=(time.monotonic()-t_phase)/60
        if done_tests>0:
            rate=elapsed_m/done_tests
            remaining=(total-done_tests)*rate
            pct=done_tests*100//total
            bar="█"*(pct//5)+"░"*(20-pct//5)
            log.log(f"  📦 [{mi}/{len(models)}] {model}  [{bar}] {pct}%",W)
            log.log(f"  ⏱️  {elapsed_m:.0f}min kulunut | ~{remaining:.0f}min jäljellä | {done_tests}/{total} testiä",DIM)
        else:
            log.log(f"  📦 [{mi}/{len(models)}] {model}",W)
        log.log(f"  {'━'*64}")
        mr={"model":model,"questions":[]}

        try:
            try: unload_all(models); gpu_snap(log,"ennen")
            except Exception: pass

            for qi,q in enumerate(questions,1):
                icon=q.get("cat_icon","❓")
                qr={"id":q["id"],"category":q["category"],"difficulty":q["difficulty"]}

                for lang,prompt,expected in [("FI",q["prompt"],q["expected"]),
                                              ("EN",q["prompt_en"],q["expected_en"])]:
                    flag="🇫🇮" if lang=="FI" else "🇬🇧"
                    try:
                        log.log(f"\n  {icon} [{q['id']}] {flag} {lang} — {q['category']} ({qi}/{len(questions)})",Y)
                        log.log(f"     {prompt[:95]}{'...' if len(prompt)>95 else ''}",DIM)

                        r=ollama_gen(model, prompt)
                        if r["error"]:
                            log.log(f"     ❌ {r['error']}",R)
                            qr[f"{lang}_error"]=r["error"]; qr[f"{lang}_score"]=0
                            continue

                        found,missing=check_terms(r["content"],expected)
                        score=len(found)/len(expected)*100 if expected else 0
                        sc=G if score>=60 else Y if score>=30 else R
                        log.log(f"     ⏱️  {r['elapsed_s']:.1f}s ({r['load_dur_s']:.1f}s lataus) | {r['tokens']} tok ({r['tokens_per_s']} tok/s)")
                        log.log(f"     🎯 Termit: {len(found)}/{len(expected)} = {score:.0f}%",sc)
                        if missing: log.log(f"     Puuttuu: {', '.join(missing)}",DIM)
                        preview=r["content"][:250].replace("\n"," ↵ ")
                        log.log(f"     💬 {preview}{'...' if len(r['content'])>250 else ''}",DIM)

                        qr[f"{lang}_score"]=score
                        qr[f"{lang}_elapsed"]=r["elapsed_s"]
                        qr[f"{lang}_tokens"]=r["tokens"]
                        qr[f"{lang}_tps"]=r["tokens_per_s"]
                        qr[f"{lang}_found"]=found
                        qr[f"{lang}_missing"]=missing
                        if lang=="FI": qr["FI_is_finnish"]=is_finnish(r["content"])
                    except Exception as e:
                        log.log(f"     💥 KAATUI: {e} — jatketaan",R)
                        qr[f"{lang}_error"]=str(e); qr[f"{lang}_score"]=0
                    done_tests+=1

                # Kieliero
                fi_s=qr.get("FI_score",0); en_s=qr.get("EN_score",0)
                qr["language_gap"]=en_s-fi_s
                try:
                    gap=en_s-fi_s
                    if abs(gap)>5:
                        log.log(f"     📊 Kieliero: {'EN ↑' if gap>0 else 'FI ↑'} {abs(gap):.0f}pp",G if gap>0 else R)
                    else:
                        log.log(f"     📊 Kieliero: ~sama (FI {fi_s:.0f}% ≈ EN {en_s:.0f}%)",DIM)
                except Exception: pass
                mr["questions"].append(qr)

            # Mallikohtaiset keskiarvot
            try:
                ok_fi=[x for x in mr["questions"] if "FI_score" in x and not x.get("FI_error")]
                ok_en=[x for x in mr["questions"] if "EN_score" in x and not x.get("EN_error")]
                if ok_fi:
                    mr["fi_avg_score"]=sum(x["FI_score"] for x in ok_fi)/len(ok_fi)
                    mr["fi_avg_tps"]=sum(x.get("FI_tps",0) for x in ok_fi)/len(ok_fi)
                    mr["finnish_pct"]=sum(1 for x in ok_fi if x.get("FI_is_finnish"))/len(ok_fi)*100
                if ok_en:
                    mr["en_avg_score"]=sum(x["EN_score"] for x in ok_en)/len(ok_en)
                    mr["en_avg_tps"]=sum(x.get("EN_tps",0) for x in ok_en)/len(ok_en)
                if ok_fi and ok_en:
                    mr["avg_language_gap"]=mr.get("en_avg_score",0)-mr.get("fi_avg_score",0)
            except Exception as e:
                log.log(f"  ⚠️  Keskiarvo-virhe: {e}",Y)

            try: gpu_snap(log,"jälkeen")
            except Exception: pass

        except Exception as e:
            log.log(f"\n  💥💥 MALLI {model} KAATUI KOKONAAN: {e}",R)
            log.log(f"  → Jatketaan seuraavaan malliin...",Y)
            done_tests+=(len(questions)*2 - (done_tests % (len(questions)*2)))

        all_res.append(mr)
        log.log(f"\n  ✅ {model} valmis — FI {mr.get('fi_avg_score',0):.0f}% / EN {mr.get('en_avg_score',0):.0f}%",G)
    return all_res

# ═══════════════════════════════════════════════════════════════
# TESTI 2: NOPEUS
# ═══════════════════════════════════════════════════════════════
def test_speed(log, models):
    log.sep(); log.log("  TESTI 2: NOPEUSTESTI — GPU vs CPU (3 toistoa)",C); log.sep()
    results=[]
    for model in models:
        log.log(f"\n  📦 {model}",W)
        for device,ng in [("GPU",99),("CPU",0)]:
            try:
                unload_all(models)
                ollama_gen(model,"moi",num_gpu=ng,max_tok=5)
                times=[]; tps=[]
                for _ in range(3):
                    r=ollama_gen(model,SPEED_PROMPT,num_gpu=ng,max_tok=100)
                    if not r["error"]: times.append(r["elapsed_s"]); tps.append(r["tokens_per_s"])
                if times:
                    at=sum(times)/len(times); atps=sum(tps)/len(tps)
                    log.log(f"     {device}: avg {at:.2f}s (min {min(times):.2f}) | {atps:.1f} tok/s")
                    results.append({"model":model,"device":device,"avg_s":round(at,2),
                                    "min_s":round(min(times),2),"max_s":round(max(times),2),
                                    "avg_tps":round(atps,1)})
                else:
                    log.log(f"     {device}: ❌ kaikki 3 epäonnistui",R)
                    results.append({"model":model,"device":device,"error":True})
            except Exception as e:
                log.log(f"     {device}: 💥 {e}",R)
                results.append({"model":model,"device":device,"error":True})
    return results

# ═══════════════════════════════════════════════════════════════
# TESTI 3: RINNAKKAISAJOT
# ═══════════════════════════════════════════════════════════════
def test_parallel(log, models):
    log.sep(); log.log("  TESTI 3: RINNAKKAISAJOT",C); log.sep()
    max_m=os.environ.get("OLLAMA_MAX_LOADED_MODELS","")
    if max_m not in ("2","3","4","5"):
        log.log(f"  ⚠️  OLLAMA_MAX_LOADED_MODELS={max_m!r} — pitäisi ≥2",Y)

    valid=[c for c in PARALLEL_COMBOS if c[0] in models and c[2] in models]
    if not valid: log.log("  Ei sopivia kombinaatioita",Y); return []

    test_qs=[("BEE1",next(q["prompt"] for q in QUESTIONS if q["id"]=="BEE1")),
             ("DATA1",next(q["prompt"] for q in QUESTIONS if q["id"]=="DATA1"))]
    results=[]
    for ci,(ma,ga,mb,gb,label) in enumerate(valid,1):
        da="GPU" if ga>0 else "CPU"; db="GPU" if gb>0 else "CPU"
        log.log(f"\n  {'─'*60}")
        log.log(f"  🔄 [{ci}/{len(valid)}] {label}",W)
        combo={"label":label,"a_model":ma,"a_dev":da,"b_model":mb,"b_dev":db,"tests":[]}
        try:
            unload_all(models); time.sleep(1)
            ollama_gen(ma,"moi",num_gpu=ga,max_tok=5)
            ollama_gen(mb,"moi",num_gpu=gb,max_tok=5)
            time.sleep(1); gpu_snap(log,"ladattu")

            for qid,prompt in test_qs:
                ra={}; rb={}
                def run_a(): nonlocal ra; ra=ollama_gen(ma,prompt,num_gpu=ga)
                def run_b(): nonlocal rb; rb=ollama_gen(mb,prompt,num_gpu=gb)
                t0=time.monotonic()
                ta=threading.Thread(target=run_a); tb=threading.Thread(target=run_b)
                ta.start(); tb.start()
                ta.join(timeout=TIMEOUT); tb.join(timeout=TIMEOUT)
                wall=time.monotonic()-t0

                empty={"content":"","elapsed_s":0,"tokens":0,"tokens_per_s":0,"error":"timeout"}
                if not ra: ra=empty
                if not rb: rb=empty

                log.log(f"\n     [{qid}] Wall: {wall:.1f}s")
                for tag,res,mdl in [("A",ra,ma),("B",rb,mb)]:
                    if res.get("error"):
                        log.log(f"       [{tag}] {mdl}: ❌ {res['error']}",R)
                    else:
                        log.log(f"       [{tag}] {mdl}: {res['elapsed_s']:.1f}s, {res['tokens']} tok ({res['tokens_per_s']} tok/s)")
                combo["tests"].append({"qid":qid,"wall_s":round(wall,2),
                    "a_time":ra.get("elapsed_s",0),"a_tps":ra.get("tokens_per_s",0),"a_err":ra.get("error"),
                    "b_time":rb.get("elapsed_s",0),"b_tps":rb.get("tokens_per_s",0),"b_err":rb.get("error")})
            gpu_snap(log,"jälkeen")
        except Exception as e:
            log.log(f"  💥 KAATUI: {e} — jatketaan",R)
        results.append(combo)
    return results

# ═══════════════════════════════════════════════════════════════
# YHTEENVETO + LOPULLINEN TUOMIO
# ═══════════════════════════════════════════════════════════════
def print_summary(log, bilingual, speed, parallel):
    log.sep("═"); log.log("  YHTEENVETO",C); log.sep("═")
    if not bilingual:
        log.log("  ⚠️  Ei tuloksia",Y); return

    # ── 1. Kielivertailu ─────────────────────────────
    try:
        log.log(f"\n  📊 KIELIVERTAILU: FI vs EN")
        log.log(f"  {'Malli':<26} {'FI%':>5} {'EN%':>5} {'Ero':>6} {'FI kieli%':>9} {'FI t/s':>7} {'EN t/s':>7}")
        log.log(f"  {'─'*70}")
        for mr in bilingual:
            fi=mr.get("fi_avg_score",0); en=mr.get("en_avg_score",0)
            gap=mr.get("avg_language_gap",0); fi_pct=mr.get("finnish_pct",0)
            gc=G if gap>20 else (Y if gap>10 else "")
            gs=f"+{gap:.0f}" if gap>0 else f"{gap:.0f}"
            log.log(f"  {mr['model']:<26} {fi:>4.0f}% {en:>4.0f}% {gs:>5}pp {fi_pct:>8.0f}% "
                    f"{mr.get('fi_avg_tps',0):>6.1f} {mr.get('en_avg_tps',0):>6.1f}",gc)
    except Exception as e: log.log(f"  ⚠️  Osio 1 kaatui: {e}",Y)

    # ── 2. Aihekohtainen kieliero ────────────────────
    try:
        cats=[]
        seen=set()
        for q in QUESTIONS:
            if q["category"] not in seen: cats.append((q["category"],q.get("cat_icon","?"))); seen.add(q["category"])
        log.log(f"\n  📊 KIELIERO PER AIHE (EN% − FI%)")
        hdr=f"  {'Malli':<26}"
        for cat,icon in cats: hdr+=f" {icon:>4}"
        log.log(hdr); log.log(f"  {'─'*(26+6*len(cats))}")
        for mr in bilingual:
            row=f"  {mr['model']:<26}"
            for cat,_ in cats:
                cqs=[x for x in mr["questions"] if x.get("category")==cat]
                if cqs:
                    ag=sum(x.get("language_gap",0) for x in cqs)/len(cqs)
                    row+=f" {'+' if ag>0 else ''}{ag:>4.0f}"
                else: row+="   --"
            log.log(row)
    except Exception as e: log.log(f"  ⚠️  Osio 2 kaatui: {e}",Y)

    # ── 3. Käännösproxy ──────────────────────────────
    try:
        log.log(f"\n  🔄 KÄÄNNÖSPROXY-ANALYYSI")
        log.log(f"  {'─'*65}")
        for mr in bilingual:
            gap=mr.get("avg_language_gap",0); fi=mr.get("fi_avg_score",0); en=mr.get("en_avg_score",0)
            if gap>25 and en>40: v=f"✅ SUURI HYÖTY (+{gap:.0f}pp)"; vc=G
            elif gap>15 and en>30: v=f"⚠️  KOHTALAINEN (+{gap:.0f}pp)"; vc=Y
            elif en<30: v=f"❌ EI AUTA (EN vain {en:.0f}%)"; vc=R
            elif gap<=5: v=f"✨ EI TARVETTA (FI {fi:.0f}% ≈ EN)"; vc=C
            else: v=f"🤷 PIENI (+{gap:.0f}pp)"; vc=DIM
            log.log(f"  {mr['model']:<26} {v}",vc)
    except Exception as e: log.log(f"  ⚠️  Osio 3 kaatui: {e}",Y)

    # ── 4. Nopeus ────────────────────────────────────
    try:
        if speed:
            log.log(f"\n  ⚡ GPU vs CPU NOPEUS")
            log.log(f"  {'Malli':<26} {'GPU s':>7} {'GPU t/s':>8} {'CPU s':>7} {'CPU t/s':>8} {'Kerroin':>8}")
            log.log(f"  {'─'*68}")
            by_m=defaultdict(dict)
            for s in speed: by_m[s["model"]][s["device"]]=s
            for model,devs in by_m.items():
                gd=devs.get("GPU",{}); cd=devs.get("CPU",{})
                gs=f"{gd['avg_s']:.2f}" if not gd.get("error") else "ERR"
                gt=f"{gd.get('avg_tps',0):.1f}" if not gd.get("error") else "-"
                cs=f"{cd['avg_s']:.2f}" if not cd.get("error") else "ERR"
                ct=f"{cd.get('avg_tps',0):.1f}" if not cd.get("error") else "-"
                ratio="-"
                if not gd.get("error") and not cd.get("error") and gd.get("avg_s",0)>0:
                    ratio=f"{cd['avg_s']/gd['avg_s']:.1f}x"
                log.log(f"  {model:<26} {gs:>7} {gt:>8} {cs:>7} {ct:>8} {ratio:>8}")
    except Exception as e: log.log(f"  ⚠️  Osio 4 kaatui: {e}",Y)

    # ── 5. Rinnakkaisajot ────────────────────────────
    try:
        if parallel:
            log.log(f"\n  🔄 RINNAKKAISAJOT")
            log.log(f"  {'Kombinaatio':<34} {'Q':>5} {'Wall':>6} {'A s':>6} {'A t/s':>6} {'B s':>6} {'B t/s':>6}")
            log.log(f"  {'─'*76}")
            for pr in parallel:
                for t in pr.get("tests",[]):
                    a_s=f"{t['a_time']:.1f}" if not t.get("a_err") else "ERR"
                    b_s=f"{t['b_time']:.1f}" if not t.get("b_err") else "ERR"
                    a_t=f"{t['a_tps']:.0f}" if not t.get("a_err") else "-"
                    b_t=f"{t['b_tps']:.0f}" if not t.get("b_err") else "-"
                    log.log(f"  {pr['label']:<34} {t['qid']:>5} {t['wall_s']:>5.1f}s {a_s:>6} {a_t:>6} {b_s:>6} {b_t:>6}")
    except Exception as e: log.log(f"  ⚠️  Osio 5 kaatui: {e}",Y)

    # ══════════════════════════════════════════════════
    # 🏁 LOPULLINEN TUOMIO
    # ══════════════════════════════════════════════════
    try:
        _print_final_verdict(log, bilingual, speed, parallel)
    except Exception as e:
        log.log(f"\n  ⚠️  Lopullinen tuomio kaatui: {e}",Y)
        log.log(f"  (Raakadata on tallessa .json-tiedostossa)",Y)

    log.log(); log.sep("═")
    log.log(f"  ✅ Yhteenveto valmis!",G)
    log.sep("═")


def _print_final_verdict(log, bilingual, speed, parallel):
    """Tiivis lopputuomio — kopioi tämä Claudelle."""
    log.log()
    log.sep("█"); log.log("  🏁 LOPULLINEN TUOMIO — WaggleDance mallisuositukset",W); log.sep("█")

    small_ids=["0.5b","1b","1.5b","1.7b","3b","3.8b","4b"]
    en_names=["llama3.2","smollm2","phi4-mini"]

    # Kerää ranking-data
    ranking=[]
    for mr in bilingual:
        fi=mr.get("fi_avg_score",0); en=mr.get("en_avg_score",0)
        gap=mr.get("avg_language_gap",0)
        is_small=any(s in mr["model"] for s in small_ids)
        is_en=any(n in mr["model"] for n in en_names)
        cpu_tps=0
        if speed:
            cpu=[s for s in speed if s.get("model")==mr["model"] and s.get("device")=="CPU" and not s.get("error")]
            if cpu: cpu_tps=cpu[0].get("avg_tps",0)
        ranking.append({"model":mr["model"],"fi":fi,"en":en,"gap":gap,
                        "cpu_tps":cpu_tps,"is_small":is_small,"is_en":is_en,
                        "fi_pct":mr.get("finnish_pct",0)})

    # A. KOKONAISRANKING
    try:
        log.log(f"\n  ┌─{'─'*72}─┐")
        log.log(f"  │  A. KOKONAISRANKING (EN-laadun mukaan){'':>33}│")
        log.log(f"  ├─{'─'*72}─┤")
        log.log(f"  │  {'#':>2} {'Malli':<26} {'FI%':>5} {'EN%':>5} {'Gap':>5} {'CPU t/s':>8} {'Tyyppi':<10}│")
        log.log(f"  ├─{'─'*72}─┤")
        for i,r in enumerate(sorted(ranking,key=lambda x:x["en"],reverse=True),1):
            typ="🇬🇧 EN" if r["is_en"] else ("📦 pieni" if r["is_small"] else "🔷 iso")
            gs=f"+{r['gap']:.0f}" if r["gap"]>0 else f"{r['gap']:.0f}"
            cpu=f"{r['cpu_tps']:.0f}" if r["cpu_tps"] else "—"
            log.log(f"  │  {i:>2} {r['model']:<26} {r['fi']:>4.0f}% {r['en']:>4.0f}% {gs:>4}pp {cpu:>7} {typ:<10}│")
        log.log(f"  └─{'─'*72}─┘")
    except Exception as e: log.log(f"  ⚠️  Ranking kaatui: {e}",Y)

    # B. ROOLISUOSITUKSET
    try:
        smalls=[r for r in ranking if r["is_small"]]
        bigs=[r for r in ranking if not r["is_small"]]

        log.log(f"\n  ┌─{'─'*72}─┐")
        log.log(f"  │  B. WAGGLEDANCE ROOLISUOSITUKSET{'':>38}│")
        log.log(f"  ├─{'─'*72}─┤")

        if bigs:
            best_chat=max(bigs,key=lambda r:r["fi"])
            log.log(f"  │  🗣️  CHAT-MALLI (GPU){'':>50}│")
            log.log(f"  │     → {best_chat['model']:<30} FI {best_chat['fi']:.0f}%{'':>22}│")
        log.log(f"  │{'':>74}│")

        if smalls:
            best_fi_s=max(smalls,key=lambda r:r["fi"])
            best_en_s=max(smalls,key=lambda r:r["en"])
            use_proxy=(best_en_s["en"]>best_fi_s["fi"]+15 and best_en_s["en"]>35)

            log.log(f"  │  💓 HEARTBEAT-MALLI (CPU){'':>46}│")
            log.log(f"  │{'':>74}│")
            log.log(f"  │     A) SUORA SUOMI: {best_fi_s['model']:<25} FI {best_fi_s['fi']:.0f}%{'':>12}│")
            if best_fi_s["fi"]<30:
                log.log(f"  │        ⚠️  Heikko suomi — vain yksinkaisiin tehtäviin{'':>19}│")
            log.log(f"  │{'':>74}│")
            log.log(f"  │     B) EN + KÄÄNNÖSPROXY: {best_en_s['model']:<21} EN {best_en_s['en']:.0f}% (+{best_en_s['gap']:.0f}pp){'':>4}│")
            log.log(f"  │        + Helsinki-NLP/opus-mt käännökseen (+~0.6s){'':>21}│")
            log.log(f"  │{'':>74}│")

            if use_proxy:
                log.log(f"  │     🏆 SUOSITUS: vaihtoehto B (käännösproxy){'':>27}│")
            else:
                log.log(f"  │     🏆 SUOSITUS: vaihtoehto A (suora suomi){'':>28}│")

            cpu_smalls=[r for r in smalls if r["cpu_tps"]>0]
            if cpu_smalls:
                fastest=max(cpu_smalls,key=lambda r:r["cpu_tps"])
                log.log(f"  │{'':>74}│")
                log.log(f"  │     ⚡ Nopein CPU: {fastest['model']:<25} {fastest['cpu_tps']:.0f} tok/s{'':>12}│")
        log.log(f"  └─{'─'*72}─┘")
    except Exception as e: log.log(f"  ⚠️  Roolisuositukset kaatui: {e}",Y)

    # C. KÄÄNNÖSPROXY-TUOMIO
    try:
        smalls=[r for r in ranking if r["is_small"]]
        proxy_helps=[r for r in smalls if r["gap"]>15 and r["en"]>35] if smalls else []

        log.log(f"\n  ┌─{'─'*72}─┐")
        log.log(f"  │  C. KÄÄNNÖSPROXY — KANNATTAAKO?{'':>39}│")
        log.log(f"  ├─{'─'*72}─┤")
        if proxy_helps:
            bp=max(proxy_helps,key=lambda r:r["en"])
            log.log(f"  │  ✅ KYLLÄ! Paras: {bp['model']:<25} EN {bp['en']:.0f}% (+{bp['gap']:.0f}pp){'':>5}│")
            log.log(f"  │{'':>74}│")
            log.log(f"  │  Arkkitehtuuri:{'':>56}│")
            log.log(f"  │    Käyttäjä FI → opus-mt-fi-en → {bp['model']} EN → opus-mt-en-fi → FI{'':>2}│")
            log.log(f"  │    Lisäviive: ~0.6s | pip install transformers sentencepiece{'':>9}│")
        else:
            log.log(f"  │  ❌ EI MERKITTÄVÄÄ HYÖTYÄ{'':>47}│")
            if smalls:
                bs=max(smalls,key=lambda r:r["fi"])
                log.log(f"  │  → Käytä suoraan: {bs['model']:<25} FI {bs['fi']:.0f}%{'':>14}│")
        log.log(f"  └─{'─'*72}─┘")
    except Exception as e: log.log(f"  ⚠️  Käännösproxy-tuomio kaatui: {e}",Y)

    # D. PARAS RINNAKKAISAJO
    try:
        if parallel:
            log.log(f"\n  ┌─{'─'*72}─┐")
            log.log(f"  │  D. PARAS RINNAKKAISKOMBINAATIO{'':>40}│")
            log.log(f"  ├─{'─'*72}─┤")
            best_combo=None; best_wall=9999
            for pr in parallel:
                ok=[t for t in pr.get("tests",[]) if not t.get("a_err") and not t.get("b_err")]
                if ok:
                    avg_w=sum(t["wall_s"] for t in ok)/len(ok)
                    if avg_w<best_wall: best_wall=avg_w; best_combo=pr
            if best_combo:
                log.log(f"  │  🏆 {best_combo['label']:<40} avg {best_wall:.1f}s{'':>16}│")
            else:
                log.log(f"  │  ⚠️  Kaikki kombinaatiot epäonnistuivat{'':>32}│")
            log.log(f"  └─{'─'*72}─┘")
    except Exception as e: log.log(f"  ⚠️  Rinnakkais-tuomio kaatui: {e}",Y)

    # E. SUOSITELTU settings.yaml
    try:
        log.log(f"\n  ┌─{'─'*72}─┐")
        log.log(f"  │  E. SUOSITELTU KONFIGURAATIO{'':>43}│")
        log.log(f"  ├─{'─'*72}─┤")

        chat_pick=max(ranking,key=lambda r:r["fi"])
        log.log(f"  │  llm:{'':>66}│")
        log.log(f"  │    model: \"{chat_pick['model']}\"{'':>{66-len(chat_pick['model'])}}│")
        log.log(f"  │    # GPU — paras suomen laatu{'':>42}│")
        log.log(f"  │{'':>74}│")

        smalls=[r for r in ranking if r["is_small"]]
        if smalls:
            best_fi_s=max(smalls,key=lambda r:r["fi"])
            best_en_s=max(smalls,key=lambda r:r["en"])
            use_proxy=(best_en_s["en"]>best_fi_s["fi"]+15 and best_en_s["en"]>35) if smalls else False
            hb_pick=best_en_s if use_proxy else best_fi_s
            log.log(f"  │  llm_heartbeat:{'':>55}│")
            log.log(f"  │    model: \"{hb_pick['model']}\"{'':>{55-len(hb_pick['model'])}}│")
            log.log(f"  │    num_gpu: 0  # CPU{'':>51}│")
            if use_proxy:
                log.log(f"  │    # + Helsinki-NLP/opus-mt-fi-en, opus-mt-en-fi{'':>22}│")
        log.log(f"  │{'':>74}│")
        log.log(f"  │  OLLAMA_MAX_LOADED_MODELS=2  # KRIITTINEN!{'':>28}│")
        log.log(f"  └─{'─'*72}─┘")
    except Exception as e: log.log(f"  ⚠️  Konfiguraatio kaatui: {e}",Y)

    # F. TILASTOT
    try:
        total_tests=sum(len(mr.get("questions",[]))*2 for mr in bilingual)
        total_errors=sum(1 for mr in bilingual for q in mr.get("questions",[])
                         for lang in ["FI","EN"] if q.get(f"{lang}_error"))
        log.log(f"\n  📈 TILASTOT")
        log.log(f"     Malleja:      {len(bilingual)}")
        log.log(f"     Kielitestejä: {total_tests}")
        log.log(f"     Virheitä:     {total_errors} ({total_errors/max(total_tests,1)*100:.1f}%)")
        if speed: log.log(f"     Nopeustestejä:    {len(speed)}")
        if parallel: log.log(f"     Rinnakkaistestejä: {sum(len(pr.get('tests',[])) for pr in parallel)}")
    except Exception as e: log.log(f"  ⚠️  Tilastot kaatui: {e}",Y)


# ═══════════════════════════════════════════════════════════════
# JSON-VÄLITALLENNUS
# ═══════════════════════════════════════════════════════════════
def _save_json(jp, ts, hw, available, bilingual, speed, parallel):
    try:
        raw={"timestamp":ts,"version":"2.2-bilingual-crashproof",
             "hardware":hw,"models":available,
             "bilingual":bilingual or [],"speed":speed or [],
             "parallel":parallel or [],"saved_at":datetime.now().isoformat()}
        jp.write_text(json.dumps(raw,indent=2,ensure_ascii=False,default=str),encoding="utf-8")
    except Exception as e:
        print(f"  ⚠️  JSON-tallennus epäonnistui: {e}")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    quick=len(sys.argv)>1 and sys.argv[1].lower()=="quick"
    list_mode=len(sys.argv)>1 and sys.argv[1].lower()=="models"

    if list_mode:
        installed=get_installed()
        print(f"\n{W}Mallit:{X}\n")
        for m in MODELS:
            m_base=m.split(":")[0].split("/")[-1]
            ok=any(m_base in i for i in installed)
            print(f"  {m:<28} {G+'✅'+X if ok else R+'❌ ollama pull '+m+X}")
        print(); return

    ts=datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path=Path(f"benchmark_{ts}.log")
    jp=log_path.with_suffix(".json")
    log=Logger(log_path)

    target_models=QUICK_MODELS if quick else MODELS
    questions=[q for q in QUESTIONS if q["id"] in QUICK_QUESTIONS] if quick else QUESTIONS

    log.sep("═")
    log.log(f"  WaggleDance — Ollama Full Benchmark v2.2 (bilingual, crashproof)",W)
    log.log(f"  {datetime.now():%Y-%m-%d %H:%M:%S}")
    log.log(f"  {'QUICK' if quick else 'FULL'}: {len(target_models)} mallia × {len(questions)} kysymystä × 2 kieltä")
    log.sep("═"); log.log()

    try:
        r=subprocess.run(["ollama","list"],capture_output=True,text=True,timeout=10)
        if r.returncode!=0: log.log("  ❌ Ollama ei vastaa!",R); log.close(); return
    except FileNotFoundError: log.log("  ❌ Ollama puuttuu!",R); log.close(); return

    installed=get_installed()
    available=[m for m in target_models if any(m.split(":")[0].split("/")[-1] in i for i in installed)]
    missing=[m for m in target_models if m not in available]

    if missing:
        log.log(f"  ⚠️  Puuttuu {len(missing)}: {', '.join(missing)}",Y)
        if not available: log.log("  ❌ Ei yhtään mallia!",R); log.close(); return
        ans=input(f"  {Y}Jatketaanko {len(available)} mallilla? (k/e): {X}").strip()
        if ans.lower() not in ("k",""): log.close(); return

    est=len(available)*len(questions)*2*0.5
    log.log(f"  ✅ {len(available)} mallia × {len(questions)} × 2 = {len(available)*len(questions)*2} kielitestiä")
    log.log(f"  ⏱️  Arvio: {est:.0f}–{est*3:.0f} min")
    log.log(f"  💾 Välitallennus: {jp}")
    log.log()

    hw={}; bilingual=[]; speed=[]; parallel=[]
    t_start=time.monotonic()

    # VAIHE 1: Rauta
    try: hw=collect_hardware(log)
    except Exception as e: log.log(f"  ⚠️  Rauta: {e}",Y)
    _save_json(jp,ts,hw,available,bilingual,speed,parallel)

    # VAIHE 2: Kielitestit
    try: bilingual=test_bilingual(log,available,questions)
    except Exception as e: log.log(f"\n  💥 KIELITESTIT: {e}",R)
    _save_json(jp,ts,hw,available,bilingual,speed,parallel)
    log.log(f"\n  💾 Välitallennettu ({(time.monotonic()-t_start)/60:.1f} min)",DIM)

    # VAIHE 3: Nopeus
    try: speed=test_speed(log,available)
    except Exception as e: log.log(f"\n  💥 NOPEUS: {e}",R)
    _save_json(jp,ts,hw,available,bilingual,speed,parallel)

    # VAIHE 4: Rinnakkais
    try: parallel=test_parallel(log,available)
    except Exception as e: log.log(f"\n  💥 RINNAKKAIS: {e}",R)
    _save_json(jp,ts,hw,available,bilingual,speed,parallel)

    # VAIHE 5: Yhteenveto + Lopullinen tuomio
    try: print_summary(log,bilingual,speed,parallel)
    except Exception as e: log.log(f"\n  💥 YHTEENVETO: {e}",R)

    # Lopputallennus
    elapsed=(time.monotonic()-t_start)/60
    _save_json(jp,ts,hw,available,bilingual,speed,parallel)

    log.log(f"\n  ⏱️  Kokonaisaika: {elapsed:.1f} min")
    log.sep("═")
    log.log(f"  ✅ BENCHMARK VALMIS!",G)
    log.log(f"  📄 Logi: {log_path}")
    log.log(f"  📊 JSON: {jp}")
    log.log(f"  📋 Lähetä molemmat Claudelle analysointiin!")
    log.sep("═")
    log.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n  {Y}⏹️  Ctrl+C — JSON-välitallennus on tallessa.{X}")
    except Exception as e:
        print(f"\n  {R}💥 VIRHE: {type(e).__name__}: {e}{X}")
        print(f"  {Y}JSON-välitallennus on todennäköisesti tallessa.{X}")
        import traceback; traceback.print_exc()
