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

See docs/BRIDGE_MODEL_REGISTRY.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lane_profile_catalog import LANES, load_catalog  # noqa: E402

SCHEMA = "wd.model-registry.v1"
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
        with path.open("rb") as stream:
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


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise RegistryError(f"{label} must be a non-empty string")
    return value


def _score(value: Any, label: str, upper: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= upper:
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
    """Refuse anything but the exact registry shape; return the registry unchanged."""
    _exact_keys(registry, TOP_KEYS, "registry")
    if registry["schema"] != SCHEMA:
        raise RegistryError(f"schema must be {SCHEMA}")
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
        _exact_keys(entry, MODEL_KEYS, f"models.{key}", OPTIONAL_MODEL_KEYS)
        if "benchmark_variant" in entry:
            _text(entry["benchmark_variant"], f"models.{key}.benchmark_variant")
        if entry["provider"] not in PROVIDERS:
            raise RegistryError(f"models.{key}.provider must be one of {PROVIDERS}")
        _text(entry["model"], f"models.{key}.model")
        if key != f"{entry['provider']}/{entry['model']}":
            raise RegistryError(f"models.{key} key must be '<provider>/<model>'")
        if entry["coding_agent_index"] is not None:
            _score(entry["coding_agent_index"], f"models.{key}.coding_agent_index", 100)
        efforts = entry["efforts"]
        if not isinstance(efforts, dict) or not efforts:
            raise RegistryError(f"models.{key}.efforts must be a non-empty object")
        for effort, row in efforts.items():
            if effort not in EFFORTS:
                raise RegistryError(f"models.{key}.efforts has unknown effort {effort!r}")
            _exact_keys(row, EFFORT_KEYS, f"models.{key}.efforts.{effort}")
            _score(row["intelligence_index"], f"models.{key}.efforts.{effort}.intelligence_index", 100)
            _score(row["usd_per_task"], f"models.{key}.efforts.{effort}.usd_per_task", 1000)
    return registry


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
    return result


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
           codex_models: dict | None = None) -> dict:
    table = rows(registry)
    return {"schema": REPORT_SCHEMA, "execution_allowed": False,
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
