"""Live monitor - continuous T-snapshots every 5 min."""
import json, time, datetime, subprocess, os

DATA = "U:/project2/data"
LOG = "U:/project2/data/monitor_log.jsonl"

def count_lines(path):
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for _ in f)
    except: return 0

def tail_jsonl(path, n=5):
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        return [json.loads(l.strip()) for l in lines[-n:]]
    except: return []

def api_status():
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:8000/api/status", timeout=5) as r:
            return json.loads(r.read())
    except: return None

def snapshot(tick):
    ts = datetime.datetime.now().isoformat()
    curated = count_lines(f"{DATA}/finetune_curated.jsonl")
    rejected = count_lines(f"{DATA}/finetune_rejected.jsonl")
    metrics = count_lines(f"{DATA}/learning_metrics.jsonl")
    
    api = api_status()
    night_active = api.get("night_mode", {}).get("active") if api else None
    idle_s = api.get("night_mode", {}).get("idle_seconds") if api else None
    uptime = api.get("uptime") if api else None
    enricher = api.get("night_enricher", {}) if api else {}
    learning = api.get("learning", {}) if api else {}
    
    # Recent scores
    recent_curated = tail_jsonl(f"{DATA}/finetune_curated.jsonl", 5)
    recent_rejected = tail_jsonl(f"{DATA}/finetune_rejected.jsonl", 5)
    
    snap = {
        "tick": tick, "ts": ts, "uptime": uptime,
        "curated": curated, "rejected": rejected, "metrics": metrics,
        "night_active": night_active, "idle_s": idle_s,
        "learning_session": learning,
        "enricher_total_checked": enricher.get("total_checked", 0),
        "enricher_total_stored": enricher.get("total_stored", 0),
        "enricher_in_benchmark": enricher.get("tuner", {}).get("in_benchmark"),
        "rss_available": enricher.get("sources", {}).get("rss_feed", {}).get("available"),
        "self_gen_checked": enricher.get("sources", {}).get("self_generate", {}).get("total_checked", 0),
        "self_gen_passed": enricher.get("sources", {}).get("self_generate", {}).get("total_passed", 0),
    }
    
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(snap) + "\n")
    
    print(f"T{tick} [{ts[11:19]}] up={uptime} curated={curated} rejected={rejected} "
          f"night={night_active} idle={idle_s}s "
          f"enrich_check={snap['enricher_total_checked']} enrich_store={snap['enricher_total_stored']} "
          f"rss={snap['rss_available']} benchmark={snap['enricher_in_benchmark']}")
    
    # Print latest curated timestamps
    if recent_curated:
        last = recent_curated[-1]
        print(f"  latest_curated: ts={last.get('timestamp','?')} reason={last.get('reasoning','?')[:40]}")
    if recent_rejected:
        last = recent_rejected[-1]
        print(f"  latest_rejected: ts={last.get('timestamp','?')} reason={last.get('rejection_reason', last.get('reasoning','?'))[:40]}")
    
    return snap

if __name__ == "__main__":
    tick = 4  # Starting from T4
    print(f"=== Live Monitor started (interval=300s) ===")
    while True:
        try:
            snapshot(tick)
        except Exception as e:
            print(f"T{tick} ERROR: {e}")
        tick += 1
        time.sleep(300)
