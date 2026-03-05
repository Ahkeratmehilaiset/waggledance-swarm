"""
test_night_enricher.py — 12-section test suite for NightEnricher
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from unittest.mock import AsyncMock, MagicMock, patch


PASS = 0
FAIL = 0


def ok(label):
    global PASS
    PASS += 1
    print(f"  [PASS] {label}")


def fail(label, detail=""):
    global FAIL
    FAIL += 1
    msg = f"  [FAIL] {label}"
    if detail:
        msg += f" — {detail}"
    print(msg)


def check(condition, label, detail=""):
    if condition:
        ok(label)
    else:
        fail(label, detail)


# ===============================================================
# 1. SourceManager — register, get, metrics, availability
# ===============================================================

def test_source_manager():
    print("\n=== 1. SourceManager ===")
    from core.night_enricher import (
        SourceManager, SelfGenerateSource, WebScrapeSource,
        ChatHistorySource, RssFeedSource, ClaudeDistillSource,
    )

    sm = SourceManager()

    # SelfGenerate needs LLMs — pass None to test registration
    sg = SelfGenerateSource(None, None)
    sm.register(sg)
    check(sm.get_source("self_generate") is sg, "register + get_source")
    check(sm.get_metrics("self_generate") is sg.metrics,
          "get_metrics returns source metrics")
    check(not sg.is_available(), "self_generate unavailable when LLMs None")

    ws = WebScrapeSource(daily_budget=10)
    sm.register(ws)
    check(not sm.is_source_available("web_scrape"),
          "web_scrape stub unavailable")

    cd = ClaudeDistillSource(weekly_budget_eur=5.0)
    sm.register(cd)
    check(not sm.is_source_available("claude_distill"),
          "claude_distill stub unavailable")

    ch = ChatHistorySource()
    sm.register(ch)
    check(not sm.is_source_available("chat_history"),
          "chat_history stub unavailable")

    rf = RssFeedSource()
    sm.register(rf)
    check(not sm.is_source_available("rss_feed"),
          "rss_feed stub unavailable")

    check(len(sm.source_ids) == 5, f"5 sources registered ({len(sm.source_ids)})")
    check(sm.available_ids == [], "no sources available (all stubs/None)")

    stats = sm.get_all_stats()
    check("self_generate" in stats, "get_all_stats includes self_generate")
    check("web_scrape" in stats, "get_all_stats includes web_scrape")
    check(stats["web_scrape"]["available"] is False,
          "web_scrape stats shows unavailable")


# ===============================================================
# 2. Source scoring simulation
# ===============================================================

def test_source_scoring():
    print("\n=== 2. Source Scoring ===")
    from core.night_enricher import SourceMetrics

    m = SourceMetrics()

    # Feed 10 outcomes: 7 pass, 3 fail; 5 novel
    for i in range(10):
        passed = i < 7
        novel = i < 5
        m.record_outcome(passed, novel, cycle_time_s=1.0)

    check(abs(m.pass_rate - 0.7) < 0.01,
          f"pass_rate=0.7 (got {m.pass_rate:.2f})")
    check(abs(m.novelty_score - 0.5) < 0.01,
          f"novelty_score=0.5 (got {m.novelty_score:.2f})")
    check(abs(m.throughput - 60.0) < 0.1,
          f"throughput=60/min (got {m.throughput:.1f})")

    expected_yield = 0.7 * 0.5 * 60.0
    check(abs(m.effective_yield - expected_yield) < 0.5,
          f"effective_yield={expected_yield} (got {m.effective_yield:.1f})")

    check(m.consecutive_failures == 3,
          f"consecutive_failures=3 (got {m.consecutive_failures})")

    # Test pause
    check(not m.is_paused, "not paused initially")
    m.pause(0.1)
    check(m.is_paused, "paused after pause(0.1)")
    time.sleep(0.15)
    check(not m.is_paused, "unpaused after 0.15s")

    d = m.to_dict()
    check("pass_rate" in d and "effective_yield" in d,
          "to_dict has expected keys")


# ===============================================================
# 3. AdaptiveTuner benchmark phase
# ===============================================================

def test_tuner_benchmark():
    print("\n=== 3. AdaptiveTuner Benchmark ===")
    from core.night_enricher import (
        AdaptiveTuner, SourceManager, SelfGenerateSource,
    )

    sm = SourceManager()
    # Create a "available" source by mocking is_available
    src = SelfGenerateSource(MagicMock(), MagicMock())
    sm.register(src)

    tuner = AdaptiveTuner(sm, benchmark_count=3, rebalance_every=10)
    check(tuner.in_benchmark, "starts in benchmark phase")

    # During benchmark, next_source should return the only available source
    picked = tuner.next_source()
    check(picked == "self_generate",
          f"picks self_generate during benchmark (got {picked})")

    # Feed results but not enough for benchmark completion
    for _ in range(2):
        src.metrics.record_outcome(True, True, 1.0)
        tuner.record_result("self_generate", True, True)
    check(tuner.in_benchmark, "still in benchmark (2 < 3)")

    # One more
    src.metrics.record_outcome(True, True, 1.0)
    tuner.record_result("self_generate", True, True)

    # Now call next_source to trigger benchmark check
    tuner.next_source()
    check(not tuner.in_benchmark,
          "benchmark complete after 3+ outcomes + next_source call")


# ===============================================================
# 4. AdaptiveTuner burst mode
# ===============================================================

def test_tuner_burst():
    print("\n=== 4. AdaptiveTuner Burst Mode ===")
    from core.night_enricher import (
        AdaptiveTuner, SourceManager, SelfGenerateSource,
    )

    sm = SourceManager()
    src = SelfGenerateSource(MagicMock(), MagicMock())
    sm.register(src)

    tuner = AdaptiveTuner(
        sm, benchmark_count=5, rebalance_every=5,
        burst_threshold=0.80, burst_max_share=0.80)

    # Feed 10 pass results (pass_rate = 1.0 >> 0.80 threshold)
    for _ in range(10):
        src.metrics.record_outcome(True, True, 1.0)
        tuner.record_result("self_generate", True, True)

    # Force next_source to trigger alloc check
    tuner.next_source()

    allocs = tuner.allocations
    check("self_generate" in allocs, "self_generate in allocations")
    if "self_generate" in allocs:
        check(allocs["self_generate"] >= 0.80,
              f"burst: alloc >= 0.80 (got {allocs['self_generate']:.2f})")


# ===============================================================
# 5. AdaptiveTuner throttle
# ===============================================================

def test_tuner_throttle():
    print("\n=== 5. AdaptiveTuner Throttle ===")
    from core.night_enricher import (
        AdaptiveTuner, SourceManager, SelfGenerateSource, WebScrapeSource,
    )

    sm = SourceManager()
    src = SelfGenerateSource(MagicMock(), MagicMock())
    sm.register(src)
    # Register a second available source so self_generate isn't "last source standing"
    # (last source standing protection resets failures instead of pausing)
    src2 = WebScrapeSource()
    src2.is_available = lambda: True
    sm.register(src2)

    tuner = AdaptiveTuner(
        sm, benchmark_count=1,
        throttle_pass_rate=0.15, throttle_window=5,
        throttle_pause_seconds=0.2)  # Short pause for test

    # Feed 6 consecutive failures with pass_rate = 0 (< 0.15)
    for _ in range(6):
        src.metrics.record_outcome(False, False, 1.0)
        tuner.record_result("self_generate", False, False)

    check(src.metrics.is_paused,
          "source paused after consecutive failures + low pass_rate")
    check(src.metrics.consecutive_failures >= 5,
          f"consecutive_failures >= 5 (got {src.metrics.consecutive_failures})")

    # After pause expires
    time.sleep(0.3)
    check(not src.metrics.is_paused, "source unpaused after timeout")


# ===============================================================
# 6. QualityGate steps
# ===============================================================

def test_quality_gate():
    print("\n=== 6. QualityGate Steps ===")
    from core.night_enricher import QualityGate, EnrichmentCandidate

    # Mock consciousness with embed + memory
    mock_consciousness = MagicMock()
    mock_consciousness.embed.embed_query.return_value = [0.1] * 768
    mock_match = MagicMock()
    mock_match.score = 0.5
    mock_match.text = "Some existing fact"
    mock_consciousness.memory.search.return_value = [mock_match]

    # Mock LLM that returns VALID
    mock_llm = MagicMock()
    valid_resp = MagicMock()
    valid_resp.error = False
    valid_resp.content = "VALID"
    mock_llm.generate = AsyncMock(return_value=valid_resp)

    gate = QualityGate(mock_consciousness, mock_llm,
                       novelty_threshold=0.85, dedup_threshold=0.93)

    candidate = EnrichmentCandidate(
        text="Varroa mites are a major pest in beekeeping",
        source_id="self_generate",
        agent_id="beekeeper",
        gap_topic="varroa",
    )

    # Test passing case
    verdict = asyncio.run(gate.check(candidate))
    check(verdict.passed, "valid candidate passes QualityGate")
    check(verdict.novel, f"novel when score=0.5 < 0.85 (novel={verdict.novel})")

    # Test LLM INVALID rejection
    invalid_resp = MagicMock()
    invalid_resp.error = False
    invalid_resp.content = "INVALID"
    mock_llm.generate = AsyncMock(return_value=invalid_resp)

    verdict = asyncio.run(gate.check(candidate))
    check(not verdict.passed, "INVALID response -> rejected")
    check(verdict.step_failed == "llm_validate",
          f"step_failed=llm_validate (got {verdict.step_failed})")

    # Test near-duplicate (score >= 0.93)
    mock_llm.generate = AsyncMock(return_value=valid_resp)  # Restore VALID
    near_dupe_match = MagicMock()
    near_dupe_match.score = 0.95
    near_dupe_match.text = "Some very similar fact"
    mock_consciousness.memory.search.return_value = [near_dupe_match]

    verdict = asyncio.run(gate.check(candidate))
    check(not verdict.passed, "near-duplicate (0.95) -> rejected")
    check(verdict.step_failed == "novelty",
          f"step_failed=novelty (got {verdict.step_failed})")

    # Test novel=False in gray zone (0.85 <= score < 0.93)
    gray_match = MagicMock()
    gray_match.score = 0.88
    gray_match.text = "Somewhat similar fact"
    mock_consciousness.memory.search.return_value = [gray_match]

    verdict = asyncio.run(gate.check(candidate))
    check(verdict.passed, "gray zone (0.88) -> passes")
    check(not verdict.novel,
          f"gray zone (0.88) -> novel=False (got {verdict.novel})")

    # Test contradiction detection
    mock_consciousness.memory.search.return_value = [mock_match]  # Reset
    mock_match.score = 0.75
    mock_match.text = "Varroa mites are not harmful to bees"

    neg_candidate = EnrichmentCandidate(
        text="Varroa mites are harmful to bees",
        source_id="self_generate", agent_id="disease_monitor",
        gap_topic="varroa")

    verdict = asyncio.run(gate.check(neg_candidate))
    check(not verdict.passed,
          "contradiction detected (negation mismatch)")
    check(verdict.step_failed == "contradiction",
          f"step_failed=contradiction (got {verdict.step_failed})")

    # Stats check
    stats = gate.stats
    check("llm_validate" in stats, "stats has llm_validate")
    check(stats["llm_validate"]["checked"] >= 4,
          f"llm_validate checked >= 4 (got {stats['llm_validate']['checked']})")


# ===============================================================
# 7. GapWeightedScheduler — weighted selection
# ===============================================================

def test_gap_scheduler():
    print("\n=== 7. GapWeightedScheduler ===")
    from core.night_enricher import GapWeightedScheduler

    mock_consciousness = MagicMock()
    # Simulate: embed returns vector, search returns few matches (gap!)
    mock_consciousness.embed.embed_query.return_value = [0.1] * 768

    # Return different match counts for different queries
    call_count = [0]

    def mock_search(vec, top_k=50, min_score=0.5):
        call_count[0] += 1
        # First few calls: return few matches (big gap)
        # Later calls: return many matches (no gap)
        if call_count[0] <= 6:
            return [MagicMock(score=0.6)] * 5  # 5 facts -> weight 3.0
        else:
            return [MagicMock(score=0.6)] * 50  # 50+ facts -> weight 0.5

    mock_consciousness.memory.search = mock_search

    scheduler = GapWeightedScheduler(
        mock_consciousness, rebalance_every=100,
        gap_high=20, gap_medium=50, gap_normal=100)

    agent_id, topic = scheduler.next_agent_and_topic()
    check(isinstance(agent_id, str) and len(agent_id) > 0,
          f"returns agent_id string (got '{agent_id}')")
    check(isinstance(topic, str) and len(topic) > 0,
          f"returns topic string (got '{topic}')")

    # Run 100 selections, check distribution
    agents_selected = {}
    for _ in range(100):
        aid, _ = scheduler.next_agent_and_topic()
        agents_selected[aid] = agents_selected.get(aid, 0) + 1

    check(len(agents_selected) >= 2,
          f"multiple agents selected ({len(agents_selected)} unique)")

    # Gap summary
    summary = scheduler.gap_summary
    check(isinstance(summary, dict), "gap_summary returns dict")
    if summary:
        first_agent = list(summary.keys())[0]
        check("fact_count" in summary[first_agent],
              "gap_summary entries have fact_count")
        check("level" in summary[first_agent],
              "gap_summary entries have level")


# ===============================================================
# 8. GapWeightedScheduler rebalance
# ===============================================================

def test_gap_rebalance():
    print("\n=== 8. GapScheduler Rebalance ===")
    from core.night_enricher import GapWeightedScheduler

    mock_consciousness = MagicMock()
    mock_consciousness.embed.embed_query.return_value = [0.1] * 768
    mock_consciousness.memory.search.return_value = [
        MagicMock(score=0.6)] * 10

    scheduler = GapWeightedScheduler(
        mock_consciousness, rebalance_every=5)

    # Initialize
    scheduler.next_agent_and_topic()
    initial_count = len(scheduler._weighted_list)
    check(initial_count > 0,
          f"weighted_list built ({initial_count} entries)")

    # Record 5 enrichments to trigger rebalance
    for _ in range(5):
        scheduler.record_enrichment()

    # Change mock to return different counts
    mock_consciousness.memory.search.return_value = [
        MagicMock(score=0.6)] * 30

    scheduler.next_agent_and_topic()  # Triggers rebalance
    check(scheduler._enriched_since_rebalance == 0,
          "rebalance resets counter")


# ===============================================================
# 9. SelfGenerateSource — candidates (mock LLM)
# ===============================================================

def test_self_generate_source():
    print("\n=== 9. SelfGenerateSource ===")
    from core.night_enricher import SelfGenerateSource

    mock_llm = MagicMock()
    gen_resp = MagicMock()
    gen_resp.error = False
    gen_resp.content = (
        "1. Varroa mites reproduce in capped brood cells.\n"
        "2. Oxalic acid treatment is effective in broodless periods.\n"
        "3. Regular monitoring of varroa levels prevents colony collapse."
    )
    mock_llm.generate = AsyncMock(return_value=gen_resp)

    src = SelfGenerateSource(mock_llm, MagicMock())
    check(src.is_available(), "available when both LLMs provided")
    check(src.source_id == "self_generate", "source_id correct")

    candidates = asyncio.run(
        src.generate_candidates("varroa", "disease_monitor", 3))
    check(len(candidates) == 3,
          f"generates 3 candidates (got {len(candidates)})")
    if candidates:
        check(candidates[0].source_id == "self_generate",
              "candidate has correct source_id")
        check(candidates[0].agent_id == "disease_monitor",
              "candidate has correct agent_id")
        check(candidates[0].gap_topic == "varroa",
              "candidate has correct gap_topic")
        check("Varroa" in candidates[0].text or "varroa" in candidates[0].text.lower(),
              f"candidate text about varroa: '{candidates[0].text[:60]}'")

    # Test with LLM error
    mock_llm.generate = AsyncMock(side_effect=Exception("LLM offline"))
    empty = asyncio.run(
        src.generate_candidates("test", "test", 3))
    check(len(empty) == 0, "returns empty on LLM error")


# ===============================================================
# 10. NightEnricher full cycle (mock integration)
# ===============================================================

def test_night_enricher_cycle():
    print("\n=== 10. NightEnricher Full Cycle ===")
    from core.night_enricher import NightEnricher

    # Mock consciousness
    mock_consciousness = MagicMock()
    mock_consciousness.embed.embed_query.return_value = [0.1] * 768
    mock_match = MagicMock()
    mock_match.score = 0.4  # Low score = novel
    mock_match.text = "Some fact"
    mock_consciousness.memory.search.return_value = [mock_match]
    mock_consciousness.learn.return_value = True

    # Mock LLM fast (generates)
    mock_llm_fast = MagicMock()
    gen_resp = MagicMock()
    gen_resp.error = False
    gen_resp.content = (
        "Varroa destructor is the most significant bee parasite.\n"
        "Regular monitoring helps detect varroa infestations early.\n"
        "Oxalic acid is used for varroa treatment in winter."
    )
    mock_llm_fast.generate = AsyncMock(return_value=gen_resp)

    # Mock LLM validate (validates)
    mock_llm_val = MagicMock()
    val_resp = MagicMock()
    val_resp.error = False
    val_resp.content = "VALID"
    mock_llm_val.generate = AsyncMock(return_value=val_resp)

    config = {
        "advanced_learning": {
            "night_enricher": {
                "enabled": True,
                "benchmark_count": 1,
                "rebalance_every": 50,
            }
        }
    }

    enricher = NightEnricher(
        mock_consciousness, mock_llm_fast, mock_llm_val, config)

    # Run one cycle
    stored = asyncio.run(enricher.enrichment_cycle())
    check(stored >= 1, f"stored >= 1 facts (got {stored})")
    check(enricher._total_stored >= 1,
          f"total_stored tracked (got {enricher._total_stored})")
    check(enricher._total_checked >= 1,
          f"total_checked tracked (got {enricher._total_checked})")
    check(mock_consciousness.learn.called,
          "consciousness.learn() was called")

    # Verify learn kwargs
    call_kwargs = mock_consciousness.learn.call_args
    if call_kwargs:
        kw = call_kwargs[1] if call_kwargs[1] else {}
        check(kw.get("source_type") == "autonomous_enrichment",
              f"source_type=autonomous_enrichment (got {kw.get('source_type')})")
        check(kw.get("confidence") == 0.80,
              f"confidence=0.80 (got {kw.get('confidence')})")
        check(kw.get("validated") is True,
              "validated=True")
        meta = kw.get("metadata", {})
        check(meta.get("enrichment_source") == "self_generate",
              f"metadata.enrichment_source (got {meta.get('enrichment_source')})")
        check(meta.get("validation") == "quality_gate",
              "metadata.validation=quality_gate")


# ===============================================================
# 11. Morning report format
# ===============================================================

def test_morning_report():
    print("\n=== 11. Morning Report ===")
    from core.night_enricher import NightEnricher

    mock_consciousness = MagicMock()
    mock_consciousness.embed.embed_query.return_value = [0.1] * 768
    mock_consciousness.memory.search.return_value = [
        MagicMock(score=0.4, text="fact")]
    mock_consciousness.learn.return_value = True

    mock_llm_fast = MagicMock()
    gen_resp = MagicMock()
    gen_resp.error = False
    gen_resp.content = "Test fact about bees for enrichment.\n"
    mock_llm_fast.generate = AsyncMock(return_value=gen_resp)

    mock_llm_val = MagicMock()
    val_resp = MagicMock()
    val_resp.error = False
    val_resp.content = "VALID"
    mock_llm_val.generate = AsyncMock(return_value=val_resp)

    config = {"advanced_learning": {"night_enricher": {
        "enabled": True, "benchmark_count": 1}}}
    enricher = NightEnricher(
        mock_consciousness, mock_llm_fast, mock_llm_val, config)

    # Run a cycle to have some data
    asyncio.run(enricher.enrichment_cycle())

    report = enricher.generate_morning_report()

    required_keys = [
        "timestamp", "session_duration_min", "total_checked",
        "total_stored", "overall_pass_rate", "per_source",
        "per_agent", "tuner", "quality_gate", "gap_summary_top10",
        "capacity_used_pct",
    ]
    for key in required_keys:
        check(key in report, f"report has '{key}'")

    check(isinstance(report.get("per_source"), dict),
          "per_source is dict")
    check(isinstance(report.get("per_agent"), dict),
          "per_agent is dict")
    check(isinstance(report.get("tuner"), dict),
          "tuner is dict")
    check(isinstance(report.get("quality_gate"), dict),
          "quality_gate is dict")
    check(isinstance(report.get("gap_summary_top10"), dict),
          "gap_summary_top10 is dict")

    # Verify JSON serializable
    try:
        json.dumps(report, ensure_ascii=False)
        ok("report is JSON-serializable")
    except (TypeError, ValueError) as e:
        fail("report JSON-serializable", str(e))


# ===============================================================
# 12. Config keys in settings.yaml
# ===============================================================

def test_config_keys():
    print("\n=== 12. Config Keys ===")
    import yaml

    path = Path("configs/settings.yaml")
    check(path.exists(), "settings.yaml exists")

    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    al = cfg.get("advanced_learning", {})
    ne = al.get("night_enricher", {})

    check("night_enricher" in al,
          "night_enricher section exists in advanced_learning")

    expected_keys = [
        "enabled", "chat_history_enabled", "rss_feed_enabled",
        "benchmark_count", "rebalance_every",
        "burst_threshold", "burst_max_share",
        "throttle_pass_rate", "throttle_window", "throttle_pause_seconds",
        "gap_high_threshold", "gap_medium_threshold", "gap_normal_threshold",
        "novelty_score_threshold", "dedup_score_threshold",
    ]
    for key in expected_keys:
        check(key in ne, f"config has '{key}'")

    # Value sanity checks
    check(ne.get("enabled") is True, "enabled=true")
    check(ne.get("burst_threshold") == 0.80, "burst_threshold=0.80")
    check(ne.get("novelty_score_threshold") == 0.85,
          "novelty_score_threshold=0.85")
    check(ne.get("dedup_score_threshold") == 0.93,
          "dedup_score_threshold=0.93")


# ===============================================================
# MAIN
# ===============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("NightEnricher Test Suite")
    print("=" * 60)

    test_source_manager()
    test_source_scoring()
    test_tuner_benchmark()
    test_tuner_burst()
    test_tuner_throttle()
    test_quality_gate()
    test_gap_scheduler()
    test_gap_rebalance()
    test_self_generate_source()
    test_night_enricher_cycle()
    test_morning_report()
    test_config_keys()

    print("\n" + "=" * 60)
    total = PASS + FAIL
    print(f"RESULTS: {PASS}/{total} passed, {FAIL} failed")
    if FAIL == 0:
        print("ALL TESTS PASSED")
    print("=" * 60)
    sys.exit(1 if FAIL > 0 else 0)
