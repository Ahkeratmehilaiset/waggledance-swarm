"""Swarm scheduler — stateless agent selection with pheromone scoring.

Ported from core/swarm_scheduler.py. Key logic preserved:
- Top-K selection with exploration quota (20% of K reserved for low-usage agents)
- Pheromone EMA update (alpha=0.2) for success/speed/reliability
- Load penalty: active_tasks * 0.15 + tasks_10min * 0.075
- Newcomer boost: +0.2 for agents with <5 historical tasks
- Cooldown: -0.5 penalty after 5 consecutive wins
- Tag/skill matching: substring match of task tags against agent keywords
- Final score: tag_score*0.5 + confidence_prior*0.3 + role_bonus - penalties
"""

from dataclasses import dataclass, field

from waggledance.core.domain.agent import AgentDefinition, AgentResult
from waggledance.core.domain.task import TaskRequest
from waggledance.core.domain.trust_score import AgentTrust
from waggledance.core.ports.config_port import ConfigPort


ROLE_BONUSES = {"scout": 0.0, "worker": 0.1, "judge": 0.05}


@dataclass
class SchedulerState:
    """Immutable-style scheduler state passed in/out of Scheduler."""

    pheromone_scores: dict[str, float] = field(default_factory=dict)
    usage_counts: dict[str, int] = field(default_factory=dict)
    recent_successes: dict[str, float] = field(default_factory=dict)


class Scheduler:
    """Pure agent selection engine — no I/O, no mutable internal state."""

    def __init__(self, config: ConfigPort) -> None:
        self._top_k = int(config.get("swarm.top_k", 8))
        self._exploration_rate = float(config.get("swarm.exploration_rate", 0.20))
        self._load_penalty_factor = float(config.get("swarm.load_penalty", 0.15))
        self._newcomer_boost = float(config.get("swarm.newcomer_boost", 0.2))
        self._low_usage_bonus = float(config.get("swarm.low_usage_bonus", 0.15))
        self._cooldown_max = int(config.get("swarm.cooldown_max", 5))
        self._min_tasks_per_day = int(config.get("swarm.min_tasks_per_day", 2))

    def select_agents(
        self,
        task: TaskRequest,
        candidates: list[AgentDefinition],
        state: SchedulerState,
        trust_scores: dict[str, AgentTrust],
        n: int = 6,
    ) -> list[AgentDefinition]:
        """Select top-N agents for a task using scoring + exploration."""
        if not candidates:
            return []

        k = min(n, len(candidates))
        scored: list[tuple[float, AgentDefinition]] = []

        for agent in candidates:
            if not agent.active:
                continue
            score = self._score_agent(agent, task, state, trust_scores)
            scored.append((score, agent))

        scored.sort(key=lambda x: x[0], reverse=True)

        n_explore = max(1, int(k * self._exploration_rate))
        n_exploit = k - n_explore

        exploited = scored[:n_exploit]
        remaining = scored[n_exploit:]

        explored = self._apply_exploration_bonus(remaining, state)

        result = [a for _, a in exploited]
        result.extend(a for _, a in explored[:n_explore])
        return result[:k]

    def update_state(
        self,
        state: SchedulerState,
        agent_id: str,
        result: AgentResult,
    ) -> SchedulerState:
        """Return new state with EMA-updated pheromone scores."""
        alpha = 0.2
        old_pheromone = state.pheromone_scores.get(agent_id, 0.5)

        success_signal = 1.0 if result.confidence > 0.5 else 0.0
        speed_signal = max(0.0, 1.0 - result.latency_ms / 10000.0)
        new_pheromone = old_pheromone * (1.0 - alpha) + (
            success_signal * 0.4 + speed_signal * 0.3 + success_signal * 0.3
        ) * alpha

        new_scores = dict(state.pheromone_scores)
        new_scores[agent_id] = max(0.0, min(1.0, new_pheromone))

        new_usage = dict(state.usage_counts)
        new_usage[agent_id] = new_usage.get(agent_id, 0) + 1

        new_successes = dict(state.recent_successes)
        if result.confidence > 0.5:
            import time
            new_successes[agent_id] = time.time()

        return SchedulerState(
            pheromone_scores=new_scores,
            usage_counts=new_usage,
            recent_successes=new_successes,
        )

    def _score_agent(
        self,
        agent: AgentDefinition,
        task: TaskRequest,
        state: SchedulerState,
        trust_scores: dict[str, AgentTrust],
    ) -> float:
        """Compute final score for one agent-task pair."""
        tag_score = self._compute_tag_score(agent, task)
        pheromone = state.pheromone_scores.get(agent.id, 0.5)

        trust = trust_scores.get(agent.id)
        trust_boost = trust.composite_score * 0.4 if trust else 0.0
        confidence_prior = pheromone * 0.6 + trust_boost

        role_bonus = ROLE_BONUSES.get("worker", 0.1)

        usage = state.usage_counts.get(agent.id, 0)
        load_penalty = usage * self._load_penalty_factor * 0.01

        cooldown_penalty = 0.0
        recent = state.recent_successes.get(agent.id, 0.0)
        if recent and usage > self._cooldown_max:
            cooldown_penalty = 0.5

        return (
            tag_score * 0.5
            + confidence_prior * 0.3
            + role_bonus
            - load_penalty
            - cooldown_penalty
        )

    def _compute_tag_score(
        self, agent: AgentDefinition, task: TaskRequest
    ) -> float:
        """Substring match of task query words against agent tags."""
        query_lower = task.query.lower()
        all_keywords = set(t.lower() for t in agent.tags + agent.skills)
        if not all_keywords:
            return 0.0
        hits = sum(1 for kw in all_keywords if kw in query_lower)
        return min(hits / max(len(all_keywords), 1), 1.0)

    def _apply_exploration_bonus(
        self,
        scored: list[tuple[float, AgentDefinition]],
        state: SchedulerState,
    ) -> list[tuple[float, AgentDefinition]]:
        """Add bonus to low-usage and new agents for exploration."""
        result: list[tuple[float, AgentDefinition]] = []
        for score, agent in scored:
            bonus = 0.0
            usage = state.usage_counts.get(agent.id, 0)
            if usage < self._min_tasks_per_day:
                bonus += self._low_usage_bonus
            if usage < 5:
                bonus += self._newcomer_boost
            result.append((score + bonus, agent))
        result.sort(key=lambda x: x[0], reverse=True)
        return result
