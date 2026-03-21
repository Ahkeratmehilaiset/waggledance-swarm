"""
OpenClaw Oracle Agent
=====================
Tiedonhankinta-agentti jolla kolme kanavaa:
1. Web-haku (DuckDuckGo) - itsenäinen, ilmainen
2. Claude-konsultaatio (dashboardin kautta) - käyttäjä välittää
3. Tietopankin dokumentit (knowledge/) - automaattinen

Toimintaperiaate:
- Agentit lähettävät kysymyksiä Oraakkelille
- Oracle etsii ensin verkosta (ilmainen)
- Jos ei löydy → kokoaa kysymyksen Claudelle (data/oracle_questions.md)
- Käyttäjä kopioi dashboardista, pasteaa Claudelle, pasteaa vastauksen takaisin
- Oracle jakaa tiedon kaikille agenteille muistin kautta
"""

import asyncio
import json
from datetime import datetime
from typing import Optional
from pathlib import Path

from agents.base_agent import Agent
from core.llm_provider import LLMProvider
from core.token_economy import TokenEconomy, ORACLE_PRICES
from memory.shared_memory import SharedMemory


ORACLE_SYSTEM_PROMPT = """Olet OracleAgent - OpenClaw-järjestelmän tiedonhankinta-asiantuntija.

KYVYT:
1. Verkkohaku (DuckDuckGo) - etsit tietoa verkosta itsenäisesti
2. Claude-konsultaatio - keräät vaikeat kysymykset Claudelle
3. Tutkimus - syvällinen tiedonhankinta usealla haulla

PERIAATTEET:
- Etsi ensin itse verkosta ennen kuin kysyt Claudelta
- Tiivistä löydökset selkeästi suomeksi
- Jaa kaikki arvokas tieto muille agenteille
- Priorisoi Janin projekteihin liittyvä tieto

KONTEKSTI:
- Janin projektit: mehiläistarhaus (300 pesää), TikTok/YouTube, Korvenranta, Tesla V2L
- Yritys: JKH Service, hunajan myynti (Wolt, R-kioski)
- Tekniikka: Whisper, Ollama, FFmpeg, Python

Vastaa suomeksi."""


class OracleAgent(Agent):
    """
    Tiedonhankinta-agentti: verkkohaku + Claude-konsultaatio.
    """

    def __init__(self, llm: LLMProvider, memory: SharedMemory,
                 token_economy: TokenEconomy,
                 name: str = "OracleAgent"):
        super().__init__(
            name=name,
            agent_type="oracle",
            system_prompt=ORACLE_SYSTEM_PROMPT,
            llm=llm,
            memory=memory,
            skills=["web_search", "research", "knowledge_synthesis", "consultation"]
        )

        self.token_economy = token_economy
        self.queries_served = 0
        self.total_tokens_collected = 0
        self.searches_performed = 0
        self.pending_questions: list[dict] = []
        self.questions_file = Path("data/oracle_questions.md")
        self.answers_log = Path("data/oracle_answers.md")
        self.research_log = Path("data/oracle_research.md")
        self._search_tool = None

    def _get_search(self):
        """Hae tai alusta hakutyökalu."""
        if self._search_tool is None:
            try:
                from tools.web_search import WebSearchTool
                self._search_tool = WebSearchTool()
            except ImportError:
                print("⚠️  WebSearchTool ei saatavilla")
        return self._search_tool

    async def initialize(self):
        """Alusta OracleAgent."""
        await super().initialize()
        self.questions_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.questions_file.exists():
            self.questions_file.write_text(
                "# 🔮 Oracle - Kysymykset Claudelle\n\n_Ei avoimia kysymyksiä._\n",
                encoding="utf-8"
            )

    # ── Web-haku ────────────────────────────────────────────────

    async def web_search(self, query: str, max_results: int = 5) -> list[dict]:
        """Hae verkosta DuckDuckGolla."""
        search = self._get_search()
        if not search:
            return [{"title": "Virhe", "url": "", "body": "Hakutyökalu ei saatavilla"}]

        results = await search.search(query, max_results=max_results)
        self.searches_performed += 1

        if results:
            summary = "; ".join(f"{r['title']}" for r in results[:3])
            await self.memory.store_memory(
                content=f"🔍 Verkkohaku '{query}': {summary}",
                agent_id=self.id,
                memory_type="observation",
                importance=0.5
            )

        return results

    async def search_and_learn(self, query: str, context: str = "") -> str:
        """Hae verkosta ja tiivistä LLM:llä. Tallentaa muistiin."""
        search = self._get_search()
        if not search:
            return "Hakutyökalu ei saatavilla. Asenna: pip install duckduckgo-search"

        summary = await search.search_and_summarize(
            query, self.llm, max_results=5, system_context=context
        )

        if summary and len(summary) > 20:
            await self.memory.store_memory(
                content=f"🔍 Tutkimus '{query}': {summary}",
                agent_id=self.id,
                memory_type="insight",
                importance=0.8
            )
            self.queries_served += 1

        return summary

    async def research_topic(self, topic: str, depth: int = 2) -> dict:
        """Syvällinen tutkimus (useita hakuja + jatkohaut)."""
        search = self._get_search()
        if not search:
            return {"success": False, "error": "Hakutyökalu ei saatavilla"}

        result = await search.research_topic(topic, self.llm, depth=depth)

        if result.get("summary"):
            await self.memory.store_memory(
                content=f"📊 Tutkimus '{topic}': {result['summary'][:500]}",
                agent_id=self.id,
                memory_type="insight",
                importance=0.85
            )

            try:
                with open(self.research_log, "a", encoding="utf-8") as f:
                    f.write(f"\n\n## {topic} ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n")
                    f.write(f"{result['summary']}\n")
            except Exception:
                pass

        result["success"] = True
        return result

    # ── Proaktiivinen tutkimus (heartbeat) ──────────────────────

    async def auto_research_cycle(self, agents: list) -> int:
        """
        Oracle tutkii itsenäisesti agenttien aiheita verkosta.
        Kysy agentilta hakusana → hae → tallenna tulos agentin muistiin.
        """
        researched = 0

        for agent in agents:
            if agent.agent_type in ("oracle", "hivemind"):
                continue

            try:
                prompt = (
                    f"Olet {agent.name}. Mikä on YKSI asia josta tarvitsisit "
                    f"tuoretta tietoa verkosta Janin projektin kannalta? "
                    f"Vastaa VAIN lyhyellä hakusanalla (2-5 sanaa), ei muuta."
                )
                search_query = await agent.think(prompt, "")
                search_query = search_query.strip().strip('"').strip("'")

                if search_query and 3 < len(search_query) < 100:
                    summary = await self.search_and_learn(
                        search_query,
                        context=f"Tutkimus agentille {agent.name} ({agent.agent_type})"
                    )

                    if summary and len(summary) > 20:
                        await self.memory.store_memory(
                            content=f"🔍 Oracle tutki puolestasi '{search_query}': {summary[:400]}",
                            agent_id=agent.id,
                            memory_type="insight",
                            importance=0.75
                        )
                        researched += 1
                        print(f"🔍 [{agent.name}] tutkittu: {search_query[:50]}")

            except Exception as e:
                print(f"⚠️  Autotutkimus epäonnistui ({agent.name}): {e}")

        return researched

    # ── Claude-konsultaatio ─────────────────────────────────────

    async def submit_question(self, asking_agent_id: str, asking_agent_name: str,
                               question: str, priority: int = 5,
                               context: str = "") -> dict:
        """Agentti lähettää kysymyksen Claudelle."""
        entry = {
            "id": len(self.pending_questions) + 1,
            "agent_id": asking_agent_id,
            "agent_name": asking_agent_name,
            "question": question,
            "context": context[:300],
            "priority": priority,
            "timestamp": datetime.now().isoformat(),
            "answered": False,
        }
        self.pending_questions.append(entry)
        await self._update_questions_file()

        return {"success": True, "question_id": entry["id"]}

    async def _update_questions_file(self):
        """Päivitä oracle_questions.md."""
        unanswered = [q for q in self.pending_questions if not q["answered"]]

        if not unanswered:
            self.questions_file.write_text(
                "# 🔮 Oracle - Kysymykset Claudelle\n\n_Ei avoimia kysymyksiä._\n",
                encoding="utf-8"
            )
            return

        unanswered.sort(key=lambda x: x["priority"], reverse=True)

        lines = [
            "# 🔮 Oracle - Kysymykset Claudelle\n",
            f"_Generoitu: {datetime.now().strftime('%Y-%m-%d %H:%M')}_\n",
            f"_Avoimia: {len(unanswered)}_\n",
            "\nKopioi alla oleva teksti Claudelle ja pasteaa vastaus dashboardiin.\n",
            "---\n",
            "Hei Claude! OpenClaw-agenttijärjestelmä tarvitsee apuasi.",
            "Vastaa jokaiseen kysymykseen erikseen ja numeroi vastauksesi.\n",
        ]

        for q in unanswered:
            lines.append(f"\n### Kysymys {q['id']} ({q['agent_name']}, prioriteetti {q['priority']}/10)")
            lines.append(f"{q['question']}")
            if q["context"]:
                lines.append(f"\n_Konteksti: {q['context']}_")

        lines.append("\n---\nVastaa suomeksi, konkreettisesti ja lyhyesti per kysymys.")

        self.questions_file.write_text("\n".join(lines), encoding="utf-8")

    async def receive_answer(self, answer_text: str) -> dict:
        """Käyttäjä pasteaa Clauden vastauksen → jaetaan agenteille."""
        unanswered = [q for q in self.pending_questions if not q["answered"]]

        if not unanswered:
            return {"success": False, "error": "Ei avoimia kysymyksiä"}

        log_entry = f"\n\n## Vastaus {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        for q in unanswered:
            log_entry += f"- [{q['agent_name']}] {q['question'][:100]}\n"
        log_entry += f"\n### Clauden vastaus:\n{answer_text}\n"

        with open(self.answers_log, "a", encoding="utf-8") as f:
            f.write(log_entry)

        distributed_to = []
        for q in unanswered:
            q["answered"] = True

            await self.memory.store_memory(
                content=f"🔮 Claude-vastaus: {answer_text[:800]}",
                agent_id=q["agent_id"],
                memory_type="insight",
                importance=0.95
            )

            distributed_to.append(q["agent_name"])
            self.queries_served += 1

        await self._update_questions_file()

        return {"success": True, "distributed_to": distributed_to, "questions_answered": len(distributed_to)}

    async def auto_generate_questions(self, agents: list) -> int:
        """Oracle kierrättää agentit ja kokoaa kysymykset Claudelle."""
        questions_added = 0

        for agent in agents:
            if agent.agent_type == "oracle":
                continue

            try:
                prompt = (
                    f"Olet {agent.name}. Mikä on YKSI tärkeä kysymys jonka haluaisit "
                    f"kysyä kokeneelta asiantuntijalta (Claude AI)? "
                    f"Vastaa VAIN kysymyksellä."
                )

                question = await agent.think(prompt, "")

                if question and len(question.strip()) > 10:
                    await self.submit_question(
                        asking_agent_id=agent.id,
                        asking_agent_name=agent.name,
                        question=question.strip(),
                        priority=6
                    )
                    questions_added += 1

            except Exception as e:
                print(f"⚠️  Kysymys epäonnistui ({agent.name}): {e}")

        return questions_added

    # ── API ─────────────────────────────────────────────────────

    def get_pending_questions(self) -> list[dict]:
        return [q for q in self.pending_questions if not q["answered"]]

    def get_questions_text(self) -> str:
        if self.questions_file.exists():
            return self.questions_file.read_text(encoding="utf-8")
        return "Ei avoimia kysymyksiä."

    def get_stats(self) -> dict:
        base = super().get_stats()
        base.update({
            "queries_served": self.queries_served,
            "searches_performed": self.searches_performed,
            "pending_questions": len(self.get_pending_questions()),
            "active_provider": "web_search + claude_dashboard",
            "browser_connected": False,
            "search_available": self._get_search() is not None,
            "leaderboard": self.token_economy.get_leaderboard()
        })
        return base
