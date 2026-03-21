"""Tests for DomainCapsule and SmartRouterV2."""
import os
import sys
import tempfile
import textwrap

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.domain_capsule import DomainCapsule, CAPSULE_DIR
from core.smart_router_v2 import SmartRouterV2, RouteResult

OK = 0
FAIL = 0


def check(name, condition, detail=""):
    global OK, FAIL
    if condition:
        OK += 1
        print(f"  OK {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} — {detail}")


# ── Capsule Loading ─────────────────────────────────────────

print("\n=== DomainCapsule Loading ===")

for profile in ["gadget", "cottage", "home", "factory"]:
    try:
        c = DomainCapsule.load(profile)
        check(f"Load {profile}", c.domain == profile,
              f"expected {profile}, got {c.domain}")
    except Exception as e:
        check(f"Load {profile}", False, str(e))


# ── Schema Validation ───────────────────────────────────────

print("\n=== Schema Validation ===")

# Missing required field
try:
    DomainCapsule({"domain": "test", "version": "1", "layers": {}})
    check("Missing key_decisions raises", False, "no error raised")
except ValueError as e:
    check("Missing key_decisions raises", "key_decisions" in str(e))

# Invalid layer name
try:
    DomainCapsule({
        "domain": "test", "version": "1",
        "layers": {"magic_layer": {"enabled": True, "priority": 1}},
        "key_decisions": [],
    })
    check("Invalid layer name raises", False, "no error raised")
except ValueError as e:
    check("Invalid layer name raises", "magic_layer" in str(e))

# Valid minimal capsule
try:
    c = DomainCapsule({
        "domain": "minimal", "version": "1",
        "layers": {"llm_reasoning": {"enabled": True, "priority": 1}},
        "key_decisions": [{"id": "test_q", "primary_layer": "llm_reasoning"}],
    })
    check("Minimal capsule loads", c.domain == "minimal")
except Exception as e:
    check("Minimal capsule loads", False, str(e))


# ── Layer Queries ───────────────────────────────────────────

print("\n=== Layer Queries ===")

c = DomainCapsule.load("cottage")

ordered = c.get_layers_by_priority()
check("Layers sorted by priority", ordered[0].priority <= ordered[-1].priority,
      f"got {[l.priority for l in ordered]}")
check("All 5 layers enabled (cottage)", len(ordered) == 5)
check("rule_constraints is priority 1", ordered[0].name == "rule_constraints")

c_gadget = DomainCapsule.load("gadget")
ordered_g = c_gadget.get_layers_by_priority()
check("Gadget has 3 enabled layers", len(ordered_g) == 3,
      f"got {len(ordered_g)}")
check("Gadget LLM disabled", not c_gadget.is_layer_enabled("llm_reasoning"))


# ── Decision Matching ───────────────────────────────────────

print("\n=== Decision Matching ===")

c = DomainCapsule.load("cottage")

m = c.match_decision("heating cost kwh energy price")
check("Cottage: heating keywords match", m is not None and m.decision_id == "heating_cost",
      f"got {m}")

m = c.match_decision("frost pipe freeze")
check("Cottage: frost keywords match", m is not None and m.decision_id == "frost_protection",
      f"got {m}")

m = c.match_decision("electricity consumption watt")
check("Cottage: energy consumption match", m is not None and m.decision_id == "energy_consumption",
      f"got {m}")

m = c.match_decision("completely unrelated topic about cooking")
check("No match for unrelated query", m is None, f"got {m}")

c_home = DomainCapsule.load("home")
m = c_home.match_decision("heating schedule cheapest price")
check("Home: heating schedule match", m is not None and m.decision_id == "heating_schedule",
      f"got {m}")

m = c_home.match_decision("device forgotten safety check")
check("Home: safety check match", m is not None and m.decision_id == "safety_check",
      f"got {m}")


# ── SmartRouter v2 ──────────────────────────────────────────

print("\n=== SmartRouter v2 ===")

c = DomainCapsule.load("cottage")
router = SmartRouterV2(c)

r = router.route("calculate heating cost kwh price")
check("Router: math keywords -> model_based",
      r.layer == "model_based",
      f"got {r.layer} ({r.reason})")

r = router.route("is the trend normal average deviation")
check("Router: stat keywords -> statistical",
      r.layer == "statistical",
      f"got {r.layer} ({r.reason})")

r = router.route("safety check alert threshold limit")
check("Router: rule keywords -> rule_constraints",
      r.layer == "rule_constraints",
      f"got {r.layer} ({r.reason})")

r = router.route("tell me something interesting")
check("Router: no match -> priority fallback",
      r.reason == "capsule_priority_fallback",
      f"got {r.reason}")

check("Router: has routing_time_ms", r.routing_time_ms >= 0)

# RouteResult serialization
d = r.to_dict()
check("RouteResult.to_dict() has layer", "layer" in d)
check("RouteResult.to_dict() has routing_time_ms", "routing_time_ms" in d)

# Router stats
stats = router.stats()
check("Router stats has total_routes", stats["total_routes"] == 4,
      f"got {stats['total_routes']}")
check("Router stats has capsule_domain", stats["capsule_domain"] == "cottage")

# Gadget: LLM disabled -> fallback does not use LLM
c_gadget = DomainCapsule.load("gadget")
r_gadget = SmartRouterV2(c_gadget)
r = r_gadget.route("tell me about batteries")
check("Gadget: general query does not route to LLM",
      r.layer != "llm_reasoning",
      f"got {r.layer}")


# ── Capsule Serialization ───────────────────────────────────

print("\n=== Serialization ===")

c = DomainCapsule.load("factory")
d = c.to_dict()
check("to_dict has domain", d["domain"] == "factory")
check("to_dict has layers list", isinstance(d["layers"], list) and len(d["layers"]) == 5)
check("to_dict has key_decisions", len(d["key_decisions"]) == 4,
      f"got {len(d['key_decisions'])}")
check("to_dict has rules_count", d["rules_count"] == 3)
check("to_dict has models_count", d["models_count"] == 3)


# ── load_from_settings ──────────────────────────────────────

print("\n=== load_from_settings ===")

try:
    c = DomainCapsule.load_from_settings()
    check("load_from_settings works", c.domain in ["gadget", "cottage", "home", "factory"],
          f"got {c.domain}")
except Exception as e:
    check("load_from_settings works", False, str(e))


# ── Summary ─────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Results: {OK} ok, {FAIL} fail")
if FAIL:
    sys.exit(1)
