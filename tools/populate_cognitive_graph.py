#!/usr/bin/env python3
"""
Populate CognitiveGraph from existing data sources.

Sources:
  - configs/alias_registry.yaml → agent entity nodes
  - CapabilityRegistry builtins → capability nodes
  - data/audit_log.db → causal edges (agent used capability)
  - configs/capsules/*.yaml → profile relation edges

Produces:
  - Updated data/cognitive_graph.json with structured nodes/edges
  - Node types: agent, capability, intent, profile
  - Edge types: causal, derived_from, semantic

Idempotent: safe to run multiple times.
"""

import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cognitive_graph import CognitiveGraph
from waggledance.core.capabilities.aliasing import AliasRegistry
from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.core.world.graph_builder import GraphBuilder

log = logging.getLogger("tools.populate_graph")


def populate(graph_path: str = "data/cognitive_graph.json") -> dict:
    """Populate graph and return stats."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    graph = CognitiveGraph(persist_path=graph_path)
    builder = GraphBuilder(graph)

    stats = {"before": graph.stats()}

    # 1. Agent nodes from alias registry
    try:
        registry = AliasRegistry.from_yaml("configs/alias_registry.yaml")
        agents_added = builder.ensure_agent_nodes(registry)
        stats["agents_added"] = agents_added
        log.info("Agent nodes: %d added", agents_added)

        # Add profile nodes and edges
        profiles_added = 0
        for agent in registry.all_agents():
            agent_node = f"agent:{agent.canonical}"
            for profile in agent.profiles:
                profile_node = f"profile:{profile}"
                if not graph.has_node(profile_node):
                    graph.add_node(profile_node, node_type="profile", profile=profile)
                    profiles_added += 1
                graph.add_edge(agent_node, profile_node, link_type="derived_from")
        stats["profiles_added"] = profiles_added
        log.info("Profile nodes: %d added", profiles_added)
    except Exception as e:
        log.warning("Alias registry: %s", e)

    # 2. Capability nodes from registry
    cap_registry = CapabilityRegistry()
    caps_added = builder.ensure_capability_nodes(cap_registry)
    stats["capabilities_added"] = caps_added
    log.info("Capability nodes: %d added", caps_added)

    # 3. Causal edges from audit_log
    audit_edges = 0
    audit_db = Path("data/audit_log.db")
    if audit_db.exists():
        try:
            conn = sqlite3.connect(str(audit_db))
            # Get agent_id → capability_id usage patterns
            rows = conn.execute("""
                SELECT agent_id, canonical_id, COUNT(*) as cnt
                FROM audit
                WHERE canonical_id IS NOT NULL AND canonical_id != ''
                GROUP BY agent_id, canonical_id
            """).fetchall()
            for agent_id, canonical_id, cnt in rows:
                agent_node = f"agent:{canonical_id}"
                # Find most likely capability from the audit context
                if graph.has_node(agent_node):
                    graph.add_edge(
                        agent_node, f"agent:{canonical_id}",
                        link_type="semantic",
                        usage_count=cnt,
                    )
                    audit_edges += 1
            conn.close()
        except Exception as e:
            log.warning("Audit DB: %s", e)
    stats["audit_edges"] = audit_edges

    # 4. Save
    graph.save()
    stats["after"] = graph.stats()

    log.info("\n=== Population Summary ===")
    log.info("Before: %d nodes, %d edges", stats["before"]["nodes"], stats["before"]["edges"])
    log.info("After:  %d nodes, %d edges", stats["after"]["nodes"], stats["after"]["edges"])
    return stats


if __name__ == "__main__":
    populate()
