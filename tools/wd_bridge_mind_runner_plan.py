"""Dormant pure Source planner; never invokes gh, a gate, a helper or a merge.

All observations, including a gate-shaped DTO, are caller-declared simulation
inputs. CANDIDATE is only a suggestion to independently revalidate with the
installed gate, not gate PASS, signature verification, admission or execution.
No apply/CLI entrypoint exists. Every output has can_execute=False.
"""
from datetime import datetime, timezone
import re

REQUIRED = ("lock-resolver-check", "security-scan", "test (3.11)",
            "test (3.12)", "test (3.13)", "unified")
SIGNED_CLASSES = ("a", "protected", "off_allowlist")
MAX_ATTEMPTS = 2


def _head(value):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{40}", value) is not None


def _utc(value):
    if type(value) is not str or len(value) > 40 or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|[+-]\d{2}:\d{2})", value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _refuse(reason):
    return {"schema": "wd.mind-runner-source-plan.v1", "status": "REFUSE",
            "reason": reason, "can_execute": False, "authority": "none"}


def plan(item, read, gate, state):
    """Produce a fixed-label, bounded simulation plan without side effects."""
    if type(state) is not dict or type(state.get("kill_switch")) is not bool:
        return _refuse("runner_state_unknown")
    if state["kill_switch"]:
        return _refuse("kill_switch")
    if (type(item) is not dict or type(item.get("pr")) is not int or item["pr"] <= 0
            or not _head(item.get("head"))):
        return _refuse("mind_item_malformed")
    if type(read) is not dict or read.get("ok") is not True:
        return _refuse("github_read_failed")
    if type(read.get("pr")) is not int or read["pr"] != item["pr"]:
        return _refuse("github_pr_not_mind_pr")
    cycle, observed = _utc(item.get("cycle_utc")), _utc(read.get("utc"))
    if cycle is None or observed is None:
        return _refuse("observation_time_invalid")
    if observed <= cycle:
        return _refuse("mind_cycle_not_older_than_read")
    if read.get("state") != "OPEN":
        return _refuse("pr_not_open")
    if read.get("draft") is True:
        return _refuse("draft")
    if read.get("draft") is not False:
        return _refuse("draft_unknown")
    if read.get("head") != item["head"]:
        return _refuse("head_moved_since_mind_cycle")
    if not _head(read.get("base")):
        return _refuse("github_base_unknown")
    if read.get("base_ref") != "main":
        return _refuse("github_base_ref_not_main")
    completed, attempts = state.get("merged"), state.get("attempts")
    if (type(completed) not in (set, list) or len(completed) > 4096 or type(attempts) is not dict
            or len(attempts) > 4096):
        return _refuse("runner_state_unknown")
    for entry in completed:
        if (type(entry) not in (tuple, list) or len(entry) != 2 or type(entry[0]) is not int
                or entry[0] <= 0 or not _head(entry[1])):
            return _refuse("runner_state_unknown")
        if entry[0] == item["pr"] and entry[1] == read["head"]:
            return _refuse("already_merged_at_head")
    if any(type(pr) is not int or pr <= 0 or type(count) is not int or count < 0
           for pr, count in attempts.items()):
        return _refuse("runner_state_unknown")
    if attempts.get(item["pr"], 0) >= MAX_ATTEMPTS:
        return _refuse("attempt_budget_spent")
    checks = read.get("checks")
    if type(checks) is not dict or not set(REQUIRED).issubset(checks):
        return _refuse("required_checks_incomplete")
    if any(checks[name] != "SUCCESS" for name in REQUIRED):
        return _refuse("required_checks_not_green")
    if read.get("mergeable") != "MERGEABLE" or read.get("merge_state") != "CLEAN":
        return _refuse("github_not_clean")
    if type(gate) is not dict or gate.get("ok") is not True or gate.get("error"):
        return _refuse("gate_failed_closed")
    if gate.get("head") != read["head"]:
        return _refuse("gate_head_not_runner_head")
    if type(gate.get("pr")) is not int or gate["pr"] != item["pr"]:
        return _refuse("gate_pr_not_runner_pr")
    if not _head(gate.get("base")) or gate["base"] != read["base"]:
        return _refuse("gate_base_not_runner_base")
    if gate.get("veto_clear") is not True:
        return _refuse("veto_not_clear")
    if gate.get("consensus") is not True:
        return _refuse("consensus_missing")
    classification = gate.get("class")
    if classification not in ("allowlist",) + SIGNED_CLASSES:
        return _refuse("class_unknown")
    if classification in SIGNED_CLASSES and gate.get("operator_signed") is not True:
        return _refuse("operator_signature_required")
    return {"schema": "wd.mind-runner-source-plan.v1", "status": "CANDIDATE",
            "pr": item["pr"], "head": read["head"], "base": read["base"],
            "next_action": "independently_revalidate_with_installed_gate",
            "gate_observation": "caller_declared_not_authenticated",
            "can_execute": False, "authority": "none"}
