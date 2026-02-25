#!/usr/bin/env python3
"""
OpenClaw v1.4 — Schema Normalizer
Varmistaa että kaikilla 50 agentilla on identtinen avainjoukko.
Lisää puuttuvat: PROCESS_FLOWS, KNOWLEDGE_TABLES, SOURCE_REGISTRY
"""
import yaml, os
from pathlib import Path

AGENTS_DIR = Path("agents")
REQUIRED_KEYS = [
    "header", "ASSUMPTIONS", "DECISION_METRICS_AND_THRESHOLDS",
    "SEASONAL_RULES", "FAILURE_MODES", "PROCESS_FLOWS",
    "KNOWLEDGE_TABLES", "COMPLIANCE_AND_LEGAL", "UNCERTAINTY_NOTES",
    "SOURCE_REGISTRY", "eval_questions"
]

def generate_process_flows(agent_id, core):
    metrics = core.get("DECISION_METRICS_AND_THRESHOLDS", {})
    seasons = core.get("SEASONAL_RULES", [])
    failures = core.get("FAILURE_MODES", [])
    header = core.get("header", {})
    role = header.get("role", agent_id)
    sid = f"src:{agent_id[:4].upper()}"
    flows = []
    for k, v in list(metrics.items())[:1]:
        if isinstance(v, dict) and v.get("action"):
            flows.append({"flow_id": f"FLOW_{agent_id[:4].upper()}_01",
                "trigger": f"{k} ylittää kynnysarvon ({v.get('value', 'N/A')})",
                "action": v.get("action", "Tarkista tilanne"), "output": "Tilanneraportti", "source": sid})
    if len(seasons) >= 2:
        s = seasons[0]
        flows.append({"flow_id": f"FLOW_{agent_id[:4].upper()}_02",
            "trigger": f"Kausi vaihtuu: {s.get('season', 'kevät')}",
            "action": s.get("action", "Kausitoimenpiteet")[:100], "output": "Tarkistuslista", "source": sid})
    if failures:
        fm = failures[0]
        flows.append({"flow_id": f"FLOW_{agent_id[:4].upper()}_03",
            "trigger": f"Havaittu: {fm.get('mode', 'poikkeama')}",
            "action": fm.get("action", "Korjaava toimenpide"), "output": "Poikkeamaraportti", "source": sid})
    flows.append({"flow_id": f"FLOW_{agent_id[:4].upper()}_04",
        "trigger": "Säännöllinen heartbeat", "action": f"{role}: rutiiniarviointi",
        "output": "Status-raportti", "source": sid})
    return flows

def generate_knowledge_tables(agent_id, core):
    metrics = core.get("DECISION_METRICS_AND_THRESHOLDS", {})
    header = core.get("header", {})
    rows = []
    for k, v in list(metrics.items())[:6]:
        if isinstance(v, dict):
            rows.append({"metric": k, "value": str(v.get("value", "")), "action": str(v.get("action", ""))[:80]})
    if not rows: return []
    return [{"table_id": f"TBL_{agent_id[:4].upper()}_01",
        "title": f"{header.get('agent_name', agent_id)} — Kynnysarvot",
        "columns": ["metric", "value", "action"], "rows": rows,
        "source": f"src:{agent_id[:4].upper()}"}]

def main():
    print("═══ OpenClaw v1.4 Schema Normalizer ═══")
    stats = {"pf": 0, "kt": 0, "sr": 0, "total": 0}
    for d in sorted(os.listdir(str(AGENTS_DIR))):
        core_path = AGENTS_DIR / d / "core.yaml"
        if not core_path.exists(): continue
        with open(core_path, encoding="utf-8") as f:
            core = yaml.safe_load(f)
        changed = False
        if "PROCESS_FLOWS" not in core:
            core["PROCESS_FLOWS"] = generate_process_flows(d, core)
            stats["pf"] += 1; changed = True
        if "KNOWLEDGE_TABLES" not in core:
            core["KNOWLEDGE_TABLES"] = generate_knowledge_tables(d, core)
            stats["kt"] += 1; changed = True
        sources_path = AGENTS_DIR / d / "sources.yaml"
        if "SOURCE_REGISTRY" not in core and sources_path.exists():
            with open(sources_path, encoding="utf-8") as f:
                sources = yaml.safe_load(f)
            if sources:
                core["SOURCE_REGISTRY"] = sources
                stats["sr"] += 1; changed = True
        if changed:
            ordered = {}
            for k in REQUIRED_KEYS:
                if k in core: ordered[k] = core[k]
            for k in core:
                if k not in ordered: ordered[k] = core[k]
            with open(core_path, "w", encoding="utf-8") as f:
                yaml.dump(ordered, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        stats["total"] += 1
    print(f"  PROCESS_FLOWS lisätty: {stats['pf']}")
    print(f"  KNOWLEDGE_TABLES lisätty: {stats['kt']}")
    print(f"  SOURCE_REGISTRY mergattu: {stats['sr']}")
    print(f"  Agentit: {stats['total']}/50")
    # Verify
    variants = set()
    for d in sorted(os.listdir(str(AGENTS_DIR))):
        p = AGENTS_DIR / d / "core.yaml"
        if not p.exists(): continue
        with open(p) as f:
            c = yaml.safe_load(f)
        variants.add("|".join(sorted(c.keys())))
    print(f"  Schema-variaatioita: {len(variants)} (tavoite: 1)")
    if len(variants) == 1:
        print("✅ YHTENÄINEN SCHEMA")
    else:
        print("⚠️  Schema ei vielä yhtenäinen")

if __name__ == "__main__":
    main()
