#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Model registry and read-only value analysis (lane profile switching PR-6).

``configs/model_registry.json`` records, per provider model and reasoning effort,
a public quality score and cost per task from ONE benchmark source, so every row
is comparable. It also records the provider's coding-agent score where
published. This module validates that file and answers three questions, all
read-only:

* **frontier**: per provider, which model/effort rows are not dominated? Row A
  dominates B when A is at least as good and at most as expensive, and strictly
  better on one of the two. A dominated row is never worth selecting: another
  row of the same provider gives more for the same money.
* **lane value table**: for each catalog lane and its CURRENT model/effort, the
  best quality at no higher cost ("more for the same money") and the lowest cost
  at no lower quality ("the same for less"). Neither suggestion ever lowers
  quality, so reviewer lanes (raise-or-same) are respected by construction.
* **catalog diff**: frontier rows the catalog lacks (``propose_add``), catalog
  profiles that are dominated (``dominated``), and catalog profiles with no
  registry row (``unrated``).

Codex availability is checked against the CLI's own ``~/.codex/models_cache.json``.
Claude publishes no local model list, so Claude rows are ``cli_available: None``
(unverified) until a real turn observes them.

Nothing here selects, writes or signals anything. A public benchmark is not our
workload: it informs a proposal, while our qualification and measured quota burn
decide (spec v3). Every report carries ``execution_allowed: false``.

Schema ``wd.model-registry.v2`` (Bridge v2 F3 foundation) keeps the v1 tables
(``benchmark``, ``coding_benchmark``, ``models``) byte-for-byte in shape and marks
them ``historical`` with their source measurement date. The value analysis above
reads only those tables, so it is identical for v1 and v2, and v1 files still
load. v2 adds four tables:

* ``historical``: the status, source measurement date (an upper bound when the
  source's own date was not recorded) and the cost basis of the v1 tables. Their
  ``usd_per_task`` is an API list price, never a quota unit.
* ``pools``: quota pools with ``limit_id``, window, tier and a verification
  state. A pool is ``verified`` only by an operator reading, a local measurement
  or an F21 receipt; nothing else can upgrade it.
* ``candidates``: models that are not rated for our workload, for example new
  Grok or Haiku placeholders. Their admission is always ``none`` and their
  capability ``unknown``.
* ``observations``: typed quality, cost, context, tier, pool and limit values,
  each with provenance, a measurement date, a TTL and a stated uncertainty.
  Only a ``measured`` observation inside its TTL counts as known. An expired
  one is ``stale`` and a missing one is ``unknown``; neither is guessed.
  API-dollar costs and quota units never mix.

``model_table`` renders one row per model and effort with explicit unknown
cells (the plan's ``wd-model models`` table). It is advisory and grants nothing.

See docs/BRIDGE_MODEL_REGISTRY.md (schema v2; v1 files still load).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lane_profile_catalog import LANES, load_catalog  # noqa: E402

SCHEMA_V1 = "wd.model-registry.v1"
SCHEMA_V2 = "wd.model-registry.v2"
SCHEMA = SCHEMA_V2  # the shipped schema; v1 files still load (deliberate read compatibility)
SCHEMAS = (SCHEMA_V1, SCHEMA_V2)
REPORT_SCHEMA = "wd.model-value-report.v1"
PROVIDERS = ("codex", "claude", "grok")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MAX_REGISTRY_BYTES = 256 * 1024
MAX_CACHE_BYTES = 8 * 1024 * 1024
TOP_KEYS = frozenset({"schema", "updated_at", "benchmark", "coding_benchmark", "models"})
BENCHMARK_KEYS = frozenset({"name", "version", "quality_metric", "cost_metric", "fetched_at", "sources"})
CODING_KEYS = frozenset({"name", "effort", "fetched_at", "sources"})
MODEL_KEYS = frozenset({"provider", "model", "coding_agent_index", "efforts"})
# Optional per-model label of the benchmark variant the scores come from, e.g. "with_fallback" when the
# Artificial Analysis release page labels the effort rows that way (codex-tools-1 N2 on #1743).
OPTIONAL_MODEL_KEYS = frozenset({"benchmark_variant"})
EFFORT_KEYS = frozenset({"intelligence_index", "usd_per_task"})

# ---- v2 (F3) tables
V2_TOP_KEYS = TOP_KEYS | {"historical", "pools", "candidates", "observations"}
HISTORICAL_KEYS = frozenset({"status", "applies_to", "source_measured_at", "source_measured_at_basis",
                             "cost_basis"})
HISTORICAL_TABLES = ["benchmark", "coding_benchmark", "models"]
POOL_KEYS = frozenset({"provider", "limit_id", "window", "tier", "verification", "provenance",
                       "measured_at", "ttl_seconds"})
CANDIDATE_KEYS = frozenset({"provider", "model", "admission", "capability", "pool", "note"})
OBSERVATION_KEYS = frozenset({"id", "subject", "kind", "class", "value", "unit", "status", "provenance",
                              "measured_at", "ttl_seconds", "uncertainty"})
SUBJECT_KEYS = frozenset({"provider", "model", "effort", "pool"})
PROVENANCE_KEYS = frozenset({"kind", "reference", "observer"})
UNCERTAINTY_KEYS = frozenset({"kind", "low", "high", "note"})
TIERS = ("economy", "standard", "strong", "premium")
WINDOWS = ("5h", "daily", "weekly", "monthly", "unknown")
OBSERVATION_STATUSES = ("measured", "historical", "unverified", "unknown")
PROVENANCE_KINDS = ("external_benchmark", "provider_documentation", "operator_reading", "local_measurement",
                    "f21_receipt", "plan_transcription")
MEASURING_KINDS = ("operator_reading", "local_measurement", "f21_receipt")
UNITS = {
    "quality": ("intelligence_index", "coding_agent_index", "score_0_100"),
    "cost": ("usd_per_task_api_price", "usd_per_mtok_api_price", "pool_points_per_mtok"),
    "context": ("tokens",),
    "tier": ("label",),
    "pool": ("pool_id",),
    "limit": ("pool_points", "requests", "tokens", "percent_of_pool"),
}
API_DOLLAR_UNITS = ("usd_per_task_api_price", "usd_per_mtok_api_price")
QUOTA_UNITS = ("pool_points_per_mtok", "pool_points", "percent_of_pool")
POOL_SUBJECT_KINDS = ("limit",)
ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
TASK_CLASS_RE = re.compile(r"task:[a-z0-9][a-z0-9_-]{0,47}")
WHEN_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}Z)?")
MAX_TTL_SECONDS = 400 * 86400
FUTURE_SKEW = timedelta(minutes=5)
STATE_RANK = {"fresh": 0, "historical": 1, "unverified": 2, "stale": 3, "unknown": 4}
UNCERTAINTY_KINDS = ("none_stated", "interval", "exact", "unknown")
# A measured (known) value needs a STATED uncertainty: an interval, or "exact" with a justification.
MEASURED_UNCERTAINTY = ("interval", "exact")
CATEGORICAL_KINDS = ("tier", "pool")  # no interval: containment is meaningless for a label
DEFAULT_REGISTRY = Path(__file__).resolve().parents[1] / "configs" / "model_registry.json"
DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "configs" / "lane_profile_catalog.json"


class RegistryError(ValueError):
    """The registry is malformed or unsafe; the analysis refuses rather than guesses."""


# ---------------------------------------------------------------- loading


def _pairs(pairs: list) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise RegistryError(f"duplicate key: {key}")
        result[key] = value
    return result


def _constant(name: str) -> Any:
    raise RegistryError(f"non-finite number: {name}")


def _read_json(path: Path, limit: int, label: str) -> tuple[Any, bytes]:
    try:
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise RegistryError(f"{label} path is a symlink or reparse point")
        # Non-blocking open and a regular-file check: a FIFO or device is refused, never waited on.
        # O_NOFOLLOW (POSIX) fails the open (ELOOP) if a symlink replaced the file after the check
        # above. Windows has no such flag: a reparse point swapped in between the check and the
        # open is NOT closed here (disclosed in docs/BRIDGE_MODEL_REGISTRY.md).
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
                     | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise RegistryError(f"{label} is not a regular file")
            data = stream.read(limit + 1)
    except OSError as exc:
        raise RegistryError(f"{label} unreadable: {exc.__class__.__name__}") from None
    if len(data) > limit:
        raise RegistryError(f"{label} exceeds the size bound")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant), data
    except RegistryError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise RegistryError(f"{label} is not UTF-8 JSON: {exc.__class__.__name__}") from None


def _short(value: Any) -> str:
    """An input-derived key in an error message, bounded (never echoed at length)."""
    return str(value)[:64]


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise RegistryError(f"{label} must be a non-empty string")
    return value


def _score(value: Any, label: str, upper: float) -> float:
    # Range first: an int of any size compares exactly with a float bound, while math.isfinite(10**1000) raises
    # OverflowError (codex-tools-1 0F41B0C1). NaN and +-inf already fail the range; isfinite stays as a guard.
    if type(value) not in (int, float) or not 0 <= value <= upper or not math.isfinite(value):
        raise RegistryError(f"{label} must be a finite number in 0..{upper}")
    return float(value)


def _exact_keys(value: Any, keys: frozenset, label: str, optional: frozenset = frozenset()) -> dict:
    if not isinstance(value, dict):
        raise RegistryError(f"{label} must be an object")
    if not keys <= set(value) <= keys | optional:
        raise RegistryError(f"{label} keys must be exactly {sorted(keys)}"
                            + (f", optionally with {sorted(optional)}" if optional else ""))
    return value


def _sources(value: Any, label: str) -> None:
    if not isinstance(value, list) or not value or not all(
            isinstance(url, str) and url.startswith("https://") and len(url) <= 512 for url in value):
        raise RegistryError(f"{label} must be a non-empty list of https URLs")


def validate_registry(registry: Any) -> dict:
    """Refuse anything but an exact v1 or v2 registry shape; return the registry unchanged."""
    if not isinstance(registry, dict):
        raise RegistryError("registry must be an object")
    if registry.get("schema") not in SCHEMAS:
        raise RegistryError(f"schema must be one of {SCHEMAS}")
    _exact_keys(registry, V2_TOP_KEYS if registry["schema"] == SCHEMA_V2 else TOP_KEYS, "registry")
    _validate_v1_tables(registry)
    if registry["schema"] == SCHEMA_V2:
        _validate_v2_tables(registry)
    return registry


def _validate_v1_tables(registry: dict) -> None:
    """The v1 tables, identical in v1 and v2 (v2 marks them historical)."""
    _text(registry["updated_at"], "updated_at")
    benchmark = _exact_keys(registry["benchmark"], BENCHMARK_KEYS, "benchmark")
    for key in BENCHMARK_KEYS - {"sources"}:
        _text(benchmark[key], f"benchmark.{key}")
    _sources(benchmark["sources"], "benchmark.sources")
    coding = _exact_keys(registry["coding_benchmark"], CODING_KEYS, "coding_benchmark")
    for key in CODING_KEYS - {"sources"}:
        _text(coding[key], f"coding_benchmark.{key}")
    _sources(coding["sources"], "coding_benchmark.sources")
    models = registry["models"]
    if not isinstance(models, dict) or not models:
        raise RegistryError("models must be a non-empty object")
    for key, entry in models.items():
        _exact_keys(entry, MODEL_KEYS, f"models.{_short(key)}", OPTIONAL_MODEL_KEYS)
        if "benchmark_variant" in entry:
            _text(entry["benchmark_variant"], f"models.{_short(key)}.benchmark_variant")
        if entry["provider"] not in PROVIDERS:
            raise RegistryError(f"models.{_short(key)}.provider must be one of {PROVIDERS}")
        _text(entry["model"], f"models.{_short(key)}.model")
        if key != f"{entry['provider']}/{entry['model']}":
            raise RegistryError(f"models.{_short(key)} key must be '<provider>/<model>'")
        if entry["coding_agent_index"] is not None:
            _score(entry["coding_agent_index"], f"models.{_short(key)}.coding_agent_index", 100)
        efforts = entry["efforts"]
        if not isinstance(efforts, dict) or not efforts:
            raise RegistryError(f"models.{_short(key)}.efforts must be a non-empty object")
        for effort, row in efforts.items():
            if effort not in EFFORTS:
                raise RegistryError(f"models.{_short(key)}.efforts has unknown effort {_short(effort)!r}")
            _exact_keys(row, EFFORT_KEYS, f"models.{_short(key)}.efforts.{effort}")
            _score(row["intelligence_index"], f"models.{_short(key)}.efforts.{effort}.intelligence_index", 100)
            _score(row["usd_per_task"], f"models.{_short(key)}.efforts.{effort}.usd_per_task", 1000)


# ---------------------------------------------------------------- v2 tables (F3)


def _when(value: Any, label: str) -> datetime:
    """A UTC date (``YYYY-MM-DD``, read as its start) or ``YYYY-MM-DDTHH:MM:SSZ``."""
    if not isinstance(value, str) or not WHEN_RE.fullmatch(value):
        raise RegistryError(f"{label} must be YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ" if "T" in value else "%Y-%m-%d")
    except ValueError:
        raise RegistryError(f"{label} is not a valid date") from None
    return parsed.replace(tzinfo=timezone.utc)


def _optional_text(value: Any, label: str) -> None:
    if value is not None:
        _text(value, label)


def _provenance(value: Any, label: str) -> dict:
    _exact_keys(value, PROVENANCE_KEYS, label)
    if value["kind"] not in PROVENANCE_KINDS:
        raise RegistryError(f"{label}.kind must be one of {PROVENANCE_KINDS}")
    _text(value["reference"], f"{label}.reference")
    _optional_text(value["observer"], f"{label}.observer")
    return value


def _number(value: Any, label: str, lower: float, upper: float) -> float:
    # Range first, as in _score: a huge int is refused as out of range instead of raising OverflowError.
    if type(value) not in (int, float) or not lower <= value <= upper or not math.isfinite(value):
        raise RegistryError(f"{label} must be a finite number in {lower}..{upper}")
    return float(value)


def _validate_v2_tables(registry: dict) -> None:
    models = registry["models"]
    historical = _exact_keys(registry["historical"], HISTORICAL_KEYS, "historical")
    if historical["status"] != "historical" or historical["applies_to"] != HISTORICAL_TABLES:
        raise RegistryError(f"historical must mark exactly {HISTORICAL_TABLES} as historical")
    _when(historical["source_measured_at"], "historical.source_measured_at")
    _text(historical["source_measured_at_basis"], "historical.source_measured_at_basis")
    if historical["cost_basis"] != "api_price_not_quota":
        raise RegistryError("historical.cost_basis must be 'api_price_not_quota': API dollars are never quota units")

    pools = registry["pools"]
    if not isinstance(pools, dict):
        raise RegistryError("pools must be an object")
    for pool_id, pool in pools.items():
        if not ID_RE.fullmatch(pool_id):
            raise RegistryError(f"pools key {pool_id[:64]!r} is not a lowercase identifier")
        _exact_keys(pool, POOL_KEYS, f"pools.{pool_id}")
        if pool["provider"] not in PROVIDERS:
            raise RegistryError(f"pools.{pool_id}.provider must be one of {PROVIDERS}")
        _optional_text(pool["limit_id"], f"pools.{pool_id}.limit_id")
        if pool["window"] not in WINDOWS:
            raise RegistryError(f"pools.{pool_id}.window must be one of {WINDOWS}")
        if pool["tier"] not in TIERS + ("unknown",):
            raise RegistryError(f"pools.{pool_id}.tier must be one of {TIERS} or 'unknown'")
        provenance = _provenance(pool["provenance"], f"pools.{pool_id}.provenance")
        if pool["verification"] not in ("unverified", "verified"):
            raise RegistryError(f"pools.{pool_id}.verification must be 'unverified' or 'verified'")
        if pool["measured_at"] != "unknown":
            _when(pool["measured_at"], f"pools.{pool_id}.measured_at")
        pool_ttl = pool["ttl_seconds"]
        if pool_ttl is not None and (type(pool_ttl) is not int or not 1 <= pool_ttl <= MAX_TTL_SECONDS):
            raise RegistryError(f"pools.{pool_id}.ttl_seconds must be null or an integer 1..{MAX_TTL_SECONDS}")
        # A verified pool is a measurement like any other: provenance, a date and a TTL, and it expires.
        if pool["verification"] == "verified" and (provenance["kind"] not in MEASURING_KINDS
                                                   or pool["limit_id"] is None
                                                   or pool["measured_at"] == "unknown" or pool_ttl is None):
            raise RegistryError(f"pools.{pool_id} is verified only by a measuring provenance with a limit_id, "
                                f"a measured_at date and a ttl_seconds")

    candidates = registry["candidates"]
    if not isinstance(candidates, dict):
        raise RegistryError("candidates must be an object")
    for key, candidate in candidates.items():
        _exact_keys(candidate, CANDIDATE_KEYS, f"candidates.{_short(key)}")
        if candidate["provider"] not in PROVIDERS:
            raise RegistryError(f"candidates.{_short(key)}.provider must be one of {PROVIDERS}")
        _text(candidate["model"], f"candidates.{_short(key)}.model")
        if key != f"{candidate['provider']}/{candidate['model']}" or key in models:
            raise RegistryError(f"candidates.{_short(key)} key must be '<provider>/<model>' and not a rated model")
        if candidate["admission"] != "none" or candidate["capability"] != "unknown":
            raise RegistryError(f"candidates.{_short(key)} must have admission 'none' and capability 'unknown'")
        if candidate["pool"] is not None and (not isinstance(candidate["pool"], str) or candidate["pool"] not in pools
                                              or pools[candidate["pool"]]["provider"] != candidate["provider"]):
            raise RegistryError(f"candidates.{_short(key)}.pool must name a pool of the same provider")
        _optional_text(candidate["note"], f"candidates.{_short(key)}.note")

    observations = registry["observations"]
    if not isinstance(observations, list):
        raise RegistryError("observations must be a list")
    seen: set = set()
    for index, observation in enumerate(observations):
        label = f"observations[{index}]"
        _exact_keys(observation, OBSERVATION_KEYS, label)
        if not isinstance(observation["id"], str) or not ID_RE.fullmatch(observation["id"]) \
                or observation["id"] in seen:
            raise RegistryError(f"{label}.id must be a unique lowercase identifier")
        seen.add(observation["id"])
        _validate_observation(observation, label, models, candidates, pools)


def _validate_observation(obs: dict, label: str, models: dict, candidates: dict, pools: dict) -> None:
    kind, unit, status, value = obs["kind"], obs["unit"], obs["status"], obs["value"]
    if not isinstance(kind, str) or kind not in UNITS or unit not in UNITS[kind]:
        raise RegistryError(f"{label} kind/unit must be one of {UNITS}")
    subject = _exact_keys(obs["subject"], SUBJECT_KEYS, f"{label}.subject")
    if subject["provider"] not in PROVIDERS:
        raise RegistryError(f"{label}.subject.provider must be one of {PROVIDERS}")
    if subject["effort"] is not None and subject["effort"] not in EFFORTS:
        raise RegistryError(f"{label}.subject.effort must be null or one of {EFFORTS}")
    if subject["pool"] is not None and (not isinstance(subject["pool"], str) or subject["pool"] not in pools
                                        or pools[subject["pool"]]["provider"] != subject["provider"]):
        raise RegistryError(f"{label}.subject.pool must name a pool of the same provider")
    if subject["model"] is None:
        if kind not in POOL_SUBJECT_KINDS:
            raise RegistryError(f"{label}.subject.model is required for {kind}")
    elif f"{subject['provider']}/{subject['model']}" not in models \
            and f"{subject['provider']}/{subject['model']}" not in candidates:
        raise RegistryError(f"{label}.subject must name a rated model or a candidate")
    # API dollars are never quota units: a dollar cost has no pool; a quota unit needs one.
    if unit in API_DOLLAR_UNITS and subject["pool"] is not None:
        raise RegistryError(f"{label}: an API-dollar cost is never attributed to a quota pool")
    if (unit in QUOTA_UNITS or kind in POOL_SUBJECT_KINDS) and subject["pool"] is None:
        raise RegistryError(f"{label}: a quota unit or limit needs subject.pool")
    if kind == "pool" and subject["pool"] is not None:
        raise RegistryError(f"{label}: a pool-membership observation names its pool in value")
    if kind == "quality":
        if obs["class"] not in ("general", "coding_agent") and not (
                isinstance(obs["class"], str) and TASK_CLASS_RE.fullmatch(obs["class"])):
            raise RegistryError(f"{label}.class must be general, coding_agent or task:<name>")
    elif obs["class"] is not None:
        raise RegistryError(f"{label}.class is only for quality observations")

    if status not in OBSERVATION_STATUSES:
        raise RegistryError(f"{label}.status must be one of {OBSERVATION_STATUSES}")
    if (status == "unknown") != (value is None):
        raise RegistryError(f"{label}: status 'unknown' and a null value go together")
    if value is not None:
        if kind == "quality":
            _number(value, f"{label}.value", 0, 100)
        elif kind == "cost":
            _number(value, f"{label}.value", 0, 1_000_000)
        elif kind == "context":
            if type(value) is not int or not 1 <= value <= 100_000_000:
                raise RegistryError(f"{label}.value must be an integer token count")
        elif kind == "tier":
            if value not in TIERS:
                raise RegistryError(f"{label}.value must be one of {TIERS}")
        elif kind == "pool":
            if not isinstance(value, str) or value not in pools or pools[value]["provider"] != subject["provider"]:
                raise RegistryError(f"{label}.value must name a pool of the same provider")
        else:
            _number(value, f"{label}.value", 0, 1e12)

    provenance = _provenance(obs["provenance"], f"{label}.provenance")
    if obs["measured_at"] != "unknown":
        _when(obs["measured_at"], f"{label}.measured_at")
    ttl = obs["ttl_seconds"]
    if ttl is not None and (type(ttl) is not int or not 1 <= ttl <= MAX_TTL_SECONDS):
        raise RegistryError(f"{label}.ttl_seconds must be null or an integer 1..{MAX_TTL_SECONDS}")
    uncertainty = _exact_keys(obs["uncertainty"], UNCERTAINTY_KEYS, f"{label}.uncertainty")
    if uncertainty["kind"] not in UNCERTAINTY_KINDS:
        raise RegistryError(f"{label}.uncertainty.kind must be one of {UNCERTAINTY_KINDS}")
    if uncertainty["kind"] == "interval" and kind in CATEGORICAL_KINDS:
        raise RegistryError(f"{label}.uncertainty interval is only for numeric kinds")
    if uncertainty["kind"] == "exact" and not (isinstance(uncertainty["note"], str) and uncertainty["note"].strip()):
        raise RegistryError(f"{label}.uncertainty exact needs a justification note")
    if uncertainty["kind"] == "interval":
        low = _number(uncertainty["low"], f"{label}.uncertainty.low", -1e12, 1e12)
        high = _number(uncertainty["high"], f"{label}.uncertainty.high", -1e12, 1e12)
        if low > high or (type(value) in (int, float) and not low <= value <= high):
            raise RegistryError(f"{label}.uncertainty interval must contain the value")
    elif uncertainty["low"] is not None or uncertainty["high"] is not None:
        raise RegistryError(f"{label}.uncertainty low/high are only for an interval")
    _optional_text(uncertainty["note"], f"{label}.uncertainty.note")

    if status == "measured":
        # Known = validated provenance + a date + a TTL + stated uncertainty (plan section 2.1).
        if provenance["kind"] not in MEASURING_KINDS or ttl is None or obs["measured_at"] == "unknown" \
                or uncertainty["kind"] not in MEASURED_UNCERTAINTY:
            raise RegistryError(f"{label}: 'measured' needs a measuring provenance, a date, a TTL and "
                                f"a stated uncertainty (an interval, or exact with a justification)")
        if kind == "pool" and pools[value]["verification"] != "verified":
            raise RegistryError(f"{label}: an observation never verifies an unverified pool")
    if status == "historical" and obs["measured_at"] == "unknown":
        raise RegistryError(f"{label}: a historical value keeps its source measurement date")


def _evaluation_time(now: Any) -> datetime | None:
    """``now`` as aware UTC, or None (a conservative unknown) when it is absent, not EXACTLY a
    datetime (a subclass could override the offset or the arithmetic), naive (no tzinfo, or a
    tzinfo that names no offset), has an offset that is not an exact timedelta, has a tzinfo that
    fails in any way (NotImplementedError included), or is unrepresentable in UTC (RCO2 R1; RCO1
    7f32cfea S2). The offset is read exactly ONCE and subtracted from the naive wall time;
    astimezone is never used, so a stateful tzinfo cannot answer differently on a second read
    and nothing is ever read as local time."""
    if type(now) is not datetime or now.tzinfo is None:
        return None
    try:
        offset = now.utcoffset()                     # the single read
    except Exception:  # noqa: BLE001 - a tzinfo that cannot say its offset gives no evaluation time
        return None
    if type(offset) is not timedelta or not -timedelta(days=1) < offset < timedelta(days=1):
        return None
    try:
        return (now.replace(tzinfo=None) - offset).replace(tzinfo=timezone.utc)
    except (OverflowError, ValueError):
        return None


def observation_state(observation: dict, now: datetime | None) -> str:
    """fresh | stale | historical | unverified | unknown. Only ``fresh`` is known (plan section 2.1).

    An evaluation time that is None, not a datetime, naive or unrepresentable in UTC makes every
    measured value unknown; the other statuses never depend on the clock."""
    status = observation["status"]
    if status != "measured":
        return status
    current = _evaluation_time(now)
    if current is None:
        return "unknown"
    measured = _when(observation["measured_at"], "measured_at")
    if measured - current > FUTURE_SKEW:
        return "unknown"  # dated in the future: never trusted
    return "fresh" if current - measured <= timedelta(seconds=observation["ttl_seconds"]) else "stale"


def pool_state(pool: dict, now: datetime | None) -> str:
    """verified (inside its TTL), stale, unverified or unknown: a verified pool expires (RCO2 S1).

    The clock is normalized as in ``observation_state``; an unverified pool never depends on it."""
    if pool["verification"] != "verified":
        return "unverified"
    current = _evaluation_time(now)
    if current is None:
        return "unknown"
    measured = _when(pool["measured_at"], "pool measured_at")
    if measured - current > FUTURE_SKEW:
        return "unknown"
    return "verified" if current - measured <= timedelta(seconds=pool["ttl_seconds"]) else "stale"


def _resolve(candidates: list) -> dict:
    """One cell from every candidate for it, explicitly (RCO2 S2): the best state wins; within
    it the latest measured_at wins; different values at that latest time are a ``conflict``
    (no value). Older disagreeing values are counted in ``superseded``; nothing wins silently."""
    best = min(rank for rank, _, _ in candidates)
    group = [(when, cell) for rank, when, cell in candidates if rank == best]
    if all(cell["value"] is None for _, cell in group):
        return dict(group[0][1])
    dated = [when for when, _ in group if when is not None]
    latest = max(dated) if dated else None
    top = [cell for when, cell in group if when == latest]
    values = {json.dumps(cell["value"], sort_keys=True) for cell in top}
    if len(values) > 1:
        return _cell(None, "conflict", None, top[0]["measured_at"],
                     str(len(top)) + " observations with the same state and date disagree")
    chosen = dict(top[0])
    superseded = sum(1 for when, cell in group
                     if when != latest and json.dumps(cell["value"], sort_keys=True) not in values)
    if superseded:
        chosen["superseded"] = superseded
    return chosen


def _cell(value: Any = None, state: str = "unknown", source: str | None = None,
          measured_at: str | None = None, note: str | None = None) -> dict:
    return {"value": value, "state": state, "source": source, "measured_at": measured_at, "note": note}


def _column(observation: dict) -> str | None:
    kind, unit = observation["kind"], observation["unit"]
    if kind == "quality":
        return {"general": "quality_general", "coding_agent": "quality_coding_agent"}.get(
            observation["class"], "quality:" + observation["class"])
    if kind == "cost":
        return {"usd_per_task_api_price": "cost_api_usd_per_task", "usd_per_mtok_api_price": "cost_api_usd_per_mtok",
                "pool_points_per_mtok": "cost_pool_points_per_mtok"}[unit]
    return {"context": "context_tokens", "tier": "tier", "pool": "pool"}.get(kind)


TABLE_COLUMNS = ("quality_general", "quality_coding_agent", "cost_api_usd_per_task", "cost_api_usd_per_mtok",
                 "cost_pool_points_per_mtok", "context_tokens", "tier", "pool")


def model_table(registry: dict, now: datetime | None = None) -> dict:
    """One row per model and effort (rated models and candidates), every cell explicit.

    A cell is ``{value, state, source, measured_at, note}``; ``state`` is fresh, historical,
    unverified, stale or unknown. A stale or unknown cell has no value. Advisory only.
    """
    current = datetime.now(timezone.utc) if now is None else now
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise RegistryError("model_table needs a timezone-aware time")
    # None: unrepresentable in UTC, or a tzinfo that names no offset (never read as local time),
    # so every freshness-dependent cell is unknown.
    current = _evaluation_time(current)
    v2 = registry["schema"] == SCHEMA_V2
    measured_at = registry["historical"]["source_measured_at"] if v2 else registry["benchmark"]["fetched_at"]
    benchmark = f"{registry['benchmark']['name']} {registry['benchmark']['version']}"
    table: dict[tuple, dict] = {}
    pending: dict[tuple, list] = {}

    def row_for(provider: str, model: str, effort: str | None, admission: str) -> dict:
        key = (provider, model, effort)
        if key not in table:
            table[key] = {"provider": provider, "model": model, "effort": effort, "admission": admission,
                          **{column: _cell() for column in TABLE_COLUMNS}, "task_quality": {}}
        return table[key]

    def offer(row_key: tuple, scope: str, name: str, cell: dict) -> None:
        try:
            when = _when(cell["measured_at"], "measured_at") if cell["measured_at"] else None
        except RegistryError:
            when = None
        pending.setdefault((row_key, scope, name), []).append((STATE_RANK[cell["state"]], when, cell))

    for entry in registry["models"].values():
        for effort, values in entry["efforts"].items():
            key = (entry["provider"], entry["model"], effort)
            row_for(*key, "see_signed_catalog")
            offer(key, "row", "quality_general", _cell(values["intelligence_index"], "historical", benchmark,
                                                        measured_at, entry.get("benchmark_variant")))
            offer(key, "row", "cost_api_usd_per_task", _cell(values["usd_per_task"], "historical", benchmark,
                                                              measured_at, "api_price_not_quota"))
            if entry["coding_agent_index"] is not None:
                offer(key, "row", "quality_coding_agent", _cell(entry["coding_agent_index"], "historical",
                                                                 registry["coding_benchmark"]["name"],
                                                                 registry["coding_benchmark"]["fetched_at"]))
    if v2:
        for candidate in registry["candidates"].values():
            efforts = sorted({o["subject"]["effort"] for o in registry["observations"]
                              if (o["subject"]["provider"], o["subject"]["model"])
                              == (candidate["provider"], candidate["model"]) and o["subject"]["effort"]},
                             key=EFFORTS.index) or [None]
            for effort in efforts:
                row_for(candidate["provider"], candidate["model"], effort, "none")
        for observation in registry["observations"]:
            subject, column = observation["subject"], _column(observation)
            if column is None or subject["model"] is None:
                continue
            state = observation_state(observation, current)
            known = state in ("fresh", "historical", "unverified")
            cell = _cell(observation["value"] if known else None, state, observation["provenance"]["reference"],
                         None if observation["measured_at"] == "unknown" else observation["measured_at"],
                         observation["uncertainty"]["note"])
            efforts = [subject["effort"]] if subject["effort"] else [
                effort for (p, m, effort) in list(table) if (p, m) == (subject["provider"], subject["model"])]
            for effort in efforts:
                row_for(subject["provider"], subject["model"], effort,
                        "none" if f"{subject['provider']}/{subject['model']}" in registry["candidates"]
                        else "see_signed_catalog")
                if column.startswith("quality:"):
                    offer((subject["provider"], subject["model"], effort), "task", column.split(":", 1)[1], cell)
                else:
                    offer((subject["provider"], subject["model"], effort), "row", column, cell)
    for (row_key, scope, name), candidates in pending.items():
        target = table[row_key]["task_quality"] if scope == "task" else table[row_key]
        target[name] = _resolve(candidates)
    for row in table.values():
        membership = row["pool"]
        if not v2 or membership["state"] == "unknown":
            row["pool_verification"] = "unknown"
        elif membership["state"] != "fresh":
            # The pool table speaks only through a fresh membership (RCO2 nit b).
            row["pool_verification"] = "membership_" + membership["state"]
        else:
            row["pool_verification"] = pool_state(registry["pools"][membership["value"]], current)
    rows_out = sorted(table.values(), key=lambda r: (r["provider"], r["model"],
                                                     EFFORTS.index(r["effort"]) if r["effort"] else -1))
    return {"schema": "wd.model-table.v1", "execution_allowed": False, "generated_for_utc":
            current.strftime("%Y-%m-%dT%H:%M:%SZ") if current is not None else None, "rows": rows_out,
            "limitations": ["unknown_cells_are_unknown_not_zero", "historical_benchmarks_are_not_our_workload",
                            "api_price_is_never_quota_cost", "stale_values_are_unknown",
                            "candidates_have_no_admission", "pool_verification_needs_a_fresh_membership",
                            "equal_state_disagreement_is_a_conflict_not_a_choice"]}


def load_registry(path: str | Path = DEFAULT_REGISTRY) -> tuple[dict, str]:
    """Read exactly one registry file; return (registry, sha256 of its bytes)."""
    registry, data = _read_json(Path(path), MAX_REGISTRY_BYTES, "registry")
    return validate_registry(registry), hashlib.sha256(data).hexdigest()


def default_codex_cache(env: Mapping[str, str] | None = None) -> Path:
    """``$CODEX_HOME/models_cache.json``, as the Codex CLI and start-wd-agent read it; else ``~/.codex``.

    Resolved at call time: a path frozen at import would ignore a CODEX_HOME set later
    (codex-tools-1 N1 on #1743).
    """
    env = os.environ if env is None else env
    home = env.get("CODEX_HOME")
    base = Path(os.path.abspath(home.strip())) if isinstance(home, str) and home.strip() else Path.home() / ".codex"
    return base / "models_cache.json"


def codex_cli_models(path: str | Path | None = None) -> dict[str, list[str]] | None:
    """Model slug -> supported efforts from the Codex CLI's own cache; None when unknown."""
    try:
        cache, _ = _read_json(Path(path) if path is not None else default_codex_cache(), MAX_CACHE_BYTES,
                              "codex models cache")
    except RegistryError:
        return None
    models = cache.get("models") if isinstance(cache, dict) else None
    if not isinstance(models, list):
        return None
    result: dict[str, list[str]] = {}
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get("slug"), str):
            continue
        levels = model.get("supported_reasoning_levels")
        efforts = [level.get("effort") for level in levels if isinstance(level, dict)] \
            if isinstance(levels, list) else []
        result[model["slug"]] = [effort for effort in efforts if isinstance(effort, str)]
    # A cache that names no model at all (an empty list or only junk entries) proves nothing about availability:
    # unknown, never "every Codex row unavailable" (RCO1 LOWREG-R1 C1).
    return result or None


# ---------------------------------------------------------------- analysis


def rows(registry: dict) -> list[dict]:
    return [{"provider": entry["provider"], "model": entry["model"], "effort": effort,
             "quality": float(row["intelligence_index"]), "cost": float(row["usd_per_task"]),
             "coding_agent_index": entry["coding_agent_index"],
             "benchmark_variant": entry.get("benchmark_variant")}
            for entry in registry["models"].values() for effort, row in entry["efforts"].items()]


def _dominates(a: dict, b: dict) -> bool:
    return (a["provider"] == b["provider"] and a["quality"] >= b["quality"] and a["cost"] <= b["cost"]
            and (a["quality"] > b["quality"] or a["cost"] < b["cost"]))


def dominated_by(row: dict, table: list[dict]) -> list[dict]:
    """Same-provider rows that dominate ``row``, best quality first."""
    return sorted((other for other in table if _dominates(other, row)),
                  key=lambda other: (-other["quality"], other["cost"]))


def frontier(table: list[dict]) -> dict[str, list[dict]]:
    """Per provider, the non-dominated rows, cheapest first."""
    result: dict[str, list[dict]] = {}
    for row in table:
        if not dominated_by(row, table):
            result.setdefault(row["provider"], []).append(row)
    return {provider: sorted(front, key=lambda r: (r["cost"], -r["quality"]))
            for provider, front in sorted(result.items())}


def _brief(row: dict | None) -> dict | None:
    if row is None:
        return None
    # The coding-agent score travels with every suggestion: the quality index is a
    # general score, and a coding lane must see when a cheaper model codes worse.
    # The benchmark variant travels too, so a "with_fallback" score is never read as a fixed model.
    brief = {key: row[key] for key in ("provider", "model", "effort", "quality", "cost", "coding_agent_index")}
    brief["benchmark_variant"] = row.get("benchmark_variant")
    return brief


def value_for(current: dict, table: list[dict]) -> dict:
    """More for the same money, and the same for less - never lower quality."""
    same_provider = [row for row in table if row["provider"] == current["provider"]]
    affordable = [row for row in same_provider if row["cost"] <= current["cost"]]
    best = max(affordable, key=lambda row: (row["quality"], -row["cost"]), default=None)
    if best is not None and best["quality"] <= current["quality"]:
        best = None
    good_enough = [row for row in same_provider if row["quality"] >= current["quality"]]
    cheapest = min(good_enough, key=lambda row: (row["cost"], -row["quality"]), default=None)
    if cheapest is not None and cheapest["cost"] >= current["cost"]:
        cheapest = None

    def delta(row: dict | None) -> dict | None:
        if row is None:
            return None
        pct = None if current["cost"] == 0 else round(100 * (row["cost"] - current["cost"]) / current["cost"], 1)
        return dict(_brief(row), quality_delta=round(row["quality"] - current["quality"], 2), cost_delta_percent=pct)

    return {"more_for_same_money": delta(best), "same_for_less": delta(cheapest),
            "dominated": bool(dominated_by(current, table))}


def _catalog_profile(catalog: dict, provider: str, model: str, effort: str) -> tuple[str | None, bool]:
    for profile_id, profile in catalog["capacity_policy"]["profiles"].items():
        if (profile["provider"], profile["model"], profile["effort"]) == (provider, model, effort):
            return profile_id, profile.get("approved") is True
    return None, False


def _cli_available(row: dict, codex_models: dict | None) -> bool | None:
    if row["provider"] != "codex" or codex_models is None:
        return None
    return row["effort"] in codex_models.get(row["model"], [])


def _annotate(entry: dict | None, catalog: dict, codex_models: dict | None) -> dict | None:
    if entry is None:
        return None
    profile_id, approved = _catalog_profile(catalog, entry["provider"], entry["model"], entry["effort"])
    return dict(entry, catalog_profile=profile_id, approved=approved,
                cli_available=_cli_available(entry, codex_models))


def _lane_provider(catalog: dict, lane: str) -> str:
    profiles = catalog["capacity_policy"]["profiles"]
    return profiles[catalog["lanes"][lane]["allowed_profiles"][0]]["provider"]


def parse_current(value: Any) -> tuple[str, str] | None:
    """``"model:effort"`` -> (model, effort); anything else -> None."""
    if not isinstance(value, str) or value.count(":") != 1:
        return None
    model, effort = value.split(":")
    return (model, effort) if model and effort in EFFORTS else None


def lane_value_table(registry: dict, catalog: dict, current: dict, *,
                     codex_models: dict | None = None) -> dict:
    table = rows(registry)
    index = {(row["provider"], row["model"], row["effort"]): row for row in table}
    result = {}
    for lane in catalog["lanes"]:
        provider = _lane_provider(catalog, lane)
        entry = {"lane": lane, "provider": provider, "reviewer": catalog["lanes"][lane]["reviewer"],
                 "current": None, "status": None, "more_for_same_money": None, "same_for_less": None,
                 "dominated": None, "dominated_by": []}
        result[lane] = entry
        parsed = parse_current(current.get(lane)) if isinstance(current, dict) else None
        if parsed is None:
            entry["status"] = "current_unknown"
            continue
        row = index.get((provider, *parsed))
        if row is None:
            entry.update(status="current_unrated", current={"provider": provider, "model": parsed[0],
                                                            "effort": parsed[1]})
            continue
        value = value_for(row, table)
        entry.update(status="rated", current=_annotate(_brief(row), catalog, codex_models),
                     dominated=value["dominated"],
                     dominated_by=[_annotate(_brief(r), catalog, codex_models) for r in dominated_by(row, table)[:3]],
                     more_for_same_money=_annotate(value["more_for_same_money"], catalog, codex_models),
                     same_for_less=_annotate(value["same_for_less"], catalog, codex_models))
    return result


def catalog_diff(registry: dict, catalog: dict, *, codex_models: dict | None = None) -> dict:
    table = rows(registry)
    index = {(row["provider"], row["model"], row["effort"]): row for row in table}
    providers = {_lane_provider(catalog, lane) for lane in catalog["lanes"]}
    front = frontier(table)
    propose_add = [_annotate(_brief(row), catalog, codex_models)
                   for provider in sorted(providers) for row in front.get(provider, [])
                   if _catalog_profile(catalog, row["provider"], row["model"], row["effort"])[0] is None]
    dominated, unrated = [], []
    for profile_id, profile in sorted(catalog["capacity_policy"]["profiles"].items()):
        row = index.get((profile["provider"], profile["model"], profile["effort"]))
        if row is None:
            unrated.append(profile_id)
            continue
        better = dominated_by(row, table)
        if better:
            dominated.append({"catalog_profile": profile_id, "current": _brief(row),
                              "best_replacement": _annotate(_brief(better[0]), catalog, codex_models)})
    return {"propose_add": propose_add, "dominated": dominated, "unrated": unrated}


def report(registry: dict, registry_sha256: str, catalog: dict, catalog_sha256: str, current: dict, *,
           codex_models: dict | None = None, now: datetime | None = None) -> dict:
    table = rows(registry)
    return {"schema": REPORT_SCHEMA, "execution_allowed": False,
            "registry_schema": registry["schema"],
            "model_table": model_table(registry, now),
            "registry_sha256": registry_sha256, "catalog_sha256": catalog_sha256,
            "benchmark": {key: registry["benchmark"][key] for key in ("name", "version", "fetched_at")},
            "frontier": {provider: [_brief(row) for row in front] for provider, front in frontier(table).items()},
            "lanes": lane_value_table(registry, catalog, current, codex_models=codex_models),
            "catalog_diff": catalog_diff(registry, catalog, codex_models=codex_models),
            "codex_cli_models": "unknown" if codex_models is None else sorted(codex_models),
            "limitations": ["public_benchmark_not_our_workload", "claude_cli_availability_unverified",
                            "dominance_and_value_are_benchmark_only",
                            "benchmark_variant_labels_are_per_model_see_rows",
                            "decisions_need_qualification_measured_cost_and_a_signed_catalog"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    parser.add_argument("--codex-models-cache", default=None,
                        help="default: $CODEX_HOME/models_cache.json, else ~/.codex/models_cache.json")
    parser.add_argument("--current-profiles", default="{}",
                        help='JSON object lane -> "model:effort", e.g. {"codex-lead-1": "gpt-6-sol:high"}')
    args = parser.parse_args(argv)
    try:
        current = json.loads(args.current_profiles)
        if not isinstance(current, dict) or not all(lane in LANES for lane in current):
            raise RegistryError("current profiles must map known lanes to 'model:effort'")
        registry, registry_sha = load_registry(args.registry)
        catalog, catalog_sha = load_catalog(args.catalog)
        result = report(registry, registry_sha, catalog, catalog_sha, current,
                        codex_models=codex_cli_models(args.codex_models_cache))
    except (OSError, ValueError) as exc:
        print(json.dumps({"schema": REPORT_SCHEMA, "error": f"{exc.__class__.__name__}: {exc}",
                          "execution_allowed": False}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
