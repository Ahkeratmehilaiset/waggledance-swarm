"""
OpenClaw Agent Base
===================
Pohjaluokka kaikille agenteille.
Jokainen agentti voi:
- Ajatella (think)
- Toimia (act)
- Muistaa (remember)
- Kommunikoida (communicate)
- Oppia (learn)
- Lukea tietopankkia (knowledge)
"""

import asyncio
import json
import uuid
import time
from datetime import datetime
from typing import Optional, Callable
from abc import ABC, abstractmethod

from core.llm_provider import LLMProvider, LLMResponse
from memory.shared_memory import SharedMemory


class Agent:
    """
    Perusagentti joka voi ajatella, toimia ja oppia.
    Kaikki erikoistuneet agentit perivät tämän.
    """

    def __init__(self, name: str, agent_type: str, system_prompt: str,
                 llm: LLMProvider, memory: SharedMemory,
                 skills: list[str] = None, config: dict = None,
                 monitor=None):
        self.id = f"{agent_type}_{uuid.uuid4().hex[:8]}"
        self.name = name
        self.agent_type = agent_type
        self.system_prompt = system_prompt
        self.llm = llm
        self.memory = memory
        self.monitor = monitor
        self.skills = skills or []
        self.config = config or {}

        # Tila
        self.status = "idle"
        self.current_task = None
        self.conversation_history: list[dict] = []
        self.insights_generated = 0
        self.tasks_completed = 0
        self.created_at = datetime.now()
        self.last_active = None

        # Oppiminen
        self.learning_rate = 0.1
        self.experience_points = 0
        self.specializations: dict[str, float] = {}

        # Knowledge loader (alustetaan lazy)
        self._knowledge_loader = None
        self._knowledge_cache: str = ""
        self._knowledge_loaded_at: Optional[datetime] = None

    def _get_knowledge(self) -> str:
        """Hae agentin tietopankki (cached, päivittyy 5 min välein)."""
        now = datetime.now()

        # Päivitä cache 5 min välein
        if (self._knowledge_loaded_at is None or
                (now - self._knowledge_loaded_at).seconds > 300):
            try:
                if self._knowledge_loader is None:
                    from core.knowledge_loader import KnowledgeLoader
                    self._knowledge_loader = KnowledgeLoader("knowledge")

                self._knowledge_cache = self._knowledge_loader.get_knowledge_summary(
                    self.agent_type
                )
                self._knowledge_loaded_at = now
            except Exception as e:
                self._knowledge_cache = ""

        return self._knowledge_cache

    async def initialize(self):
        """Rekisteröi agentti muistiin."""
        await self.memory.register_agent(
            self.id, self.name, self.agent_type,
            config={"skills": self.skills, "system_prompt_hash": hash(self.system_prompt)}
        )
        await self.memory.log_event(self.id, "initialized", f"{self.name} käynnistyi")

    async def think(self, query: str, context: str = "") -> str:
        """
        Agentin ajatteluprosessi.
        Kerää kontekstin, muistaa relevanttia tietoa, ja tuottaa vastauksen.
        """
        self.status = "thinking"
        self.last_active = datetime.now()
        await self.memory.update_agent_status(self.id, "thinking")

        try:
            # 1. Hae relevantti muisti
            memories = await self.memory.recall(query, limit=10, agent_id=self.id)
            all_memories = await self.memory.recall(query, limit=5)

            # 2. Hae lukemattomat viestit
            messages = await self.memory.get_messages(self.id)

            # 3. Hae tietopankki
            knowledge = self._get_knowledge()

            # 4. Rakenna konteksti
            full_context = self._build_context(
                query, memories, all_memories, messages, context, knowledge
            )

            # 5. Generoi vastaus
            response = await self.llm.generate(
                prompt=full_context,
                system=self.system_prompt
            )

            # 5b. Monitoroi ajattelu
            if self.monitor:
                await self.monitor.thought(
                    self.id, self.name,
                    prompt=full_context[:500],
                    response=response.content[:500]
                )

            # 6. Tallenna ajatteluprosessi muistiin
            await self.memory.store_memory(
                content=f"Kysymys: {query[:200]} -> Vastaus: {response.content[:300]}",
                agent_id=self.id,
                memory_type="observation",
                importance=0.4
            )

            # 7. Merkitse viestit luetuiksi
            await self.memory.mark_messages_read(self.id)

            self.status = "idle"
            await self.memory.update_agent_status(self.id, "idle")

            return response.content

        except Exception as e:
            self.status = "error"
            await self.memory.update_agent_status(self.id, "error")
            await self.memory.log_event(self.id, "error", str(e))
            raise

    def _build_context(self, query: str, own_memories: list, all_memories: list,
                       messages: list, extra_context: str,
                       knowledge: str = "") -> str:
        """Rakenna täysi konteksti LLM:lle."""
        parts = [f"## Tehtävä\n{query}"]

        if extra_context:
            parts.append(f"\n## Lisäkonteksti\n{extra_context}")

        # Tietopankki (PDF:t ja dokumentit)
        if knowledge:
            parts.append(knowledge)

        if own_memories:
            parts.append("\n## Relevantit Muistot")
            for m in own_memories[:5]:
                parts.append(f"- [{m['memory_type']}] {m['content'][:200]}")

        if all_memories:
            other_memories = [m for m in all_memories if m.get("agent_id") != self.id]
            if other_memories:
                parts.append("\n## Muiden Agenttien Oivalluksia")
                for m in other_memories[:3]:
                    parts.append(f"- [{m.get('agent_id', '?')}] {m['content'][:150]}")

        if messages:
            parts.append("\n## Lukemattomat Viestit")
            for msg in messages[:5]:
                parts.append(f"- {msg['from_agent']}: {msg['content'][:150]}")

        parts.append(f"\n## Vastaa suomeksi. Ole konkreettinen ja hyödyllinen.")

        return "\n".join(parts)

    async def execute_task(self, task: dict) -> str:
        """Suorita tehtävä."""
        self.status = "acting"
        self.current_task = task
        task_id = task["id"]

        await self.memory.update_task(task_id, status="in_progress",
                                       started_at=datetime.now().isoformat())
        await self.memory.log_event(self.id, "task_started", task["title"])

        try:
            context = ""
            if task.get("project_id"):
                context = await self.memory.get_full_context(task["title"])

            result = await self.think(
                query=f"Suorita seuraava tehtävä:\n\nOtsikko: {task['title']}\n"
                      f"Kuvaus: {task.get('description', 'Ei kuvausta')}\n\n"
                      f"Anna konkreettinen tulos.",
                context=context
            )

            await self.memory.update_task(
                task_id, status="completed", result=result,
                completed_at=datetime.now().isoformat()
            )

            await self.memory.store_memory(
                content=f"Tehtävä '{task['title']}' valmis. Tulos: {result[:300]}",
                agent_id=self.id,
                project_id=task.get("project_id"),
                memory_type="insight",
                importance=0.6
            )

            self.tasks_completed += 1
            self.experience_points += 10
            self.status = "idle"
            self.current_task = None

            await self.memory.log_event(self.id, "task_completed", task["title"])
            return result

        except Exception as e:
            await self.memory.update_task(task_id, status="failed", result=str(e))
            await self.memory.log_event(self.id, "task_failed", f"{task['title']}: {e}")
            self.status = "idle"
            self.current_task = None
            raise

    async def generate_insight(self, topic: str = "") -> Optional[str]:
        """Generoi oivallus perustuen kaikkeen mitä agentti tietää."""
        context = await self.memory.get_full_context(topic or self.agent_type)

        prompt = f"""Analysoi seuraava konteksti ja generoi YKSI uusi oivallus.
Oivalluksen tulee olla konkreettinen, perusteltu ja uusi.

Konteksti:
{context}

{'Aihe: ' + topic if topic else ''}

Vastaa muodossa:
OIVALLUS: [yksi selkeä lause]
PERUSTELU: [miksi tämä on tärkeä]
EHDOTUS: [konkreettinen seuraava askel]"""

        try:
            response = await self.llm.generate(prompt, system=self.system_prompt)

            await self.memory.store_memory(
                content=response.content,
                agent_id=self.id,
                memory_type="insight",
                importance=0.8,
                metadata={"topic": topic}
            )

            self.insights_generated += 1
            self.experience_points += 5
            return response.content

        except Exception as e:
            await self.memory.log_event(self.id, "insight_failed", str(e))
            return None

    async def communicate(self, to_agent_id: str, message: str,
                          message_type: str = "info"):
        """Lähetä viesti toiselle agentille."""
        await self.memory.send_message(self.id, to_agent_id, message, message_type)
        await self.memory.log_event(
            self.id, "message_sent",
            f"-> {to_agent_id}: {message[:100]}"
        )
        if self.monitor:
            await self.monitor.chat(
                self.id, self.name,
                to_agent_id, to_agent_id,
                message
            )

    async def reflect(self) -> str:
        """Agentin itsearviointi ja oppiminen."""
        events = await self.memory.get_timeline(limit=20, agent_id=self.id)
        own_memories = await self.memory.get_recent_memories(limit=15, agent_id=self.id)

        events_text = "\n".join(f"- [{e['event_type']}] {e['description']}" for e in events)
        memories_text = "\n".join(f"- {m['content'][:200]}" for m in own_memories)

        prompt = f"""Olet {self.name} ({self.agent_type}).
Tee itsearviointi ja tunnista parannuskohteet.

Viimeisimmät tapahtumat:
{events_text}

Viimeisimmät muistot:
{memories_text}

Tilastot: {self.tasks_completed} tehtävää, {self.insights_generated} oivallusta, {self.experience_points} XP

Analysoi:
1. Mikä on mennyt hyvin?
2. Missä voisin parantaa?
3. Mitä uusia taitoja minun pitäisi kehittää?
4. Mikä on tärkein seuraava asia johon keskittyä?"""

        response = await self.llm.generate(prompt, system=self.system_prompt)

        await self.memory.store_memory(
            content=f"Reflektio: {response.content[:500]}",
            agent_id=self.id,
            memory_type="reflection",
            importance=0.7
        )

        return response.content

    def get_stats(self) -> dict:
        """Agentin tilastot."""
        return {
            "id": self.id,
            "name": self.name,
            "type": self.agent_type,
            "status": self.status,
            "tasks_completed": self.tasks_completed,
            "insights_generated": self.insights_generated,
            "experience_points": self.experience_points,
            "skills": self.skills,
            "specializations": self.specializations,
            "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat() if self.last_active else None,
            "current_task": self.current_task["title"] if self.current_task else None
        }
