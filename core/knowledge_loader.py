"""
WaggleDance Swarm AI — Knowledge Loader v0.0.2
================================================
Jani Korpi (Ahkerat Mehiläiset)
Claude 4.6 • v0.0.2 • Built: 2026-02-22 18:00 EET

Lukee PDF/YAML/TXT tiedostoja agenttien knowledge-kansioista.

v0.0.2 MUUTOKSET:
  FIX-5: get_knowledge() backward-compat wrapper
  FIX-5: get_agent_metadata() — palauttaa skills/tags schedulerille

Aiemmat korjaukset (v0.0.1):
  K1: _read_yaml() oli määritelty KOLME kertaa — nyt yksi.
  K1: elif-ketjussa kolminkertainen YAML-ehto — nyt yksi.
  K6: _read_yaml() lukee NYT KAIKKI YAML-data
"""

import os
import hashlib
from pathlib import Path
from typing import Optional


class KnowledgeLoader:
    """Lukee dokumentteja agenttien tietopankeista."""

    def __init__(self, knowledge_dir: str = "knowledge"):
        self.knowledge_dir = Path(knowledge_dir)
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}
        self._summary_cache: dict[str, str] = {}
        self._fitz_available = self._check_fitz()

    def _check_fitz(self) -> bool:
        try:
            import fitz
            return True
        except ImportError:
            return False

    # ══════════════════════════════════════════════════════════
    # FIX-5: Backward-compat wrappers
    # ══════════════════════════════════════════════════════════

    def get_knowledge(self, agent_type: str, max_chars: int = 2000) -> str:
        """
        BACKWARD-COMPAT wrapper.
        Vanha koodi kutsuu: loader.get_knowledge("tarhaaja")
        → delegoi get_knowledge_summary():iin.
        """
        return self.get_knowledge_summary(agent_type)[:max_chars]

    def get_agent_metadata(self, agent_type: str) -> dict:
        """
        Palauttaa agentin metadata schedulerille.
        Lukee YAML-tiedostosta: skills, tags, role.

        Palautusformaatti:
          {
            "skills": ["varroa_count", "honey_yield", ...],
            "tags": ["mehiläi", "pesä", "hunaj", ...],
            "role": "worker",
          }

        Ei vaadi agents/-kansion muokkausta.
        """
        docs = self.load_for_agent(agent_type)
        skills = []
        tags = []

        for doc in docs:
            if doc["type"] in (".yaml", ".yml") and doc.get("_raw_data"):
                data = doc["_raw_data"]
                # Skills = DECISION_METRICS keys
                metrics = data.get("DECISION_METRICS_AND_THRESHOLDS", {})
                for k in metrics:
                    skills.append(k.replace("_", " ")[:30])
                # Tags from header
                header = data.get("header", {})
                if header.get("keywords"):
                    tags.extend(header["keywords"])

        return {
            "skills": skills[:8],
            "tags": tags,
        }

    # ══════════════════════════════════════════════════════════
    # Core methods (unchanged from v0.0.1)
    # ══════════════════════════════════════════════════════════

    def load_for_agent(self, agent_type: str) -> list[dict]:
        """Lataa kaikki dokumentit agentille."""
        docs = []
        agent_dir = self.knowledge_dir / agent_type
        if agent_dir.exists():
            docs.extend(self._load_directory(agent_dir, f"[{agent_type}]"))
        shared_dir = self.knowledge_dir / "shared"
        if shared_dir.exists():
            docs.extend(self._load_directory(shared_dir, "[shared]"))
        return docs

    def _load_directory(self, directory: Path, source_tag: str) -> list[dict]:
        """Lataa kaikki tuetut tiedostot kansiosta."""
        docs = []
        supported = {".pdf", ".txt", ".md", ".csv", ".json", ".yaml", ".yml"}

        for file_path in sorted(directory.iterdir()):
            if file_path.suffix.lower() not in supported:
                continue
            if file_path.name.startswith("."):
                continue

            file_hash = self._file_hash(file_path)
            if file_hash in self._cache:
                docs.append(self._cache[file_hash])
                continue

            content = self._read_file(file_path)
            if content:
                doc = {
                    "file": file_path.name,
                    "path": str(file_path),
                    "source": source_tag,
                    "content": content,
                    "size": len(content),
                    "type": file_path.suffix.lower(),
                }
                self._cache[file_hash] = doc
                docs.append(doc)

        return docs

    def _read_file(self, file_path: Path) -> Optional[str]:
        """Lue tiedosto tekstiksi. KORJAUS K1: yksi haara per tyyppi."""
        suffix = file_path.suffix.lower()
        try:
            if suffix == ".pdf":
                return self._read_pdf(file_path)
            elif suffix in (".txt", ".md", ".csv"):
                return file_path.read_text(encoding="utf-8", errors="replace")
            elif suffix in (".yaml", ".yml"):
                return self._read_yaml(file_path)
            elif suffix == ".json":
                import json
                data = json.loads(file_path.read_text(encoding="utf-8"))
                return json.dumps(data, indent=2, ensure_ascii=False)
            else:
                return None
        except Exception as e:
            print(f"  ⚠️  Virhe luettaessa {file_path.name}: {e}")
            return None

    def _read_pdf(self, file_path: Path) -> Optional[str]:
        """Lue PDF PyMuPDF:llä."""
        if not self._fitz_available:
            return f"[PDF: {file_path.name} - PyMuPDF ei asennettu]"
        try:
            import fitz
            doc = fitz.open(str(file_path))
            text_parts = []
            for page_num, page in enumerate(doc, 1):
                text = page.get_text()
                if text.strip():
                    text_parts.append(f"--- Sivu {page_num} ---\n{text.strip()}")
            doc.close()
            return "\n\n".join(text_parts) if text_parts else None
        except Exception as e:
            print(f"  ⚠️  PDF-virhe ({file_path.name}): {e}")
            return None

    def _read_yaml(self, file_path: Path) -> Optional[str]:
        """
        KORJAUS K6: Lue KAIKKI YAML-data, ei vain header+metrics.
        """
        try:
            import yaml
            with open(file_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data:
                return None

            parts = []

            # 1. Header
            header = data.get("header", {})
            if header:
                parts.append(f"# {header.get('agent_name', 'Agentti')}")
                if header.get("role"):
                    parts.append(f"Rooli: {header['role']}")
                if header.get("description"):
                    parts.append(header["description"])

            # 2. ASSUMPTIONS
            assumptions = data.get("ASSUMPTIONS", {})
            if assumptions:
                parts.append("\n## Oletukset")
                if isinstance(assumptions, dict):
                    for k, v in assumptions.items():
                        parts.append(f"- {k}: {v}")
                elif isinstance(assumptions, list):
                    for item in assumptions:
                        if isinstance(item, dict):
                            for k, v in item.items():
                                parts.append(f"- {k}: {v}")
                        else:
                            parts.append(f"- {item}")

            # 3. DECISION_METRICS (kaikki)
            metrics = data.get("DECISION_METRICS_AND_THRESHOLDS", {})
            if metrics:
                parts.append("\n## Kynnysarvot")
                for k, v in metrics.items():
                    if isinstance(v, dict):
                        val = v.get("value", "")
                        action = v.get("action", "")
                        source = v.get("source", "")
                        line = f"- {k}: {val}"
                        if action:
                            line += f" → {action}"
                        if source:
                            line += f" [{source}]"
                        parts.append(line)
                    else:
                        parts.append(f"- {k}: {v}")

            # 4. SEASONAL_RULES (kaikki)
            seasons = data.get("SEASONAL_RULES", [])
            if seasons:
                parts.append("\n## Vuosikello")
                for s in seasons:
                    parts.append(
                        f"- {s.get('season', '?')}: "
                        f"{s.get('action', s.get('focus', ''))}"
                    )

            # 5. FAILURE_MODES (kaikki) — KORJAUS K6
            failures = data.get("FAILURE_MODES", [])
            if failures:
                parts.append("\n## Vikatilat")
                for fm in failures:
                    parts.append(
                        f"- {fm.get('mode', '?')}: "
                        f"{fm.get('detection', '')} → "
                        f"{fm.get('action', '')} (P{fm.get('priority', '?')})"
                    )

            # 6. KNOWLEDGE_TABLES (KAIKKI taulukkodata) — KORJAUS K6
            tables = data.get("KNOWLEDGE_TABLES", {})
            if tables:
                parts.append("\n## Tietotaulukot")
                for table_name, table_data in tables.items():
                    parts.append(f"\n### {table_name}")
                    if isinstance(table_data, list):
                        for row in table_data[:30]:
                            if isinstance(row, dict):
                                row_str = ", ".join(
                                    f"{rk}: {rv}" for rk, rv in row.items()
                                )
                                parts.append(f"  - {row_str}")
                            else:
                                parts.append(f"  - {row}")
                    elif isinstance(table_data, dict):
                        for tk, tv in table_data.items():
                            parts.append(f"  - {tk}: {tv}")

            # 7. PROCESS_FLOWS — KORJAUS K6
            flows = data.get("PROCESS_FLOWS", {})
            if flows:
                parts.append("\n## Prosessit")
                for flow_name, steps in flows.items():
                    parts.append(f"\n### {flow_name}")
                    if isinstance(steps, list):
                        for i, step in enumerate(steps, 1):
                            if isinstance(step, dict):
                                parts.append(
                                    f"  {i}. {step.get('step', step.get('action', str(step)))}"
                                )
                            else:
                                parts.append(f"  {i}. {step}")

            # 8. COMPLIANCE_AND_LEGAL
            legal = data.get("COMPLIANCE_AND_LEGAL", {})
            if legal:
                parts.append("\n## Laki/compliance")
                if isinstance(legal, dict):
                    for k, v in legal.items():
                        parts.append(f"- {k}: {v}")
                elif isinstance(legal, list):
                    for item in legal:
                        if isinstance(item, dict):
                            for k, v in item.items():
                                parts.append(f"- {k}: {v}")
                        else:
                            parts.append(f"- {item}")

            # 9. eval_questions
            questions = data.get("eval_questions", [])
            if questions:
                parts.append(f"\n## Eval-kysymykset ({len(questions)} kpl)")
                for q in questions[:5]:
                    if isinstance(q, dict):
                        parts.append(f"  Q: {q.get('question', '')}")
                        parts.append(f"  A: {q.get('answer', '')}")

            if len(parts) > 1:
                return "\n".join(parts)

            # Fallback: generic dump
            return self._yaml_to_text(data, file_path.stem)

        except Exception:
            return None

    def _yaml_to_text(self, data, title="", depth=0, max_depth=4) -> str:
        """Muunna mikä tahansa YAML-rakenne luettavaksi tekstiksi."""
        if depth > max_depth:
            return ""
        parts = []
        indent = "  " * depth

        if isinstance(data, dict):
            for k, v in data.items():
                key_str = str(k).replace("_", " ")
                if isinstance(v, dict):
                    parts.append(
                        f"{indent}## {key_str}" if depth == 0
                        else f"{indent}**{key_str}**:"
                    )
                    sub = self._yaml_to_text(v, "", depth + 1, max_depth)
                    if sub:
                        parts.append(sub)
                elif isinstance(v, list):
                    parts.append(f"{indent}**{key_str}**:")
                    for item in v[:20]:
                        if isinstance(item, dict):
                            item_parts = [
                                f"{ik}: {iv}" for ik, iv in item.items()
                            ]
                            parts.append(
                                f"{indent}  - {', '.join(item_parts[:5])}"
                            )
                        else:
                            parts.append(f"{indent}  - {item}")
                else:
                    parts.append(f"{indent}- {key_str}: {v}")
        elif isinstance(data, list):
            for item in data[:20]:
                if isinstance(item, dict):
                    item_parts = [f"{ik}: {iv}" for ik, iv in item.items()]
                    parts.append(f"{indent}- {', '.join(item_parts[:5])}")
                else:
                    parts.append(f"{indent}- {item}")

        return "\n".join(parts)

    def _file_hash(self, file_path: Path) -> str:
        stat = file_path.stat()
        key = f"{file_path}:{stat.st_size}:{stat.st_mtime}"
        return hashlib.md5(key.encode()).hexdigest()

    def get_knowledge_summary(self, agent_type: str) -> str:
        """
        Palauta tiivistelmä agentin tietopankista.
        KORJAUS K6: Budget nostettu 500 → 2000 merkkiä.
        """
        if agent_type in self._summary_cache:
            return self._summary_cache[agent_type]

        docs = self.load_for_agent(agent_type)
        if not docs:
            return ""

        summary_parts = [f"\n## Tietopankki ({len(docs)} dokumenttia)"]
        total_chars = 0
        max_chars = 3000

        for doc in docs:
            remaining = max_chars - total_chars
            if remaining <= 0:
                summary_parts.append(
                    f"... ja {len(docs) - docs.index(doc)} dokumenttia lisää"
                )
                break
            if doc["file"] == "core.yaml":
                continue
            snippet = doc["content"][:min(2000, remaining)]
            summary_parts.append(f"\n### {doc['file']} {doc['source']}\n{snippet}")
            total_chars += len(snippet)

        result = "\n".join(summary_parts)
        self._summary_cache[agent_type] = result
        return result

    def list_all_knowledge(self) -> dict:
        result = {}
        if not self.knowledge_dir.exists():
            return result
        for subdir in sorted(self.knowledge_dir.iterdir()):
            if subdir.is_dir():
                files = [
                    f.name for f in sorted(subdir.iterdir())
                    if f.is_file() and not f.name.startswith(".")
                ]
                result[subdir.name] = files
        return result
