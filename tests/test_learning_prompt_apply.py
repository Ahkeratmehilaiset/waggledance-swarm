"""Tests for prompt win persistence and apply/rollback (v1.16.0).

10 tests covering:
- PromptWin.to_dict() has all fields
- Default status is "pending_apply"
- Save/load round-trip (temp dir)
- Load populates _prompt_overrides for applied wins only
- get_prompt_override returns None for unknown agent
- Decide experiment — evolved wins -> PromptWin(status="pending_apply") when auto_apply=false
- Decide experiment — original wins -> PromptWin(status="lost")
- Timeout -> PromptWin(status="timeout")
- Config min_delta=2.0 + scores delta=1.0 -> original wins
- get_status() includes "prompt_wins" key
"""

import sys
import os
import json
import asyncio
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.learning_engine import PromptWin, PromptExperiment, LearningEngine

PASS = []
FAIL = []


def OK(msg):
    PASS.append(msg)
    print(f"  OK  {msg}")


def FAIL_MSG(msg, detail=""):
    FAIL.append(msg)
    print(f"  FAIL {msg}" + (f" -- {detail}" if detail else ""))


def _make_engine(learn_overrides=None):
    """Create a minimal LearningEngine with no LLM/memory."""
    cfg = {
        "learning": {
            "auto_apply_prompt_wins": False,
            "prompt_apply_min_delta": 0.5,
            "prompt_apply_min_samples_per_arm": 3,
            "prompt_live_apply": False,
            "prompt_rollback_on_regression": True,
        }
    }
    if learn_overrides:
        cfg["learning"].update(learn_overrides)
    return LearningEngine(llm_evaluator=None, memory=None, config=cfg)


def run():
    # 1. PromptWin.to_dict() has all fields
    pw = PromptWin(agent_id="test", evolved_prompt="new", original_prompt="old")
    d = pw.to_dict()
    expected_keys = {"agent_id", "evolved_prompt", "original_prompt", "source",
                     "experiment_started_at", "avg_original", "avg_evolved",
                     "winner", "replaced_prompt_preview", "status",
                     "applied_at", "created_at"}
    if expected_keys.issubset(set(d.keys())):
        OK("PromptWin.to_dict() has all fields")
    else:
        FAIL_MSG("PromptWin.to_dict() fields", f"missing: {expected_keys - set(d.keys())}")

    # 2. Default status is "pending_apply"
    pw2 = PromptWin(agent_id="x", evolved_prompt="e", original_prompt="o")
    if pw2.status == "pending_apply":
        OK("Default status is 'pending_apply'")
    else:
        FAIL_MSG("Default status", f"got {pw2.status}")

    # 3. Save/load round-trip
    with tempfile.TemporaryDirectory() as tmpdir:
        eng = _make_engine()
        eng._prompt_wins_path = os.path.join(tmpdir, "prompt_wins.json")
        from pathlib import Path
        eng._prompt_wins_path = Path(eng._prompt_wins_path)
        pw3 = PromptWin(agent_id="bee", evolved_prompt="new_prompt",
                         original_prompt="old_prompt", status="applied",
                         avg_original=5.0, avg_evolved=7.0, winner="evolved")
        eng._prompt_wins["bee"] = pw3
        eng._save_prompt_wins()

        # Load into fresh engine
        eng2 = _make_engine()
        eng2._prompt_wins_path = eng._prompt_wins_path
        eng2._prompt_wins = {}
        eng2._prompt_overrides = {}
        eng2._load_prompt_wins()

        if "bee" in eng2._prompt_wins:
            loaded = eng2._prompt_wins["bee"]
            if (loaded.agent_id == "bee" and loaded.status == "applied"
                    and loaded.avg_evolved == 7.0):
                OK("Save/load round-trip works")
            else:
                FAIL_MSG("Save/load data mismatch", str(loaded.to_dict()))
        else:
            FAIL_MSG("Save/load missing 'bee'")

    # 4. Load populates _prompt_overrides for applied wins only
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(os.path.join(tmpdir, "prompt_wins.json"))
        data = {
            "agent_a": PromptWin(agent_id="agent_a", evolved_prompt="new_a",
                                  original_prompt="old_a", status="applied").to_dict(),
            "agent_b": PromptWin(agent_id="agent_b", evolved_prompt="new_b",
                                  original_prompt="old_b", status="lost").to_dict(),
        }
        with open(path, "w") as f:
            json.dump(data, f)

        eng3 = _make_engine()
        eng3._prompt_wins_path = path
        eng3._prompt_wins = {}
        eng3._prompt_overrides = {}
        eng3._load_prompt_wins()

        if "agent_a" in eng3._prompt_overrides and "agent_b" not in eng3._prompt_overrides:
            OK("Load populates overrides for applied wins only")
        else:
            FAIL_MSG("Override map wrong", str(eng3._prompt_overrides))

    # 5. get_prompt_override returns None for unknown agent
    eng4 = _make_engine()
    if eng4.get_prompt_override("nonexistent") is None:
        OK("get_prompt_override returns None for unknown agent")
    else:
        FAIL_MSG("get_prompt_override should return None")

    # 6. Decide experiment — evolved wins -> pending_apply when auto_apply=false
    eng5 = _make_engine({"auto_apply_prompt_wins": False})
    exp = PromptExperiment(
        agent_id="bee_agent", original_prompt="orig",
        evolved_prompt="evolved", reason="test")
    exp.original_scores = [5.0, 5.0, 5.0]
    exp.evolved_scores = [7.0, 7.0, 7.0]
    eng5._experiments["bee_agent"] = exp
    asyncio.new_event_loop().run_until_complete(eng5._decide_experiment(exp))
    if "bee_agent" in eng5._prompt_wins:
        pw_r = eng5._prompt_wins["bee_agent"]
        if pw_r.status == "pending_apply" and pw_r.winner == "evolved":
            OK("Evolved wins -> pending_apply when auto_apply=false")
        else:
            FAIL_MSG("Evolved win status wrong", f"status={pw_r.status}, winner={pw_r.winner}")
    else:
        FAIL_MSG("No PromptWin created for evolved win")

    # 7. Decide experiment — original wins -> lost
    eng6 = _make_engine()
    exp2 = PromptExperiment(
        agent_id="orig_agent", original_prompt="orig",
        evolved_prompt="evolved", reason="test")
    exp2.original_scores = [7.0, 7.0, 7.0]
    exp2.evolved_scores = [6.0, 6.0, 6.0]
    eng6._experiments["orig_agent"] = exp2
    asyncio.new_event_loop().run_until_complete(eng6._decide_experiment(exp2))
    if "orig_agent" in eng6._prompt_wins:
        pw_r2 = eng6._prompt_wins["orig_agent"]
        if pw_r2.status == "lost" and pw_r2.winner == "original":
            OK("Original wins -> status='lost'")
        else:
            FAIL_MSG("Original win status wrong", f"status={pw_r2.status}")
    else:
        FAIL_MSG("No PromptWin created for original win")

    # 8. Timeout -> PromptWin(status="timeout")
    eng7 = _make_engine()
    exp3 = PromptExperiment(
        agent_id="timeout_agent", original_prompt="orig",
        evolved_prompt="evolved", reason="test")
    exp3.started_at = time.monotonic() - 8000  # >7200s ago
    eng7._experiments["timeout_agent"] = exp3
    asyncio.new_event_loop().run_until_complete(eng7._check_experiments())
    if "timeout_agent" in eng7._prompt_wins:
        pw_t = eng7._prompt_wins["timeout_agent"]
        if pw_t.status == "timeout":
            OK("Timeout -> status='timeout'")
        else:
            FAIL_MSG("Timeout status wrong", f"status={pw_t.status}")
    else:
        FAIL_MSG("No PromptWin created for timeout")

    # 9. Config min_delta=2.0 + scores delta=1.0 -> original wins
    eng8 = _make_engine({"prompt_apply_min_delta": 2.0})
    exp4 = PromptExperiment(
        agent_id="delta_agent", original_prompt="orig",
        evolved_prompt="evolved", reason="test")
    exp4.original_scores = [5.0, 5.0, 5.0]
    exp4.evolved_scores = [6.0, 6.0, 6.0]  # delta=1.0 < 2.0
    eng8._experiments["delta_agent"] = exp4
    asyncio.new_event_loop().run_until_complete(eng8._decide_experiment(exp4))
    if "delta_agent" in eng8._prompt_wins:
        pw_d = eng8._prompt_wins["delta_agent"]
        if pw_d.status == "lost":
            OK("Config min_delta=2.0 + delta=1.0 -> original wins (lost)")
        else:
            FAIL_MSG("Min delta config not respected", f"status={pw_d.status}")
    else:
        FAIL_MSG("No PromptWin created for delta test")

    # 10. get_status() includes "prompt_wins" key
    eng9 = _make_engine()
    status = eng9.get_status()
    if "prompt_wins" in status:
        OK("get_status() includes 'prompt_wins' key")
    else:
        FAIL_MSG("get_status() missing prompt_wins", str(status.keys()))


def main():
    print("\n=== test_learning_prompt_apply ===")
    run()
    total = len(PASS) + len(FAIL)
    print(f"\nResult: {len(PASS)}/{total} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:")
        for f in FAIL:
            print(f"  - {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
