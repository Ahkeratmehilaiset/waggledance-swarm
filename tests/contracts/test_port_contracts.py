"""Contract tests — validate ports, DTOs, and route types match PORT_CONTRACTS.md."""

import inspect

import pytest

from waggledance.core.domain.agent import AgentDefinition, AgentResult
from waggledance.core.domain.events import DomainEvent, EventType
from waggledance.core.domain.memory_record import MemoryRecord
from waggledance.core.domain.task import TaskRequest, TaskRoute
from waggledance.core.domain.trust_score import AgentTrust, TrustSignals
from waggledance.core.orchestration.routing_policy import ALLOWED_ROUTE_TYPES
from waggledance.core.ports.config_port import ConfigPort
from waggledance.core.ports.event_bus_port import EventBusPort
from waggledance.core.ports.hot_cache_port import HotCachePort
from waggledance.core.ports.llm_port import LLMPort
from waggledance.core.ports.memory_repository_port import MemoryRepositoryPort
from waggledance.core.ports.sensor_port import SensorPort
from waggledance.core.ports.trust_store_port import TrustStorePort
from waggledance.core.ports.vector_store_port import VectorStorePort


ALL_PORTS = [
    LLMPort, MemoryRepositoryPort, HotCachePort, VectorStorePort,
    TrustStorePort, EventBusPort, ConfigPort, SensorPort,
]


class TestPortProtocols:
    @pytest.mark.parametrize("port_cls", ALL_PORTS)
    def test_ports_are_protocols(self, port_cls):
        assert hasattr(port_cls, "__protocol_attrs__") or "Protocol" in str(port_cls.__mro__)

    def test_memory_and_hotcache_are_separate(self):
        assert MemoryRepositoryPort is not HotCachePort
        mem_methods = {m for m in dir(MemoryRepositoryPort) if not m.startswith("_")}
        cache_methods = {m for m in dir(HotCachePort) if not m.startswith("_")}
        assert "search" in mem_methods
        assert "get" in cache_methods
        assert "get" not in mem_methods


class TestDTOFields:
    def test_agent_definition_fields(self):
        fields = {f.name for f in AgentDefinition.__dataclass_fields__.values()}
        expected = {"id", "name", "domain", "tags", "skills", "trust_level",
                    "specialization_score", "active", "profile"}
        assert fields == expected

    def test_agent_result_source_is_str(self):
        field = AgentResult.__dataclass_fields__["source"]
        assert field.type is str or field.type == "str"

    def test_task_route_type_is_str(self):
        field = TaskRoute.__dataclass_fields__["route_type"]
        assert field.type is str or field.type == "str"

    def test_memory_record_fields(self):
        fields = {f.name for f in MemoryRecord.__dataclass_fields__.values()}
        expected = {"id", "content", "content_fi", "source", "confidence",
                    "tags", "agent_id", "created_at", "ttl_seconds"}
        assert fields == expected

    def test_trust_signals_fields(self):
        fields = {f.name for f in TrustSignals.__dataclass_fields__.values()}
        expected = {"hallucination_rate", "validation_rate", "consensus_agreement",
                    "correction_rate", "fact_production_rate", "freshness_score"}
        assert fields == expected


class TestTrustStoreContracts:
    """Verify both InMemory and SQLite trust stores implement TrustStorePort."""

    def test_in_memory_has_get_trust(self):
        from waggledance.adapters.trust.in_memory_trust_store import InMemoryTrustStore
        assert hasattr(InMemoryTrustStore, "get_trust")
        assert hasattr(InMemoryTrustStore, "update_trust")
        assert hasattr(InMemoryTrustStore, "get_ranking")

    def test_sqlite_has_get_trust(self):
        from waggledance.adapters.trust.sqlite_trust_store import SQLiteTrustStore
        assert hasattr(SQLiteTrustStore, "get_trust")
        assert hasattr(SQLiteTrustStore, "update_trust")
        assert hasattr(SQLiteTrustStore, "get_ranking")

    def test_sqlite_method_signatures_match_port(self):
        from waggledance.adapters.trust.sqlite_trust_store import SQLiteTrustStore
        sig_get = inspect.signature(SQLiteTrustStore.get_trust)
        sig_update = inspect.signature(SQLiteTrustStore.update_trust)
        sig_rank = inspect.signature(SQLiteTrustStore.get_ranking)
        assert "agent_id" in sig_get.parameters
        assert "agent_id" in sig_update.parameters
        assert "signals" in sig_update.parameters
        assert "limit" in sig_rank.parameters


class TestRouteTypes:
    def test_allowed_route_types_exact(self):
        assert ALLOWED_ROUTE_TYPES == frozenset({"hotcache", "memory", "micromodel", "solver", "llm", "swarm"})

    def test_micromodel_in_route_types(self):
        assert "micromodel" in ALLOWED_ROUTE_TYPES

    def test_no_rules(self):
        assert "rules" not in ALLOWED_ROUTE_TYPES


class TestEventTypes:
    def test_has_night_stall_detected(self):
        assert hasattr(EventType, "NIGHT_STALL_DETECTED")

    def test_has_task_error(self):
        assert hasattr(EventType, "TASK_ERROR")

    def test_has_app_shutdown(self):
        assert hasattr(EventType, "APP_SHUTDOWN")

    def test_event_type_count(self):
        assert len(EventType) == 34  # 16 legacy + 18 autonomy Phase 2


class TestPortSignatures:
    def test_llm_generate_signature(self):
        sig = inspect.signature(LLMPort.generate)
        params = list(sig.parameters.keys())
        assert "prompt" in params
        assert "model" in params
        assert "temperature" in params
        assert "max_tokens" in params

    def test_memory_repo_search_signature(self):
        sig = inspect.signature(MemoryRepositoryPort.search)
        params = list(sig.parameters.keys())
        assert "query" in params
        assert "limit" in params
        assert "language" in params
        assert "tags" in params
