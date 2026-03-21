"""Deep diagnostic analysis of WaggleDance learning data."""
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.chdir(os.path.dirname(os.path.dirname(__file__)))

now = datetime.now(timezone.utc)

print("=" * 80)
print("WAGGLEDANCE SWARM - PERUSTEELLINEN DIAGNOSTIIKKA")
print(f"Generoitu: {now.strftime('%Y-%m-%d %H:%M UTC')}")
print("=" * 80)

# ============================================================
# 1. FINETUNE CURATED - laadun analyysi
# ============================================================
print("\n## 1. KURATOITUJEN FAKTOJEN LAATU-ANALYYSI")
print("-" * 60)

curated = []
with open("data/finetune_curated.jsonl", "r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        try:
            curated.append(json.loads(line))
        except Exception:
            pass

print(f"Kuratoituja yhteensa: {len(curated)}")

# Score distribution
scores = Counter()
for e in curated:
    s = e.get("quality_score", 0)
    scores[s] = scores.get(s, 0) + 1
print(f"Score-jakauma: {dict(sorted(scores.items()))}")

# Agent distribution
agents = Counter(e.get("agent_type", "?") for e in curated)
print("Top-10 agentit:")
for a, c in agents.most_common(10):
    print(f"  {a}: {c} ({c * 100 // len(curated)}%)")

# Response length stats
resp_lens = []
for e in curated:
    msgs = e.get("messages", [])
    for m in msgs:
        if m.get("role") == "assistant":
            resp_lens.append(len(m.get("content", "")))
if resp_lens:
    s_lens = sorted(resp_lens)
    print(
        f"Vastausten pituus: min={min(resp_lens)}, "
        f"avg={sum(resp_lens) // len(resp_lens)}, "
        f"max={max(resp_lens)}, "
        f"median={s_lens[len(s_lens) // 2]}"
    )

# Language / quality heuristic
en_count = fi_count = mixed_count = empty_count = boilerplate_count = 0
hallucination_suspect = 0
repetitive_count = 0
for e in curated:
    msgs = e.get("messages", [])
    for m in msgs:
        if m.get("role") == "assistant":
            text = m.get("content", "")
            if len(text) < 30:
                empty_count += 1
            elif text.count("Tietopankki") > 1 or text.count("##") > 4:
                boilerplate_count += 1
            elif text.count("?") > 5 or "Vastaus: **?**" in text:
                hallucination_suspect += 1
            elif len(set(text.split())) < len(text.split()) * 0.3:
                repetitive_count += 1
            elif any(
                w in text.lower()
                for w in ["the ", " is ", " and ", " for ", "recommend"]
            ):
                if any(w in text.lower() for w in [" on ", " ja ", " tai ", "ovat"]):
                    mixed_count += 1
                else:
                    en_count += 1
            else:
                fi_count += 1

print(
    f"Sisaltoanalyysi:\n"
    f"  Suomi:         {fi_count}\n"
    f"  Englanti:      {en_count}\n"
    f"  Sekakielinen:  {mixed_count}\n"
    f"  Tyhja/lyhyt:   {empty_count}\n"
    f"  Boilerplate:   {boilerplate_count}\n"
    f"  Hallus. epaily:{hallucination_suspect}\n"
    f"  Toistava:      {repetitive_count}"
)

# Timestamp distribution by day
daily = Counter()
for e in curated:
    ts = e.get("timestamp", "")[:10]
    if ts:
        daily[ts] += 1
print("Paivittainen tuotanto:")
for day in sorted(daily.keys()):
    bar = "#" * (daily[day] // 10)
    print(f"  {day}: {daily[day]:>4} {bar}")

# ============================================================
# 2. REJECTED analysis
# ============================================================
print("\n## 2. HYLATTYJEN ANALYYSI")
print("-" * 60)

rejected = []
with open("data/finetune_rejected.jsonl", "r", encoding="utf-8", errors="ignore") as f:
    for i, line in enumerate(f):
        if i >= 5000:
            break
        try:
            rejected.append(json.loads(line))
        except Exception:
            pass

rej_scores = Counter()
for e in rejected:
    s = e.get("quality_score", 0)
    rej_scores[s] = rej_scores.get(s, 0) + 1
print(
    f"Hylattyjen score-jakauma (sample {len(rejected)}): "
    f"{dict(sorted(rej_scores.items()))}"
)

rej_agents = Counter(e.get("agent_type", "?") for e in rejected)
print("Top-10 hylatyt agentit:")
for a, c in rej_agents.most_common(10):
    print(f"  {a}: {c}")

# Rejection reasons
reasons = Counter()
for e in rejected:
    r = e.get("reasoning", "").lower()
    if not r:
        reasons["ei syyta"] += 1
    elif "halluc" in r:
        reasons["hallusinaatio"] += 1
    elif "repeti" in r or "duplic" in r:
        reasons["toisto"] += 1
    elif "short" in r or "empty" in r:
        reasons["liian lyhyt"] += 1
    elif "quality" in r:
        reasons["heikko laatu"] += 1
    elif "boilerplate" in r or "template" in r:
        reasons["boilerplate"] += 1
    else:
        reasons["muu"] += 1
print(f"Hylkayssyyt: {dict(reasons.most_common(10))}")

# ============================================================
# 3. LEARNING METRICS
# ============================================================
print("\n## 3. LEARNING METRICS ANALYYSI")
print("-" * 60)

metrics = []
with open(
    "data/learning_metrics.jsonl", "r", encoding="utf-8", errors="ignore"
) as f:
    for line in f:
        try:
            metrics.append(json.loads(line))
        except Exception:
            pass

print(f"Metriikkariveja: {len(metrics)}")

times = [m.get("response_time_ms", 0) for m in metrics if m.get("response_time_ms")]
if times:
    times_sorted = sorted(times)
    p50 = times_sorted[len(times) // 2]
    p95 = times_sorted[int(len(times) * 0.95)]
    p99 = times_sorted[int(len(times) * 0.99)]
    print(
        f"Response time: avg={sum(times) // len(times)}ms, "
        f"p50={p50:.0f}ms, p95={p95:.0f}ms, p99={p99:.0f}ms"
    )

hall = sum(1 for m in metrics if m.get("was_hallucination"))
print(f"Hallusinaatiot: {hall}/{len(metrics)} ({hall * 100 // max(len(metrics), 1)}%)")

cache = sum(1 for m in metrics if m.get("cache_hit"))
print(f"Cache hits: {cache}/{len(metrics)} ({cache * 100 // max(len(metrics), 1)}%)")

routes = Counter(m.get("route", "?") for m in metrics)
print(f"Reitit: {dict(routes.most_common(5))}")

models = Counter(m.get("model_used", "?") for m in metrics)
print(f"Mallit: {dict(models.most_common(5))}")

confs = [m.get("confidence", 0) for m in metrics if "confidence" in m]
if confs:
    print(f"Confidence: avg={sum(confs) / len(confs):.2f}")

# Per-hour throughput (last 48h)
hourly_count = defaultdict(int)
cutoff = now - timedelta(hours=48)
for m in metrics:
    ts = m.get("ts", "")
    if not ts:
        continue
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if t >= cutoff:
            hourly_count[t.strftime("%m-%d %H:00")] += 1
    except Exception:
        pass

print("Tuntitaso (viimeiset 48h):")
for h in sorted(hourly_count.keys()):
    bar = "#" * (hourly_count[h] // 5)
    print(f"  {h}: {hourly_count[h]:>4} {bar}")

# ============================================================
# 4. MORNING REPORTS
# ============================================================
print("\n## 4. MORNING REPORTS - OPPIMISEN TRENDI")
print("-" * 60)

reports = []
with open(
    "data/morning_reports.jsonl", "r", encoding="utf-8", errors="ignore"
) as f:
    for line in f:
        try:
            reports.append(json.loads(line))
        except Exception:
            pass

print(f"Raportteja: {len(reports)}")
print(f"{'Aika':<20} {'Checked':>8} {'Stored':>8} {'Pass%':>6} {'Novelty':>8}")
for r in reports:
    ts = r.get("timestamp", "")[:16]
    checked = r.get("total_checked", 0)
    stored = r.get("total_stored", 0)
    ps = r.get("per_source", {}).get("self_generate", {})
    pr = ps.get("pass_rate", 0)
    nov = ps.get("novelty_score", 0)
    print(f"{ts:<20} {checked:>8} {stored:>8} {pr * 100:>5.1f}% {nov:>7.2f}")

# ============================================================
# 5. AGENT PERFORMANCE
# ============================================================
print("\n## 5. AGENTTIEN SUORITUSKYKY")
print("-" * 60)

agent_stats = defaultdict(lambda: {"curated": 0, "rejected": 0, "total_quality": 0})
for e in curated:
    a = e.get("agent_type", "?")
    agent_stats[a]["curated"] += 1
    agent_stats[a]["total_quality"] += e.get("quality_score", 0)

for e in rejected:
    a = e.get("agent_type", "?")
    agent_stats[a]["rejected"] += 1

print(f"{'Agentti':<25} {'Curat':>6} {'Rej':>6} {'Rate%':>6} {'AvgQ':>6}")
for a in sorted(
    agent_stats.keys(), key=lambda x: agent_stats[x]["curated"], reverse=True
)[:25]:
    s = agent_stats[a]
    total = s["curated"] + s["rejected"]
    rate = s["curated"] * 100 // max(total, 1)
    avgq = s["total_quality"] / max(s["curated"], 1)
    print(f"{a:<25} {s['curated']:>6} {s['rejected']:>6} {rate:>5}% {avgq:>5.1f}")

# ============================================================
# 6. ONGELMA-ANALYYSI
# ============================================================
print("\n## 6. TUNNISTETUT ONGELMAT")
print("-" * 60)

# Check for repetitive content in curated
from hashlib import md5

content_hashes = Counter()
for e in curated:
    msgs = e.get("messages", [])
    for m in msgs:
        if m.get("role") == "assistant":
            text = m.get("content", "").strip()[:200]
            h = md5(text.encode()).hexdigest()[:8]
            content_hashes[h] += 1

duplicates = sum(c - 1 for c in content_hashes.values() if c > 1)
unique_ratio = len(content_hashes) / max(len(curated), 1) * 100
print(f"Duplikaattianalyysi:")
print(f"  Uniikit vastaukset: {len(content_hashes)} / {len(curated)} ({unique_ratio:.0f}%)")
print(f"  Duplikaatteja: {duplicates}")

# Top repeated content
print("  Top-5 toistuva sisalto:")
for h, c in content_hashes.most_common(5):
    if c > 1:
        # Find the actual text
        for e in curated:
            for m in e.get("messages", []):
                if m.get("role") == "assistant":
                    text = m.get("content", "").strip()[:200]
                    if md5(text.encode()).hexdigest()[:8] == h:
                        print(f"    [{c}x] {text[:80]}...")
                        break
            else:
                continue
            break

print()
print("=" * 80)
print("DIAGNOSTIIKKA VALMIS")
print("=" * 80)
