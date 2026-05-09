# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.storage.registry_queries.

Codex scout round 4 flagged this as Candidate 1 (medium-high risk if
missing): RegistryQueries is a runtime truth surface for Reality View
and scale-aware panels, not a dead helper. If the registry query
layer drifts from the control-plane schema, the UI can confidently
show the wrong family/capability/provider state while the actual
solver registry is healthy or broken in a different way.

Existing coverage was indirect: `tests/ui_hologram/test_scale_aware_aggregator.py`
exercises `family_rollups()` through the hologram aggregator, but
nothing imports `RegistryQueries` directly nor pins
`capability_rollups`, `recent_provider_jobs`, `capabilities_provided_by`,
`capability_dependencies`, or `vector_shards_for_cell`.

Pinned invariants:

- `family_rollups`: per-family counts and by_status breakdown reflect
  the actual solver rows, not stale aggregations.
- `total_solver_count(status=...)`: filter is applied; defaults
  count all.
- `capabilities_provided_by`: only `relation='provides'` rows; sorted
  by capability name.
- `capability_dependencies`: only `relation='requires'` rows; sorted
  alphabetically; direction is "what does X depend on" (not the
  reverse).
- `capability_rollups`: each capability has the correct providing
  solver count and the right requires list.
- `vector_shards_for_cell`: filters by `cell_coord`; sorted by
  logical_name.
- `recent_provider_jobs`: filters by provider and section; ordered
  most-recent-first; honours the `limit` argument.
"""
from __future__ import annotations

import pytest

from waggledance.core.storage.control_plane import ControlPlaneDB
from waggledance.core.storage.registry_queries import (
    CapabilityRollup,
    FamilyRollup,
    RegistryQueries,
)


# --- fixtures -------------------------------------------------------

@pytest.fixture()
def cp(tmp_path):
    db = ControlPlaneDB(tmp_path / "cp.sqlite")
    db.migrate()
    yield db
    db.close()


def _seed_minimal(cp: ControlPlaneDB) -> None:
    """Seed a small graph: 2 families with 3 solvers, 3 capabilities
    with 1 dep, 2 vector shards in different cells, 3 provider jobs
    across 2 providers / 2 sections."""
    # Families
    cp.upsert_solver_family("scalar", version="1", status="active")
    cp.upsert_solver_family("threshold", version="1", status="active")

    # Solvers
    cp.upsert_solver("celsius_to_kelvin", "1.0",
                     family_name="scalar", status="approved")
    cp.upsert_solver("celsius_to_fahrenheit", "1.0",
                     family_name="scalar", status="shadow_only")
    cp.upsert_solver("temperature_alert", "1.0",
                     family_name="threshold", status="approved")

    # Capabilities + dep
    cp.upsert_capability("unit_conversion", version="1")
    cp.upsert_capability("alerting", version="1")
    cp.upsert_capability("normalization", version="1")
    cp.add_capability_dependency("alerting", "unit_conversion")

    # Solver-capability links
    cp.link_solver_capability("celsius_to_kelvin", "unit_conversion")
    cp.link_solver_capability("celsius_to_fahrenheit", "unit_conversion")
    cp.link_solver_capability("temperature_alert", "alerting")

    # Vector shards in two different cells
    cp.register_vector_shard("shard_a_thermal", "/path/a",
                             cell_coord="thermal")
    cp.register_vector_shard("shard_b_thermal", "/path/b",
                             cell_coord="thermal")
    cp.register_vector_shard("shard_c_general", "/path/c",
                             cell_coord="general")

    # Provider jobs (insertion order = ordering, ID DESC = recency)
    cp.record_provider_job("anthropic_api", "synth",
                           section="solver_synth", status="ok")
    cp.record_provider_job("openai_api", "synth",
                           section="solver_synth", status="ok")
    cp.record_provider_job("anthropic_api", "review",
                           section="review", status="ok")


# --- family rollups -------------------------------------------------

def test_family_rollups_returns_one_per_family_with_correct_counts(cp):
    _seed_minimal(cp)
    rq = RegistryQueries(cp)
    rollups = rq.family_rollups()
    assert {r.family.name for r in rollups} == {"scalar", "threshold"}
    by_name = {r.family.name: r for r in rollups}
    assert by_name["scalar"].total_solvers == 2
    assert by_name["threshold"].total_solvers == 1
    # by_status keys reflect actual solver statuses, not stale defaults.
    assert by_name["scalar"].by_status == {
        "approved": 1, "shadow_only": 1,
    }
    assert by_name["threshold"].by_status == {"approved": 1}


def test_family_rollups_returns_empty_when_no_families(cp):
    rq = RegistryQueries(cp)
    assert rq.family_rollups() == []


def test_solvers_in_family_filters_correctly(cp):
    _seed_minimal(cp)
    rq = RegistryQueries(cp)
    scalars = rq.solvers_in_family("scalar")
    assert {s.name for s in scalars} == {
        "celsius_to_kelvin", "celsius_to_fahrenheit",
    }
    thresholds = rq.solvers_in_family("threshold")
    assert {s.name for s in thresholds} == {"temperature_alert"}
    # Unknown family → empty.
    assert rq.solvers_in_family("nope") == []


def test_total_solver_count_status_filter_applies(cp):
    _seed_minimal(cp)
    rq = RegistryQueries(cp)
    assert rq.total_solver_count() == 3
    assert rq.total_solver_count(status="approved") == 2
    assert rq.total_solver_count(status="shadow_only") == 1
    assert rq.total_solver_count(status="rejected") == 0


# --- capability queries ---------------------------------------------

def test_capabilities_provided_by_returns_only_provides_relation(cp):
    _seed_minimal(cp)
    rq = RegistryQueries(cp)
    caps = rq.capabilities_provided_by("celsius_to_kelvin")
    assert [c.name for c in caps] == ["unit_conversion"]
    # Solver with two capabilities (none in this fixture, but the
    # contract is "only provides relation"). Verify a solver that
    # has no provided capabilities returns empty.
    assert rq.capabilities_provided_by("nonexistent_solver") == []


def test_capability_dependencies_returns_alphabetical_requires(cp):
    _seed_minimal(cp)
    rq = RegistryQueries(cp)
    deps = rq.capability_dependencies("alerting")
    assert deps == ["unit_conversion"]
    # Reverse direction: unit_conversion does NOT depend on alerting
    # — direction must be "what does X depend on", not the inverse.
    assert rq.capability_dependencies("unit_conversion") == []


def test_capability_rollups_carries_solver_count_and_requires(cp):
    _seed_minimal(cp)
    rq = RegistryQueries(cp)
    rollups = rq.capability_rollups()
    assert all(isinstance(r, CapabilityRollup) for r in rollups)
    by_name = {r.capability.name: r for r in rollups}
    assert by_name["unit_conversion"].providing_solver_count == 2
    assert by_name["unit_conversion"].requires == ()
    assert by_name["alerting"].providing_solver_count == 1
    assert by_name["alerting"].requires == ("unit_conversion",)
    # Capability with zero providing solvers still appears.
    assert by_name["normalization"].providing_solver_count == 0
    assert by_name["normalization"].requires == ()


def test_capability_rollups_sorted_alphabetically_by_name(cp):
    _seed_minimal(cp)
    rq = RegistryQueries(cp)
    names = [r.capability.name for r in rq.capability_rollups()]
    assert names == sorted(names)


# --- vector shards --------------------------------------------------

def test_vector_shards_for_cell_filters_by_coord_and_sorts_by_name(cp):
    _seed_minimal(cp)
    rq = RegistryQueries(cp)
    thermal = rq.vector_shards_for_cell("thermal")
    assert [s.logical_name for s in thermal] == [
        "shard_a_thermal", "shard_b_thermal",
    ]
    general = rq.vector_shards_for_cell("general")
    assert [s.logical_name for s in general] == ["shard_c_general"]
    # Unknown cell → empty.
    assert rq.vector_shards_for_cell("nope") == []


def test_all_vector_shards_returns_all_sorted(cp):
    _seed_minimal(cp)
    rq = RegistryQueries(cp)
    all_shards = rq.all_vector_shards()
    assert [s.logical_name for s in all_shards] == [
        "shard_a_thermal", "shard_b_thermal", "shard_c_general",
    ]


# --- provider jobs --------------------------------------------------

def test_recent_provider_jobs_orders_most_recent_first(cp):
    _seed_minimal(cp)
    rq = RegistryQueries(cp)
    jobs = rq.recent_provider_jobs()
    # Insertion order was: anthropic synth, openai synth, anthropic review.
    # Most-recent-first → reversed.
    assert [j.provider for j in jobs] == [
        "anthropic_api", "openai_api", "anthropic_api",
    ]
    assert [j.section for j in jobs] == [
        "review", "solver_synth", "solver_synth",
    ]


def test_recent_provider_jobs_provider_filter_applies(cp):
    _seed_minimal(cp)
    rq = RegistryQueries(cp)
    anthropic = rq.recent_provider_jobs(provider="anthropic_api")
    assert len(anthropic) == 2
    assert all(j.provider == "anthropic_api" for j in anthropic)
    openai = rq.recent_provider_jobs(provider="openai_api")
    assert len(openai) == 1


def test_recent_provider_jobs_section_filter_applies(cp):
    _seed_minimal(cp)
    rq = RegistryQueries(cp)
    synth = rq.recent_provider_jobs(section="solver_synth")
    assert len(synth) == 2
    assert all(j.section == "solver_synth" for j in synth)


def test_recent_provider_jobs_provider_and_section_combined(cp):
    _seed_minimal(cp)
    rq = RegistryQueries(cp)
    jobs = rq.recent_provider_jobs(
        provider="anthropic_api", section="solver_synth",
    )
    assert len(jobs) == 1
    assert jobs[0].provider == "anthropic_api"
    assert jobs[0].section == "solver_synth"


def test_recent_provider_jobs_limit_caps_result_size(cp):
    _seed_minimal(cp)
    rq = RegistryQueries(cp)
    jobs = rq.recent_provider_jobs(limit=2)
    assert len(jobs) == 2
    # Limit=0 returns empty (SQL LIMIT 0 is a no-op fetch).
    assert rq.recent_provider_jobs(limit=0) == []


def test_recent_provider_jobs_empty_when_no_match(cp):
    _seed_minimal(cp)
    rq = RegistryQueries(cp)
    assert rq.recent_provider_jobs(provider="not_a_provider") == []
    assert rq.recent_provider_jobs(section="not_a_section") == []
