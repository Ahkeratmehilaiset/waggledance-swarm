"""Mass chat test — send ALL YAML eval_questions to API, analyze routing accuracy.

Loads eval_questions from all 50 agents, sends to /api/chat,
checks if the response comes from the correct agent or contains the right answer.
Outputs detailed failure analysis for code improvement.

Uses concurrent requests (10 workers) for speed.
"""
import json
import os
import re
import sys
import time
import yaml
import requests
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# Fix Windows console encoding for Finnish/special chars
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

API_URL = "http://localhost:8000/api/chat"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def fix_double_utf8(text):
    """Fix double-encoded UTF-8."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


def fix_yaml_strings(obj):
    """Recursively fix double-encoded UTF-8 in YAML data."""
    if isinstance(obj, str):
        return fix_double_utf8(obj)
    elif isinstance(obj, dict):
        return {fix_double_utf8(k) if isinstance(k, str) else k: fix_yaml_strings(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [fix_yaml_strings(item) for item in obj]
    return obj


def resolve_ref(data, ref):
    """Resolve a dot-path reference in YAML data."""
    parts = re.split(r"\.", ref)
    current = data
    for part in parts:
        if current is None:
            return None
        m = re.match(r"^(.+)\[(\d+)\]$", part)
        if m:
            key, idx = m.group(1), int(m.group(2))
            current = current.get(key) if isinstance(current, dict) else None
            if isinstance(current, list) and idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
    return current


def format_answer_key(resolved):
    """Extract key content words from the resolved YAML value for matching."""
    if resolved is None:
        return []
    if isinstance(resolved, str):
        return [w.lower() for w in resolved.split() if len(w) >= 3][:8]
    elif isinstance(resolved, (int, float)):
        return [str(resolved)]
    elif isinstance(resolved, dict):
        words = []
        for key in ("value", "action", "detection", "rule"):
            if key in resolved and resolved[key]:
                words.extend(w.lower() for w in str(resolved[key]).split() if len(w) >= 3)
        return words[:8]
    elif isinstance(resolved, list):
        words = []
        for item in resolved[:3]:
            words.extend(w.lower() for w in str(item).split() if len(w) >= 3)
        return words[:8]
    return []


def load_all_eval_questions():
    """Load all eval_questions from YAML files."""
    qa_pairs = []
    seen = set()
    skip_prefixes = (
        "Operatiivinen", "Kytkentä muihin", "Miten tämä agentti",
        "Operatiivinen päätöskysymys", "Operatiivinen lisäkysymys",
    )
    # Generic questions identical across agents — not useful for routing tests
    skip_exact = {
        "Epävarmuudet?", "Oletukset?",
        "Kausiohje (Kevät)?", "Kausiohje (Kesä)?",
        "Kausiohje (Syksy)?", "Kausiohje (Talvi)?",
        # Generic seasonal questions identical across 10+ agents
        "Mitä kevät huomioidaan?", "Mitä kesä huomioidaan?",
        "Mitä syksy huomioidaan?", "Mitä talvi huomioidaan?",
        "Mitä keväällä huomioidaan?", "Mitä kesällä huomioidaan?",
        "Mitä syksyllä huomioidaan?", "Mitä talvella huomioidaan?",
        "Mitkä ovat merkittävimmät epävarmuudet?",
    }

    for base_dir_name in ["knowledge", "agents"]:
        base_dir = PROJECT_ROOT / base_dir_name
        if not base_dir.exists():
            continue
        for agent_dir in sorted(base_dir.iterdir()):
            if not agent_dir.is_dir():
                continue
            core_yaml = agent_dir / "core.yaml"
            if not core_yaml.exists():
                continue
            try:
                with open(core_yaml, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if not data or not isinstance(data, dict):
                    continue
                data = fix_yaml_strings(data)
                header = data.get("header", {})
                agent_name = header.get("agent_name", agent_dir.name)
                agent_id = header.get("agent_id", agent_dir.name)

                for eq in data.get("eval_questions", []):
                    if not isinstance(eq, dict):
                        continue
                    q = eq.get("q", "").strip()
                    a_ref = eq.get("a_ref", "").strip()
                    if not q or not a_ref:
                        continue
                    if any(q.startswith(sp) for sp in skip_prefixes):
                        continue
                    if q in skip_exact:
                        continue
                    key = f"{agent_id}|{q}"
                    if key in seen:
                        continue
                    seen.add(key)

                    resolved = resolve_ref(data, a_ref)
                    answer_keys = format_answer_key(resolved)
                    qa_pairs.append({
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "question": q,
                        "a_ref": a_ref,
                        "answer_keys": answer_keys,
                        "resolved_raw": str(resolved)[:200] if resolved else None,
                    })
            except Exception as e:
                print(f"  ERROR loading {core_yaml}: {e}", file=sys.stderr)

    return qa_pairs


def send_query(question):
    """Send a question to the chat API."""
    try:
        r = requests.post(API_URL, json={"message": question}, timeout=10)
        return r.json().get("response", "")
    except Exception as e:
        return f"ERROR: {e}"


def check_response(qa, response):
    """Check if the response is from the correct agent and contains relevant info."""
    resp_lower = response.lower()
    agent_name = qa["agent_name"]

    # Check 1: Agent attribution (response starts with [AgentName])
    correct_agent = f"[{agent_name}]".lower() in resp_lower[:80]

    # Check 2: Content match — do answer key words appear in response?
    if qa["answer_keys"]:
        matched_keys = sum(1 for k in qa["answer_keys"] if k in resp_lower)
        content_score = matched_keys / len(qa["answer_keys"]) if qa["answer_keys"] else 0
    else:
        content_score = 0.0

    # Check 3: Is it a fallback response?
    is_fallback = any(f in resp_lower for f in [
        "stub-tilassa", "hivemind", "en löytänyt", "menee yli stub",
    ])

    # Check 4: Which agent actually responded?
    actual_agent = None
    m = re.match(r"^\[([^\]]+)\]", response)
    if m:
        actual_agent = m.group(1)

    return {
        "correct_agent": correct_agent,
        "content_score": content_score,
        "is_fallback": is_fallback,
        "actual_agent": actual_agent,
        "response_len": len(response),
    }


def run_mass_test():
    """Run the mass test and generate analysis."""
    print("Loading YAML eval_questions...")
    qa_pairs = load_all_eval_questions()
    print(f"Loaded {len(qa_pairs)} questions from {len(set(q['agent_id'] for q in qa_pairs))} agents\n")

    results = []
    agent_stats = defaultdict(lambda: {"total": 0, "correct_agent": 0, "content_match": 0, "fallback": 0, "wrong_agent": 0})

    print(f"Sending {len(qa_pairs)} queries to API (10 concurrent workers)...\n")
    start = time.time()
    done_count = 0

    def process_one(idx_qa):
        idx, qa = idx_qa
        response = send_query(qa["question"])
        check = check_response(qa, response)
        return idx, {**qa, **check, "response": response[:200]}

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(process_one, (i, qa)): i for i, qa in enumerate(qa_pairs)}
        indexed_results = [None] * len(qa_pairs)
        for future in as_completed(futures):
            idx, result = future.result()
            indexed_results[idx] = result
            done_count += 1
            if done_count % 100 == 0:
                elapsed = time.time() - start
                print(f"  {done_count}/{len(qa_pairs)} done ({elapsed:.1f}s)")

    for result in indexed_results:
        results.append(result)
        stats = agent_stats[result["agent_id"]]
        stats["total"] += 1
        if result["correct_agent"]:
            stats["correct_agent"] += 1
        if result["content_score"] >= 0.3:
            stats["content_match"] += 1
        if result["is_fallback"]:
            stats["fallback"] += 1
        if result["actual_agent"] and not result["correct_agent"] and not result["is_fallback"]:
            stats["wrong_agent"] += 1

    elapsed = time.time() - start
    print(f"\nCompleted {len(qa_pairs)} queries in {elapsed:.1f}s ({len(qa_pairs)/elapsed:.0f} q/s)\n")

    # === ANALYSIS ===
    total = len(results)
    correct_agent_count = sum(1 for r in results if r["correct_agent"])
    content_match_count = sum(1 for r in results if r["content_score"] >= 0.3)
    fallback_count = sum(1 for r in results if r["is_fallback"])
    wrong_agent_count = sum(1 for r in results if r["actual_agent"] and not r["correct_agent"] and not r["is_fallback"])

    print("=" * 70)
    print("OVERALL RESULTS")
    print("=" * 70)
    print(f"Total questions:     {total}")
    print(f"Correct agent:       {correct_agent_count} ({100*correct_agent_count/total:.1f}%)")
    print(f"Content match (>=30%): {content_match_count} ({100*content_match_count/total:.1f}%)")
    print(f"Wrong agent:         {wrong_agent_count} ({100*wrong_agent_count/total:.1f}%)")
    print(f"Fallback (no match): {fallback_count} ({100*fallback_count/total:.1f}%)")
    print()

    # Per-agent breakdown
    print("=" * 70)
    print("PER-AGENT ACCURACY (sorted by accuracy)")
    print("=" * 70)
    sorted_agents = sorted(agent_stats.items(), key=lambda x: x[1]["correct_agent"]/max(x[1]["total"],1))
    for agent_id, stats in sorted_agents:
        pct = 100 * stats["correct_agent"] / max(stats["total"], 1)
        content_pct = 100 * stats["content_match"] / max(stats["total"], 1)
        print(f"  {agent_id:30s} {stats['correct_agent']:3d}/{stats['total']:3d} agent ({pct:5.1f}%)  content:{content_pct:5.1f}%  fallback:{stats['fallback']}  wrong:{stats['wrong_agent']}")

    # Failure details — wrong agent responses
    print()
    print("=" * 70)
    print("WRONG AGENT RESPONSES (top 50)")
    print("=" * 70)
    wrong = [r for r in results if r["actual_agent"] and not r["correct_agent"] and not r["is_fallback"]]
    for r in wrong[:50]:
        print(f"  Q: {r['question'][:80]}")
        print(f"    Expected: {r['agent_name']}  Got: {r['actual_agent']}")
        print(f"    Response: {r['response'][:120]}")
        print()

    # Fallback responses
    print("=" * 70)
    print(f"FALLBACK RESPONSES ({fallback_count} total, showing top 30)")
    print("=" * 70)
    fallbacks = [r for r in results if r["is_fallback"]]
    for r in fallbacks[:30]:
        print(f"  [{r['agent_id']}] Q: {r['question'][:80]}")
        print(f"    answer_keys: {r['answer_keys'][:5]}")
        print()

    # Layer 1 hijack analysis — responses that DON'T start with [Agent]
    print("=" * 70)
    print("LAYER 1 HIJACK (no [Agent] prefix, not fallback)")
    print("=" * 70)
    layer1 = [r for r in results if not r["actual_agent"] and not r["is_fallback"]]
    agent_hijack = defaultdict(int)
    for r in layer1:
        agent_hijack[r["agent_id"]] += 1
    for agent_id, count in sorted(agent_hijack.items(), key=lambda x: -x[1]):
        print(f"  {agent_id:30s} {count} queries hijacked by Layer 1")
    print(f"\n  Total Layer 1 hijacks: {len(layer1)}")

    # Save detailed results to JSON
    output_path = PROJECT_ROOT / "data" / "mass_test_results.json"
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "total": total,
            "correct_agent_pct": round(100 * correct_agent_count / total, 1),
            "content_match_pct": round(100 * content_match_count / total, 1),
            "fallback_pct": round(100 * fallback_count / total, 1),
            "wrong_agent_pct": round(100 * wrong_agent_count / total, 1),
            "layer1_hijack_count": len(layer1),
            "per_agent": {k: v for k, v in agent_stats.items()},
            "failures": [
                {"agent": r["agent_id"], "q": r["question"], "expected": r["agent_name"],
                 "got": r["actual_agent"], "response": r["response"][:200]}
                for r in results if not r["correct_agent"]
            ],
        }, f, ensure_ascii=False, indent=2)
    print(f"\nDetailed results saved to {output_path}")

    # --- POST wrong-agent failures to /api/confusion ---
    confusion_url = "http://localhost:8000/api/confusion"
    wrong_agent_failures = [
        r for r in results
        if r.get("actual_agent") and not r["correct_agent"] and not r["is_fallback"]
    ]
    if wrong_agent_failures:
        print(f"\nReporting {len(wrong_agent_failures)} wrong-agent failures to /api/confusion ...")
        reported = 0
        for r in wrong_agent_failures:
            try:
                requests.post(confusion_url, json={
                    "question": r["question"],
                    "wrong_agent": r["actual_agent"],
                    "correct_agent": r["agent_name"],
                }, timeout=5)
                reported += 1
            except Exception as e:
                # Server might not be running or endpoint missing — that's OK
                if reported == 0:
                    print(f"  Could not reach {confusion_url}: {e}")
                    print("  (Skipping remaining confusion reports)")
                break
        if reported:
            print(f"  Reported {reported} confusion entries to API")

    return results, agent_stats


if __name__ == "__main__":
    run_mass_test()
