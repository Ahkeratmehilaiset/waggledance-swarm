# WaggleDance Swarm AI • v0.0.1 • Built: 2026-02-22 14:37 EET
# Jani Korpi (Ahkerat Mehiläiset)
"""
AdaptiveThrottle — Dynaaminen nopeudensäätö Ollamalle
=====================================================
Mittaa Ollaman vasteaikoja ja säätää automaattisesti:
  - heartbeat_interval (30s...300s)
  - max_concurrent (1...8)
  - idle_research_interval (joka 3....joka 30. heartbeat)
  - idle_research_batch (1...5 agenttia kerrallaan)

Toimii millä tahansa koneella: nopea GPU → tiheä rytmi,
hidas CPU → harva rytmi. Adaptoituu lennossa.
"""
import asyncio
import time
import logging
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger("openclaw.throttle")


@dataclass
class ThrottleState:
    """Nykyinen säätötila."""
    heartbeat_interval: float = 60.0
    max_concurrent: int = 2
    idle_every_n_heartbeat: int = 5
    idle_batch_size: int = 2
    # Mittarit
    avg_latency_ms: float = 0.0
    error_rate: float = 0.0
    machine_class: str = "unknown"  # fast / medium / slow / very_slow
    # Batch pipeline optimal sizes (Phase 2)
    optimal_embed_batch: int = 10
    optimal_translate_batch: int = 10


class AdaptiveThrottle:
    """
    Mittaa Ollaman vastausaikoja ja säätää kuormaa dynaamisesti.

    Käyttö:
        throttle = AdaptiveThrottle()
        await throttle.benchmark(llm_provider)   # Käynnistyksessä
        throttle.record_success(latency_ms)       # Onnistunut pyyntö
        throttle.record_error()                   # 503 tai timeout
        state = throttle.state                    # Nykyiset asetukset
    """

    def __init__(self):
        self.state = ThrottleState()
        self._latencies = deque(maxlen=100)  # Viimeiset 100 mittausta
        self._errors = deque(maxlen=50)      # Viimeiset 50 tapahtumaa (True=virhe)
        # SAFETY: semafori aina olemassa (max_concurrent=2 oletuksena)
        # benchmark() ylikirjoittaa kalibroinnin jälkeen
        self._semaphore = asyncio.Semaphore(self.state.max_concurrent)
        self._lock = asyncio.Lock()
        self._total_requests = 0
        self._total_errors = 0
        self._last_adjust = 0

    async def benchmark(self, llm_provider, test_prompt="Sano 'OK'."):
        """
        Mittaa Ollaman nopeus käynnistyksessä.
        Palauttaa koneen luokan: fast / medium / slow / very_slow
        """
        logger.info("⏱️  Benchmarkataan Ollama...")
        print("  ⏱️  Benchmarkataan Ollama...")

        latencies = []
        for i in range(3):
            try:
                start = time.monotonic()
                resp = await llm_provider.generate(
                    test_prompt,
                    system="Vastaa yhdellä sanalla.",
                    max_tokens=10
                )
                elapsed_ms = (time.monotonic() - start) * 1000
                latencies.append(elapsed_ms)
                print(f"     Testi {i+1}/3: {elapsed_ms:.0f} ms")
            except Exception as e:
                print(f"     Testi {i+1}/3: VIRHE ({e})")
                latencies.append(30000)  # 30s = erittäin hidas

        avg = sum(latencies) / len(latencies) if latencies else 10000

        # Luokittele kone
        if avg < 500:
            machine_class = "fast"
        elif avg < 2000:
            machine_class = "medium"
        elif avg < 8000:
            machine_class = "slow"
        else:
            machine_class = "very_slow"

        # Aseta alkuarvot koneen mukaan
        profiles = {
            "fast": {
                "heartbeat_interval": 30,
                "max_concurrent": 6,
                "idle_every_n_heartbeat": 3,
                "idle_batch_size": 5,
            },
            "medium": {
                "heartbeat_interval": 60,
                "max_concurrent": 3,
                "idle_every_n_heartbeat": 5,
                "idle_batch_size": 3,
            },
            "slow": {
                "heartbeat_interval": 120,
                "max_concurrent": 2,
                "idle_every_n_heartbeat": 10,
                "idle_batch_size": 2,
            },
            "very_slow": {
                "heartbeat_interval": 300,
                "max_concurrent": 1,
                "idle_every_n_heartbeat": 20,
                "idle_batch_size": 1,
            },
        }

        profile = profiles[machine_class]
        self.state = ThrottleState(
            heartbeat_interval=profile["heartbeat_interval"],
            max_concurrent=profile["max_concurrent"],
            idle_every_n_heartbeat=profile["idle_every_n_heartbeat"],
            idle_batch_size=profile["idle_batch_size"],
            avg_latency_ms=avg,
            error_rate=0.0,
            machine_class=machine_class,
        )
        self._semaphore = asyncio.Semaphore(self.state.max_concurrent)

        print(f"""
  ╔═══════════════════════════════════════════╗
  ║  Koneluokka: {machine_class.upper():>10}                   ║
  ║  Keskiviive: {avg:>8.0f} ms                   ║
  ║  Heartbeat:  {self.state.heartbeat_interval:>8.0f} s                    ║
  ║  Max samaan aikaan: {self.state.max_concurrent:>3}                     ║
  ║  Idle tutkimus: joka {self.state.idle_every_n_heartbeat}. HB, {self.state.idle_batch_size} agenttia  ║
  ╚═══════════════════════════════════════════╝""")

        logger.info(
            f"Benchmark: {machine_class} ({avg:.0f}ms) → "
            f"HB={self.state.heartbeat_interval}s, "
            f"concurrent={self.state.max_concurrent}, "
            f"idle_every={self.state.idle_every_n_heartbeat}"
        )
        return machine_class

    async def benchmark_batch(self, consciousness=None, translation_proxy=None):
        """Benchmark batch sizes for embedding and translation.

        Tests different batch sizes and finds optimal per-item throughput.
        Stores results in self.state.optimal_embed_batch / optimal_translate_batch.
        """
        print("  ⏱️  Batch benchmark alkaa...")
        logger.info("Batch benchmark starting...")

        # ── Embed batch benchmark ──
        if consciousness and consciousness.embed.available:
            test_texts = [f"test embedding text number {i}" for i in range(50)]
            best_per_item = float('inf')
            best_batch = 10  # default

            for batch_size in [1, 5, 10, 20, 50]:
                chunk = test_texts[:batch_size]
                try:
                    t0 = time.monotonic()
                    results = consciousness.embed.embed_batch(chunk, mode="document")
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    per_item = elapsed_ms / batch_size
                    success = sum(1 for r in results if r is not None)
                    print(f"     Embed batch={batch_size}: {elapsed_ms:.0f}ms "
                          f"({per_item:.1f}ms/item, {success}/{batch_size} ok)")
                    if per_item < best_per_item and success == batch_size:
                        best_per_item = per_item
                        best_batch = batch_size
                except Exception as e:
                    print(f"     Embed batch={batch_size}: ERROR ({e})")

            self.state.optimal_embed_batch = best_batch
            print(f"     → Optimal embed batch: {best_batch}")

        # ── Translate batch benchmark ──
        if translation_proxy and hasattr(translation_proxy, 'opus') and translation_proxy.opus.available:
            test_texts_fi = [
                "Mehiläishoito on tärkeää",
                "Varroa-punkkien torjunta",
                "Hunajan laatu on hyvä",
                "Kuningatar munii munia",
                "Talviruokinta on välttämätöntä",
            ]
            best_per_item = float('inf')
            best_batch = 5  # default

            for batch_size in [1, 5]:
                chunk = test_texts_fi[:batch_size]
                try:
                    t0 = time.monotonic()
                    results = translation_proxy.opus.batch_fi_to_en(chunk)
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    per_item = elapsed_ms / batch_size
                    success = sum(1 for r in results if r is not None)
                    print(f"     Translate batch={batch_size}: {elapsed_ms:.0f}ms "
                          f"({per_item:.1f}ms/item, {success}/{batch_size} ok)")
                    if per_item < best_per_item and success == batch_size:
                        best_per_item = per_item
                        best_batch = batch_size
                except Exception as e:
                    print(f"     Translate batch={batch_size}: ERROR ({e})")

            self.state.optimal_translate_batch = best_batch
            print(f"     → Optimal translate batch: {best_batch}")

        print(f"""
  ╔═══════════════════════════════════════════╗
  ║  Batch Benchmark tulokset:                ║
  ║  Embed batch:     {self.state.optimal_embed_batch:>3}                     ║
  ║  Translate batch: {self.state.optimal_translate_batch:>3}                     ║
  ╚═══════════════════════════════════════════╝""")

        logger.info(
            f"Batch benchmark: embed={self.state.optimal_embed_batch}, "
            f"translate={self.state.optimal_translate_batch}"
        )

    def record_success(self, latency_ms: float):
        """Kirjaa onnistunut pyyntö."""
        self._latencies.append(latency_ms)
        self._errors.append(False)
        self._total_requests += 1
        self._maybe_adjust()

    def record_error(self):
        """Kirjaa virhe (503, timeout). Reagoi NOPEAMMIN kuin onnistumiseen."""
        self._errors.append(True)
        self._total_requests += 1
        self._total_errors += 1
        # Timeout → reagoi välittömästi (ei odota 30s _maybe_adjust sykliä)
        recent = list(self._errors)[-5:]
        error_count = sum(1 for e in recent if e)
        if error_count >= 2:  # 2+ virhettä viimeisestä 5:stä → hidasta heti
            self._scale_down(f"Nopeasti peräkkäiset virheet ({error_count}/5)")
        else:
            self._maybe_adjust()

    def _maybe_adjust(self):
        """Säädä asetuksia viimeisten mittausten perusteella."""
        now = time.monotonic()
        if now - self._last_adjust < 30:  # Säädä max kerran per 30s
            return
        self._last_adjust = now

        # Laske mittarit
        if self._latencies:
            self.state.avg_latency_ms = sum(self._latencies) / len(self._latencies)

        recent_errors = list(self._errors)[-20:]  # Viimeiset 20
        if recent_errors:
            self.state.error_rate = sum(1 for e in recent_errors if e) / len(recent_errors)

        # ═══ SÄÄTÖLOGIIKKA ═══

        # Virheaste korkea → hidasta
        if self.state.error_rate > 0.3:
            self._scale_down("Korkea virheaste ({:.0%})".format(self.state.error_rate))
            return

        # Virheaste nolla + hyvä latenssi → nopeuta
        if self.state.error_rate == 0 and len(self._latencies) >= 10:
            avg = self.state.avg_latency_ms
            if avg < 1000 and self.state.heartbeat_interval > 30:
                self._scale_up(f"Nopea vastaus ({avg:.0f}ms)")
            elif avg < 3000 and self.state.heartbeat_interval > 60:
                self._scale_up(f"Kohtuullinen vastaus ({avg:.0f}ms)")

    def _scale_down(self, reason: str):
        """Hidasta kaikkea."""
        self._last_adjust = time.monotonic()  # Cooldown
        old_hb = self.state.heartbeat_interval
        self.state.heartbeat_interval = min(self.state.heartbeat_interval * 1.5, 300)
        self.state.max_concurrent = max(self.state.max_concurrent - 1, 1)
        self.state.idle_every_n_heartbeat = min(self.state.idle_every_n_heartbeat + 3, 30)
        self.state.idle_batch_size = max(self.state.idle_batch_size - 1, 1)
        self._semaphore = asyncio.Semaphore(self.state.max_concurrent)
        msg = (f"⬇️  Throttle DOWN: {reason} | "
               f"HB: {old_hb:.0f}→{self.state.heartbeat_interval:.0f}s, "
               f"concurrent: {self.state.max_concurrent}")
        logger.warning(msg)
        print(f"  {msg}")  # Näkyy konsolissa

    def _scale_up(self, reason: str):
        """Nopeuta varovasti."""
        self._last_adjust = time.monotonic()
        old_hb = self.state.heartbeat_interval
        self.state.heartbeat_interval = max(self.state.heartbeat_interval * 0.85, 20)
        self.state.max_concurrent = min(self.state.max_concurrent + 1, 8)
        self.state.idle_every_n_heartbeat = max(self.state.idle_every_n_heartbeat - 1, 2)
        self.state.idle_batch_size = min(self.state.idle_batch_size + 1, 5)
        self._semaphore = asyncio.Semaphore(self.state.max_concurrent)
        msg = (f"⬆️  Throttle UP: {reason} | "
               f"HB: {old_hb:.0f}→{self.state.heartbeat_interval:.0f}s, "
               f"concurrent: {self.state.max_concurrent}")
        logger.info(msg)
        print(f"  {msg}")

    async def acquire(self):
        """Odota vuoroa ennen LLM-pyyntöä. Käytä: async with throttle:"""
        if self._semaphore:
            await self._semaphore.acquire()

    def release(self):
        """Vapauta vuoro."""
        if self._semaphore:
            self._semaphore.release()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *args):
        self.release()

    def get_status(self) -> dict:
        """Dashboard-info."""
        return {
            "machine_class": self.state.machine_class,
            "avg_latency_ms": round(self.state.avg_latency_ms, 0),
            "error_rate": round(self.state.error_rate, 3),
            "heartbeat_interval_s": round(self.state.heartbeat_interval, 0),
            "max_concurrent": self.state.max_concurrent,
            "idle_every_n_heartbeat": self.state.idle_every_n_heartbeat,
            "idle_batch_size": self.state.idle_batch_size,
            "total_requests": self._total_requests,
            "total_errors": self._total_errors,
            "optimal_embed_batch": self.state.optimal_embed_batch,
            "optimal_translate_batch": self.state.optimal_translate_batch,
        }
