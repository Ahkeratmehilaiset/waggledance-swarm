"""
OpenClaw Web Search Tool
========================
DuckDuckGo-pohjainen verkkohaku agenteille.
Ei vaadi API-avainta tai rekisteröitymistä.

Käyttö:
    searcher = WebSearchTool()
    results = await searcher.search("mehiläisten talvihoito Suomi")
    summary = await searcher.search_and_summarize("Tesla V2L tekniset tiedot", llm)
"""

import asyncio
import json
from datetime import datetime
from typing import Optional
from pathlib import Path


class WebSearchTool:
    """DuckDuckGo-verkkohaku agenteille."""

    def __init__(self, cache_dir: str = "data/search_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._ddgs_available = self._check_ddgs()
        self.search_history: list[dict] = []

    def _check_ddgs(self) -> bool:
        """Tarkista onko duckduckgo-search asennettu."""
        try:
            from duckduckgo_search import DDGS
            return True
        except ImportError:
            print("⚠️  duckduckgo-search ei asennettu. Aja: pip install duckduckgo-search")
            return False

    async def search(self, query: str, max_results: int = 5,
                     region: str = "fi-fi") -> list[dict]:
        """
        Hae DuckDuckGosta.
        Palauttaa listan tuloksia: [{title, url, body}, ...]
        """
        if not self._ddgs_available:
            return [{"title": "Virhe", "url": "", "body": "duckduckgo-search ei asennettu"}]

        try:
            # Aja synkroninen haku threadpoolissa
            results = await asyncio.get_event_loop().run_in_executor(
                None, self._sync_search, query, max_results, region
            )

            # Tallenna historiaan
            self.search_history.append({
                "query": query,
                "time": datetime.now().isoformat(),
                "results_count": len(results)
            })

            return results

        except Exception as e:
            print(f"⚠️  Hakuvirhe: {e}")
            return [{"title": "Hakuvirhe", "url": "", "body": str(e)}]

    def _sync_search(self, query: str, max_results: int, region: str) -> list[dict]:
        """Synkroninen DuckDuckGo-haku."""
        from duckduckgo_search import DDGS

        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, region=region, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", r.get("link", "")),
                    "body": r.get("body", r.get("snippet", "")),
                })

        return results

    async def search_news(self, query: str, max_results: int = 5,
                          region: str = "fi-fi") -> list[dict]:
        """Hae uutisia DuckDuckGosta."""
        if not self._ddgs_available:
            return []

        try:
            results = await asyncio.get_event_loop().run_in_executor(
                None, self._sync_news_search, query, max_results, region
            )
            return results
        except Exception as e:
            print(f"⚠️  Uutishakuvirhe: {e}")
            return []

    def _sync_news_search(self, query: str, max_results: int, region: str) -> list[dict]:
        """Synkroninen uutishaku."""
        from duckduckgo_search import DDGS

        results = []
        with DDGS() as ddgs:
            for r in ddgs.news(query, region=region, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", r.get("link", "")),
                    "body": r.get("body", r.get("snippet", "")),
                    "date": r.get("date", ""),
                    "source": r.get("source", ""),
                })

        return results

    async def search_and_summarize(self, query: str, llm, max_results: int = 5,
                                    system_context: str = "") -> str:
        """
        Hae ja tiivistä tulokset LLM:llä.
        Palauttaa suomenkielisen yhteenvedon.
        """
        results = await self.search(query, max_results=max_results)

        if not results or results[0].get("title") == "Virhe":
            return f"Hakuvirhe: {results[0].get('body', 'Tuntematon virhe')}" if results else "Ei tuloksia."

        # Muodosta konteksti LLM:lle
        search_context = f"Hakusanat: {query}\n\nTulokset:\n"
        for i, r in enumerate(results, 1):
            search_context += f"\n{i}. **{r['title']}**\n"
            search_context += f"   URL: {r['url']}\n"
            search_context += f"   {r['body']}\n"

        prompt = (
            f"{search_context}\n\n"
            f"Tiivistä hakutulokset suomeksi. "
            f"Kerro tärkeimmät löydökset ja mainitse lähteet. "
            f"{'Konteksti: ' + system_context if system_context else ''}"
            f"Vastaa 3-5 lauseella."
        )

        try:
            response = await llm.generate(
                prompt,
                system="Olet tutkimusassistentti. Tiivistä hakutulokset selkeästi suomeksi."
            )
            return response.content
        except Exception as e:
            # Fallback: palauta raaka lista
            return search_context

    async def research_topic(self, topic: str, llm, depth: int = 2) -> dict:
        """
        Syvempi tutkimus: hae, analysoi, hae lisää.
        depth=1: yksi haku, depth=2: haku + jatkohaku, jne.
        """
        all_findings = []

        # Ensimmäinen haku
        results = await self.search(topic, max_results=5)
        round1_text = "\n".join(
            f"- {r['title']}: {r['body']}" for r in results if r.get('body')
        )
        all_findings.append({"round": 1, "query": topic, "results": results})

        if depth >= 2 and llm:
            # Pyydä LLM:ää ehdottamaan jatkohakua
            followup_prompt = (
                f"Hain tietoa aiheesta '{topic}'. Tulokset:\n{round1_text[:1000]}\n\n"
                f"Mikä olisi YKSI tarkentava hakusana jolla löydän lisää hyödyllistä tietoa? "
                f"Vastaa VAIN hakusanalla, ei muuta."
            )

            try:
                response = await llm.generate(
                    followup_prompt,
                    system="Vastaa yhdellä hakusanalla suomeksi."
                )
                followup_query = response.content.strip().strip('"').strip("'")

                if followup_query and len(followup_query) > 3:
                    results2 = await self.search(followup_query, max_results=3)
                    all_findings.append({"round": 2, "query": followup_query, "results": results2})
            except Exception:
                pass

        # Koosta kaikki tulokset
        all_text = ""
        for finding in all_findings:
            for r in finding["results"]:
                all_text += f"- {r.get('title', '')}: {r.get('body', '')}\n"

        # Loppuyhteenveto
        summary = ""
        if llm:
            try:
                summary_prompt = (
                    f"Tutkimus aiheesta: {topic}\n\nKaikki löydökset:\n{all_text[:2000]}\n\n"
                    f"Kirjoita kattava yhteenveto suomeksi. Mainitse tärkeimmät lähteet."
                )
                response = await llm.generate(
                    summary_prompt,
                    system="Olet tutkija. Tiivistä löydökset kattavasti suomeksi."
                )
                summary = response.content
            except Exception:
                summary = all_text

        return {
            "topic": topic,
            "rounds": len(all_findings),
            "total_results": sum(len(f["results"]) for f in all_findings),
            "findings": all_findings,
            "summary": summary
        }

    def get_stats(self) -> dict:
        """Hakutilastot."""
        return {
            "total_searches": len(self.search_history),
            "ddgs_available": self._ddgs_available,
            "recent_queries": [h["query"] for h in self.search_history[-5:]],
        }
