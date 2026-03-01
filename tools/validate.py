#!/usr/bin/env python3
"""OpenClaw v1.4 — Validate all 50 agents"""
import yaml, os, sys

agents_dir = "agents"
total_q = 0
issues = []
count = 0

for d in sorted(os.listdir(agents_dir)):
    core_path = os.path.join(agents_dir, d, "core.yaml")
    src_path = os.path.join(agents_dir, d, "sources.yaml")
    if not os.path.exists(core_path):
        continue
    count += 1
    with open(core_path, encoding="utf-8") as f:
        c = yaml.safe_load(f)
    with open(src_path, encoding="utf-8") as f:
        s = yaml.safe_load(f)

    nq = len(c.get("eval_questions", []))
    total_q += nq
    nm = len(c.get("DECISION_METRICS_AND_THRESHOLDS", {}))
    ns = len(c.get("SEASONAL_RULES", []))
    nf = len(c.get("FAILURE_MODES", []))
    nsrc = len((s or {}).get("sources", []))

    flag = ""
    if nm < 5:  flag += f" METRICS:{nm}<5"
    if ns < 2:  flag += f" SEASONS:{ns}<2"
    if nf < 2:  flag += f" FAILURES:{nf}<2"
    if nq < 30: flag += f" QS:{nq}<30"
    if flag:
        issues.append(f"  ⚠️  {d}:{flag}")

    print(f"  {d:30s} M={nm:2d} S={ns} F={nf} Q={nq:2d} src={nsrc}{flag}")

print(f"\nYhteensä: {count} agenttia, {total_q} kysymystä")

if issues:
    print(f"\n{len(issues)} ongelmaa:")
    for i in issues:
        print(i)
    sys.exit(1)
else:
    print("✅ Kaikki agentit läpäisevät validoinnin")
    sys.exit(0)
