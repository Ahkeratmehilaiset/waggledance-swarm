"""Dependency injection container -- wires everything together."""

import hashlib
import logging
import re
import shutil
from datetime import datetime, timezone
from functools import cached_property
from itertools import count
from pathlib import Path
from threading import Lock

log = logging.getLogger(__name__)


# Audit H30: a misconfigured WAGGLE_PROFILE (typo, unknown value) used
# to silently boot WaggleDance with 0 agents. The canonical profiles
# below are the four shipped configurations; "ALL" is the meta-value
# that matches every agent and is accepted at runtime but should not
# be selected as the active profile.
KNOWN_PROFILES = frozenset({"GADGET", "COTTAGE", "HOME", "FACTORY"})
DEFAULT_RUNTIME_RECEIPT_OUT_DIR = "data/runtime/runtime_summary_receipts"
DEFAULT_CHAT_SERVED_RECEIPT_OUT_DIR = "data/runtime/chat_served_receipts"
DEFAULT_V313_SOLVER_RECEIPT_MAX_BUNDLES = 1000
_V313_SOLVER_RECEIPT_BUNDLE_DIR_RE = re.compile(r"^\d{8}T\d{12}Z-\d{6}$")


def _public_runtime_receipt_verifier_errors(errors) -> list[str]:
    public_errors: list[str] = []
    for error in errors or []:
        digest = hashlib.sha256(
            str(error).encode("utf-8", errors="replace")
        ).hexdigest()[:16]
        public_errors.append(f"verifier_error:{digest}")
    return public_errors


def _settings_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _settings_positive_int(settings, key: str, default: int) -> int:
    try:
        value = int(settings.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _prune_v313_solver_receipt_bundles(root: Path, max_bundles: int) -> None:
    if max_bundles < 1 or not root.exists():
        return

    root_resolved = root.resolve()
    candidates: list[Path] = []
    for child in root.iterdir():
        if not _V313_SOLVER_RECEIPT_BUNDLE_DIR_RE.fullmatch(child.name):
            continue
        if child.is_symlink() or not child.is_dir():
            continue
        try:
            if child.resolve().parent != root_resolved:
                continue
        except OSError:
            continue
        candidates.append(child)

    stale = sorted(candidates, key=lambda path: path.name)[:-max_bundles]
    for child in stale:
        try:
            shutil.rmtree(child)
        except FileNotFoundError:
            continue


class Container:
    """One Container instance = one running WaggleDance system."""

    def __init__(self, settings, stub: bool = False):
        self._settings = settings
        self._stub = stub

    # --- Adapters ---

    @cached_property
    def llm(self):
        """LLMPort implementation.

        Audit H49 / operator decision D3.4: production LLM calls now
        route through BridgeLLMClient's 4-tier fallback chain
        (cache -> local-ollama -> cloud -> heuristic), which brings
        BridgeLLMRedactor onto cloud calls and lets Profile S short-
        circuit to heuristic safely. Before this wiring the
        orchestrator called OllamaAdapter directly and the entire
        ~1200-LOC BridgeLLMClient stack sat unused in production.

        Stub mode still returns StubLLMAdapter unchanged. If
        BridgeLLMClient construction fails for any reason (missing
        config, import error), we fall back to the original direct
        OllamaAdapter so the runtime stays operational.
        """
        if self._stub:
            from waggledance.adapters.llm.stub_llm_adapter import StubLLMAdapter
            return StubLLMAdapter()
        from waggledance.adapters.llm.ollama_adapter import OllamaAdapter
        direct = OllamaAdapter(
            base_url=self._settings.ollama_host,
            default_model=self._settings.chat_model,
            timeout_seconds=self._settings.ollama_timeout_seconds,
        )
        try:
            from waggledance.adapters.llm.bridge_llm_adapter import BridgeLLMAdapter
            from waggledance.core.bridge_llm.client import BridgeLLMClient
            client = BridgeLLMClient.default()
            return BridgeLLMAdapter(client=client, fallback_adapter=direct)
        except Exception as exc:
            log.warning(
                "BridgeLLMClient wiring failed (D3.4); orchestrator "
                "will use OllamaAdapter directly: %s", exc,
            )
            return direct

    @cached_property
    def gemma_router(self):
        """GemmaProfileRouter — optional dual-tier Gemma 4 model routing."""
        from waggledance.application.services.gemma_profile_router import GemmaProfileRouter
        return GemmaProfileRouter(settings=self._settings, default_llm=self.llm)

    @cached_property
    def vector_store(self):
        """VectorStorePort implementation."""
        if self._stub:
            from waggledance.adapters.memory.in_memory_vector_store import InMemoryVectorStore
            return InMemoryVectorStore()
        from waggledance.adapters.memory.chroma_vector_store import ChromaVectorStore
        return ChromaVectorStore(
            persist_directory=self._settings.chroma_dir,
            embedding_model=self._settings.embed_model,
        )

    @cached_property
    def memory_repository(self):
        """MemoryRepositoryPort. Stub -> InMemoryRepository. Non-stub -> ChromaMemoryRepository."""
        if self._stub:
            from waggledance.adapters.memory.in_memory_repository import InMemoryRepository
            return InMemoryRepository()
        from waggledance.adapters.memory.chroma_memory_repository import ChromaMemoryRepository
        return ChromaMemoryRepository(
            vector_store=self.vector_store,
            collection="waggle_memory",
        )

    @cached_property
    def trust_store(self):
        """TrustStorePort -- stub=InMemory, production=SQLite with InMemory fallback.

        WARNING: If SQLite fails, falls back to InMemory (data lost on restart).
        Check logs for ERROR level.
        """
        if self._stub:
            from waggledance.adapters.trust.in_memory_trust_store import InMemoryTrustStore
            return InMemoryTrustStore()
        try:
            import os
            from waggledance.adapters.trust.sqlite_trust_store import SQLiteTrustStore
            db_dir = os.path.dirname(self._settings.db_path) or "."
            return SQLiteTrustStore(db_path=os.path.join(db_dir, "trust_store.db"))
        except Exception as exc:
            import logging
            log = logging.getLogger(__name__)
            log.error(
                "SQLiteTrustStore failed — trust data will NOT persist across restarts: %s", exc)
            from waggledance.adapters.trust.in_memory_trust_store import InMemoryTrustStore
            store = InMemoryTrustStore()
            store._fallback_active = True
            return store

    @cached_property
    def shared_memory(self):
        """SQLiteSharedMemory adapter."""
        from waggledance.adapters.memory.sqlite_shared_memory import SQLiteSharedMemory
        return SQLiteSharedMemory(db_path=self._settings.db_path)

    @cached_property
    def hot_cache(self):
        """HotCachePort implementation."""
        from waggledance.adapters.memory.hot_cache import HotCache
        return HotCache(max_size=self._settings.hot_cache_size)

    @cached_property
    def config(self):
        """ConfigPort -- settings object itself implements get/get_profile/get_hardware_tier."""
        return self._settings

    @cached_property
    def event_bus(self):
        """EventBusPort implementation."""
        from waggledance.bootstrap.event_bus import InMemoryEventBus
        return InMemoryEventBus()

    @cached_property
    def control_plane_db(self):
        """ControlPlaneDB — persistent autonomy-growth substrate.

        Audit H51 / operator decision D2.1: ControlPlaneDB used to be
        instantiated only by offline tools (tools/*.py) and tests; no
        production code path created data/control_plane.db. The R25
        decision histogram + autogrowth_scheduler + RuntimeGapDetector
        all need this DB to exist before they can write anything.

        Stub mode (e.g. unit tests, CI without persistent data/)
        returns None so consumers can degrade gracefully. Non-stub
        production opens the DB at data/control_plane.db (the
        ControlPlaneDB default), runs schema migration, and the
        connection is closed by api.py lifespan on shutdown.

        Note: ControlPlaneDB.__init__ acquires a sqlite WAL connection
        and runs migrate(). Construction is therefore O(schema
        version) — typically < 50 ms cold.
        """
        if self._stub:
            return None
        try:
            from waggledance.core.storage.control_plane import ControlPlaneDB
            return ControlPlaneDB()
        except Exception as exc:
            log.error(
                "ControlPlaneDB construction failed — autonomy-growth "
                "track will be inactive (D2 wiring): %s", exc,
            )
            return None

    @cached_property
    def autogrowth_scheduler(self):
        """AutogrowthScheduler bound to the persistent control plane."""
        cp_db = self.control_plane_db
        if cp_db is None:
            return None
        try:
            from waggledance.core.autonomy_growth import AutogrowthScheduler
            return AutogrowthScheduler(cp_db)
        except Exception as exc:
            log.error(
                "AutogrowthScheduler construction failed; background "
                "autogrowth tick inactive: %s", exc,
            )
            return None

    @cached_property
    def autogrowth_background_ticker(self):
        """Background ticker that drains autogrowth_queue during runtime."""
        scheduler = self.autogrowth_scheduler
        if scheduler is None:
            return None
        enabled = bool(
            self._settings.get("autogrowth.background_tick_enabled", True)
        )
        if not enabled:
            return None
        try:
            from waggledance.core.autonomy_growth import AutogrowthBackgroundTicker
            return AutogrowthBackgroundTicker(
                scheduler,
                interval_seconds=float(
                    self._settings.get("autogrowth.background_interval_s", 30.0)
                ),
                max_ticks_per_wake=int(
                    self._settings.get("autogrowth.background_max_ticks", 20)
                ),
            )
        except Exception as exc:
            log.error(
                "AutogrowthBackgroundTicker construction failed; background "
                "autogrowth tick inactive: %s", exc,
            )
            return None

    @cached_property
    def autogrowth_alert_feed(self):
        """Optional read-only Alertmanager feed for autogrowth alerts."""
        cfg = self._settings.get("autogrowth_alert_feed", {}) or {}
        if not cfg.get("enabled", False):
            return None
        try:
            from waggledance.adapters.http.autogrowth_alert_feed import (
                AutogrowthAlertmanagerFeed,
            )
            return AutogrowthAlertmanagerFeed.from_config(cfg)
        except Exception as exc:
            log.warning(
                "Autogrowth alert feed configuration refused; "
                "alert_state will report unavailable: %s",
                exc,
            )
            from waggledance.adapters.http.autogrowth_alert_feed import (
                UnavailableAutogrowthAlertFeed,
            )
            return UnavailableAutogrowthAlertFeed()

    @cached_property
    def runtime_receipt_sink(self):
        """Optional local MAGMA receipt sink for AutonomyRuntime query summaries.

        Disabled by default. When explicitly enabled, the sink writes sanitized
        receipt bundles to a configured local directory but returns only
        path-free verifier metadata to the runtime result.
        """
        if not _settings_bool(self._settings.get("runtime_receipts.enabled", False)):
            return None

        out_dir = Path(
            str(
                self._settings.get(
                    "runtime_receipts.out_dir",
                    DEFAULT_RUNTIME_RECEIPT_OUT_DIR,
                )
            )
        )
        evaluation_version = str(
            self._settings.get(
                "runtime_receipts.evaluation_version",
                "magma.evaluation_result.v0",
            )
        )
        sequence = count(1)
        sequence_lock = Lock()

        def sink(summary: dict):
            from tools.verify_magma_receipt import verify_manifest
            from waggledance.core.magma.runtime_summary_receipt import (
                write_runtime_summary_receipt_bundle,
            )

            now_utc = datetime.now(timezone.utc)
            with sequence_lock:
                ordinal = next(sequence)
            leaf = f"{now_utc.strftime('%Y%m%dT%H%M%S%fZ')}-{ordinal:06d}"
            verifier_report: dict = {}

            def public_verify_manifest(manifest_path: Path) -> dict:
                nonlocal verifier_report
                verifier_report = verify_manifest(manifest_path)
                return verifier_report

            try:
                report = write_runtime_summary_receipt_bundle(
                    out_dir=out_dir / leaf,
                    summary_payload=summary,
                    now_utc=now_utc,
                    verify_manifest=public_verify_manifest,
                    evaluation_version=evaluation_version,
                )
            except ValueError as exc:
                if not str(exc).startswith("receipt bundle verification failed:"):
                    raise
                report = {
                    "receipt_count": 0,
                    "verifier_report": verifier_report
                    or {
                        "ok": False,
                        "receipt_count": 0,
                        "errors": [str(exc)],
                    },
                }
            verifier_report = report.get("verifier_report", {})
            return {
                "receipt_count": int(report.get("receipt_count", 0) or 0),
                "verifier_report": {
                    "ok": bool(verifier_report.get("ok", False)),
                    "receipt_count": int(
                        verifier_report.get("receipt_count", 0) or 0
                    ),
                    "errors": _public_runtime_receipt_verifier_errors(
                        verifier_report.get("errors", [])
                    ),
                },
                "sink": "configured_local_runtime_summary_receipts",
                "paths_returned": False,
                "payloads_returned": False,
                "default_runtime_receipt_emission_changed": False,
                "runtime_authority_changed": False,
            }

        return sink

    @cached_property
    def v313_solver_receipt_sink(self):
        """Optional local MAGMA receipt sink for v3.13 solver-dispatch results.

        Disabled by default. When explicitly enabled through the existing
        ``runtime_receipts.enabled`` gate, the sink writes sanitized receipt
        bundles for ``POST /api/solvers/{case_id}`` and returns only path-free
        verifier metadata to the API caller.
        """
        if not _settings_bool(self._settings.get("runtime_receipts.enabled", False)):
            return None

        configured_out_dir = self._settings.get(
            "runtime_receipts.v313_solver_out_dir",
            None,
        )
        if configured_out_dir:
            out_dir = Path(str(configured_out_dir))
        else:
            out_dir = Path(
                str(
                    self._settings.get(
                        "runtime_receipts.out_dir",
                        DEFAULT_RUNTIME_RECEIPT_OUT_DIR,
                    )
                )
            ) / "v313_solver_dispatch"
        max_bundles = _settings_positive_int(
            self._settings,
            "runtime_receipts.v313_solver_max_bundles",
            DEFAULT_V313_SOLVER_RECEIPT_MAX_BUNDLES,
        )
        sequence = count(1)
        sink_lock = Lock()

        def sink(dispatch_receipt: dict):
            from tools.verify_magma_receipt import verify_manifest
            from waggledance.core.v3_13_0.solver_receipt_sink import (
                write_v313_solver_dispatch_receipt_bundle,
            )

            with sink_lock:
                now_utc = datetime.now(timezone.utc)
                ordinal = next(sequence)
                leaf = f"{now_utc.strftime('%Y%m%dT%H%M%S%fZ')}-{ordinal:06d}"
                verifier_report: dict = {}
                cleanup_errors: list[str] = []

                def public_verify_manifest(manifest_path: Path) -> dict:
                    nonlocal verifier_report
                    verifier_report = verify_manifest(manifest_path)
                    return verifier_report

                try:
                    report = write_v313_solver_dispatch_receipt_bundle(
                        out_dir=out_dir / leaf,
                        dispatch_receipt=dispatch_receipt,
                        verify_manifest=public_verify_manifest,
                    )
                    raw_verifier_report = report.get("verifier_report", {})
                    verifier_report = (
                        dict(raw_verifier_report)
                        if isinstance(raw_verifier_report, dict)
                        else {}
                    )
                    receipt_count = int(report.get("receipt_count", 0) or 0)
                except Exception as exc:  # noqa: BLE001 - API receipt emission is advisory.
                    verifier_report = verifier_report or {
                        "ok": False,
                        "receipt_count": 0,
                        "errors": [str(exc)],
                    }
                    receipt_count = 0

                try:
                    _prune_v313_solver_receipt_bundles(out_dir, max_bundles)
                except Exception as exc:  # noqa: BLE001 - retain dispatch response.
                    cleanup_errors.append(str(exc))

            verifier_errors = list(verifier_report.get("errors", []))
            verifier_errors.extend(cleanup_errors)
            verifier_ok = bool(verifier_report.get("ok", False)) and not cleanup_errors
            return {
                "ok": verifier_ok and receipt_count > 0,
                "receipt_count": receipt_count,
                "verifier_report": {
                    "ok": verifier_ok,
                    "receipt_count": int(
                        verifier_report.get("receipt_count", 0) or 0
                    ),
                    "errors": _public_runtime_receipt_verifier_errors(
                        verifier_errors
                    ),
                },
                "sink": "configured_local_v313_solver_dispatch_receipts",
                "paths_returned": False,
                "payloads_returned": False,
                "default_runtime_receipt_emission_changed": False,
                "runtime_authority_changed": False,
            }

        return sink

    @cached_property
    def chat_served_emitter(self):
        """Optional ChatService served-receipt emitter.

        Disabled by default. When enabled, ChatService records a synchronous
        served-pending denominator and resolves it off-loop to a MAGMA receipt.
        The emitter is fail-open and never flips claim_safe.
        """
        if not _settings_bool(self._settings.get("chat_served_receipts.enabled", False)):
            return None

        try:
            from tools.verify_magma_receipt import verify_manifest
            from waggledance.core.magma.chat_served_emitter import ChatServedEmitter
            from waggledance.core.magma.chat_served_sink import ChatServedReceiptSink

            out_dir = Path(
                str(
                    self._settings.get(
                        "chat_served_receipts.out_dir",
                        DEFAULT_CHAT_SERVED_RECEIPT_OUT_DIR,
                    )
                )
            )
            fsync_every = _settings_positive_int(
                self._settings,
                "chat_served_receipts.fsync_every",
                32,
            )
            out_dir.mkdir(parents=True, exist_ok=True)
            sink = ChatServedReceiptSink(
                str(out_dir / "ledger.jsonl"),
                fsync_every=fsync_every,
            )
            return ChatServedEmitter(
                sink=sink,
                out_dir=out_dir,
                verify_manifest=verify_manifest,
                known_profiles=KNOWN_PROFILES,
                enabled=True,
            )
        except Exception as exc:  # noqa: BLE001 - chat serving must fail open.
            log.warning("chat-served emitter unavailable: %s", exc)
            return None

    # --- Core (lazy imports -- Agent 1 may still be running) ---

    @cached_property
    def scheduler(self):
        """Scheduler from core orchestration."""
        from waggledance.core.orchestration.scheduler import Scheduler
        return Scheduler(config=self.config)

    def _load_agents(self) -> list:
        """Load agent definitions from YAML files for the current profile.

        Synchronous — called once during orchestrator construction.
        Returns activated AgentDefinition list (empty on error).

        Audit H1+H24+H30+H12+H38+H51+H49 — see below for the
        per-finding contribution:

        H30: validate WAGGLE_PROFILE against KNOWN_PROFILES; unknown
        profile returns [] with ERROR log.

        H1+H24: derive ``agent.domain`` from
        configs/alias_registry.yaml canonical IDs (e.g.
        ``beekeeper -> domain.apiary.beekeeper`` -> "apiary") so the
        75 production agents distribute across hex cells instead of
        collapsing to "general"/hub. Empirical: hub-dominance
        100% (75/75) -> ~1% (1/75) on the same agents/ directory.

        H12 / D3.2: agents are constructed with active=False and
        then activated by AgentLifecycleManager.spawn_for_profile.
        AgentLifecycleManager is the sole owner of active-state
        transitions per its docstring; before this change the
        container set active=True inline, bypassing the manager and
        leaving it unwired in production. Profile filtering still
        happens at YAML-load time because agent YAMLs declare a
        *list* of supported profiles (``profiles: [HOME, COTTAGE]``)
        and lifecycle only knows the single ``agent.profile`` slot.
        """
        try:
            import yaml
            from waggledance.core.domain.agent import AgentDefinition
            from waggledance.core.orchestration.lifecycle import (
                AgentLifecycleManager,
            )

            agents_dir = Path("agents")
            if not agents_dir.exists():
                log.warning("Agents directory not found: %s", agents_dir)
                return []

            profile = self._settings.get_profile().upper()
            if profile not in KNOWN_PROFILES:
                # H30: fail loudly on unknown profile rather than
                # silently filtering away every agent.
                log.error(
                    "Unknown WAGGLE_PROFILE %r — expected one of %s. "
                    "Returning 0 agents; readiness probe will fail.",
                    profile, sorted(KNOWN_PROFILES),
                )
                return []
            # H24: load AliasRegistry once (best-effort — falls back to
            # the pre-H24 "general" default if the registry file is
            # absent or malformed so test fixtures without configs/
            # still boot).
            alias_registry = None
            try:
                from waggledance.core.capabilities.aliasing import AliasRegistry
                alias_registry = AliasRegistry.from_yaml_default()
            except Exception as exc:
                log.warning(
                    "AliasRegistry unavailable; agent.domain will fall "
                    "back to header.domain or 'general' (H24): %s", exc,
                )
            loaded: list[AgentDefinition] = []
            for yaml_file in sorted(agents_dir.rglob("*.yaml")):
                try:
                    with open(yaml_file, encoding="utf-8") as f:
                        data = yaml.load(f, Loader=yaml.CSafeLoader)
                    if not data or not isinstance(data, dict):
                        continue
                    header = data.get("header", {})
                    if not header.get("agent_id"):
                        continue
                    profiles = [p.upper() for p in data.get("profiles", ["ALL"])]
                    if profile not in profiles and "ALL" not in profiles:
                        continue
                    agent_id = header["agent_id"]
                    # H24: derive domain via AliasRegistry canonical ID.
                    domain = self._resolve_agent_domain(
                        agent_id, header, alias_registry,
                    )
                    # H12: agent.profile picked so lifecycle filter
                    # accepts the match (avoids pre-H12 profiles[0] bug).
                    if "ALL" in profiles:
                        agent_profile = "ALL"
                    else:
                        agent_profile = profile
                    agent = AgentDefinition(
                        id=agent_id,
                        name=header.get("agent_name", agent_id),
                        domain=domain,
                        tags=data.get("tags", []),
                        skills=list(
                            data.get("DECISION_METRICS_AND_THRESHOLDS", {}).keys()
                        ),
                        trust_level=0,
                        specialization_score=0.0,
                        active=False,
                        profile=agent_profile,
                    )
                    loaded.append(agent)
                except Exception as e:
                    log.warning("Failed to load agent %s: %s", yaml_file, e)

            # AgentLifecycleManager.spawn_for_profile is the sole owner
            # of the active flag (audit H12). Before this wiring the
            # manager existed in waggledance/core/orchestration/lifecycle.py
            # but the container never called it.
            lifecycle = AgentLifecycleManager()
            activated = lifecycle.spawn_for_profile(loaded, profile)
            log.info(
                "Loaded %d agents for profile %s (activated %d via "
                "AgentLifecycleManager)",
                len(loaded), profile, len(activated),
            )
            return activated
        except Exception as e:
            log.warning("Agent loading failed, orchestrator will run with 0 agents: %s", e)
            return []

    @staticmethod
    def _resolve_agent_domain(agent_id, header, alias_registry):
        """Derive ``agent.domain`` from alias_registry canonical ID (H24).

        Resolution order:
        1. explicit ``header.domain`` field if the YAML provides one
           (preserves manual overrides);
        2. canonical ID part [1] from alias_registry (e.g.
           ``shared.energy.advisor`` -> "energy");
        3. ``"general"`` (pre-H24 default — only used when both
           registry lookup and header field are missing).
        """
        explicit = header.get("domain")
        if explicit:
            return explicit
        if alias_registry is not None:
            canonical = alias_registry.resolve(agent_id)
            if canonical and "." in canonical:
                parts = canonical.split(".")
                if len(parts) >= 2 and parts[1]:
                    return parts[1]
        return "general"

    @cached_property
    def orchestrator(self):
        """Orchestrator from core orchestration."""
        from waggledance.core.orchestration.orchestrator import Orchestrator
        from waggledance.core.orchestration.round_table import RoundTableEngine
        rt = RoundTableEngine(
            llm=self.llm, event_bus=self.event_bus,
            parallel_dispatcher=self.parallel_dispatcher,
        )
        return Orchestrator(
            scheduler=self.scheduler,
            round_table=rt,
            memory=self.memory_repository,
            vector_store=self.vector_store,
            llm=self.llm,
            trust_store=self.trust_store,
            event_bus=self.event_bus,
            config=self.config,
            agents=self._load_agents(),
            parallel_dispatcher=self.parallel_dispatcher,
        )

    # --- Application Services (lazy imports) ---

    @cached_property
    def memory_service(self):
        """MemoryService from application layer."""
        from waggledance.application.services.memory_service import MemoryService
        return MemoryService(
            vector_store=self.vector_store,
            memory=self.memory_repository,
            event_bus=self.event_bus,
            hybrid_retrieval=self.hybrid_retrieval,
        )

    @cached_property
    def chat_service(self):
        """ChatService from application layer.

        Shares case_builder/case_store/verifier_store from AutonomyRuntime
        so chat traffic feeds into the learning funnel.
        """
        from waggledance.application.services.chat_service import ChatService
        from waggledance.core.orchestration.routing_policy import select_route

        # Get learning stores from autonomy runtime (shared instances)
        rt = self.autonomy_service._runtime
        return ChatService(
            orchestrator=self.orchestrator,
            memory_service=self.memory_service,
            hot_cache=self.hot_cache,
            routing_policy_fn=select_route,
            config=self.config,
            case_builder=rt.case_builder,
            case_store=rt.case_store,
            verifier_store=rt.verifier_store,
            hybrid_retrieval=self.hybrid_retrieval,
            hex_neighbor_assist=self.hex_neighbor_assist,
            chat_served_emitter=self.chat_served_emitter,
        )

    @cached_property
    def learning_service(self):
        """LearningService from application layer."""
        from waggledance.application.services.learning_service import LearningService
        return LearningService(
            orchestrator=self.orchestrator,
            memory_service=self.memory_service,
            llm=self.llm,
            event_bus=self.event_bus,
            stall_threshold=self._settings.night_stall_threshold,
        )

    @cached_property
    def readiness_service(self):
        """ReadinessService from application layer."""
        from waggledance.application.services.readiness_service import ReadinessService
        return ReadinessService(
            orchestrator=self.orchestrator,
            vector_store=self.vector_store,
            llm=self.llm,
        )

    @cached_property
    def night_pipeline(self):
        """NightLearningPipeline from core learning."""
        from waggledance.core.learning.night_learning_pipeline import NightLearningPipeline
        return NightLearningPipeline(profile=self._settings.get_profile())

    # --- Infrastructure (HW detection, throttle, OOM guard) ---

    @cached_property
    def elastic_scaler(self):
        """ElasticScaler — single source of truth for hardware detection."""
        from core.elastic_scaler import ElasticScaler
        scaler = ElasticScaler()
        scaler.detect()
        return scaler

    @cached_property
    def adaptive_throttle(self):
        """AdaptiveThrottle — dynamic load management."""
        from core.adaptive_throttle import AdaptiveThrottle
        return AdaptiveThrottle()

    @cached_property
    def resource_guard(self):
        """ResourceGuard — OOM protection."""
        from core.resource_guard import ResourceGuard
        return ResourceGuard()

    @cached_property
    def autonomy_service(self):
        """AutonomyService — wires runtime mode from settings."""
        from waggledance.application.services.autonomy_service import AutonomyService
        from waggledance.core.autonomy.compatibility import CompatibilityLayer
        from waggledance.core.autonomy.lifecycle import AutonomyLifecycle
        from waggledance.core.autonomy.resource_kernel import ResourceKernel
        from waggledance.core.autonomy.runtime import AutonomyRuntime

        profile = self._settings.get_profile()

        # Tier: use ElasticScaler as single source of truth when "auto",
        # otherwise use the explicitly configured tier.
        tier_setting = self._settings.get_hardware_tier()
        if tier_setting == "auto":
            tier = self.elastic_scaler.tier.tier
        else:
            tier = tier_setting

        resource_kernel = ResourceKernel(
            tier=tier,
            elastic_scaler=self.elastic_scaler,
            adaptive_throttle=self.adaptive_throttle,
        )
        resource_kernel.resource_guard = self.resource_guard

        runtime = AutonomyRuntime(
            profile=profile,
            resource_kernel=resource_kernel,
            runtime_receipt_sink=self.runtime_receipt_sink,
        )
        lifecycle = AutonomyLifecycle(
            primary=self._settings.runtime_primary,
            compatibility_mode=self._settings.compatibility_mode,
            profile=profile,
        )
        compatibility = CompatibilityLayer(
            runtime=runtime,
            compatibility_mode=self._settings.compatibility_mode,
        )

        return AutonomyService(
            runtime=runtime,
            lifecycle=lifecycle,
            resource_kernel=resource_kernel,
            compatibility=compatibility,
            profile=profile,
            night_pipeline=self.night_pipeline,
        )

    @cached_property
    def faiss_registry(self):
        """FaissRegistry — named FAISS collections for cell-local retrieval.

        Returns None if faiss-cpu is not installed (optional dependency).
        HybridRetrievalService treats None as "disabled" and falls back to
        ChromaDB-only retrieval.
        """
        try:
            from core.faiss_store import FaissRegistry
        except ImportError:
            return None
        return FaissRegistry()

    @cached_property
    def hex_cell_topology(self):
        """HexCellTopology — logical cell assignment and neighbor mapping."""
        from waggledance.core.hex_cell_topology import HexCellTopology
        return HexCellTopology()

    @cached_property
    def hybrid_retrieval(self):
        """HybridRetrievalService — cell-local FAISS + global ChromaDB orchestration.

        Feature-flagged via hybrid_retrieval.enabled in settings.yaml.
        """
        from waggledance.application.services.hybrid_retrieval_service import (
            HybridRetrievalService,
        )

        enabled = bool(self._settings.get("hybrid_retrieval.enabled", False))
        ring2 = bool(self._settings.get("hybrid_retrieval.ring2_enabled", False))
        mode = str(self._settings.get("hybrid_retrieval.mode", "shadow"))
        min_score = float(self._settings.get("hybrid_retrieval.min_score", 0.35))
        sufficient_score = float(self._settings.get("hybrid_retrieval.sufficient_score", 0.70))

        # Embedding function: reuse Ollama embed via vector_store's _embed_text if available
        embed_fn = None
        try:
            vs = self.vector_store
            if hasattr(vs, "_embed_text"):
                import numpy as np

                def _embed(text: str):
                    raw = vs._embed_text(text, prefix="search_query: ")
                    return np.array(raw, dtype=np.float32) if raw else None

                embed_fn = _embed
        except Exception:
            pass

        return HybridRetrievalService(
            faiss_registry=self.faiss_registry,
            topology=self.hex_cell_topology,
            vector_store=self.vector_store,
            embed_fn=embed_fn,
            enabled=enabled,
            ring2_enabled=ring2,
            mode=mode,
            min_score=min_score,
            sufficient_score=sufficient_score,
        )

    @cached_property
    def hybrid_observer(self):
        """Phase D-2 — observer that records hybrid's would-be solver decision
        alongside keyword's actual decision in MAGMA trace.

        Per v3 §1.1 candidate mode: hybrid traces both, production uses keyword.
        Output: data/runtime/magma_hybrid_candidate_trace.jsonl
        """
        from waggledance.core.reasoning.hybrid_observer import HybridObserver
        from pathlib import Path
        import yaml

        # Load all axiom solver_output_schemas
        specs = {}
        axioms_dir = Path("configs/axioms")
        if axioms_dir.exists():
            for axiom_path in axioms_dir.rglob("*.yaml"):
                try:
                    with open(axiom_path, encoding="utf-8") as f:
                        axiom = yaml.load(f, Loader=yaml.CSafeLoader) or {}
                    if axiom.get("model_id"):
                        specs[axiom["model_id"]] = axiom.get("solver_output_schema", {})
                except Exception:
                    pass

        return HybridObserver(
            hybrid_retrieval_service=self.hybrid_retrieval,
            solver_specs=specs,
            trace_file=Path(
                self._settings.get(
                    "hybrid_retrieval.trace_file",
                    "data/runtime/magma_hybrid_candidate_trace.jsonl",
                )
            ),
        )

    @cached_property
    def hybrid_backfill(self):
        """HybridBackfillService — idempotent cell-local FAISS population."""
        from waggledance.application.services.hybrid_backfill_service import (
            HybridBackfillService,
        )
        # Get case store from autonomy runtime if available
        case_store = None
        try:
            rt = self.autonomy_service._runtime
            case_store = rt.case_store
        except Exception:
            pass

        # Reuse same embed_fn as hybrid_retrieval
        embed_fn = getattr(self.hybrid_retrieval, '_embed_fn', None)

        return HybridBackfillService(
            hybrid_retrieval=self.hybrid_retrieval,
            case_store=case_store,
            embed_fn=embed_fn,
        )

    @cached_property
    def parallel_dispatcher(self):
        """ParallelLLMDispatcher — bounded concurrent LLM dispatch.

        Feature-flagged via llm_parallel.enabled in settings.yaml.
        When disabled, dispatch() is a zero-overhead passthrough.
        """
        from waggledance.application.services.parallel_llm_dispatcher import (
            ParallelLLMDispatcher,
        )
        return ParallelLLMDispatcher(
            settings=self._settings,
            llm=self.llm,
            gemma_router=self.gemma_router,
        )

    @cached_property
    def hex_topology_registry(self):
        """HexTopologyRegistry — loads hex cell topology and maps cells to agents."""
        from waggledance.application.services.hex_topology_registry import HexTopologyRegistry
        config_path = self._settings.get("hex_mesh.cell_config_path", "configs/hex_cells.yaml")
        return HexTopologyRegistry(
            config_path=config_path,
            agents=self._load_agents(),
        )

    @cached_property
    def hex_health_monitor(self):
        """HexHealthMonitor — cell quarantine, cooldown, self-heal."""
        from waggledance.application.services.hex_health_monitor import HexHealthMonitor
        return HexHealthMonitor(
            error_threshold=int(self._settings.get("hex_mesh.health_quarantine_error_threshold", 3)),
            timeout_threshold=int(self._settings.get("hex_mesh.health_quarantine_timeout_threshold", 2)),
            cooldown_s=float(self._settings.get("hex_mesh.health_cooldown_s", 300)),
            self_heal_probe_enabled=bool(self._settings.get("hex_mesh.self_heal_probe_enabled", True)),
        )

    @cached_property
    def hex_neighbor_assist(self):
        """HexNeighborAssist — local->neighbor->global resolution coordinator.

        Feature-flagged via hex_mesh.enabled in settings.yaml.
        """
        from waggledance.application.services.hex_neighbor_assist import HexNeighborAssist
        enabled = bool(self._settings.get("hex_mesh.enabled", False))

        magma_audit = None
        try:
            rt = self.autonomy_service._runtime
            magma_audit = rt.audit
        except Exception:
            pass

        return HexNeighborAssist(
            topology_registry=self.hex_topology_registry,
            health_monitor=self.hex_health_monitor,
            llm_service=self.llm,
            parallel_dispatcher=self.parallel_dispatcher,
            magma_audit=magma_audit,
            enabled=enabled,
            local_threshold=float(self._settings.get("hex_mesh.local_confidence_threshold", 0.72)),
            neighbor_threshold=float(self._settings.get("hex_mesh.neighbor_confidence_threshold", 0.82)),
            global_threshold=float(self._settings.get("hex_mesh.global_escalation_threshold", 0.90)),
            ttl_default=int(self._settings.get("hex_mesh.ttl_default", 2)),
            max_neighbors_per_hop=int(self._settings.get("hex_mesh.max_neighbors_per_hop", 2)),
            parallel_neighbor=bool(self._settings.get("hex_mesh.parallel_neighbor_assist", True)),
            merge_policy=str(self._settings.get("hex_mesh.neighbor_merge_policy", "weighted_confidence")),
            magma_trace_enabled=bool(self._settings.get("hex_mesh.magma_trace_enabled", True)),
            allow_neighbor_llm=bool(self._settings.get("hex_mesh.allow_neighbor_llm", True)),
            # v3.5.6: efficiency settings
            local_budget_ms=float(self._settings.get("hex_mesh.local_budget_ms", 15000)),
            neighbor_budget_ms=float(self._settings.get("hex_mesh.neighbor_budget_ms", 10000)),
            total_hex_budget_ms=float(self._settings.get("hex_mesh.total_hex_budget_ms", 25000)),
            skip_low_value_neighbor_when_sequential=bool(
                self._settings.get("hex_mesh.skip_low_value_neighbor_when_sequential", True)),
            preflight_min_score=float(self._settings.get("hex_mesh.preflight_min_score", 0.3)),
        )

    @cached_property
    def solver_candidate_lab(self):
        """SolverCandidateLab — safe solver candidate generation (isolated from production)."""
        from waggledance.application.services.solver_candidate_lab import SolverCandidateLab
        return SolverCandidateLab(
            llm=self.llm if not self._stub else None,
            gemma_router=self.gemma_router,
            parallel_dispatcher=self.parallel_dispatcher,
        )

    @cached_property
    def gemma_verifier_advisor(self):
        """GemmaVerifierAdvisor — optional advisory for deterministic verifier."""
        from waggledance.application.services.solver_candidate_lab import GemmaVerifierAdvisor
        return GemmaVerifierAdvisor(gemma_router=self.gemma_router)

    @cached_property
    def synthetic_accelerator(self):
        """SyntheticTrainingAccelerator — deterministic synthetic data augmentation."""
        from waggledance.core.learning.synthetic_accelerator import SyntheticTrainingAccelerator
        gpu_enabled = bool(self._settings.get("learning.gpu_enabled", False))
        return SyntheticTrainingAccelerator(gpu_enabled=gpu_enabled)

    @cached_property
    def storage_health(self):
        """StorageHealthService — DB size/WAL introspection."""
        from waggledance.application.services.storage_health_service import StorageHealthService
        import os
        data_dir = os.path.dirname(self._settings.db_path) or "data"
        return StorageHealthService(data_dir=data_dir)

    # --- Data feeds (weather / electricity / RSS) ---

    @cached_property
    def priority_lock(self):
        """PriorityLock — async gate used by background feeds."""
        from waggledance.core.priority_lock import PriorityLock
        return PriorityLock()

    @cached_property
    def feed_ingest_sink(self):
        """FeedIngestSink — bridges legacy learn(...) into VectorStorePort.upsert."""
        from waggledance.adapters.feeds.feed_ingest_sink import FeedIngestSink
        return FeedIngestSink(vector_store=self.vector_store)

    @cached_property
    def data_feed_scheduler(self):
        """DataFeedScheduler — returns None when feeds.enabled is false."""
        feeds_cfg = self._settings.get("feeds", {}) or {}
        if not feeds_cfg.get("enabled", False):
            return None
        # integrations.* is deliberate: legacy feed modules live there and
        # the legacy-import-freeze guard only blocks ``core.*`` imports.
        from integrations.data_scheduler import DataFeedScheduler
        return DataFeedScheduler(
            config=feeds_cfg,
            consciousness=self.feed_ingest_sink,
            priority_lock=self.priority_lock,
        )

    @cached_property
    def advisory_refresh_ticker(self):
        """AdvisoryRefreshTicker — returns None when advisory_refresh.enabled is false."""
        cfg = self._settings.get("advisory_refresh", {}) or {}
        if not isinstance(cfg, dict) or not cfg.get("enabled", False):
            return None
        try:
            from waggledance.adapters.feeds.advisory_refresh_ticker import (
                AdvisoryRefreshTicker,
            )

            return AdvisoryRefreshTicker(cfg)
        except Exception as exc:
            logger.warning(
                "AdvisoryRefreshTicker construction failed; advisory "
                "refresh inactive: %s", exc,
            )
            return None

    @cached_property
    def route_stage_latency_feed(self):
        """Optional read-only Prometheus/Alertmanager route-stage feed."""
        cfg = self._settings.get("route_stage_latency_feed", {}) or {}
        if not isinstance(cfg, dict) or not cfg.get("enabled", False):
            return None
        try:
            from waggledance.adapters.http.route_stage_latency_feed import (
                RouteStageLatencyPrometheusAlertmanagerFeed,
            )

            return RouteStageLatencyPrometheusAlertmanagerFeed.from_config(cfg)
        except Exception as exc:
            log.error(
                "Route-stage latency feed configuration refused; "
                "feed_state will report unavailable: %s",
                exc,
            )
            from waggledance.adapters.http.route_stage_latency_feed import (
                UnavailableRouteStageLatencyFeed,
            )

            return UnavailableRouteStageLatencyFeed()

    @cached_property
    def magma_share_import_handoff_metrics_alert_feed(self):
        """Optional read-only Alertmanager feed for MAGMA handoff metrics."""
        cfg = self._settings.get("magma_handoff_metrics_alert_feed", {}) or {}
        if not isinstance(cfg, dict) or not cfg.get("enabled", False):
            return None
        try:
            from waggledance.adapters.http.magma_handoff_metrics_alert_feed import (
                MagmaHandoffMetricsAlertmanagerFeed,
            )

            return MagmaHandoffMetricsAlertmanagerFeed.from_config(cfg)
        except Exception as exc:
            log.error(
                "MAGMA handoff metrics alert feed configuration refused; "
                "metrics_alert_state will report unavailable: %s",
                exc,
            )
            from waggledance.adapters.http.magma_handoff_metrics_alert_feed import (
                UnavailableMagmaHandoffMetricsAlertFeed,
            )

            return UnavailableMagmaHandoffMetricsAlertFeed()

    def build_app(self):
        """Build FastAPI application."""
        from waggledance.adapters.http.api import create_app
        return create_app(self)
