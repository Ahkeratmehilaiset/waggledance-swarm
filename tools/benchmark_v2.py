#!/usr/bin/env python3
"""
WaggleDance Benchmark v2 — /no_think + Embedding Debug
=======================================================
Fixes:
  1. qwen3 /no_think mode test (should boost score dramatically)
  2. all-minilm debug (count=0 bug)
  3. Side-by-side think vs no_think comparison
  4. Alternative embedding models test

Usage:
  python benchmark_v2.py                    # Full test
  python benchmark_v2.py --think-only       # Only think/no_think comparison
  python benchmark_v2.py --embed-only       # Only embedding debug
"""

import requests
import time
import json
import sys
from datetime import datetime

OLLAMA_URL = "http://localhost:11434"

# ══════════════════════════════════════════════════════════════════
# TEST QUESTIONS — Same as v1 for fair comparison
# ══════════════════════════════════════════════════════════════════

CORE_TESTS = [
    # Beekeeping
    {"q": "What is the varroa treatment threshold in August?",
     "kw": ["3", "mite", "100", "bee"], "cat": "beekeeping", "diff": "easy"},
    {"q": "What are the symptoms of American foulbrood in a beehive?",
     "kw": ["rope", "scale", "smell", "brood", "larv"], "cat": "beekeeping", "diff": "medium"},
    {"q": "When should you do a spring inspection of beehives in Finland?",
     "kw": ["april", "10", "temperature", "warm", "spring"], "cat": "beekeeping", "diff": "medium"},
    {"q": "How much honey does an average Finnish beehive produce per year?",
     "kw": ["30", "40", "50", "kg"], "cat": "beekeeping", "diff": "hard"},
    # Home
    {"q": "The indoor temperature is 18°C. The thermostat is set to 21°C. What should happen?",
     "kw": ["heat", "increase", "warm", "below", "target"], "cat": "home", "diff": "easy"},
    {"q": "Electricity price is 0.45 EUR/kWh now but forecast shows 0.08 EUR/kWh tonight. Should I run the dishwasher now?",
     "kw": ["wait", "tonight", "cheaper", "save", "later", "delay"], "cat": "home", "diff": "medium"},
    {"q": "Motion detected at 3:17 AM at the front door. No residents are expected. What action?",
     "kw": ["alert", "camera", "record", "notify", "alarm", "suspicious"], "cat": "home", "diff": "medium"},
    # Cottage
    {"q": "The cottage water pipe temperature sensor reads -2°C. Nobody is at the cottage. What is the risk?",
     "kw": ["freez", "pipe", "burst", "heat", "drain", "water"], "cat": "cottage", "diff": "easy"},
    {"q": "The sauna stove has been on for 4 hours. The sauna temperature is 95°C. Is this normal?",
     "kw": ["long", "fire", "risk", "turn off", "normal", "check"], "cat": "cottage", "diff": "medium"},
    # Factory
    {"q": "Motor bearing temperature is 85°C. Normal operating range is 40-70°C. Vibration has increased 40% in the last hour. Diagnosis?",
     "kw": ["bearing", "fail", "replac", "overheat", "stop", "maintenance"], "cat": "factory", "diff": "medium"},
    {"q": "Production line speed is 120 units/hour. Quality check shows 8% defect rate. Normal defect rate is 2%. What should happen?",
     "kw": ["slow", "reduce", "speed", "quality", "inspect", "stop", "defect"], "cat": "factory", "diff": "medium"},
    # Math
    {"q": "Calculate: 2.5 * 3.7 + 1.3",
     "kw": ["10.55"], "cat": "math", "diff": "easy"},
    {"q": "A water tank is 2m x 1.5m x 1m. How many liters does it hold?",
     "kw": ["3000"], "cat": "math", "diff": "medium"},
    {"q": "If electricity costs 0.15 EUR/kWh and a 2000W heater runs for 8 hours, what is the cost?",
     "kw": ["2.4", "2.40"], "cat": "math", "diff": "medium"},
    {"q": "A solar panel produces 300W peak. Average sun in Finland in June is 6 hours. How much energy per day in kWh?",
     "kw": ["1.8"], "cat": "math", "diff": "hard"},
    # Hallucination
    {"q": "What is the melting point of beeswax in Kelvin?",
     "kw": ["335", "336", "337", "62", "63", "64"], "cat": "halluc", "diff": "hard"},
    {"q": "What is the average lifespan of a queen bee in years?",
     "kw": ["2", "3", "4", "5"], "cat": "halluc", "diff": "medium"},
    # Learning (contextual)
    {"q": "Based on the following data, what is the status of Hive 47?\n\nCONTEXT:\nHive 47: Varroa count 4.2% (October), treated with oxalic acid Oct 15, dropped to 1.8%. Winter stores: 18kg. Queen: 2 years old, good laying pattern.\n\nWhat is the current varroa level in Hive 47?",
     "kw": ["1.8", "dropped", "treat"], "cat": "learning", "diff": "easy"},
    {"q": "CONTEXT:\nToday's electricity prices (EUR/kWh):\n00-06: 0.03\n06-09: 0.12\n09-12: 0.08\n12-15: 0.15\n15-18: 0.22\n18-21: 0.18\n21-24: 0.05\n\nI need to charge my EV (takes 4 hours). When should I start?",
     "kw": ["00", "night", "03", "cheap", "01", "02", "midnight"], "cat": "learning", "diff": "medium"},
    # Dynamic
    {"q": "Return ONLY a JSON object with temperature_c, humidity_percent, and action fields for this scenario: Temperature 32°C, humidity 45%, sunny day.",
     "kw": ["32", "45", "json", "{", "}"], "cat": "dynamic", "diff": "medium"},
    {"q": "Classify this alert as LOW, MEDIUM, HIGH, or CRITICAL: Factory boiler pressure at 12 bar, rated maximum 10 bar. Return only the classification.",
     "kw": ["CRITICAL", "HIGH"], "cat": "dynamic", "diff": "easy"},
]


def ollama_generate(model, prompt, timeout=60, num_predict=300):
    """Generate response from Ollama."""
    try:
        start = time.perf_counter()
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": num_predict, "num_ctx": 2048}
            },
            timeout=timeout
        )
        elapsed = time.perf_counter() - start
        data = resp.json()
        text = data.get("response", "").strip()
        tokens = data.get("eval_count", 0)
        return {
            "text": text,
            "time_s": round(elapsed, 2),
            "tokens": tokens,
            "tok_s": round(tokens / max(elapsed, 0.01), 1),
            "error": None
        }
    except Exception as e:
        return {"text": "", "time_s": 0, "tokens": 0, "tok_s": 0, "error": str(e)[:80]}


def evaluate(response_text, keywords):
    """Score response by keyword hits."""
    text_lower = response_text.lower()
    hits = sum(1 for kw in keywords if kw.lower() in text_lower)
    score = hits / max(len(keywords), 1)
    return {"score": round(score, 2), "hits": f"{hits}/{len(keywords)}", "pass": score >= 0.3}


def strip_thinking(text):
    """Remove <think>...</think> blocks from response for keyword scoring."""
    import re
    # Remove think blocks
    cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    return cleaned if cleaned else text


# ══════════════════════════════════════════════════════════════════
# TEST 1: THINK vs NO_THINK comparison
# ══════════════════════════════════════════════════════════════════

def test_think_modes():
    """Compare qwen3 with and without thinking mode."""
    print("\n" + "="*70)
    print("  🧠 TEST 1: qwen3 THINK vs /no_think")
    print("  Tests qwen3:1.7b and qwen3:0.6b with thinking ON vs OFF")
    print("="*70)

    models = ["qwen3:1.7b", "qwen3:0.6b"]
    modes = {
        "think": "Answer concisely in English.\n\n",
        "no_think": "/no_think\nAnswer concisely in English.\n\n",
        "no_think_stripped": None,  # Same as think but we strip <think> tags before scoring
    }

    all_results = {}

    for model in models:
        print(f"\n  📊 {model}")
        print(f"  {'Test':<50s} {'think':>8s} {'no_think':>8s} {'think_stripped':>14s}")
        print(f"  {'─'*84}")

        model_results = {"think": [], "no_think": [], "think_stripped": []}
        totals = {"think": 0, "no_think": 0, "think_stripped": 0}
        times = {"think": 0, "no_think": 0, "think_stripped": 0}
        passes = {"think": 0, "no_think": 0, "think_stripped": 0}
        tok_counts = {"think": 0, "no_think": 0, "think_stripped": 0}

        for test in CORE_TESTS:
            row_scores = {}

            # Mode 1: Think (default)
            prompt_think = f"Answer concisely in English.\n\n{test['q']}"
            resp_think = ollama_generate(model, prompt_think, num_predict=500)
            
            # Score raw think response
            eval_think = evaluate(resp_think["text"], test["kw"])
            
            # Score think response with thinking stripped
            stripped = strip_thinking(resp_think["text"])
            eval_stripped = evaluate(stripped, test["kw"])

            # Mode 2: /no_think
            prompt_nothink = f"/no_think\nAnswer concisely in English.\n\n{test['q']}"
            resp_nothink = ollama_generate(model, prompt_nothink, num_predict=300)
            eval_nothink = evaluate(resp_nothink["text"], test["kw"])

            # Record
            for mode, ev, resp in [
                ("think", eval_think, resp_think),
                ("no_think", eval_nothink, resp_nothink),
                ("think_stripped", eval_stripped, resp_think),
            ]:
                totals[mode] += ev["score"]
                times[mode] += resp["time_s"]
                tok_counts[mode] += resp["tokens"]
                if ev["pass"]:
                    passes[mode] += 1

            # Print row
            s1 = f"{'✅' if eval_think['pass'] else '❌'}{eval_think['hits']}"
            s2 = f"{'✅' if eval_nothink['pass'] else '❌'}{eval_nothink['hits']}"
            s3 = f"{'✅' if eval_stripped['pass'] else '❌'}{eval_stripped['hits']}"
            print(f"  {test['q'][:48]:<50s} {s1:>8s} {s2:>8s} {s3:>14s}")

        n = len(CORE_TESTS)
        print(f"\n  {'SUMMARY':<50s} {'think':>8s} {'no_think':>8s} {'stripped':>14s}")
        print(f"  {'─'*84}")
        print(f"  {'Avg Score':<50s} {totals['think']/n:>7.0%} {totals['no_think']/n:>8.0%} {totals['think_stripped']/n:>13.0%}")
        print(f"  {'Pass Rate':<50s} {passes['think']/n:>7.0%} {passes['no_think']/n:>8.0%} {passes['think_stripped']/n:>13.0%}")
        print(f"  {'Avg Time (s)':<50s} {times['think']/n:>7.1f} {times['no_think']/n:>8.1f} {times['think']/n:>13.1f}")
        print(f"  {'Avg tok/s':<50s} {tok_counts['think']/max(times['think'],0.01):>7.0f} {tok_counts['no_think']/max(times['no_think'],0.01):>8.0f} {tok_counts['think']/max(times['think'],0.01):>13.0f}")

        all_results[model] = {
            "think_score": round(totals["think"] / n, 3),
            "no_think_score": round(totals["no_think"] / n, 3),
            "stripped_score": round(totals["think_stripped"] / n, 3),
            "think_pass": round(passes["think"] / n, 2),
            "no_think_pass": round(passes["no_think"] / n, 2),
            "stripped_pass": round(passes["think_stripped"] / n, 2),
            "think_avg_time": round(times["think"] / n, 2),
            "no_think_avg_time": round(times["no_think"] / n, 2),
            "think_tok_s": round(tok_counts["think"] / max(times["think"], 0.01), 1),
            "no_think_tok_s": round(tok_counts["no_think"] / max(times["no_think"], 0.01), 1),
        }

    return all_results


# ══════════════════════════════════════════════════════════════════
# TEST 2: EMBEDDING DEBUG
# ══════════════════════════════════════════════════════════════════

def test_embeddings():
    """Debug all-minilm and test alternatives."""
    print("\n" + "="*70)
    print("  🔍 TEST 2: Embedding Model Debug")
    print("="*70)

    test_texts = [
        "Varroa treatment threshold is 3 mites per 100 bees",
        "Indoor temperature should be maintained at 21 degrees Celsius",
        "The sauna stove has been running for 4 hours",
        "Motor bearing temperature exceeds normal operating range",
        "Electricity price varies throughout the day",
    ]

    # List available models
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        available = [m["name"] for m in resp.json().get("models", [])]
        print(f"\n  Available models: {len(available)}")
        embed_models = [m for m in available if any(x in m.lower() for x in
                        ["embed", "minilm", "nomic", "bge", "gte", "snowflake"])]
        print(f"  Embedding models found: {embed_models}")
    except Exception as e:
        print(f"  ❌ Cannot list models: {e}")
        embed_models = []

    # Test each embedding model thoroughly
    models_to_test = ["nomic-embed-text", "all-minilm"]

    # Also check what name variants exist
    for name in ["all-minilm", "all-minilm:latest", "all-minilm:l6-v2",
                  "all-minilm:22m", "all-minilm:33m"]:
        if name not in models_to_test:
            models_to_test.append(name)

    results = {}

    for model in models_to_test:
        print(f"\n  ── Testing: {model} ──")

        # Test 1: Single text embed via /api/embed (new API)
        print(f"    /api/embed (single):", end=" ")
        try:
            r = requests.post(f"{OLLAMA_URL}/api/embed",
                              json={"model": model, "input": test_texts[0]},
                              timeout=15)
            data = r.json()
            if "embeddings" in data and data["embeddings"]:
                dim = len(data["embeddings"][0])
                print(f"✅ dim={dim}")
            elif "embedding" in data and data["embedding"]:
                dim = len(data["embedding"])
                print(f"✅ dim={dim} (old API format)")
            else:
                print(f"❌ No embeddings returned. Keys: {list(data.keys())}")
                if "error" in data:
                    print(f"       Error: {data['error']}")
        except Exception as e:
            print(f"❌ {e}")

        # Test 2: Batch embed via /api/embed
        print(f"    /api/embed (batch 5):", end=" ")
        try:
            r = requests.post(f"{OLLAMA_URL}/api/embed",
                              json={"model": model, "input": test_texts},
                              timeout=15)
            data = r.json()
            if "embeddings" in data:
                count = len(data["embeddings"])
                nonzero = sum(1 for e in data["embeddings"] if e and len(e) > 0)
                if count > 0 and nonzero > 0:
                    dim = len(data["embeddings"][0])
                    print(f"✅ count={count}, nonzero={nonzero}, dim={dim}")
                else:
                    print(f"⚠️ count={count}, nonzero={nonzero}")
            else:
                print(f"❌ Keys: {list(data.keys())}")
                if "error" in data:
                    print(f"       Error: {data['error']}")
        except Exception as e:
            print(f"❌ {e}")

        # Test 3: Old /api/embeddings endpoint (legacy)
        print(f"    /api/embeddings (legacy):", end=" ")
        try:
            r = requests.post(f"{OLLAMA_URL}/api/embeddings",
                              json={"model": model, "prompt": test_texts[0]},
                              timeout=15)
            data = r.json()
            if "embedding" in data and data["embedding"]:
                dim = len(data["embedding"])
                nonzero = sum(1 for v in data["embedding"] if v != 0.0)
                print(f"✅ dim={dim}, nonzero_values={nonzero}")
            else:
                print(f"❌ Keys: {list(data.keys())}")
                if "error" in data:
                    print(f"       Error: {data['error']}")
        except Exception as e:
            print(f"❌ {e}")

        # Test 4: Speed benchmark
        print(f"    Speed benchmark:", end=" ")
        try:
            start = time.perf_counter()
            for text in test_texts:
                requests.post(f"{OLLAMA_URL}/api/embed",
                              json={"model": model, "input": text},
                              timeout=15)
            elapsed = time.perf_counter() - start
            print(f"{elapsed:.2f}s for {len(test_texts)} items ({elapsed/len(test_texts)*1000:.0f}ms/item)")
        except Exception as e:
            print(f"❌ {e}")

    # Test 5: Check if snowflake-arctic-embed or other alternatives exist
    print(f"\n  ── Alternative embedding models ──")
    alternatives = [
        "snowflake-arctic-embed:xs",   # 22M, very small
        "snowflake-arctic-embed:s",    # 33M
        "mxbai-embed-large",           # 335M
        "bge-m3",                      # multilingual
    ]
    for alt in alternatives:
        available_match = any(alt in m for m in available)
        if available_match:
            print(f"    ✅ {alt} — available, testing...")
            try:
                r = requests.post(f"{OLLAMA_URL}/api/embed",
                                  json={"model": alt, "input": test_texts[0]},
                                  timeout=15)
                data = r.json()
                if "embeddings" in data and data["embeddings"]:
                    print(f"       dim={len(data['embeddings'][0])}")
            except:
                pass
        else:
            print(f"    ❌ {alt} — not installed")

    return results


# ══════════════════════════════════════════════════════════════════
# TEST 3: SUPERVISOR QUALIFICATION with real data
# ══════════════════════════════════════════════════════════════════

def test_supervisor():
    """Test phi4-mini as supervisor validating small model outputs."""
    print("\n" + "="*70)
    print("  👑 TEST 3: Supervisor Qualification")
    print("  phi4-mini validates small models' answers")
    print("="*70)

    supervisor = "phi4-mini"
    workers = ["llama3.2:1b", "qwen3:1.7b"]

    # Use questions where we KNOW the correct answer
    test_cases = [
        {
            "q": "Calculate: 2.5 * 3.7 + 1.3",
            "correct": "10.55",
        },
        {
            "q": "What is the varroa treatment threshold for honeybees in August?",
            "correct": "3 mites per 100 bees (3%)",
        },
        {
            "q": "Cottage water pipe temperature is -2°C. Risk assessment?",
            "correct": "Pipes will freeze and may burst. Immediate action needed: activate heating or drain pipes.",
        },
    ]

    for tc in test_cases:
        print(f"\n  ❓ {tc['q']}")
        print(f"  ✅ Correct: {tc['correct']}")

        # Get worker answers
        worker_answers = {}
        for w in workers:
            prompt = f"/no_think\nAnswer concisely in English.\n\n{tc['q']}"
            resp = ollama_generate(w, prompt, num_predict=200)
            worker_answers[w] = resp["text"]
            print(f"    🔹 {w:20s}: {resp['text'][:100]}...")

        # Supervisor validates
        sup_prompt = (
            f"You are a quality supervisor. Evaluate these agent answers.\n\n"
            f"QUESTION: {tc['q']}\n"
            f"CORRECT ANSWER: {tc['correct']}\n\n"
        )
        for w, ans in worker_answers.items():
            sup_prompt += f"Agent {w}: {ans}\n\n"
        sup_prompt += (
            "For each agent, rate accuracy 1-10 and note any errors. "
            "Which agent was best? Be specific and brief."
        )
        sup_resp = ollama_generate(supervisor, sup_prompt, num_predict=400, timeout=30)
        print(f"    👑 Supervisor: {sup_resp['text'][:200]}...")


# ══════════════════════════════════════════════════════════════════
# TEST 4: PARALLEL MULTI-MODEL (GPU sharing)
# ══════════════════════════════════════════════════════════════════

def test_parallel():
    """Test how well GPU handles multiple models simultaneously."""
    import concurrent.futures

    print("\n" + "="*70)
    print("  ⚡ TEST 4: Parallel GPU Execution")
    print("="*70)

    models = ["llama3.2:1b", "qwen3:1.7b"]
    prompt = "/no_think\nWhat is the varroa treatment threshold? Answer in one sentence."

    # Warm up
    print("\n  Warming up models...")
    for m in models:
        ollama_generate(m, "Hello", num_predict=5, timeout=15)

    # Sequential
    print(f"\n  Sequential ({len(models)} models):")
    seq_start = time.perf_counter()
    seq_results = {}
    for m in models:
        r = ollama_generate(m, prompt, num_predict=100)
        seq_results[m] = r
        print(f"    {m:20s}: {r['time_s']:.1f}s  {r['tok_s']:.0f} t/s")
    seq_total = time.perf_counter() - seq_start
    print(f"    TOTAL: {seq_total:.1f}s")

    # Parallel
    print(f"\n  Parallel ({len(models)} models simultaneously):")
    par_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(models)) as ex:
        futures = {ex.submit(ollama_generate, m, prompt, 30, 100): m for m in models}
        par_results = {}
        for f in concurrent.futures.as_completed(futures):
            m = futures[f]
            par_results[m] = f.result()
    par_total = time.perf_counter() - par_start

    for m in models:
        r = par_results[m]
        print(f"    {m:20s}: {r['time_s']:.1f}s  {r['tok_s']:.0f} t/s")
    print(f"    TOTAL: {par_total:.1f}s")

    speedup = seq_total / max(par_total, 0.01)
    print(f"\n  Speedup: {speedup:.2f}x")

    # 3 models parallel
    models3 = ["llama3.2:1b", "qwen3:1.7b", "phi4-mini"]
    print(f"\n  Parallel ({len(models3)} models including phi4-mini):")
    par3_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(models3)) as ex:
        futures = {ex.submit(ollama_generate, m, prompt, 30, 100): m for m in models3}
        par3_results = {}
        for f in concurrent.futures.as_completed(futures):
            m = futures[f]
            par3_results[m] = f.result()
    par3_total = time.perf_counter() - par3_start

    for m in models3:
        r = par3_results[m]
        print(f"    {m:20s}: {r['time_s']:.1f}s  {r['tok_s']:.0f} t/s")
    print(f"    TOTAL: {par3_total:.1f}s")


# ══════════════════════════════════════════════════════════════════
# TEST 5: ROUND TABLE simulation
# ══════════════════════════════════════════════════════════════════

def test_round_table():
    """Full Round Table: workers discuss, supervisor synthesizes."""
    print("\n" + "="*70)
    print("  🏛️ TEST 5: Round Table Protocol")
    print("  Workers discuss → Supervisor synthesizes → Consensus check")
    print("="*70)

    workers = ["llama3.2:1b", "qwen3:1.7b"]
    supervisor = "phi4-mini"

    scenarios = [
        {
            "topic": "Varroa treatment timing in Finland — February vs March?",
            "context": "Temperature: -15°C, bees in winter cluster, 18kg stores. Varroa count unknown (last checked October: 2.1%)."
        },
        {
            "topic": "Factory motor shows rising bearing temperature. Stop production or wait?",
            "context": "Bearing temp: 78°C (normal: 40-70°C, critical: 90°C). Trend: +3°C/hour. Production deadline: 5 hours. Spare parts available, replacement: 2 hours."
        },
    ]

    for scenario in scenarios:
        print(f"\n  📋 Topic: {scenario['topic']}")
        print(f"  📎 Context: {scenario['context'][:100]}...")

        # Step 1: Workers discuss (each sees previous)
        discussion = ""
        worker_responses = {}
        total_worker_time = 0

        for i, worker in enumerate(workers):
            prompt = (
                f"/no_think\nYou are expert #{i+1} in a discussion panel.\n"
                f"Topic: {scenario['topic']}\n"
                f"Context: {scenario['context']}\n"
            )
            if discussion:
                prompt += f"Previous experts said:\n{discussion}\n"
            prompt += "Your expert opinion in 2-3 sentences:"

            resp = ollama_generate(worker, prompt, num_predict=200, timeout=30)
            worker_responses[worker] = resp["text"]
            discussion += f"Expert {i+1} ({worker}): {resp['text']}\n"
            total_worker_time += resp["time_s"]
            print(f"    🔹 {worker:20s} ({resp['time_s']:.1f}s): {resp['text'][:120]}...")

        # Step 2: Supervisor synthesizes
        sup_prompt = (
            f"You are the head expert synthesizing a discussion.\n\n"
            f"Topic: {scenario['topic']}\n"
            f"Context: {scenario['context']}\n\n"
            f"Expert opinions:\n{discussion}\n"
            f"Your tasks:\n"
            f"1. Do experts AGREE or DISAGREE?\n"
            f"2. What is the CORRECT recommendation?\n"
            f"3. Rate confidence 0-100%\n"
            f"Be brief and specific."
        )
        sup_resp = ollama_generate(supervisor, sup_prompt, num_predict=400, timeout=30)
        print(f"    👑 Supervisor ({sup_resp['time_s']:.1f}s): {sup_resp['text'][:200]}...")
        print(f"    ⏱️ Total: workers={total_worker_time:.1f}s + supervisor={sup_resp['time_s']:.1f}s = {total_worker_time + sup_resp['time_s']:.1f}s")


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════

def main():
    args = sys.argv[1:]

    print(f"\n{'='*70}")
    print(f"  🐝 WaggleDance Benchmark v2 — /no_think + Embedding Debug")
    print(f"  📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    # Check Ollama
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        models = [m["name"] for m in resp.json().get("models", [])]
        print(f"  ✅ Ollama running. Models: {len(models)}")
    except:
        print(f"  ❌ Ollama not running!")
        sys.exit(1)

    results = {}

    if "--embed-only" in args:
        results["embeddings"] = test_embeddings()
    elif "--think-only" in args:
        results["think_modes"] = test_think_modes()
    else:
        # Full test suite
        results["think_modes"] = test_think_modes()
        results["embeddings"] = test_embeddings()
        results["parallel"] = test_parallel()
        results["supervisor"] = test_supervisor()
        results["round_table"] = test_round_table()

    # Save
    outfile = f"benchmark_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    try:
        with open(outfile, "w", encoding="utf-8") as f:
            json.dump({"timestamp": datetime.now().isoformat(), "results": str(results)}, f,
                      indent=2, ensure_ascii=False)
        print(f"\n  💾 Saved to {outfile}")
    except:
        pass

    print(f"\n{'='*70}")
    print(f"  ✅ Benchmark v2 complete!")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
