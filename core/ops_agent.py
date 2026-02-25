# WaggleDance Swarm AI • v0.0.3
# Jani Korpi (Ahkerat Mehiläiset)
"""
OpsAgent — Järjestelmän operaattoriagentti
==========================================
Reaaliaikainen valvonta-agentti joka:

  1. MONITOROI Ollaman todellista tilaa (jono, latenssi, muisti)
  2. SÄÄTÄÄ throttle-arvoja mittausten perusteella (ei arvauksia)
  3. ARVIOI mallien suorituskykyä eri kuormituksilla
  4. SUOSITTELEE mallinvaihtoa kun kuorma muuttuu

Ei käytä LLM:ää monitorointipäätöksiin — puhdas mittaus + heuristiikka.
LLM:ää käytetään VAIN harvinaisiin laadun arviointeihin (~joka 30. sykli).

Filosofia:
  "Mehiläisparven portinvartija ei osallistu hunajan keräämiseen.
   Se tarkkailee liikennettä ja säätelee lentoaukon kokoa."

Integraatio:
  ops = OpsAgent(throttle, llm, llm_heartbeat, config)
  await ops.start()       # Aloittaa oman syklinsä
  ops.get_status()        # Dashboard
  await ops.stop()

  # Heartbeat-loopista (hivemind.py):
  ops.report_task_result(latency_ms, success, model_used)
"""

import asyncio
import time
import logging
import statistics
from collections import deque, defaultdict
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from datetime import datetime

logger = logging.getLogger("waggle.ops")


# ═══════════════════════════════════════════════════════════════
# Tietorakenteet
# ═══════════════════════════════════════════════════════════════

@dataclass
class ModelProfile:
    """Yhden kielimallin suoritusprofiili."""
    model_name: str
    # Mitattu
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    error_rate: float = 0.0
    tokens_per_second: float = 0.0
    # Laatu (LLM-arvioitu, 0-10)
    quality_score: float = 5.0
    quality_samples: int = 0
    # Kuormitus
    max_healthy_concurrent: int = 2
    # Meta
    last_benchmark: float = 0.0
    total_requests: int = 0
    total_errors: int = 0
    # Viimeisimmät latenssiarvot
    _latencies: deque = field(default_factory=lambda: deque(maxlen=50))

    def record(self, latency_ms: float, success: bool):
        self.total_requests += 1
        if success:
            self._latencies.append(latency_ms)
            if self._latencies:
                self.avg_latency_ms = statistics.mean(self._latencies)
                self.p95_latency_ms = (
                    sorted(self._latencies)[int(len(self._latencies) * 0.95)]
                    if len(self._latencies) >= 5 else self.avg_latency_ms
                )
        else:
            self.total_errors += 1

        recent = min(self.total_requests, 20)
        if recent > 0:
            self.error_rate = self.total_errors / max(self.total_requests, 1)
            # Painota viimeisiin → tarkempi kuva
            recent_errs = sum(
                1 for _ in range(min(len(self._latencies), 1))
                if not success
            )
            # Yksinkertainen laskuri viimeisille
            self.error_rate = min(self.error_rate, 1.0)

    def efficiency_score(self) -> float:
        """
        Kokonaishyötysuhde: laatu × nopeus × luotettavuus.
        Korkeampi = parempi.
        """
        if self.total_requests < 3:
            return 0.0  # Ei tarpeeksi dataa

        # Nopeus: 0-1 (500ms=1.0, 30s=0.0)
        speed = max(0, 1.0 - (self.avg_latency_ms / 30000))

        # Luotettavuus: 0-1
        reliability = 1.0 - self.error_rate

        # Laatu: 0-1
        quality = self.quality_score / 10.0

        # Painotettu: laatu 40%, nopeus 30%, luotettavuus 30%
        return (quality * 0.4) + (speed * 0.3) + (reliability * 0.3)


@dataclass
class OllamaSnapshot:
    """Ollaman hetkellinen tila."""
    timestamp: float = 0.0
    running_models: list = field(default_factory=list)
    queue_depth: int = 0       # ps:n perusteella arvioitu
    gpu_percent: int = 0
    cpu_percent: int = 0
    gpu_memory_mb: int = 0
    gpu_memory_total_mb: int = 0
    error: str = ""


@dataclass
class OpsDecision:
    """Yksi OpsAgentin päätös + perustelu."""
    timestamp: float
    action: str           # "scale_down" | "scale_up" | "switch_model" | "pause_idle" | "resume_idle"
    reason: str
    old_value: Any = None
    new_value: Any = None
    confidence: float = 0.0  # 0-1

    def __str__(self):
        return f"[OPS] {self.action}: {self.reason} ({self.old_value}→{self.new_value})"


# ═══════════════════════════════════════════════════════════════
# OpsAgent
# ═══════════════════════════════════════════════════════════════

class OpsAgent:
    """
    Järjestelmän operaattoriagentti.

    EI KÄYTÄ LLM:ää monitorointiin. Puhdas mittaus + heuristiikka.
    LLM vain harvinaisiin laadun arviointeihin.
    """

    def __init__(self, throttle, llm_chat, llm_heartbeat, config: dict = None):
        self.throttle = throttle
        self.llm_chat = llm_chat
        self.llm_hb = llm_heartbeat
        self.config = config or {}

        # ── Malliprofiilien ylläpito ──────────────────
        self.model_profiles: Dict[str, ModelProfile] = {}
        self._init_profiles()

        # ── Ollama-snapshots ──────────────────────────
        self._snapshots = deque(maxlen=60)  # 60 viimeisintä
        self._ollama_base_url = (
            self.config.get("llm", {}).get("base_url", "http://localhost:11434")
        )

        # ── Päätöshistoria ────────────────────────────
        self.decisions: deque = deque(maxlen=100)
        self._decision_callbacks = []

        # ── Tila ──────────────────────────────────────
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._cycle_count = 0
        self._idle_paused = False

        # ── Konfiguraatio ─────────────────────────────
        ops_cfg = self.config.get("ops_agent", {})
        self.monitor_interval = ops_cfg.get("monitor_interval", 15)  # 15s
        self.quality_eval_every = ops_cfg.get("quality_eval_every", 30)  # Joka 30. sykli
        self.auto_switch_enabled = ops_cfg.get("auto_model_switch", False)

        # ── Kynnysarvot ───────────────────────────────
        self.thresholds = {
            "latency_critical_ms": ops_cfg.get("latency_critical_ms", 15000),
            "latency_warning_ms": ops_cfg.get("latency_warning_ms", 8000),
            "error_rate_critical": ops_cfg.get("error_rate_critical", 0.25),
            "error_rate_warning": ops_cfg.get("error_rate_warning", 0.10),
            "gpu_memory_critical_pct": ops_cfg.get("gpu_memory_critical", 90),
            "queue_depth_critical": ops_cfg.get("queue_critical", 4),
        }

    def _init_profiles(self):
        """Luo profiili jokaiselle tunnetulle mallille."""
        chat_model = self.llm_chat.model if self.llm_chat else "unknown"
        hb_model = self.llm_hb.model if self.llm_hb else chat_model

        if chat_model not in self.model_profiles:
            self.model_profiles[chat_model] = ModelProfile(
                model_name=chat_model,
                quality_score=7.0,  # 32b oletus: korkea laatu
                max_healthy_concurrent=2,
            )
        if hb_model not in self.model_profiles:
            self.model_profiles[hb_model] = ModelProfile(
                model_name=hb_model,
                quality_score=5.0,  # 7b oletus: ok laatu
                max_healthy_concurrent=4,
            )

    # ─────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────

    async def start(self):
        """Käynnistä monitorointisykli."""
        self.running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("OpsAgent käynnistetty")
        print("  ✅ OpsAgent (järjestelmävalvonta) käynnissä")

    async def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("OpsAgent pysäytetty")

    # ─────────────────────────────────────────────────────────
    # Ulkoinen raportointi (hivemind kutsuu)
    # ─────────────────────────────────────────────────────────

    def report_task_result(self, latency_ms: float, success: bool,
                           model_used: str = ""):
        """
        Hivemind kutsuu joka LLM-kutsun jälkeen.
        OpsAgent kerää datan malliprofiiliin.
        """
        if not model_used:
            model_used = self.llm_hb.model if self.llm_hb else "unknown"

        profile = self.model_profiles.get(model_used)
        if not profile:
            profile = ModelProfile(model_name=model_used)
            self.model_profiles[model_used] = profile

        profile.record(latency_ms, success)

    def register_decision_callback(self, callback):
        """Dashboard/WS kuuntelija päätöksille."""
        self._decision_callbacks.append(callback)

    # ─────────────────────────────────────────────────────────
    # Pääsykli
    # ─────────────────────────────────────────────────────────

    async def _monitor_loop(self):
        """
        Monitorointisykli.
        Joka kierros:
          1. Ota Ollama-snapshot (HTTP, ei LLM)
          2. Analysoi trendi
          3. Tee päätökset
          4. Harvinaisesti: laadun arviointi (LLM)
        """
        while self.running:
            try:
                await asyncio.sleep(self.monitor_interval)
                if not self.running:
                    break

                self._cycle_count += 1

                # 1. Snapshot
                snapshot = await self._take_snapshot()
                self._snapshots.append(snapshot)

                # 2. Analysoi + toimi
                await self._analyze_and_act(snapshot)

                # 3. Laadun arviointi (harvoin)
                if (self._cycle_count % self.quality_eval_every == 0
                        and self._cycle_count > 0):
                    asyncio.create_task(self._quality_benchmark())

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"OpsAgent syklivirhe: {e}")

    # ─────────────────────────────────────────────────────────
    # 1. Snapshot: Ollaman todellinen tila
    # ─────────────────────────────────────────────────────────

    async def _take_snapshot(self) -> OllamaSnapshot:
        """Kysy Ollamalta mitä se oikeasti tekee (ei LLM-kutsu)."""
        snap = OllamaSnapshot(timestamp=time.monotonic())

        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                # /api/ps — mitä malleja ladattu, paljonko muistia
                resp = await client.get(f"{self._ollama_base_url}/api/ps")
                if resp.status_code == 200:
                    data = resp.json()
                    models = data.get("models", [])
                    snap.running_models = [
                        {
                            "name": m.get("name", "?"),
                            "size_mb": m.get("size", 0) // (1024 * 1024),
                            "vram_mb": m.get("size_vram", 0) // (1024 * 1024),
                            "expires": m.get("expires_at", ""),
                        }
                        for m in models
                    ]
                    # Jonoarvio: montako mallia ladattu = potentiaalinen konflikti
                    snap.queue_depth = len(models)
        except Exception as e:
            snap.error = f"Ollama /api/ps: {e}"

        # GPU-tiedot (nvidia-smi)
        try:
            import subprocess
            result = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=utilization.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split(",")
                if len(parts) >= 3:
                    snap.gpu_percent = int(parts[0].strip())
                    snap.gpu_memory_mb = int(parts[1].strip())
                    snap.gpu_memory_total_mb = int(parts[2].strip())
        except (FileNotFoundError, Exception):
            pass

        # CPU
        try:
            import psutil
            snap.cpu_percent = psutil.cpu_percent(interval=0)
        except ImportError:
            pass

        return snap

    # ─────────────────────────────────────────────────────────
    # 2. Analyysi + toimenpiteet
    # ─────────────────────────────────────────────────────────

    async def _analyze_and_act(self, snap: OllamaSnapshot):
        """
        Analysoi tilanne ja tee tarvittavat päätökset.
        HUOM: Ei käytä LLM:ää — puhdas logiikka.
        """
        th = self.thresholds
        actions = []

        # ── Cooldown: älä toista samaa päätöstä ───────
        last_action = (self.decisions[-1].action
                       if self.decisions else "")
        last_action_time = (self.decisions[-1].timestamp
                            if self.decisions else 0)

        # ── AUTO-RECOVERY ─────────────────────────────
        # Jos ollaan jo minimissä (conc=1) pitkään → yritä palautua
        # Kuolemanspiraali: emergency→conc=1→latenssi korkea→emergency...
        # Ratkaisu: jos 3min minimissä, kokeile varovasti nostaa
        if (self.throttle.state.max_concurrent <= 1
                and self._idle_paused
                and time.monotonic() - last_action_time > 180):  # 3min
            await self._execute_action(
                "auto_recover", 0,
                "3min minimissä → kokeillaan varovaista palautusta"
            )
            return  # Älä tee muuta tällä kierroksella

        # ── A) Latenssitrendi ─────────────────────────
        recent_latencies = []
        for profile in self.model_profiles.values():
            if profile._latencies:
                recent_latencies.extend(list(profile._latencies)[-10:])

        # ── KRIITTINEN: Suodata mallinvaihto-latenssit pois ──
        # Jos Ollama vaihtaa mallia (7b↔32b), latenssi on 60-180s.
        # Tämä EI tarkoita ylikuormaa — se on normaalia GPU:lla.
        # Käytä vain <60s latensseja päätöksenteossa.
        MODEL_SWAP_THRESHOLD_MS = 60_000
        real_latencies = [l for l in recent_latencies
                          if l < MODEL_SWAP_THRESHOLD_MS]

        if real_latencies:
            avg_lat = statistics.mean(real_latencies)
            trend = self._latency_trend()

            if avg_lat > th["latency_critical_ms"]:
                actions.append(("emergency_slowdown", avg_lat, "Kriittinen latenssi"))
            elif avg_lat > th["latency_warning_ms"] and trend > 0:
                actions.append(("gradual_slowdown", avg_lat, "Nouseva latenssi"))
            elif avg_lat < 2000 and trend < 0 and not self._idle_paused:
                actions.append(("try_speedup", avg_lat, "Laskeva latenssi"))

        # ── B) Virheaste ──────────────────────────────
        total_err_rate = self._overall_error_rate()
        if total_err_rate > th["error_rate_critical"]:
            actions.append(("error_critical", total_err_rate, "Korkea virheaste"))
        elif total_err_rate > th["error_rate_warning"]:
            actions.append(("error_warning", total_err_rate, "Kohtalainen virheaste"))

        # ── C) GPU-muisti ─────────────────────────────
        # HUOM: Korkea GPU-muisti on NORMAALIA kun malli on ladattu!
        # qwen2.5:32b ≈ 20GB → 24GB GPU = 83% aina
        # Hälytä VAIN jos muisti on korkea JA latenssi/virheitä
        if snap.gpu_memory_total_mb > 0:
            gpu_pct = (snap.gpu_memory_mb / snap.gpu_memory_total_mb) * 100
            has_latency_issues = (real_latencies
                                  and statistics.mean(real_latencies) > th["latency_warning_ms"])
            has_error_issues = total_err_rate > th["error_rate_warning"]
            if (gpu_pct > th["gpu_memory_critical_pct"]
                    and (has_latency_issues or has_error_issues)):
                actions.append(("gpu_memory_critical", gpu_pct,
                                f"GPU-muisti {gpu_pct:.0f}% + "
                                f"latenssi/virheitä"))

        # ── D) Mallien model-switch ───────────────────
        if snap.queue_depth >= th["queue_depth_critical"]:
            actions.append(("queue_deep", snap.queue_depth,
                            "Malleja jonossa / ladattu paljon"))

        # ── Toteuta päätökset (ei samaa toistuvasti) ──
        for action_type, value, reason in actions:
            # Cooldown: sama päätös max kerran per 2min
            if (action_type == last_action
                    and time.monotonic() - last_action_time < 120):
                continue
            await self._execute_action(action_type, value, reason)

        # ── E) Jos kaikki OK + pitkään stabiili → yritä palauttaa ──
        if not actions and self._idle_paused and self._stable_for(5):
            await self._execute_action("resume_idle", 0,
                                       "Stabiilin jakson jälkeen palautetaan idle-tutkimus")

    def _latency_trend(self) -> float:
        """
        Latenssitrendin suunta.
        > 0 = nouseva (huononee), < 0 = laskeva (paranee), ≈0 = stabiili.
        """
        all_lat = []
        for p in self.model_profiles.values():
            all_lat.extend(list(p._latencies))

        if len(all_lat) < 6:
            return 0.0

        recent = all_lat[-5:]
        older = all_lat[-10:-5] if len(all_lat) >= 10 else all_lat[:5]

        avg_recent = statistics.mean(recent)
        avg_older = statistics.mean(older)

        if avg_older == 0:
            return 0.0
        return (avg_recent - avg_older) / avg_older  # +0.5 = 50% huonompi

    def _overall_error_rate(self) -> float:
        """Kaikkien mallien yhteinen virheaste."""
        total_req = sum(p.total_requests for p in self.model_profiles.values())
        total_err = sum(p.total_errors for p in self.model_profiles.values())
        if total_req == 0:
            return 0.0
        return total_err / total_req

    def _stable_for(self, n_cycles: int) -> bool:
        """Onko järjestelmä ollut stabiili n viimeistä sykliä?"""
        recent_decisions = list(self.decisions)[-n_cycles:]
        # Stabiili = ei scale_down -päätöksiä
        return not any(
            d.action in ("emergency_slowdown", "gradual_slowdown",
                         "error_critical", "gpu_memory_critical")
            for d in recent_decisions
        )

    # ─────────────────────────────────────────────────────────
    # 3. Toimenpiteiden toteutus
    # ─────────────────────────────────────────────────────────

    async def _execute_action(self, action_type: str, value: Any, reason: str):
        """Toteuta yksittäinen toimenpide."""
        decision = OpsDecision(
            timestamp=time.monotonic(),
            action=action_type,
            reason=reason,
        )

        if action_type == "emergency_slowdown":
            # KORJAUS: Älä toista jos jo minimissä
            if self.throttle.state.max_concurrent <= 1 and self._idle_paused:
                return  # Jo minimissä, turha toistaa
            old = self.throttle.state.max_concurrent
            self.throttle.state.max_concurrent = 1
            self.throttle.state.heartbeat_interval = min(
                self.throttle.state.heartbeat_interval * 2, 300
            )
            self.throttle._semaphore = asyncio.Semaphore(1)
            self._idle_paused = True
            decision.old_value = old
            decision.new_value = 1
            decision.confidence = 0.95
            self._log_decision(decision,
                               f"🚨 HÄTÄJARRU: concurrent {old}→1, "
                               f"idle PYSÄYTETTY (latenssi: {value:.0f}ms)")

        elif action_type == "auto_recover":
            # Kuolemanspiraali-esto: kokeile varovasti palautua
            old_hb = self.throttle.state.heartbeat_interval
            new_hb = max(old_hb * 0.7, 60)  # Ei alle 60s
            self.throttle.state.max_concurrent = 2  # Varovainen: 2
            self.throttle.state.heartbeat_interval = new_hb
            self.throttle._semaphore = asyncio.Semaphore(2)
            self._idle_paused = False
            decision.old_value = f"conc=1, hb={old_hb:.0f}s, idle=OFF"
            decision.new_value = f"conc=2, hb={new_hb:.0f}s, idle=ON"
            decision.confidence = 0.5
            self._log_decision(decision,
                               f"🔄 AUTO-RECOVERY: conc→2, "
                               f"HB {old_hb:.0f}→{new_hb:.0f}s, idle ON")

        elif action_type == "gradual_slowdown":
            old = self.throttle.state.max_concurrent
            new = max(old - 1, 1)
            old_hb = self.throttle.state.heartbeat_interval
            new_hb = min(old_hb * 1.3, 300)
            self.throttle.state.max_concurrent = new
            self.throttle.state.heartbeat_interval = new_hb
            self.throttle._semaphore = asyncio.Semaphore(new)
            decision.old_value = f"conc={old}, hb={old_hb:.0f}s"
            decision.new_value = f"conc={new}, hb={new_hb:.0f}s"
            decision.confidence = 0.8
            self._log_decision(decision,
                               f"⬇️  Hidastus: conc {old}→{new}, "
                               f"HB {old_hb:.0f}→{new_hb:.0f}s")

        elif action_type == "error_critical":
            old = self.throttle.state.max_concurrent
            self.throttle.state.max_concurrent = 1
            self.throttle.state.heartbeat_interval = min(
                self.throttle.state.heartbeat_interval * 1.5, 300
            )
            self.throttle._semaphore = asyncio.Semaphore(1)
            self._idle_paused = True
            decision.old_value = f"err={value:.0%}"
            decision.new_value = "conc=1, idle=OFF"
            decision.confidence = 0.9
            self._log_decision(decision,
                               f"🔴 Virheaste {value:.0%}: "
                               f"concurrent→1, idle PYSÄYTETTY")

        elif action_type == "error_warning":
            old = self.throttle.state.idle_every_n_heartbeat
            new = min(old + 5, 30)
            self.throttle.state.idle_every_n_heartbeat = new
            decision.old_value = f"idle_every={old}"
            decision.new_value = f"idle_every={new}"
            decision.confidence = 0.7
            self._log_decision(decision,
                               f"🟡 Virheaste {value:.0%}: "
                               f"idle harvemmin ({old}→{new})")

        elif action_type == "gpu_memory_critical":
            self._idle_paused = True
            self.throttle.state.max_concurrent = 1
            self.throttle._semaphore = asyncio.Semaphore(1)
            decision.old_value = f"GPU mem {value:.0f}%"
            decision.new_value = "conc=1, idle=OFF"
            decision.confidence = 0.85
            self._log_decision(decision,
                               f"🔴 GPU-muisti {value:.0f}%: "
                               f"concurrent→1, idle OFF")

        elif action_type == "queue_deep":
            old_hb = self.throttle.state.heartbeat_interval
            new_hb = min(old_hb * 1.5, 300)
            self.throttle.state.heartbeat_interval = new_hb
            decision.old_value = f"hb={old_hb:.0f}s"
            decision.new_value = f"hb={new_hb:.0f}s"
            decision.confidence = 0.75
            self._log_decision(decision,
                               f"⏳ Jono syvä ({value}): HB {old_hb:.0f}→{new_hb:.0f}s")

        elif action_type == "try_speedup":
            old = self.throttle.state.max_concurrent
            new = min(old + 1, 4)  # Max 4 (varovainen)
            old_hb = self.throttle.state.heartbeat_interval
            new_hb = max(old_hb * 0.85, 30)
            self.throttle.state.max_concurrent = new
            self.throttle.state.heartbeat_interval = new_hb
            self.throttle._semaphore = asyncio.Semaphore(new)
            decision.old_value = f"conc={old}, hb={old_hb:.0f}s"
            decision.new_value = f"conc={new}, hb={new_hb:.0f}s"
            decision.confidence = 0.6
            self._log_decision(decision,
                               f"⬆️  Nopeutus: conc {old}→{new}, "
                               f"HB {old_hb:.0f}→{new_hb:.0f}s")

        elif action_type == "resume_idle":
            self._idle_paused = False
            old_idle = self.throttle.state.idle_every_n_heartbeat
            new_idle = max(old_idle - 2, 3)
            self.throttle.state.idle_every_n_heartbeat = new_idle
            decision.old_value = "idle=OFF"
            decision.new_value = f"idle=ON (every {new_idle})"
            decision.confidence = 0.65
            self._log_decision(decision,
                               f"🟢 Palautettu: idle ON (joka {new_idle}. HB)")

        self.decisions.append(decision)

        # Callbacks (dashboard)
        for cb in self._decision_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb({"type": "ops_decision", "title": str(decision)})
                else:
                    cb({"type": "ops_decision", "title": str(decision)})
            except Exception:
                pass

    def _log_decision(self, decision: OpsDecision, msg: str):
        logger.info(msg)
        print(f"  [OPS] {msg}")

    # ─────────────────────────────────────────────────────────
    # 4. Laadun arviointi (harvinainen LLM-kutsu)
    # ─────────────────────────────────────────────────────────

    async def _quality_benchmark(self):
        """
        Testaa kielimallien laatua standardikysymyksillä.
        Kutsutaan HARVOIN (~joka 30. sykli = ~7.5 min).
        """
        test_prompts = [
            {
                "prompt": "Kuinka monta siipeä mehiläisellä on?",
                "expected_keyword": "4",  # 4 siipeä
                "category": "fakta",
            },
            {
                "prompt": "Laske: 7 × 8 + 3 = ?",
                "expected_keyword": "59",
                "category": "laskenta",
            },
        ]

        for model_name, profile in self.model_profiles.items():
            # Valitse oikea LLM
            llm = (self.llm_chat if model_name == self.llm_chat.model
                   else self.llm_hb)

            correct = 0
            total = 0
            for test in test_prompts:
                try:
                    t0 = time.monotonic()
                    resp = await llm.generate(
                        test["prompt"],
                        system="Vastaa lyhyesti suomeksi. Vain fakta.",
                        max_tokens=50,
                        temperature=0.1
                    )
                    elapsed = (time.monotonic() - t0) * 1000

                    if resp and not resp.error and resp.content:
                        total += 1
                        if test["expected_keyword"] in resp.content:
                            correct += 1
                        profile.record(elapsed, True)
                    else:
                        total += 1
                        profile.record(elapsed, False)
                except Exception:
                    total += 1
                    profile.record(30000, False)

            # Päivitä laatupisteet
            if total > 0:
                raw_score = (correct / total) * 10  # 0-10
                # Liukuva keskiarvo (painota uutta 30%)
                profile.quality_score = (
                    profile.quality_score * 0.7 + raw_score * 0.3
                )
                profile.quality_samples += total
                profile.last_benchmark = time.monotonic()

                logger.info(
                    f"[OPS] Laatu {model_name}: {correct}/{total} oikein "
                    f"→ score {profile.quality_score:.1f}/10, "
                    f"eff={profile.efficiency_score():.2f}"
                )

        # Suosittele mallinvaihtoa?
        await self._evaluate_model_switch()

    async def _evaluate_model_switch(self):
        """
        Arvioi pitäisikö heartbeat-mallia vaihtaa.
        Esim. jos 7b on liian epätarkka mutta 14b olisi mahdollinen.
        Tai jos 32b on liian hidas ja 14b riittävä.
        """
        if not self.auto_switch_enabled:
            return

        profiles = sorted(
            self.model_profiles.values(),
            key=lambda p: p.efficiency_score(),
            reverse=True,
        )

        if len(profiles) < 2:
            return

        best = profiles[0]
        current_hb = self.llm_hb.model if self.llm_hb else "?"

        if (best.model_name != current_hb
                and best.efficiency_score() > 0.5
                and best.total_requests >= 5):
            decision = OpsDecision(
                timestamp=time.monotonic(),
                action="recommend_model_switch",
                reason=(f"Malli {best.model_name} (eff={best.efficiency_score():.2f}) "
                        f"on parempi kuin {current_hb}"),
                old_value=current_hb,
                new_value=best.model_name,
                confidence=best.efficiency_score(),
            )
            self.decisions.append(decision)
            self._log_decision(decision,
                               f"💡 SUOSITUS: Vaihda heartbeat "
                               f"{current_hb} → {best.model_name} "
                               f"(eff: {best.efficiency_score():.2f})")

    # ─────────────────────────────────────────────────────────
    # Julkinen API
    # ─────────────────────────────────────────────────────────

    @property
    def idle_paused(self) -> bool:
        """Onko idle-tutkimus pysäytetty OpsAgentin toimesta?"""
        return self._idle_paused

    def get_status(self) -> dict:
        """Dashboard-tiedot."""
        last_snap = self._snapshots[-1] if self._snapshots else None

        model_summaries = {}
        for name, p in self.model_profiles.items():
            model_summaries[name] = {
                "avg_latency_ms": round(p.avg_latency_ms),
                "p95_latency_ms": round(p.p95_latency_ms),
                "error_rate": round(p.error_rate, 3),
                "quality_score": round(p.quality_score, 1),
                "efficiency": round(p.efficiency_score(), 3),
                "total_requests": p.total_requests,
                "total_errors": p.total_errors,
            }

        recent_decisions_list = [
            {
                "action": d.action,
                "reason": d.reason,
                "confidence": round(d.confidence, 2),
                "old": str(d.old_value) if d.old_value else "",
                "new": str(d.new_value) if d.new_value else "",
            }
            for d in list(self.decisions)[-10:]
        ]

        return {
            "running": self.running,
            "cycle_count": self._cycle_count,
            "idle_paused": self._idle_paused,
            "monitor_interval_s": self.monitor_interval,
            "models": model_summaries,
            "decisions": recent_decisions_list,
            "ollama": {
                "running_models": (last_snap.running_models
                                   if last_snap else []),
                "gpu_percent": last_snap.gpu_percent if last_snap else 0,
                "gpu_memory_mb": last_snap.gpu_memory_mb if last_snap else 0,
                "gpu_memory_total_mb": (last_snap.gpu_memory_total_mb
                                        if last_snap else 0),
            },
            "latency_trend": round(self._latency_trend(), 3),
            "overall_error_rate": round(self._overall_error_rate(), 3),
        }

    def get_model_recommendation(self) -> dict:
        """
        Palauttaa mallisuosituksen nykyiselle kuormitukselle.
        Käytettävissä dashboard:sta tai muista agenteista.
        """
        profiles = sorted(
            self.model_profiles.values(),
            key=lambda p: p.efficiency_score(),
            reverse=True,
        )
        return {
            "recommended": profiles[0].model_name if profiles else "?",
            "rankings": [
                {
                    "model": p.model_name,
                    "efficiency": round(p.efficiency_score(), 3),
                    "quality": round(p.quality_score, 1),
                    "latency_ms": round(p.avg_latency_ms),
                    "error_rate": round(p.error_rate, 3),
                }
                for p in profiles if p.total_requests >= 3
            ],
        }
