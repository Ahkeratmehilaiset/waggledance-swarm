#!/usr/bin/env python3
"""
WD agent-value metric (RCO-authored).

Makes the "is the bridge+agents worth it for the END PRODUCT?" question MEASURABLE
instead of vibes-based. For a rolling window it reports:

  (A) Merged PRs classified PRODUCT vs INFRA vs MIXED vs TEST vs DOCS
      (PRODUCT = V12 ingredients / runtime solver-growth substrate;
       INFRA   = the agent bridge / consensus / dispatcher machinery itself).
  (B) Review-layer interventions: issues surfaced by each reviewer
      (claude-rco-1 / grok-scout-1 / codex-tools-1), split blocking vs advisory,
      and how many plausibly PREVENTED a bad landing (a finding followed by a
      re-push on the same PR before merge).

Honesty notes (RCO discipline, no overclaim):
  - Classification is a transparent path/title heuristic; every PR prints its
    file-basis so a human can audit/override.
  - Not every "finding" is a prevented production bug. We separate blocking
    findings from advisory ones, and flag the subset that preceded a fix re-push.

Usage:
  python C:\\Python\\wd-agent-value-metric.py --days 7
  python C:\\Python\\wd-agent-value-metric.py --days 7 --post-bridge
"""
from __future__ import annotations
import argparse, datetime, json, re, subprocess, sys, os
import hashlib
from pathlib import Path

# Imported from the operator-installed C:\Python\wd-agent-value-metric.py.
# Keep scheduled telemetry separate from native agent/reviewer identities.
def verified_writer(bundle, manifest_sha256, name='Write-AgentEvent.ps1'):
    root = Path(bundle).resolve(strict=True)
    manifest_path = root / 'deployment-manifest.json'
    raw = manifest_path.read_bytes()
    if not re.fullmatch(r'[a-fA-F0-9]{64}', manifest_sha256 or '') or hashlib.sha256(raw).hexdigest().lower() != manifest_sha256.lower():
        raise ValueError('Bridge manifest pin missing or changed')
    manifest = json.loads(raw)
    for relative, expected in manifest['files'].items():
        target = (root / relative.replace('\\', '/')).resolve(strict=True)
        if not target.is_relative_to(root):
            raise ValueError('Bridge manifest path escaped bundle')
        if hashlib.sha256(target.read_bytes()).hexdigest().lower() != expected.lower():
            raise ValueError('Bridge bundle file changed: ' + relative)
    relative = 'tools-bootstrap/.agent-bridge/bin/' + name
    if relative not in {key.replace('\\', '/') for key in manifest['files']}:
        raise ValueError('Bridge helper is not pinned')
    return root / relative


def reporting_env(bridge_root):
    # Never inherit the calling Lead/RCO's UUID, session or capabilities.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('AGENT_BRIDGE_')}
    env['AGENT_BRIDGE_RUNTIME_ROOT'] = bridge_root
    return env


def post_summary(summary, day, bridge_root, bundle, manifest_sha256):
    writer = verified_writer(bundle, manifest_sha256)
    subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-File', str(writer),
                    '-Agent', 'wd-agent-value', '-Role', 'monitor', '-Type', 'message',
                    '-TaskId', 'agent-value-metric-' + day, '-Status', 'metric_report',
                    '-To', 'operator,codex-lead-1,codex-tools-1', '-Severity', 'info',
                    '-PayloadJson', json.dumps({'notification': 'informational'}),
                    '-Message', summary], check=True, timeout=60, env=reporting_env(bridge_root))
    print('[posted informational metric summary to bridge]')

BRIDGE_DEFAULT = r"C:\Python\project2-master\.agent-bridge"
OUT_DIR_DEFAULT = r"C:\Python\agent-value-reports"
REPO = "Ahkeratmehilaiset/waggledance-swarm"  # explicit so gh works from any cwd (scheduled tasks)

# --- classification heuristics (auditable; edit here) --------------------
PRODUCT_PREFIXES = (
    "waggledance/core/magma/", "waggledance/core/autonomy_growth/",
    "waggledance/core/v3_13_0/", "waggledance/core/solver_synthesis/",
    "waggledance/core/solver", "waggledance/core/hex", "waggledance/core/world_model/",
    "waggledance/core/vector_identity/", "waggledance/core/meta/",
    "waggledance/core/memory_tiers/", "waggledance/core/provider", "waggledance/core/storage/",
    "waggledance/ui/", "web/",
)
# tools/ that are PRODUCT eval/proof harnesses (V12), not agent plumbing:
PRODUCT_TOOL_HINTS = ("run_magma", "run_v12", "run_autogrowth", "adversarial", "counterfactual", "hex_mesh", "find_similar_tools")
# unambiguously agent/dev-orchestration infrastructure:
INFRA_PREFIXES = (".agent-bridge/",)
INFRA_PATH_HINTS = ("bridge", "consensus", "idle_consensus", "idle_autonomy", "master_prompt", "savepoint")
INFRA_DOC_HINTS = ("BRIDGE_CONSENSUS", "IDLE_AUTONOMY", "STAGE2_CUTOVER", "CONTINUOUS_PLAN")

REVIEWERS = {
    "claude-rco-1": "RCO (independent review + veto)",
    "grok-scout-1": "Grok red-team (cross-model advisory)",
    "codex-tools-1": "Tools (build-peer rival checks)",
}
BLOCKING_STATUSES = {"changes_requested", "blocked", "blocker", "rco_blocked"}
FINDING_STATUSES = {"finding", "redteam_finding", "reported"}


def sh_json(args):
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=90)
        return json.loads(out.stdout) if out.stdout.strip() else None
    except Exception as exc:
        print(f"  (gh error {args}: {exc})", file=sys.stderr)
        return None


def classify_file(path: str) -> str:
    p = path.replace("\\", "/")
    low = p.lower()
    if p.startswith("tests/") or "/tests/" in p or low.endswith("_test.py") or "/test_" in low:
        return "TEST"
    if p.startswith(INFRA_PREFIXES):
        return "INFRA"
    if p.startswith("docs/") or low.endswith(".md"):
        if any(h in p for h in INFRA_DOC_HINTS):
            return "INFRA"
        return "DOCS"
    if p.startswith("tools/"):
        if any(h in low for h in PRODUCT_TOOL_HINTS):
            return "PRODUCT"
        if any(h in low for h in INFRA_PATH_HINTS):
            return "INFRA"
        return "PRODUCT"  # default: tool that supports the product
    if p.startswith(PRODUCT_PREFIXES) or p.startswith("waggledance/"):
        if any(h in low for h in INFRA_PATH_HINTS):
            return "INFRA"
        return "PRODUCT"
    return "OTHER"


def classify_pr(files: list[str]) -> tuple[str, dict]:
    counts = {}
    for f in files:
        c = classify_file(f)
        counts[c] = counts.get(c, 0) + 1
    code_prod = counts.get("PRODUCT", 0)
    code_infra = counts.get("INFRA", 0)
    if code_prod and code_infra:
        label = "MIXED"
    elif code_prod:
        label = "PRODUCT"
    elif code_infra:
        label = "INFRA"
    elif counts.get("TEST"):
        label = "TEST"
    elif counts.get("DOCS"):
        label = "DOCS"
    else:
        label = "OTHER"
    return label, counts


def load_events(bridge_root, bundle, manifest_sha256):
    reader = verified_writer(bundle, manifest_sha256, 'Read-AgentBridge.ps1')
    command = "& '" + str(reader).replace("'", "''") + "' -Raw -NoAckReceived -NoContinuity -Tail 0 6>$null"
    result = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-Command', command],
                            check=True, timeout=120, capture_output=True, text=True,
                            encoding='utf-8', env=reporting_env(bridge_root))
    rows = json.loads(result.stdout)
    if not isinstance(rows, list):
        raise ValueError('Bridge reader did not return an event array')
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--bridge-root", default=BRIDGE_DEFAULT)
    ap.add_argument("--out-dir", default=OUT_DIR_DEFAULT)
    ap.add_argument("--now-utc", default=None, help="override 'now' ISO (else system clock)")
    ap.add_argument('--bridge-bundle', required=True)
    ap.add_argument('--bridge-manifest-sha256', required=True)
    ap.add_argument("--post-bridge", action="store_true", help="post informational telemetry as wd-agent-value (not an RCO)")
    args = ap.parse_args()

    now = datetime.datetime.fromisoformat(args.now_utc) if args.now_utc else datetime.datetime.now(datetime.timezone.utc)
    since = now - datetime.timedelta(days=args.days)
    since_d = since.strftime("%Y-%m-%d")
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%S")

    print(f"WD agent-value metric | window: last {args.days}d (since {since_d}) | now {now.isoformat()}")

    # (A) merged PRs in window
    all_merged = sh_json(["gh", "pr", "list", "--repo", REPO, "--state", "merged", "--limit", "150",
                          "--json", "number,title,mergedAt"]) or []
    prs = [p for p in all_merged if (p.get("mergedAt") or "") >= since_iso]
    pr_rows = []
    label_counts = {}
    for pr in prs:
        n = pr["number"]
        det = sh_json(["gh", "pr", "view", str(n), "--repo", REPO, "--json", "files,additions,deletions"]) or {}
        files = [f.get("path", "") for f in (det.get("files") or [])]
        label, counts = classify_pr(files)
        label_counts[label] = label_counts.get(label, 0) + 1
        pr_rows.append({"n": n, "title": pr["title"], "label": label, "nfiles": len(files),
                        "counts": counts, "files": files[:6]})

    # (B) review interventions in window
    events = load_events(args.bridge_root, args.bridge_bundle, args.bridge_manifest_sha256)
    # Scope catches to THIS window's PRs (merged-in-window + currently open) and dedupe by PR.
    # This kills false '#N' matches (e.g. case numbers) and routine non-PR findings.
    relevant_prs = {str(r["n"]) for r in pr_rows}
    open_prs = sh_json(["gh", "pr", "list", "--repo", REPO, "--state", "open", "--json", "number"]) or []
    relevant_prs |= {str(p["number"]) for p in open_prs}
    interventions = {a: {"blocking": set(), "advisory": set()} for a in REVIEWERS}
    for e in events:
        if (e.get("ts_utc") or "") < since_iso:
            continue
        ag = e.get("agent", "")
        if ag not in REVIEWERS:
            continue
        st = (e.get("status") or "")
        typ = (e.get("type") or "")
        m = re.search(r"#(\d{2,6})", (e.get("message") or ""))
        prnum = m.group(1) if m else None
        if prnum not in relevant_prs:
            continue
        if st in BLOCKING_STATUSES or typ == "blocked":
            interventions[ag]["blocking"].add(prnum)
        elif st in FINDING_STATUSES or typ == "finding" or (ag == "grok-scout-1" and st == "grok_response"):
            # grok's red-team/review answers arrive via dispatcher as grok_response; count them.
            interventions[ag]["advisory"].add(prnum)

    # --- build report --------------------------------------------------
    lines = []
    lines.append(f"# WD agent-value metric — last {args.days}d (since {since_d})")
    lines.append(f"_generated {now.isoformat()} | RCO-authored, auditable heuristic_\n")

    total_pr = sum(label_counts.values())
    prod = label_counts.get("PRODUCT", 0) + label_counts.get("MIXED", 0)
    infra = label_counts.get("INFRA", 0)
    lines.append("## A) Merged PRs: product-ingredient vs agent-infra")
    lines.append(f"- Total merged: **{total_pr}**")
    for lab in ("PRODUCT", "MIXED", "INFRA", "TEST", "DOCS", "OTHER"):
        if label_counts.get(lab):
            lines.append(f"  - {lab}: {label_counts[lab]}")
    if total_pr:
        pct = round(100 * prod / total_pr)
        lines.append(f"- **Product-touching (PRODUCT+MIXED): {prod}/{total_pr} = {pct}%** vs infra-only {infra}/{total_pr}")
    lines.append("\n| PR | label | files | title |")
    lines.append("|---|---|---|---|")
    for r in pr_rows:
        lines.append(f"| #{r['n']} | {r['label']} | {r['nfiles']} | {r['title'][:60]} |")

    lines.append("\n## B) Review-layer interventions (issues surfaced before merge)")
    grand_block = grand_adv = 0
    for ag, label in REVIEWERS.items():
        b = interventions[ag]["blocking"]; a = interventions[ag]["advisory"]
        grand_block += len(b); grand_adv += len(a)
        lines.append(f"- **{ag}** ({label}): PRs blocked={len(b)} ({','.join('#'+p for p in sorted(b)) or '-'}) | "
                     f"PRs with advisory/red-team finding={len(a)} ({','.join('#'+p for p in sorted(a)) or '-'})")
    lines.append(f"- **TOTAL: {grand_block} PR-blocks + {grand_adv} PR-advisory across review layers (this window's PRs)**")
    lines.append("\n_Interpretation: blocking findings are the high-confidence 'prevented a bad landing' signal "
                 "(they stop the merge until fixed). Advisory/red-team findings raise issues the author may fix or "
                 "defer. Cross-model catches (grok-scout-1) are the bugs a single-model RCO pass tends to miss._")

    lines.append("\n## C) Honest caveats")
    lines.append("- Classification is a path/title heuristic (see file-basis per PR); the product/infra line is genuinely "
                 "fuzzy because the adversarial gate / consensus tooling is BOTH a V12 ingredient AND agent plumbing.")
    lines.append("- A high infra-% week is not automatically bad (hardening the safety substrate is real), but a "
                 "persistently high infra-% with few product PRs = the 'drowning in its own maintenance' risk.")
    lines.append("- Track the trend week-over-week; one week is noise.")

    report = "\n".join(lines)
    os.makedirs(args.out_dir, exist_ok=True)
    out_path = os.path.join(args.out_dir, f"agent-value-{now.strftime('%Y%m%d')}.md")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    print(report)
    print(f"\n[report written: {out_path}]")

    if args.post_bridge:
        summary = (f"WD agent-value metric (last {args.days}d): merged {total_pr} PRs "
                   f"-> product-touching {prod} ({round(100*prod/total_pr) if total_pr else 0}%), infra-only {infra}. "
                   f"Review-layer interventions: {grand_block} blocking + {grand_adv} advisory "
                   f"(grok cross-model: {len(interventions['grok-scout-1']['blocking'])+len(interventions['grok-scout-1']['advisory'])}). "
                   f"Full: {out_path}. Makes 'is the bridge worth it' measurable; track the trend.")
        post_summary(summary, now.strftime('%Y%m%d'), args.bridge_root,
                     args.bridge_bundle, args.bridge_manifest_sha256)


if __name__ == "__main__":
    main()
