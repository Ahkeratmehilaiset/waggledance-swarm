"""
WaggleDance Swarm AI — Swarm Scheduler v0.0.2
================================================
Jani Korpi (Ahkerat Mehiläiset)
Claude 4.6 • v0.0.2 • Built: 2026-02-22 18:00 EET

Ratkaisee 50 agentin "swarm" -ongelmat:
  1. Kandidaattisuodatus (Top-K) — ei kaikki 50 biddaa
  2. Cold start -kalibrointi — jokainen saa alkutehtäviä
  3. Pheromone-alustus — ei nollasta
  4. Exploration-kiintiö — harvinaiset agentit pääsevät hommiin
  5. Kaksivaiheinen bidding (halpa + kallis)
  6. Kuormituksen hallinta (load_penalty, cooldown)
  7. Roolitus (Scout / Worker / Judge)
  8. Minimibidi-säännöt (min_tasks_per_day)

v0.0.2 MUUTOKSET:
  - agent_count property (hivemind tarkistaa onko agentteja)
  - register_agent() hyväksyy tags-parametrin
  - Tags tallennetaan AgentScoreen → käytetään tag-matchissä
  - Bulk-register via register_from_yaml_bridge()
"""

import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional


# ── Roolimäärittelyt ─────────────────────────────────────────

AGENT_ROLES = {
    "scout": {
        "description": "Nopeat, halvat: hakuja, tiivistyksiä, ehdotuksia",
        "max_tokens": 150,
        "priority_boost": 0.0,
    },
    "worker": {
        "description": "Syvemmät: varsinainen ratkaisu",
        "max_tokens": 500,
        "priority_boost": 0.1,
    },
    "judge": {
        "description": "Arvioijat: tarkistaa, pisteyttää, etsii virheitä",
        "max_tokens": 200,
        "priority_boost": 0.05,
    },
}

# Oletusmappaus: agenttityyppi → rooli
DEFAULT_ROLE_MAP = {
    # Scouts — nopeat tiedonhakijat
    "meteorologi": "scout", "lentosaa": "scout", "ilmanlaatu": "scout",
    "mikroilmasto": "scout", "fenologi": "scout", "rantavahti": "scout",
    "pihavahti": "scout", "logistikko": "scout", "valo_varjo": "scout",
    "myrskyvaroittaja": "scout", "jaaasiantuntija": "scout",
    "inventaariopaallikko": "scout", "siivousvastaava": "scout",
    "luontokuvaaja": "scout",
    # Workers — syvätyöntekijät
    "tarhaaja": "worker", "hortonomi": "worker", "metsanhoitaja": "worker",
    "sahkoasentaja": "worker", "lvi_asiantuntija": "worker",
    "timpuri": "worker", "nuohooja": "worker", "erakokki": "worker",
    "leipuri": "worker", "saunamajuri": "worker",
    "pesalampo": "worker", "nektari_informaatikko": "worker",
    "tautivahti": "worker", "parveiluvahti": "worker",
    "kalastusopas": "worker", "limnologi": "worker",
    "kybervahti": "worker", "lukkoseppa": "worker",
    "hacker": "worker", "oracle": "worker",
    # Judges — laadunvalvonta
    "core_dispatcher": "judge", "pesaturvallisuus": "judge",
    "paloesimies": "judge", "privaattisuus": "judge",
    "riistanvartija": "judge", "ornitologi": "judge",
    "entomologi": "judge", "kierratys_jate": "judge",
    "matemaatikko_fyysikko": "judge", "tahtitieteilija": "judge",
    "ravintoterapeutti": "judge", "kalantunnistaja": "judge",
    "routa_maapera": "judge", "valaistusmestari": "judge",
    "laitehuoltaja": "judge", "pienelain_tuholais": "judge",
    "viihdepaallikko": "judge", "elokuva_asiantuntija": "judge",
}


@dataclass
class AgentScore:
    """Agentin pheromone-pisteet."""
    agent_id: str
    agent_type: str
    role: str = "worker"
    tags: list = field(default_factory=list)  # v0.0.2: routing keywords
    skills: list = field(default_factory=list)
    # Pheromone
    success_score: float = 0.5      # 0..1 onnistumisprosentti
    speed_score: float = 0.5        # 0..1 nopeus
    reliability_score: float = 0.5  # 0..1 luotettavuus (ei korjauksia)
    # Kuormitus
    active_tasks: int = 0
    tasks_last_10min: int = 0
    consecutive_wins: int = 0
    total_tasks_today: int = 0
    total_bids_today: int = 0
    last_task_time: float = 0.0
    # Cold start
    calibrated: bool = False
    calibration_score: float = 0.0


@dataclass
class TaskBid:
    """Yksittäinen bidi tehtävään."""
    agent_id: str
    agent_type: str
    role: str
    # Vaihe A (halpa)
    tag_match: bool = False
    tag_score: float = 0.0
    confidence_prior: float = 0.0
    estimated_cost: float = 1.0
    # Vaihe B (kallis, vain shortlistatut)
    llm_confidence: float = 0.0
    llm_reasoning: str = ""
    # Lopullinen
    final_score: float = 0.0


class SwarmScheduler:
    """
    50 agentin taskien jakaja.
    Toteuttaa kaikki 8 parannusta yhtenäisenä järjestelmänä.
    """

    def __init__(self, config: dict = None):
        config = config or {}
        self._scores: dict[str, AgentScore] = {}
        self._task_history: list[dict] = []

        # Asetukset
        self.top_k = config.get("top_k", 8)
        self.exploration_rate = config.get("exploration_rate", 0.20)
        self.cooldown_max = config.get("cooldown_max", 5)
        self.load_penalty_factor = config.get("load_penalty", 0.15)
        self.min_tasks_per_day = config.get("min_tasks_per_day", 2)
        self.min_bids_per_day = config.get("min_bids_per_day", 3)
        self.newcomer_boost = config.get("newcomer_boost", 0.2)
        self.low_usage_bonus = config.get("low_usage_bonus", 0.15)
        self._cold_start_done = False

    # ── Properties (v0.0.2) ──────────────────────────────────

    @property
    def agent_count(self) -> int:
        """Kuinka monta agenttia on rekisteröity. Hivemind käyttää tätä."""
        return len(self._scores)

    # ── Agentin rekisteröinti ────────────────────────────────

    def register_agent(self, agent_id: str, agent_type: str,
                       skills: list[str] = None, tags: list[str] = None):
        """
        Rekisteröi agentti scheduleriin.

        Args:
            agent_id: Agentin uniikki ID
            agent_type: Agenttityyppi (esim. "tarhaaja")
            skills: Agentin kyvyt (YAML DECISION_METRICS keys)
            tags: Routing-avainsanat (YAML:sta tai ROUTING_KEYWORDS:sta)
        """
        if agent_id in self._scores:
            # Päivitä olemassaoleva (idempotent)
            existing = self._scores[agent_id]
            if tags:
                existing.tags = tags
            if skills:
                existing.skills = skills
            return

        role = DEFAULT_ROLE_MAP.get(agent_type, "worker")
        score = AgentScore(
            agent_id=agent_id,
            agent_type=agent_type,
            role=role,
            tags=tags or [],
            skills=skills or [],
        )
        # Prior score tagien perusteella
        if skills:
            score.confidence_prior = min(len(skills) * 0.1, 0.8)
        self._scores[agent_id] = score

    def register_from_yaml_bridge(self, yaml_bridge, spawner=None):
        """
        Bulk-register kaikki agentit YAMLBridgesta.
        Ei vaadi agents/-kansion muokkausta.

        Args:
            yaml_bridge: YAMLBridge-instanssi
            spawner: AgentSpawner (valinnainen, aktiivisten agenttien ID:t)
        """
        routing = yaml_bridge.get_routing_rules()
        templates = yaml_bridge.get_spawner_templates()
        count = 0

        for agent_type, tmpl in templates.items():
            # Käytä spawnerin agentti-ID:tä jos saatavilla
            agent_id = agent_type  # fallback
            if spawner:
                agents = spawner.get_agents_by_type(agent_type)
                if agents:
                    agent_id = agents[0].id

            tags = routing.get(agent_type, [])
            skills = tmpl.get("skills", [])

            self.register_agent(
                agent_id=agent_id,
                agent_type=agent_type,
                skills=skills,
                tags=tags,
            )
            count += 1

        return count

    def get_role(self, agent_id: str) -> str:
        """Palauttaa agentin roolin."""
        s = self._scores.get(agent_id)
        return s.role if s else "worker"

    # ── 1. Kandidaattisuodatus (Top-K) ───────────────────────

    def select_candidates(self, task_type: str, task_tags: list[str],
                          routing_rules: dict, top_k: int = None) -> list[str]:
        """
        Valitsee Top-K kandidaatit tehtävälle.
        Vaihe A: halpa, ilman LLM:ää.
        """
        k = top_k or self.top_k
        bids: list[TaskBid] = []

        for agent_id, score in self._scores.items():
            bid = self._phase_a_bid(agent_id, score, task_type, task_tags,
                                     routing_rules)
            bids.append(bid)
            score.total_bids_today += 1

        # Järjestä ja valitse top-K
        bids.sort(key=lambda b: b.final_score, reverse=True)

        # Exploration: osa paikoista varattu explorointiin
        n_explore = max(1, int(k * self.exploration_rate))
        n_exploit = k - n_explore

        top_exploit = [b.agent_id for b in bids[:n_exploit]]
        remaining = [b for b in bids[n_exploit:] if b.agent_id not in top_exploit]

        # Exploration-valinnat: suosi vähän käytettyjä ja uusia
        explore_pool = self._exploration_candidates(remaining)
        top_explore = [b.agent_id for b in explore_pool[:n_explore]]

        return top_exploit + top_explore

    def _phase_a_bid(self, agent_id: str, score: AgentScore,
                     task_type: str, task_tags: list[str],
                     routing_rules: dict) -> TaskBid:
        """Vaihe A: halpa bidi ilman LLM:ää."""
        bid = TaskBid(
            agent_id=agent_id,
            agent_type=score.agent_type,
            role=score.role,
        )

        # Tag match — käytä sekä routing_rules ETTÄ agentin omia tageja
        agent_keywords = routing_rules.get(score.agent_type, [])
        # v0.0.2: yhdistä agentin omat tagit
        all_keywords = set(agent_keywords) | set(score.tags)

        tag_matches = sum(1 for tag in task_tags if any(
            kw in tag.lower() for kw in all_keywords
        ))
        bid.tag_match = tag_matches > 0
        bid.tag_score = min(tag_matches / max(len(task_tags), 1), 1.0)

        # Prior confidence (boot-score + pheromone)
        pheromone = (score.success_score * 0.4 +
                     score.speed_score * 0.3 +
                     score.reliability_score * 0.3)
        bid.confidence_prior = (pheromone * 0.6 +
                                score.calibration_score * 0.4
                                if score.calibrated else pheromone * 0.5)

        # Kuormitusrangaistus
        load_penalty = (score.active_tasks * self.load_penalty_factor +
                        score.tasks_last_10min * self.load_penalty_factor * 0.5)

        # Cooldown-rangaistus
        cooldown_penalty = 0.0
        if score.consecutive_wins >= self.cooldown_max:
            cooldown_penalty = 0.5

        # Roolibonus
        role_bonus = AGENT_ROLES.get(score.role, {}).get("priority_boost", 0.0)

        # Final score
        bid.final_score = (
            bid.tag_score * 0.5 +
            bid.confidence_prior * 0.3 +
            role_bonus
            - load_penalty
            - cooldown_penalty
        )

        return bid

    def _exploration_candidates(self, candidates: list[TaskBid]) -> list[TaskBid]:
        """Valitse exploration-ehdokkaat: vähän käytetyt + epävarmat + uudet."""
        for bid in candidates:
            score = self._scores.get(bid.agent_id)
            if not score:
                continue

            bonus = 0.0
            # Vähän ajoja viime aikoina
            if score.total_tasks_today < self.min_tasks_per_day:
                bonus += self.low_usage_bonus

            # Epävarma datapohja
            if not score.calibrated:
                bonus += self.newcomer_boost * 0.5

            # Uusi agentti (alle 5 tehtävää koskaan)
            total_history = sum(1 for t in self._task_history
                                if t.get("agent_id") == bid.agent_id)
            if total_history < 5:
                bonus += self.newcomer_boost

            bid.final_score += bonus

        candidates.sort(key=lambda b: b.final_score, reverse=True)
        return candidates

    # ── 2. Cold Start -kalibrointi ───────────────────────────

    def get_cold_start_tasks(self, agent_id: str, agent_type: str) -> list[dict]:
        """Palauttaa 3 kalibrointitehtävää uudelle agentille."""
        return [
            {
                "type": "summarize",
                "prompt": f"Tiivistä roolisi {agent_type} yhdellä lauseella suomeksi.",
                "max_tokens": 80,
            },
            {
                "type": "eval",
                "prompt": f"Mikä on tärkein asia jonka {agent_type}-agentti tarkistaa helmikuussa?",
                "max_tokens": 100,
            },
            {
                "type": "action_list",
                "prompt": f"Listaa 3 konkreettista toimenpidettä jotka {agent_type} tekee nyt (helmikuu).",
                "max_tokens": 120,
            },
        ]

    def record_calibration(self, agent_id: str, results: list[dict]):
        """Tallenna kalibrointitulokset."""
        score = self._scores.get(agent_id)
        if not score:
            return

        successes = sum(1 for r in results if r.get("success", False))
        avg_speed = sum(r.get("latency_ms", 5000) for r in results) / max(len(results), 1)

        score.calibrated = True
        score.success_score = successes / max(len(results), 1)
        score.speed_score = max(0, 1.0 - (avg_speed / 10000))
        score.reliability_score = successes / max(len(results), 1)
        score.calibration_score = (
            score.success_score * 0.5 +
            score.speed_score * 0.3 +
            score.reliability_score * 0.2
        )

    # ── 3. Tulosten kirjaus (pheromone update) ───────────────

    def record_task_result(self, agent_id: str, success: bool,
                           latency_ms: float = 0, had_corrections: bool = False):
        """Päivitä pheromone-pisteet tehtävän jälkeen."""
        score = self._scores.get(agent_id)
        if not score:
            return

        # EMA-päivitys
        alpha = 0.2
        score.success_score = score.success_score * (1 - alpha) + (1.0 if success else 0.0) * alpha
        score.speed_score = score.speed_score * (1 - alpha) + max(0, 1.0 - latency_ms / 10000) * alpha
        if had_corrections:
            score.reliability_score = score.reliability_score * (1 - alpha) + 0.3 * alpha
        elif success:
            score.reliability_score = score.reliability_score * (1 - alpha) + 1.0 * alpha

        # Kuormituskirjanpito
        score.tasks_last_10min += 1
        score.total_tasks_today += 1
        score.last_task_time = time.monotonic()

        if success:
            score.consecutive_wins += 1
        else:
            score.consecutive_wins = 0

        self._task_history.append({
            "agent_id": agent_id,
            "success": success,
            "latency_ms": latency_ms,
            "time": time.monotonic(),
        })

    def record_task_start(self, agent_id: str):
        """Merkitse tehtävä alkaneeksi."""
        score = self._scores.get(agent_id)
        if score:
            score.active_tasks += 1

    def record_task_end(self, agent_id: str):
        """Merkitse tehtävä päättyneeksi."""
        score = self._scores.get(agent_id)
        if score:
            score.active_tasks = max(0, score.active_tasks - 1)

    # ── 6. Kuormituksen hallinta ─────────────────────────────

    def cleanup_load_counters(self):
        """Nollaa 10min-laskurit. Kutsu heartbeatista."""
        cutoff = time.monotonic() - 600
        for score in self._scores.values():
            recent = sum(1 for t in self._task_history
                         if t.get("agent_id") == score.agent_id
                         and t.get("time", 0) > cutoff)
            score.tasks_last_10min = recent

    def reset_daily_counters(self):
        """Nollaa päivälaskurit. Kutsu keskiyöllä."""
        for score in self._scores.values():
            score.total_tasks_today = 0
            score.total_bids_today = 0
            score.consecutive_wins = 0

    # ── 8. Minimum participation ─────────────────────────────

    def get_underused_agents(self) -> list[str]:
        """Palauttaa agentit jotka eivät ole osallistuneet tarpeeksi."""
        return [
            s.agent_id for s in self._scores.values()
            if s.total_tasks_today < self.min_tasks_per_day
            or s.total_bids_today < self.min_bids_per_day
        ]

    def get_priority_invite(self, task_tags: list[str],
                            routing_rules: dict) -> Optional[str]:
        """Palauttaa alisuoriutujan joka sopii tehtävään (tag-match)."""
        underused = self.get_underused_agents()
        if not underused:
            return None

        best_agent = None
        best_match = 0
        for agent_id in underused:
            score = self._scores.get(agent_id)
            if not score:
                continue
            keywords = routing_rules.get(score.agent_type, [])
            match_count = sum(1 for tag in task_tags
                              if any(kw in tag.lower() for kw in keywords))
            if match_count > best_match:
                best_match = match_count
                best_agent = agent_id

        return best_agent if best_match > 0 else None

    # ── Diagnostiikka ────────────────────────────────────────

    def get_stats(self) -> dict:
        """Dashboard-tilastot."""
        roles = defaultdict(int)
        calibrated = 0
        for s in self._scores.values():
            roles[s.role] += 1
            if s.calibrated:
                calibrated += 1

        return {
            "total_agents": len(self._scores),
            "calibrated": calibrated,
            "roles": dict(roles),
            "underused_today": len(self.get_underused_agents()),
            "exploration_rate": self.exploration_rate,
            "top_k": self.top_k,
            "task_history_size": len(self._task_history),
        }

    def get_agent_scores(self) -> list[dict]:
        """Kaikkien agenttien pisteet."""
        return [
            {
                "agent_id": s.agent_id,
                "type": s.agent_type,
                "role": s.role,
                "success": round(s.success_score, 2),
                "speed": round(s.speed_score, 2),
                "reliability": round(s.reliability_score, 2),
                "active_tasks": s.active_tasks,
                "tasks_today": s.total_tasks_today,
                "calibrated": s.calibrated,
                "consecutive_wins": s.consecutive_wins,
                "tags_count": len(s.tags),
            }
            for s in sorted(self._scores.values(),
                            key=lambda x: x.success_score, reverse=True)
        ]
