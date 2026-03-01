#!/usr/bin/env python3
"""
OpenClaw v1.4 — STRICT Validator
Tarkistaa AGENT_AUTHORING_RULES.md mukaiset vaatimukset.
Exit code 0 = OK, 1 = virheitä, 2 = varoituksia
"""
import yaml, os, sys, re

agents_dir = "agents"
total_q = 0
errors = []
warnings = []
count = 0

# Kielletyt ilmaisut (AGENT_AUTHORING_RULES.md kohta 2)
BANNED_PHRASES = [
    "seuraa tilannetta",
    "tarkista tarvittaessa",
    "huolehdi asianmukaisesti",
    "seuraa säännöllisesti",
]

def has_number(s):
    """Tarkista sisältääkö merkkijono numeron"""
    return bool(re.search(r'\d', str(s)))

def check_banned(text, agent_id, field):
    """Tarkista kielletyt ilmaisut"""
    for phrase in BANNED_PHRASES:
        if phrase.lower() in str(text).lower():
            warnings.append(f"  ⚠️  {agent_id}/{field}: kielletty ilmaisu '{phrase}'")

print("═══ OpenClaw v1.4 STRICT Validator ═══\n")

for d in sorted(os.listdir(agents_dir)):
    core_path = os.path.join(agents_dir, d, "core.yaml")
    src_path = os.path.join(agents_dir, d, "sources.yaml")
    if not os.path.exists(core_path):
        continue
    count += 1

    with open(core_path, encoding="utf-8") as f:
        c = yaml.safe_load(f)

    src_exists = os.path.exists(src_path)
    if not src_exists:
        errors.append(f"  🔴 {d}: sources.yaml PUUTTUU")

    # ═══ METRICS ═══
    metrics = c.get("DECISION_METRICS_AND_THRESHOLDS", {})
    nm = len(metrics)
    
    # Count metrics with action field
    action_count = 0
    numeric_count = 0
    for k, v in metrics.items():
        if isinstance(v, dict):
            if "action" in v:
                action_count += 1
            val = str(v.get("value", ""))
            if has_number(val):
                numeric_count += 1
            # Check for banned phrases
            check_banned(v.get("action", ""), d, f"metrics.{k}.action")
            check_banned(val, d, f"metrics.{k}.value")
        elif isinstance(v, (int, float)):
            numeric_count += 1

    if nm < 5:
        errors.append(f"  🔴 {d}: METRICS {nm} < 5")
    if action_count < 3:
        errors.append(f"  🔴 {d}: ACTION-METRIIKAT {action_count} < 3")
    if numeric_count < 3:
        errors.append(f"  🔴 {d}: NUMEERISET METRIIKAT {numeric_count} < 3")

    # ═══ SEASONAL RULES ═══
    seasons = c.get("SEASONAL_RULES", [])
    ns = len(seasons)
    specific_seasons = 0
    for s in seasons:
        act = s.get("action", s.get("focus", ""))
        check_banned(act, d, "SEASONAL_RULES")
        # Spesifinen = sisältää numeron tai viikko/kuukausi-viittauksen
        if has_number(str(act)) or any(w in str(act).lower() for w in ["vko", "viikko", "kuukausi", "°c", "kg", "cm", "mm"]):
            specific_seasons += 1

    if ns < 4:
        errors.append(f"  🔴 {d}: KAUSIA {ns} < 4")
    if specific_seasons < 2:
        warnings.append(f"  ⚠️  {d}: vain {specific_seasons}/4 kautta on spesifisiä (vaatimus ≥2)")

    # ═══ FAILURE MODES ═══
    failures = c.get("FAILURE_MODES", [])
    nf = len(failures)
    for fm in failures:
        if "detection" not in fm:
            errors.append(f"  🔴 {d}: FAILURE_MODE '{fm.get('mode','')}' puuttuu detection")
        if "action" not in fm:
            errors.append(f"  🔴 {d}: FAILURE_MODE '{fm.get('mode','')}' puuttuu action")
        check_banned(fm.get("action", ""), d, "FAILURE_MODES")

    if nf < 2:
        errors.append(f"  🔴 {d}: FAILURE_MODES {nf} < 2")

    # ═══ EVAL QUESTIONS ═══
    nq = len(c.get("eval_questions", []))
    total_q += nq
    if nq < 30:
        errors.append(f"  🔴 {d}: KYSYMYKSIÄ {nq} < 30")

    # ═══ SOURCE REFERENCES ═══
    # Check that metrics reference sources
    missing_src = 0
    for k, v in metrics.items():
        if isinstance(v, dict) and "source" not in v:
            missing_src += 1
    if missing_src > 0:
        warnings.append(f"  ⚠️  {d}: {missing_src} metriikkaa ilman source-viitettä")

    # Summary line
    flag = ""
    if nm < 5: flag += " M!"
    if action_count < 3: flag += " A!"
    if numeric_count < 3: flag += " N!"
    if ns < 4: flag += " S!"
    if nf < 2: flag += " F!"
    if nq < 30: flag += " Q!"
    
    status = "✅" if not flag else "❌"
    print(f"  {status} {d:30s} M={nm:2d} A={action_count} N={numeric_count} S={ns} F={nf} Q={nq:2d}{flag}")

# ═══ SUMMARY ═══
print(f"\n{'═'*60}")
print(f"Agentit: {count}/50 | Kysymykset: {total_q}")
print(f"Virheet (🔴): {len(errors)} | Varoitukset (⚠️): {len(warnings)}")

if errors:
    print(f"\n🔴 VIRHEET ({len(errors)}):")
    for e in errors:
        print(e)

if warnings:
    print(f"\n⚠️  VAROITUKSET ({len(warnings)}):")
    for w in warnings:
        print(w)

if not errors and not warnings:
    print("\n✅ KAIKKI AGENTIT LÄPÄISEVÄT STRICT-VALIDOINNIN")
    sys.exit(0)
elif errors:
    print(f"\n❌ {len(errors)} virhettä — korjaa ennen käyttöönottoa")
    sys.exit(1)
else:
    print(f"\n⚠️  {len(warnings)} varoitusta — tarkista manuaalisesti")
    sys.exit(0)
