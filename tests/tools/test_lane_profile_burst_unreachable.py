"""The SHIPPED catalog's burst profiles are never a steady-state choice (claude-rco-2 on #1746).

The consumer suites read a frozen fixture without burst profiles, so this pins, on the shipped
catalog itself, that classify, check_request and the pacer never select a burst profile.
"""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.lane_effective_model import classify
from tools.lane_profile_catalog import load_catalog
from tools.wd_capacity_pacing import recommend
from tools.wd_lane_relaunch import check_request

ROOT = Path(__file__).resolve().parents[2]
CATALOG, _ = load_catalog(ROOT / "configs" / "lane_profile_catalog.json")
PROFILES = CATALOG["capacity_policy"]["profiles"]
BURSTS = [(lane, pid) for lane, spec in sorted(CATALOG["lanes"].items()) for pid in spec.get("burst_profiles", [])]


def test_the_shipped_catalog_has_burst_profiles():
    assert BURSTS                                                    # otherwise the tests below are vacuous


@pytest.mark.parametrize(("lane", "burst"), BURSTS)
def test_a_running_burst_profile_is_never_classified_allowed(lane, burst):
    p = PROFILES[burst]
    resolved = {"resolved": True, "provider": p["provider"], "model": p["model"], "effort": p["effort"]}
    assert classify(CATALOG, lane, resolved)["verdict"] == "not_in_lane_allowlist"


@pytest.mark.parametrize(("lane", "burst"), BURSTS)
def test_a_relaunch_to_a_burst_profile_parks(lane, burst):
    request = {"lane": lane, "current_profile": CATALOG["lanes"][lane]["default"], "target_profile": burst}
    verdict = check_request(CATALOG, request, [], now=datetime.now(timezone.utc))
    assert verdict["verdict"] == "park" and verdict["reasons"] == ["target_not_allowed"]


@pytest.mark.parametrize("start", ["default", "ceiling"])
def test_the_pacer_never_targets_a_burst_profile(start):
    paced = {f"{p['provider']}/{limit['id']}/{window}": {"verdict": "underused", "short": False,
                                                         "provider": p["provider"], "limit_id": limit["id"],
                                                         "window": window}
             for p in PROFILES.values() for limit in p["limits"] for window in limit["windows"]}
    current = {lane: spec["default"] if start == "default" else spec["allowed_profiles"][0]
               for lane, spec in CATALOG["lanes"].items()}
    for lane, entry in recommend(CATALOG, paced, current).items():
        assert entry["target_profile"] in (None, *CATALOG["lanes"][lane]["allowed_profiles"])
