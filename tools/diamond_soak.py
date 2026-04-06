#!/usr/bin/env python3
"""Diamond Run P5 — Honest Soak Test.

Each cycle:
1. Solver query (math)
2. Normal LLM query (short, no escalation)
3. Orchestrator round table (complex query, triggers escalation)
4. Status + ops snapshot

Runs continuously for target_hours or until stopped.
Writes NDJSON metrics to soak/ directory.

Usage:
    python tools/diamond_soak.py --api-key KEY [--hours 4] [--cycle-pause 30]
"""

import argparse
import json
import os
import sys
import time
import datetime
import traceback

try:
    import urllib.request
    import urllib.error
except ImportError:
    pass

BASE = "http://localhost:8000"


def http_post(path, data, headers, timeout=120):
    """Simple sync HTTP POST."""
    url = f"{BASE}{path}"
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"error": body}, e.code
    except Exception as e:
        return {"error": str(e)}, 0


def http_get(path, timeout=10):
    """Simple sync HTTP GET."""
    url = f"{BASE}{path}"
    try:
        resp = urllib.request.urlopen(url, timeout=timeout)
        return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode("utf-8", errors="replace")}, e.code
    except Exception as e:
        return {"error": str(e)}, 0


def run_cycle(cycle_num, headers, soak_dir):
    """Run one soak cycle. Returns metrics dict."""
    ts = datetime.datetime.now().isoformat()
    metrics = {
        "cycle": cycle_num,
        "timestamp": ts,
        "queries": {},
        "status_code": {},
        "errors": [],
    }

    # 1. Solver query (math)
    t0 = time.monotonic()
    data, code = http_post("/api/chat",
        {"query": f"What is 17 * {23 + cycle_num}", "profile": "HOME"},
        headers)
    elapsed = (time.monotonic() - t0) * 1000
    metrics["queries"]["solver"] = {
        "wall_ms": round(elapsed, 1),
        "status": code,
        "source": data.get("source", "?"),
        "response_len": len(data.get("response", "")),
    }
    metrics["status_code"]["solver"] = code
    if code != 200:
        metrics["errors"].append(f"solver: HTTP {code}")

    # 2. Normal LLM query (short, no escalation)
    t0 = time.monotonic()
    short_queries = [
        "Tell about bees", "What is honey", "How is wax made",
        "Define pollen", "Bee anatomy", "Hive facts",
        "Queen role", "Drone purpose", "Worker tasks",
    ]
    q = short_queries[cycle_num % len(short_queries)]
    data, code = http_post("/api/chat",
        {"query": q, "profile": "HOME"}, headers)
    elapsed = (time.monotonic() - t0) * 1000
    metrics["queries"]["llm_short"] = {
        "wall_ms": round(elapsed, 1),
        "status": code,
        "source": data.get("source", "?"),
        "round_table": data.get("round_table", False),
    }
    metrics["status_code"]["llm_short"] = code
    if code != 200:
        metrics["errors"].append(f"llm_short: HTTP {code}")

    # 3. Complex query (triggers round table escalation + parallel batch)
    complex_queries = [
        "Explain the relationship between soil microbiome diversity and crop yield in Nordic boreal conditions",
        "How do temperature fluctuations affect Varroa mite reproductive cycles in managed beehives",
        "Describe the mechanisms by which neonicotinoid pesticides impact pollinator navigation and foraging",
        "What is the ecological significance of mycorrhizal networks for forest tree communication",
        "Analyze the effects of light pollution on nocturnal pollinator behavior in urban environments",
        "How does climate-driven phenological mismatch affect plant-pollinator mutualism timing",
    ]
    q = complex_queries[cycle_num % len(complex_queries)]
    # Append cycle number to avoid hot cache
    q_unique = f"{q} (cycle {cycle_num})"
    t0 = time.monotonic()
    data, code = http_post("/api/chat",
        {"query": q_unique, "profile": "HOME"}, headers)
    elapsed = (time.monotonic() - t0) * 1000
    metrics["queries"]["round_table"] = {
        "wall_ms": round(elapsed, 1),
        "status": code,
        "source": data.get("source", "?"),
        "round_table": data.get("round_table", False),
        "confidence": data.get("confidence", 0),
        "response_len": len(data.get("response", "")),
    }
    metrics["status_code"]["round_table"] = code
    if code != 200:
        metrics["errors"].append(f"round_table: HTTP {code}")

    # 4. Status + ops snapshot
    status_data, status_code = http_get("/api/status")
    ops_data, ops_code = http_get("/api/ops")
    metrics["status_code"]["status"] = status_code
    metrics["status_code"]["ops"] = ops_code

    if status_code == 200:
        par = status_data.get("llm_parallel", {})
        metrics["parallel_metrics"] = {
            "completed_parallel_batches": par.get("completed_parallel_batches", 0),
            "total_dispatched": par.get("total_dispatched", 0),
            "total_completed": par.get("total_completed", 0),
            "timeout_count": par.get("timeout_count", 0),
            "cancelled_count": par.get("cancelled_count", 0),
            "deduped_requests": par.get("deduped_requests", 0),
            "degrade_to_sequential_count": par.get("degrade_to_sequential_count", 0),
            "inflight_total": par.get("inflight_total", 0),
        }
    else:
        metrics["errors"].append(f"status: HTTP {status_code}")

    metrics["error_count"] = len(metrics["errors"])
    return metrics


def main():
    parser = argparse.ArgumentParser(description="Diamond Soak Test")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--hours", type=float, default=4.0)
    parser.add_argument("--cycle-pause", type=int, default=30,
                        help="Seconds between cycles")
    parser.add_argument("--soak-dir", default="C:/WaggleDance_DiamondRun/20260406_023547/soak")
    args = parser.parse_args()

    os.makedirs(args.soak_dir, exist_ok=True)
    ndjson_path = os.path.join(args.soak_dir, "soak_metrics.ndjson")
    summary_path = os.path.join(args.soak_dir, "soak_summary.json")

    headers = {"Authorization": f"Bearer {args.api_key}"}
    target_seconds = args.hours * 3600
    start_time = time.monotonic()
    start_ts = datetime.datetime.now().isoformat()

    print(f"Soak started: {start_ts}")
    print(f"Target: {args.hours}h ({target_seconds:.0f}s)")
    print(f"Cycle pause: {args.cycle_pause}s")
    print(f"Output: {ndjson_path}")

    # Verify WD is up
    status, code = http_get("/api/status")
    if code != 200:
        print(f"ERROR: WD not reachable (HTTP {code})")
        sys.exit(1)
    print(f"WD status: {status.get('status', '?')}")
    print(f"Parallel: {status.get('llm_parallel', {}).get('enabled', '?')}")

    cycle = 0
    total_errors = 0
    total_queries = 0
    status_5xx = 0
    restarts = 0
    batch_path_fired = False

    try:
        while (time.monotonic() - start_time) < target_seconds:
            cycle += 1
            elapsed_h = (time.monotonic() - start_time) / 3600

            try:
                metrics = run_cycle(cycle, headers, args.soak_dir)
            except Exception as e:
                metrics = {
                    "cycle": cycle,
                    "timestamp": datetime.datetime.now().isoformat(),
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                    "error_count": 1,
                }
                total_errors += 1

            # Write NDJSON line
            with open(ndjson_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(metrics) + "\n")

            # Tally
            total_queries += 3  # solver + llm_short + round_table
            err_count = metrics.get("error_count", 0)
            total_errors += err_count

            for key, code in metrics.get("status_code", {}).items():
                if code >= 500:
                    status_5xx += 1

            par = metrics.get("parallel_metrics", {})
            if par.get("completed_parallel_batches", 0) > 0:
                batch_path_fired = True

            rt_info = metrics.get("queries", {}).get("round_table", {})
            rt_used = rt_info.get("round_table", False)

            print(f"  Cycle {cycle} [{elapsed_h:.1f}h]: "
                  f"errs={err_count}, "
                  f"rt={rt_used}, "
                  f"batches={par.get('completed_parallel_batches', '?')}, "
                  f"dispatched={par.get('total_dispatched', '?')}")

            # Check if WD is still alive every cycle
            _, s_code = http_get("/api/status")
            if s_code != 200:
                print(f"  WARNING: WD status check failed (HTTP {s_code})")
                restarts += 1
                # Wait and retry
                time.sleep(10)
                _, s_code = http_get("/api/status")
                if s_code != 200:
                    print(f"  ERROR: WD appears dead after retry")
                    break

            time.sleep(args.cycle_pause)

    except KeyboardInterrupt:
        print("\nSoak interrupted by user (Ctrl+C)")

    end_ts = datetime.datetime.now().isoformat()
    elapsed_total = time.monotonic() - start_time
    elapsed_h = elapsed_total / 3600

    # Determine soak type
    if elapsed_h >= args.hours * 0.95:
        soak_type = f"continuous {args.hours:.0f}h soak"
    else:
        soak_type = f"aggregate clean cycles ({elapsed_h:.1f}h of {args.hours:.0f}h target)"

    # Final status check
    final_status, _ = http_get("/api/status")
    final_par = final_status.get("llm_parallel", {})

    summary = {
        "run_id": "20260406_023547",
        "started": start_ts,
        "ended": end_ts,
        "elapsed_hours": round(elapsed_h, 2),
        "target_hours": args.hours,
        "soak_type": soak_type,
        "total_cycles": cycle,
        "total_queries": total_queries,
        "total_errors": total_errors,
        "status_5xx": status_5xx,
        "restarts": restarts,
        "batch_path_fired": batch_path_fired,
        "final_parallel_metrics": final_par,
        "verdict": "PASS" if total_errors == 0 and status_5xx == 0 else "FAIL",
    }

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print(f"\n{'='*60}")
    print(f"SOAK COMPLETE")
    print(f"  Type: {soak_type}")
    print(f"  Cycles: {cycle}")
    print(f"  Queries: {total_queries}")
    print(f"  Errors: {total_errors}")
    print(f"  5xx: {status_5xx}")
    print(f"  Restarts: {restarts}")
    print(f"  Batch path fired: {batch_path_fired}")
    print(f"  completed_parallel_batches: {final_par.get('completed_parallel_batches', '?')}")
    print(f"  total_dispatched: {final_par.get('total_dispatched', '?')}")
    print(f"  Verdict: {summary['verdict']}")
    print(f"  Summary: {summary_path}")


if __name__ == "__main__":
    main()
