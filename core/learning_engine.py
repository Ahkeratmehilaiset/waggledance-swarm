# WaggleDance Swarm AI • v0.0.3
# Jani Korpi (Ahkerat Mehiläiset)
"""
LearningEngine — Suljettu oppimissilmukka
==========================================
Parven "aivot" jotka arvioivat, oppivat ja kehittyvät.

ARKKITEHTUURI:
  ┌──────────────────────────────────────────────────┐
  │              LearningEngine                       │
  │                                                   │
  │  1. QualityGate                                   │
  │     └→ 7b arvioi vastauksen laadun (1-10)         │
  │     └→ Pisteet → SwarmScheduler pheromone         │
  │     └→ Pisteet → TokenEconomy bonus/rangaistus    │
  │     └→ Hyvät (7+) → finetune_curated.jsonl       │
  │                                                   │
  │  2. PromptEvolver                                 │
  │     └→ Vertaa parhaat vs huonoimmat agentit       │
  │     └→ 7b ehdottaa parannuksia system promptiin   │
  │     └→ A/B testaa: vanha vs uusi prompt           │
  │     └→ Parempi jää voimaan                        │
  │                                                   │
  │  3. InsightDistiller                              │
  │     └→ Kerää parhaat oivallukset kaikista         │
  │     └→ Tiivistää → jaettu tietopankki             │
  │     └→ Relevanteille agenteille kontekstiin       │
  │                                                   │
  │  4. PerformanceTracker                            │
  │     └→ Seuraa kuka paranee, kuka huononee         │
  │     └→ Laukaisee PromptEvolver tarvittaessa       │
  └──────────────────────────────────────────────────┘

MIKSI 7b (CPU/heartbeat-malli):
  - Arviointi on yksinkertainen tehtävä → 7b riittää
  - Ei kilpaile 32b:n kanssa GPU:sta
  - Pyörii taustalla, ei tarvitse olla nopea
  - OpsAgent voi pysäyttää jos kuorma kasvaa

ETIIKKA:
  - Ei muokkaa system prompteja ilman lokitusta
  - Kaikki muutokset tallentuvat audit-lokiin
  - Alkuperäiset promptit aina palautettavissa
"""

import asyncio
import json
import time
import logging
import statistics
from collections import deque, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, List, Any, TYPE_CHECKING
from datetime import datetime

if TYPE_CHECKING:
    from core.swarm_scheduler import SwarmScheduler
    from core.token_economy import TokenEconomy

logger = logging.getLogger("waggle.learning")


# ═══════════════════════════════════════════════════════════════
# Tietorakenteet
# ═══════════════════════════════════════════════════════════════

@dataclass
class QualityScore:
    """Yhden vastauksen laadun arviointi."""
    agent_id: str
    agent_type: str
    score: float           # 1-10
    reasoning: str         # 7b:n perustelu
    prompt_preview: str
    response_preview: str
    timestamp: float = field(default_factory=time.monotonic)

    @property
    def is_good(self) -> bool:
        return self.score >= 7.0

    @property
    def is_bad(self) -> bool:
        return self.score < 4.0


@dataclass
class PromptExperiment:
    """A/B-testi: vanha vs uusi system prompt."""
    agent_id: str
    original_prompt: str
    evolved_prompt: str
    reason: str
    # Tulokset
    original_scores: list = field(default_factory=list)
    evolved_scores: list = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    decided: bool = False
    winner: str = ""  # "original" | "evolved"

    @property
    def has_enough_data(self) -> bool:
        return len(self.original_scores) >= 3 and len(self.evolved_scores) >= 3


@dataclass
class AgentPerformance:
    """Agentin suorituskykyprofiili."""
    agent_id: str
    agent_type: str
    scores: deque = field(default_factory=lambda: deque(maxlen=50))
    # Trendit
    avg_7day: float = 5.0
    avg_recent: float = 5.0
    trend: float = 0.0          # +/- muutos
    total_evaluated: int = 0
    good_count: int = 0
    bad_count: int = 0
    # Prompt-evoluutio
    prompt_version: int = 0
    last_evolution: float = 0.0

    @property
    def good_rate(self) -> float:
        if self.total_evaluated == 0:
            return 0.0
        return self.good_count / self.total_evaluated

    @property
    def needs_help(self) -> bool:
        """Agentti tarvitsee prompt-evoluutiota."""
        return (self.total_evaluated >= 5
                and self.avg_recent < 5.0
                and self.trend <= 0)


# ═══════════════════════════════════════════════════════════════
# Vakiot
# ═══════════════════════════════════════════════════════════════

# Laadun arviointiin käytetty prompt
QUALITY_EVAL_PROMPT = """Arvioi seuraava AI-agentin vastaus asteikolla 1-10.

KRITEERIT:
- Relevanssi: Vastaako kysymykseen? (0-3 pistettä)
- Tarkkuus: Ovatko faktat oikein? (0-3 pistettä)
- Hyödyllisyys: Onko käytännön apua? (0-2 pistettä)
- Selkeys: Onko helppolukuinen? (0-2 pistettä)

ESIMERKKEJÄ:
- 3/10: Vastaus on epäolennainen, ei liity kysymykseen, tai sisältää virheitä
- 5/10: Vastaus on osittain oikein mutta puutteellinen tai epätarkka
- 7/10: Hyvä vastaus, oikeat faktat, mutta voisi olla kattavampi
- 8.5/10: Erinomainen, tarkka, käytännöllinen ja selkeä vastaus

Käytä KOKO asteikkoa 1-10. Älä anna aina 8 tai 10 — arvioi rehellisesti.

AGENTIN TYYPPI: {agent_type}
KYSYMYS/TEHTÄVÄ: {prompt}
VASTAUS: {response}

Vastaa TASAN tässä muodossa:
PISTEET: X/10
PERUSTELU: (1 lause miksi juuri tämä pistemäärä)"""

# Prompt-evoluution ohje
PROMPT_EVOLVE_PROMPT = """Olet AI-järjestelmän optimoija. Agentin "{agent_type}" system prompt
tuottaa heikkoja vastauksia (keskiarvo {avg_score:.1f}/10).

NYKYINEN SYSTEM PROMPT:
{current_prompt}

ESIMERKKEJÄ HUONOISTA VASTAUKSISTA:
{bad_examples}

ESIMERKKEJÄ HYVISTÄ VASTAUKSISTA (muilta agenteilta):
{good_examples}

Kirjoita PARANNETTU system prompt. Säilytä:
- Agentin rooli ja erikoisala
- Suomen kieli
- Sama pituus (max 500 merkkiä)

Korjaa:
- Epäselvät ohjeet
- Puuttuvat rajoitukset
- Liian yleiset neuvot

Vastaa VAIN uudella system promptilla, ei muuta."""

# Insight-tiivistyksen ohje
DISTILL_PROMPT = """Tiivistä nämä {n} oivallusta YHDEKSI tiiviiksi faktaksi (max 2 lausetta suomeksi).
Priorisoi: käytännön hyöty > teoria.

OIVALLUKSET:
{insights}

TIIVISTELMÄ:"""


# ═══════════════════════════════════════════════════════════════
# LearningEngine
# ═══════════════════════════════════════════════════════════════

class LearningEngine:
    """
    Suljettu oppimissilmukka.

    Käyttää 7b (CPU/heartbeat) -mallia arviointiin.
    EI kilpaile 32b:n kanssa GPU:sta.
    OpsAgent voi pysäyttää jos kuorma liian kova.
    """

    def __init__(self, llm_evaluator, memory, scheduler=None,
                 token_economy=None, config: dict = None):
        """
        Args:
            llm_evaluator: 7b-malli (heartbeat LLM) — arviointiin
            memory: SharedMemory
            scheduler: SwarmScheduler — pheromone-päivitykset
            token_economy: TokenEconomy — bonus/rangaistus
            config: settings.yaml learning-osio
        """
        self.llm = llm_evaluator       # 7b CPU
        self.memory = memory
        self.scheduler = scheduler
        self.token_economy = token_economy
        self.config = config or {}

        # ── Konfiguraatio ─────────────────────────────
        learn_cfg = self.config.get("learning", {})
        self.eval_queue_size = learn_cfg.get("eval_queue_size", 20)
        self.evolve_interval = learn_cfg.get("evolve_interval_min", 30)  # min
        self.distill_interval = learn_cfg.get("distill_interval_min", 15)
        self.min_score_for_finetune = learn_cfg.get("min_finetune_score", 7.0)
        self.auto_evolve_enabled = learn_cfg.get("auto_evolve", False)

        # ── Tila ──────────────────────────────────────
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._eval_queue: deque = deque(maxlen=self.eval_queue_size)
        self._performances: Dict[str, AgentPerformance] = {}
        self._experiments: Dict[str, PromptExperiment] = {}
        self._quality_history: deque = deque(maxlen=200)
        self._cycle_count = 0
        self._seen_hashes: set = set()  # Dedup: content hashes of curated responses

        # ── Tiedostopolut ─────────────────────────────
        self._curated_path = Path("data/finetune_curated.jsonl")
        self._rejected_path = Path("data/finetune_rejected.jsonl")
        self._audit_path = Path("data/learning_audit.jsonl")
        self._curated_path.parent.mkdir(exist_ok=True)

        # ── Tilastot ──────────────────────────────────
        self.stats = {
            "total_evaluated": 0,
            "total_curated": 0,
            "total_rejected": 0,
            "total_evolutions": 0,
            "total_distillations": 0,
            "avg_quality": 0.0,
        }

    # ─────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────

    async def start(self):
        self.running = True
        self._task = asyncio.create_task(self._learning_loop())
        logger.info("LearningEngine käynnistetty")

    async def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ─────────────────────────────────────────────────────────
    # Ulkoinen API: hivemind kutsuu jokaisen vastauksen jälkeen
    # ─────────────────────────────────────────────────────────

    def submit_for_evaluation(self, agent_id: str, agent_type: str,
                               system_prompt: str, prompt: str,
                               response: str):
        """
        Lisää vastaus arviointijonoon.
        Hivemind kutsuu tätä joka onnistuneen LLM-vastauksen jälkeen.
        Evaluointi tapahtuu taustalla, ei blokkaa.
        """
        if not response or len(response.strip()) < 10:
            return

        self._eval_queue.append({
            "agent_id": agent_id,
            "agent_type": agent_type,
            "system_prompt": system_prompt[:500],
            "prompt": prompt[:500],
            "response": response[:500],
            "submitted_at": time.monotonic(),
        })

    # ─────────────────────────────────────────────────────────
    # Pääsilmukka
    # ─────────────────────────────────────────────────────────

    async def _learning_loop(self):
        """
        Oppimissykli:
          - Joka 30s: arvioi jonossa olevat vastaukset (7b)
          - Joka 15min: tiivistä oivallukset
          - Joka 30min: ehdota prompt-parannuksia (jos auto_evolve)
        """
        while self.running:
            try:
                await asyncio.sleep(30)  # 30s sykli
                if not self.running:
                    break

                self._cycle_count += 1

                # 1. Arvioi jonossa olevat
                await self._evaluate_queue()

                # 2. Tarkista kokeet
                await self._check_experiments()

                # 3. Tiivistä oivallukset (joka 30. sykli = ~15min)
                if self._cycle_count % 30 == 0:
                    await self._distill_insights()

                # 4. Prompt-evoluutio (joka 60. sykli = ~30min)
                if (self.auto_evolve_enabled
                        and self._cycle_count % 60 == 0
                        and self._cycle_count > 0):
                    await self._evolve_weak_agents()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"LearningEngine syklivirhe: {e}")

    # ═══════════════════════════════════════════════════════════
    # 1. QualityGate — Laadun arviointi
    # ═══════════════════════════════════════════════════════════

    async def _evaluate_queue(self):
        """Arvioi max 8 vastausta per sykli (optimoitu GPU-käytölle)."""
        evaluated = 0
        max_per_cycle = 8

        while self._eval_queue and evaluated < max_per_cycle:
            item = self._eval_queue.popleft()
            try:
                score = await self._evaluate_one(item)
                if score:
                    # Apply composite scoring adjustments
                    score.score = self._composite_score(score, item)
                    await self._process_score(score, item)
                    evaluated += 1
            except Exception as e:
                logger.warning(f"Evaluointi epäonnistui: {e}")

    async def _evaluate_one(self, item: dict) -> Optional[QualityScore]:
        """Arvioi yksi vastaus 7b:llä."""
        eval_prompt = QUALITY_EVAL_PROMPT.format(
            agent_type=item["agent_type"],
            prompt=item["prompt"][:300],
            response=item["response"][:300],
        )

        try:
            resp = await self.llm.generate(
                eval_prompt,
                system="Olet AI-vastausten laadun arvioija. Vastaa aina muodossa 'PISTEET: X/10'.",
                max_tokens=100,
                temperature=0.1
            )

            if not resp or resp.error or not resp.content:
                return None

            # Parsitaan pisteet
            score_val = self._parse_score(resp.content)
            if score_val is None:
                return None

            # Parsitaan perustelu
            reasoning = ""
            if "PERUSTELU:" in resp.content:
                reasoning = resp.content.split("PERUSTELU:")[-1].strip()[:200]

            return QualityScore(
                agent_id=item["agent_id"],
                agent_type=item["agent_type"],
                score=score_val,
                reasoning=reasoning,
                prompt_preview=item["prompt"][:100],
                response_preview=item["response"][:100],
            )

        except Exception as e:
            logger.warning(f"7b evaluointi virhe: {e}")
            return None

    def _parse_score(self, text: str) -> Optional[float]:
        """Parsii 'PISTEET: X/10' tekstistä."""
        import re
        # Etsii: 7/10, 8.5/10, PISTEET: 6/10
        matches = re.findall(r'(\d+(?:\.\d+)?)\s*/\s*10', text)
        if matches:
            val = float(matches[0])
            return max(1.0, min(10.0, val))

        # Fallback: pelkkä numero
        matches = re.findall(r'(\d+(?:\.\d+)?)', text)
        if matches:
            val = float(matches[0])
            if 1 <= val <= 10:
                return val
        return None

    def _composite_score(self, score: QualityScore, item: dict) -> float:
        """Composite scoring: LLM score (60%) + length + specificity + uniqueness.

        Produces a non-binary distribution instead of relying solely on LLM.
        Includes anti-ceiling bias: penalizes suspiciously perfect 10/10 scores
        from small models that tend to give binary 8 or 10.
        """
        llm_score = score.score
        response = item.get("response", "")

        # Anti-ceiling bias: LLM models like llama3.2:1b give 10/10 too freely
        # Discount perfect scores slightly to spread the distribution
        if llm_score >= 10.0:
            llm_score = 9.2  # Cap raw 10 to 9.2 — still high, but not ceiling

        # Length bonus: reward answers of reasonable length (50-300 chars)
        resp_len = len(response.strip())
        if resp_len < 20:
            length_bonus = -1.5
        elif resp_len < 50:
            length_bonus = -0.3
        elif resp_len <= 300:
            length_bonus = 0.5
        elif resp_len <= 600:
            length_bonus = 0.3
        else:
            length_bonus = 0.1  # Very long answers often ramble

        # Specificity: reward answers with numbers, Finnish terms, etc.
        specificity_bonus = 0.0
        import re
        if re.search(r'\d+', response):
            specificity_bonus += 0.3  # Contains numbers
        bee_terms = ["mehiläi", "pesä", "hunaj", "varroa", "kuningat",
                     "kehä", "poikk", "tarha", "siitepöly", "nektar",
                     "rakenn", "talveh", "pölyt"]
        matches = sum(1 for t in bee_terms if t in response.lower())
        specificity_bonus += min(matches * 0.2, 0.6)  # Cap at 0.6

        # Uniqueness: penalize very generic answers
        generic_markers = ["yleisesti", "yleensä", "tietysti", "luonnollisesti",
                          "generally", "typically", "of course", "obviously"]
        generic_count = sum(1 for g in generic_markers if g in response.lower())
        uniqueness_penalty = min(generic_count * -0.3, 0.0)

        # Weighted composite: LLM 50%, adjustments 50%
        composite = (llm_score * 0.50
                     + (llm_score + length_bonus + specificity_bonus
                        + uniqueness_penalty) * 0.50)

        return max(1.0, min(10.0, round(composite, 1)))

    async def _process_score(self, score: QualityScore, item: dict):
        """Käsittele arvioitu vastaus: päivitä kaikki järjestelmät."""

        self._quality_history.append(score)
        self.stats["total_evaluated"] += 1

        # ── A) Päivitä agentin suoritusprofiili ───────
        perf = self._performances.get(score.agent_id)
        if not perf:
            perf = AgentPerformance(
                agent_id=score.agent_id,
                agent_type=score.agent_type,
            )
            self._performances[score.agent_id] = perf

        perf.scores.append(score.score)
        perf.total_evaluated += 1
        if score.is_good:
            perf.good_count += 1
        if score.is_bad:
            perf.bad_count += 1

        # Päivitä keskiarvot
        all_scores = list(perf.scores)
        perf.avg_recent = statistics.mean(all_scores[-10:]) if all_scores else 5.0
        perf.avg_7day = statistics.mean(all_scores) if all_scores else 5.0
        if len(all_scores) >= 6:
            older = statistics.mean(all_scores[:-3])
            newer = statistics.mean(all_scores[-3:])
            perf.trend = newer - older
        else:
            perf.trend = 0.0

        # ── B) Päivitä SwarmScheduler pheromone ───────
        if self.scheduler:
            self.scheduler.record_task_result(
                agent_id=score.agent_id,
                success=score.is_good,
                latency_ms=0,  # Ei mitattavissa jälkikäteen
                had_corrections=score.is_bad,
            )

        # ── C) TokenEconomy bonus/rangaistus ──────────
        if self.token_economy:
            if score.score >= 9.0:
                await self.token_economy.reward(
                    score.agent_id, "excellent_quality",
                    custom_amount=15
                )
            elif score.score >= 7.0:
                await self.token_economy.reward(
                    score.agent_id, "good_quality",
                    custom_amount=5
                )
            elif score.score < 3.0:
                # Negatiivinen palaute: vähennä tokeneita
                await self.token_economy.spend(
                    score.agent_id, 3, "poor_quality_penalty"
                )

        # ── D) Finetune-datan kuratointi ──────────────
        # Strip system prompt boilerplate before saving to curated data
        _sys_content = item.get("system_prompt", "")
        _boilerplate_markers = ["OLETUKSET JA KONTEKSTI", "ASSUMPTIONS AND CONTEXT"]
        for _marker in _boilerplate_markers:
            if _marker in _sys_content:
                _sys_content = ""
                break

        finetune_entry = {
            "messages": [
                {"role": "user", "content": item["prompt"]},
                {"role": "assistant", "content": item["response"]},
            ],
            "agent_type": score.agent_type,
            "score": score.score,
            "quality_score": score.score,
            "reasoning": score.reasoning,
            "timestamp": datetime.now().isoformat(),
        }
        # Only include system message if it's not boilerplate
        if _sys_content.strip():
            finetune_entry["messages"].insert(
                0, {"role": "system", "content": _sys_content})

        # Auto-generate reasoning if empty — include content signals
        if not score.reasoning:
            _resp = item.get("response", "")
            _resp_len = len(_resp.strip())
            if score.score < 3.0:
                score.reasoning = f"very_low_quality (len={_resp_len})"
            elif score.score < 5.0:
                score.reasoning = f"below_average (score={score.score:.1f}, len={_resp_len})"
            elif score.score < self.min_score_for_finetune:
                score.reasoning = f"not_curated_quality (score={score.score:.1f})"
            elif _resp_len < 30:
                score.reasoning = f"short_response (len={_resp_len})"
            elif "ASSUMPTIONS AND CONTEXT" in _resp or "OLETUKSET JA KONTEKSTI" in _resp:
                score.reasoning = "boilerplate_content"
            else:
                score.reasoning = f"auto_accepted (score={score.score:.1f}, len={_resp_len})"
            finetune_entry["reasoning"] = score.reasoning

        # ── Pre-curation content filter: reject boilerplate/low-value ──
        _resp_raw = item.get("response", "")
        _boilerplate_reject = False
        _BOILERPLATE_PATTERNS = [
            "ASSUMPTIONS AND CONTEXT", "OLETUKSET JA KONTEKSTI",
            "DECISION METRICS AND THRESHOLDS", "PÄÄTÖSMITTARIT",
            "Tietopankki", "## ASSUMPTIONS",
        ]
        for _bp in _BOILERPLATE_PATTERNS:
            if _bp in _resp_raw:
                _boilerplate_reject = True
                break
        # Also reject very short or question-mark-heavy responses
        if not _boilerplate_reject:
            if len(_resp_raw.strip()) < 20:
                _boilerplate_reject = True
            elif _resp_raw.count("?") > 5:
                _boilerplate_reject = True
            elif _resp_raw.strip().startswith("Vastaus: **?**"):
                _boilerplate_reject = True

        if _boilerplate_reject and score.score >= self.min_score_for_finetune:
            finetune_entry["rejection_reason"] = "boilerplate_content"
            finetune_entry["reasoning"] = "boilerplate_content"
            self._append_jsonl(self._rejected_path, finetune_entry)
            self.stats["total_rejected"] += 1
            logger.debug(f"Boilerplate rejected: {_resp_raw[:60]}...")
        elif score.score >= self.min_score_for_finetune:
            # Dedup: hash the response content, reject exact/near duplicates
            import hashlib as _hl
            _resp_text = item.get("response", "").strip()[:200]
            _content_hash = _hl.md5(_resp_text.encode("utf-8", errors="replace")).hexdigest()[:12]
            if _content_hash in self._seen_hashes:
                finetune_entry["rejection_reason"] = "duplicate_content"
                finetune_entry["reasoning"] = "duplicate_content"
                self._append_jsonl(self._rejected_path, finetune_entry)
                self.stats["total_rejected"] += 1
                logger.debug(f"Dedup rejected: {_resp_text[:60]}...")
            else:
                self._seen_hashes.add(_content_hash)
                # Cap dedup set to prevent memory growth
                if len(self._seen_hashes) > 50000:
                    # Keep last ~40k by clearing and re-adding recent
                    self._seen_hashes.clear()
                self._append_jsonl(self._curated_path, finetune_entry)
                self.stats["total_curated"] += 1
        else:
            # Add rejection reason to rejected entries
            finetune_entry["rejection_reason"] = score.reasoning
            self._append_jsonl(self._rejected_path, finetune_entry)
            self.stats["total_rejected"] += 1

        # ── E) Tallenna muistiin ──────────────────────
        if self.memory and score.score >= 8.0:
            await self.memory.store_memory(
                content=(f"[Laatuarvio] {score.agent_type}: "
                         f"{score.score}/10 — {score.reasoning}"),
                agent_id="learning_engine",
                memory_type="evaluation",
                importance=0.5,
            )

        # Päivitä kokonaiskeskiarvo
        recent = [s.score for s in list(self._quality_history)[-50:]]
        self.stats["avg_quality"] = statistics.mean(recent) if recent else 0

    # ═══════════════════════════════════════════════════════════
    # 2. PromptEvolver — System promptien kehitys
    # ═══════════════════════════════════════════════════════════

    async def _evolve_weak_agents(self):
        """Etsi heikoimmat agentit ja ehdota parannuksia."""
        # Etsi agentit jotka tarvitsevat apua
        weak = [p for p in self._performances.values() if p.needs_help]
        if not weak:
            return

        # Etsi parhaiden esimerkkejä
        good_examples = self._get_good_examples(limit=3)

        for perf in weak[:2]:  # Max 2 evoluutiota kerrallaan
            # Älä kehitä samaa agenttia liian usein
            if time.monotonic() - perf.last_evolution < self.evolve_interval * 60:
                continue

            # Älä tee jos koe jo käynnissä
            if perf.agent_id in self._experiments:
                continue

            bad_examples = self._get_bad_examples(perf.agent_id, limit=3)
            if not bad_examples:
                continue

            # Hae nykyinen system prompt muistista
            current_prompt = await self._get_agent_prompt(perf.agent_id)
            if not current_prompt:
                continue

            # 7b ehdottaa parannusta
            evolved = await self._generate_evolved_prompt(
                perf, current_prompt, bad_examples, good_examples
            )
            if not evolved:
                continue

            # Aloita A/B-koe
            experiment = PromptExperiment(
                agent_id=perf.agent_id,
                original_prompt=current_prompt,
                evolved_prompt=evolved,
                reason=f"avg={perf.avg_recent:.1f}, trend={perf.trend:+.1f}",
            )
            self._experiments[perf.agent_id] = experiment
            perf.last_evolution = time.monotonic()

            self._audit_log("prompt_experiment_started", {
                "agent_id": perf.agent_id,
                "reason": experiment.reason,
                "evolved_prompt_preview": evolved[:200],
            })

            self.stats["total_evolutions"] += 1
            logger.info(
                f"[LEARN] Prompt-koe aloitettu: {perf.agent_id} "
                f"(avg={perf.avg_recent:.1f}, trend={perf.trend:+.1f})"
            )
            logger.info(f"[LEARN] Prompt-koe: {perf.agent_type} (laatu {perf.avg_recent:.1f}/10)")

    async def _generate_evolved_prompt(self, perf: AgentPerformance,
                                        current_prompt: str,
                                        bad_examples: str,
                                        good_examples: str) -> Optional[str]:
        """Generoi parannettu system prompt 7b:llä."""
        prompt = PROMPT_EVOLVE_PROMPT.format(
            agent_type=perf.agent_type,
            avg_score=perf.avg_recent,
            current_prompt=current_prompt[:500],
            bad_examples=bad_examples[:500],
            good_examples=good_examples[:500],
        )

        try:
            resp = await self.llm.generate(
                prompt,
                system="Olet AI-promptien optimoija. Vastaa VAIN uudella promptilla.",
                max_tokens=300,
                temperature=0.3,
            )
            if resp and not resp.error and resp.content:
                evolved = resp.content.strip()
                # Perusvalidointi
                if 50 < len(evolved) < 600 and evolved != current_prompt:
                    return evolved
        except Exception as e:
            logger.warning(f"Prompt-evoluutio epäonnistui: {e}")
        return None

    async def _check_experiments(self):
        """Tarkista käynnissä olevat A/B-kokeet."""
        for agent_id, exp in list(self._experiments.items()):
            if exp.decided:
                continue

            # Kerää pisteet tämän agentin viimeisistä arvioinneista
            recent_scores = [
                s for s in self._quality_history
                if s.agent_id == agent_id
                and s.timestamp > exp.started_at
            ]

            # Jako: parilliset → original, parittomat → evolved
            # (yksinkertaistettu — oikeasti pitäisi vuorotella promptia)
            for i, s in enumerate(recent_scores):
                if i % 2 == 0:
                    exp.original_scores.append(s.score)
                else:
                    exp.evolved_scores.append(s.score)

            # Tarpeeksi dataa päätökseen?
            if exp.has_enough_data:
                await self._decide_experiment(exp)

            # Timeout: 2h ilman päätöstä → peruuta
            if time.monotonic() - exp.started_at > 7200:
                exp.decided = True
                exp.winner = "original"  # Turvallinen oletus
                self._audit_log("prompt_experiment_timeout", {
                    "agent_id": agent_id,
                })

    async def _decide_experiment(self, exp: PromptExperiment):
        """Päätä A/B-koe: kumpi prompt voittaa?"""
        avg_orig = statistics.mean(exp.original_scores)
        avg_evolved = statistics.mean(exp.evolved_scores)

        # Evolved voittaa jos selkeästi parempi (>0.5 pistettä)
        if avg_evolved > avg_orig + 0.5:
            exp.winner = "evolved"
            exp.decided = True

            # TODO: Vaihda agentin system prompt
            # Tämä vaatii hivemind-integraation: agent.system_prompt = exp.evolved_prompt

            self._audit_log("prompt_experiment_won", {
                "agent_id": exp.agent_id,
                "avg_original": round(avg_orig, 1),
                "avg_evolved": round(avg_evolved, 1),
                "evolved_prompt": exp.evolved_prompt[:300],
            })
            logger.info(
                f"[LEARN] ✅ Evolved prompt VOITTI: {exp.agent_id} "
                f"({avg_orig:.1f} → {avg_evolved:.1f})"
            )
            logger.info(f"[LEARN] Prompt parantunut: {exp.agent_id} ({avg_orig:.1f} → {avg_evolved:.1f})")
        else:
            exp.winner = "original"
            exp.decided = True
            self._audit_log("prompt_experiment_lost", {
                "agent_id": exp.agent_id,
                "avg_original": round(avg_orig, 1),
                "avg_evolved": round(avg_evolved, 1),
            })
            logger.info(
                f"[LEARN] ❌ Evolved prompt HÄVISI: {exp.agent_id} "
                f"({avg_orig:.1f} vs {avg_evolved:.1f})"
            )

        # Siivoa
        if exp.agent_id in self._experiments:
            del self._experiments[exp.agent_id]

    # ═══════════════════════════════════════════════════════════
    # 3. InsightDistiller — Oivalluksien tiivistys
    # ═══════════════════════════════════════════════════════════

    async def _distill_insights(self):
        """
        Kerää parhaat oivallukset ja tiivistä ne.
        Tiivistetty tieto → SharedMemory "distilled" -tyyppinä.
        """
        if not self.memory:
            return

        try:
            # Hae viimeisimmät oivallukset
            all_memories = await self.memory.get_recent_memories(limit=30)
            insights = [
                m for m in all_memories
                if m.get("memory_type") == "insight"
                and m.get("content", "")
            ]

            if len(insights) < 5:
                return

            # Ryhmittele aiheen mukaan (yksinkertaistettu)
            insights_text = "\n".join([
                f"- [{m.get('agent_id', '?')}] {m['content'][:150]}"
                for m in insights[:15]
            ])

            # 7b tiivistää
            resp = await self.llm.generate(
                DISTILL_PROMPT.format(n=min(len(insights), 15),
                                      insights=insights_text[:1000]),
                system="Olet tiedon tiivistäjä. Vastaa suomeksi, max 2 lausetta.",
                max_tokens=150,
                temperature=0.3,
            )

            if resp and not resp.error and resp.content:
                distilled = resp.content.strip()
                if len(distilled) > 20:
                    await self.memory.store_memory(
                        content=f"[Tiivistetty] {distilled}",
                        agent_id="learning_engine",
                        memory_type="distilled",
                        importance=0.9,
                    )
                    self.stats["total_distillations"] += 1
                    logger.info(f"[LEARN] Tiivistetty: {distilled[:100]}")

        except Exception as e:
            logger.warning(f"Insight-tiivistys epäonnistui: {e}")

    # ═══════════════════════════════════════════════════════════
    # Apufunktiot
    # ═══════════════════════════════════════════════════════════

    def _get_good_examples(self, limit: int = 3) -> str:
        """Hae parhaiden arviointien vastausesimerkit."""
        good = sorted(
            [s for s in self._quality_history if s.score >= 8.0],
            key=lambda s: s.score, reverse=True
        )[:limit]
        if not good:
            return "(ei esimerkkejä vielä)"
        return "\n".join([
            f"[{s.agent_type}, {s.score}/10]: {s.response_preview}"
            for s in good
        ])

    def _get_bad_examples(self, agent_id: str, limit: int = 3) -> str:
        """Hae agentin huonoimmat vastaukset."""
        bad = sorted(
            [s for s in self._quality_history
             if s.agent_id == agent_id and s.score < 5.0],
            key=lambda s: s.score
        )[:limit]
        if not bad:
            return ""
        return "\n".join([
            f"[{s.score}/10] Q: {s.prompt_preview} → A: {s.response_preview}"
            for s in bad
        ])

    async def _get_agent_prompt(self, agent_id: str) -> Optional[str]:
        """Hae agentin nykyinen system prompt."""
        # Yritetään hakea muistista
        if self.memory:
            try:
                cursor = await self.memory._db.execute(
                    "SELECT content FROM memories "
                    "WHERE agent_id = ? AND memory_type = 'system_prompt' "
                    "ORDER BY created_at DESC LIMIT 1",
                    (agent_id,)
                )
                row = await cursor.fetchone()
                if row:
                    return row[0]
            except Exception:
                pass
        return None

    def _append_jsonl(self, path: Path, entry: dict):
        """Kirjoita yksi rivi JSONL-tiedostoon."""
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _audit_log(self, event_type: str, data: dict):
        """Kirjoita audit-lokiin (kaikki muutokset jäljitettävissä)."""
        entry = {
            "type": event_type,
            "timestamp": datetime.now().isoformat(),
            "data": data,
        }
        self._append_jsonl(self._audit_path, entry)

    # ─────────────────────────────────────────────────────────
    # Julkinen API
    # ─────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Dashboard-tiedot."""
        perf_summaries = {}
        for aid, p in self._performances.items():
            perf_summaries[aid] = {
                "type": p.agent_type,
                "avg_recent": round(p.avg_recent, 1),
                "trend": round(p.trend, 2),
                "good_rate": round(p.good_rate, 2),
                "total_evaluated": p.total_evaluated,
                "needs_help": p.needs_help,
                "prompt_version": p.prompt_version,
            }

        experiments = {}
        for aid, exp in self._experiments.items():
            experiments[aid] = {
                "reason": exp.reason,
                "original_scores": len(exp.original_scores),
                "evolved_scores": len(exp.evolved_scores),
                "decided": exp.decided,
                "winner": exp.winner,
            }

        return {
            "running": self.running,
            "cycle_count": self._cycle_count,
            "queue_size": len(self._eval_queue),
            "auto_evolve": self.auto_evolve_enabled,
            "stats": self.stats,
            "agent_performance": perf_summaries,
            "experiments": experiments,
        }

    def get_leaderboard(self) -> list:
        """Agentit laadun mukaan."""
        return sorted(
            [
                {
                    "agent_id": p.agent_id,
                    "type": p.agent_type,
                    "avg_quality": round(p.avg_recent, 1),
                    "trend": round(p.trend, 2),
                    "good_rate": round(p.good_rate, 2),
                    "total": p.total_evaluated,
                }
                for p in self._performances.values()
                if p.total_evaluated >= 3
            ],
            key=lambda x: x["avg_quality"],
            reverse=True,
        )
