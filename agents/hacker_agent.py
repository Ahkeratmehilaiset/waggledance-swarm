"""
OpenClaw Hacker Agent
=====================
Autonominen koodausagentti joka osaa:
- Lukea ja analysoida koodia
- Kirjoittaa uutta koodia
- Ajaa ja testata koodia
- Refaktoroida ja parantaa olemassa olevaa
- Debugata virheitä
- Generoida testejä
- Parantaa itseään ja muita agentteja
- Lukea ja muokata OpenClaw-projektin tiedostoja
"""

import asyncio
import subprocess
import tempfile
import os
import re
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

from agents.base_agent import Agent
from core.llm_provider import LLMProvider
from memory.shared_memory import SharedMemory


HACKER_SYSTEM_PROMPT = """Olet HackerAgent - autonominen koodausasiantuntija ja järjestelmäkehittäjä.

YDINOSAAMISET:
- Python, Bash, JavaScript/TypeScript, SQL, HTML/CSS
- Järjestelmäarkkitehtuuri ja suunnittelu
- Debuggaus ja virheiden korjaus
- Koodin refaktorointi ja optimointi
- Testien kirjoittaminen
- Turvallisuusanalyysi
- API-integraatiot

PERIAATTEET:
1. Kirjoita aina puhdasta, hyvin kommentoitua koodia
2. Testaa ennen kuin annat tuloksen
3. Selitä mitä teit ja miksi
4. Ehdota parannuksia proaktiivisesti
5. Turvallisuus ensin - älä koskaan aja vaarallista koodia
6. Käytä type hinttejä ja docstringejä

KONTEKSTI:
- Janin tech-stack: Python, Whisper, FFmpeg, Ollama, FastAPI
- Tärkeät projektit: OpenClaw, video-pipelinet, transkriptiot
- Ympäristö: Windows, watercooled PC, 64GB RAM, dual GPU
- OpenClaw-projekti sijaitsee: S:\\Python\\openclaw\\

Kun saat tehtävän:
1. Analysoi ensin mitä pitää tehdä
2. Suunnittele ratkaisu
3. Kirjoita koodi
4. Testaa se
5. Raportoi tulos

Vastaa suomeksi. Koodi englanniksi (kommentit voivat olla suomeksi)."""


# Sallitut hakemistot joihin HackerAgent saa kirjoittaa
ALLOWED_WRITE_DIRS = [
    "data/hacker_workspace",
    "tools",
    "data",
]

# Hakemistot joista saa lukea (laajempi)
ALLOWED_READ_DIRS = [
    ".",          # Koko OpenClaw-projekti
    "agents",
    "core",
    "memory",
    "web",
    "tools",
    "configs",
    "data",
    "knowledge",
]

# Tiedostot joita EI saa muokata (turvallisuus)
PROTECTED_FILES = [
    "main.py",
    "configs/settings.yaml",
    ".env",
    ".gitignore",
]


class HackerAgent(Agent):
    """
    Autonominen koodausagentti.
    Perii perusagentin ja lisää koodaustyökalut + projektin tiedostohallinta.
    """

    def __init__(self, llm: LLMProvider, memory: SharedMemory,
                 workspace: str = "data/hacker_workspace",
                 name: str = "HackerAgent"):
        super().__init__(
            name=name,
            agent_type="hacker",
            system_prompt=HACKER_SYSTEM_PROMPT,
            llm=llm,
            memory=memory,
            skills=["coding", "debugging", "refactoring", "testing",
                    "security_audit", "system_admin", "ai_ml",
                    "file_read", "file_write"]
        )

        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.project_root = Path(__file__).parent.parent  # openclaw/

        # Execution history for learning
        self.execution_history: list[dict] = []
        self.bugs_fixed = 0
        self.lines_written = 0
        self.improvements_made = 0
        self.files_modified: list[dict] = []

    # ── Projektin tiedostohallinta ─────────────────────────────

    def read_project_file(self, relative_path: str) -> dict:
        """
        Lue tiedosto OpenClaw-projektista.
        Palauttaa sisällön tai virheilmoituksen.
        """
        path = (self.project_root / relative_path).resolve()

        # Turvatarkistus: onko projektin sisällä?
        try:
            path.relative_to(self.project_root.resolve())
        except ValueError:
            return {"success": False, "error": f"Polku projektin ulkopuolella: {relative_path}"}

        if not path.exists():
            return {"success": False, "error": f"Tiedostoa ei löydy: {relative_path}"}

        if path.is_dir():
            # Listaa kansion sisältö
            files = []
            for f in sorted(path.iterdir()):
                if f.name.startswith(".") or "__pycache__" in str(f):
                    continue
                files.append({
                    "name": f.name,
                    "type": "dir" if f.is_dir() else "file",
                    "size": f.stat().st_size if f.is_file() else 0
                })
            return {"success": True, "type": "directory", "files": files, "path": relative_path}

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            return {
                "success": True,
                "type": "file",
                "content": content,
                "path": relative_path,
                "size": len(content),
                "lines": content.count("\n") + 1
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def write_project_file(self, relative_path: str, content: str,
                            create_backup: bool = True) -> dict:
        """
        Kirjoita tiedosto OpenClaw-projektiin.
        Luo automaattisen backupin ennen muokkausta.
        """
        path = (self.project_root / relative_path).resolve()

        # Turvatarkistus 1: projektin sisällä?
        try:
            path.relative_to(self.project_root.resolve())
        except ValueError:
            return {"success": False, "error": f"Polku projektin ulkopuolella: {relative_path}"}

        # Turvatarkistus 2: suojattu tiedosto?
        if relative_path in PROTECTED_FILES:
            return {"success": False, "error": f"Suojattu tiedosto: {relative_path}"}

        # Turvatarkistus 3: sallittu hakemisto?
        is_allowed = any(
            relative_path.startswith(d) for d in ALLOWED_WRITE_DIRS
        )
        if not is_allowed:
            return {
                "success": False,
                "error": f"Kirjoitus ei sallittu: {relative_path}. "
                         f"Sallitut: {ALLOWED_WRITE_DIRS}"
            }

        try:
            # Backup
            if create_backup and path.exists():
                backup_dir = self.workspace / "backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_name = f"{path.stem}_{timestamp}{path.suffix}"
                backup_path = backup_dir / backup_name
                backup_path.write_text(path.read_text(encoding="utf-8", errors="replace"))

            # Kirjoita
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

            self.files_modified.append({
                "path": relative_path,
                "time": datetime.now().isoformat(),
                "size": len(content),
                "action": "modified" if path.exists() else "created"
            })
            self.lines_written += content.count("\n") + 1

            return {
                "success": True,
                "path": relative_path,
                "size": len(content),
                "lines": content.count("\n") + 1,
                "backup": True if create_backup else False
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_project_structure(self, max_depth: int = 3) -> dict:
        """Listaa koko OpenClaw-projektin rakenne."""
        def _scan(path: Path, depth: int = 0) -> list:
            if depth >= max_depth:
                return []
            items = []
            try:
                for f in sorted(path.iterdir()):
                    if f.name.startswith(".") or "__pycache__" in f.name:
                        continue
                    if f.name == "node_modules":
                        continue
                    entry = {"name": f.name, "type": "dir" if f.is_dir() else "file"}
                    if f.is_file():
                        entry["size"] = f.stat().st_size
                    if f.is_dir():
                        entry["children"] = _scan(f, depth + 1)
                    items.append(entry)
            except PermissionError:
                pass
            return items

        return {
            "project": str(self.project_root),
            "structure": _scan(self.project_root)
        }

    async def smart_edit(self, file_path: str, instruction: str) -> dict:
        """
        Älykäs tiedoston muokkaus: lue tiedosto, anna LLM:lle
        muokkausohje, tallenna tulos.
        """
        # Lue nykyinen sisältö
        read_result = self.read_project_file(file_path)
        if not read_result["success"]:
            return read_result

        current_code = read_result["content"]

        # Pyydä LLM:ää muokkaamaan
        prompt = f"""Muokkaa seuraava tiedosto ({file_path}) ohjeen mukaan.

OHJE: {instruction}

NYKYINEN KOODI:
```
{current_code[:6000]}
```

Palauta KOKO muokattu tiedosto (ei vain muutetut osat).
Vastaa VAIN koodilla:
```
[koko muokattu tiedosto]
```"""

        response = await self.think(prompt, "")
        new_code = self._extract_code(response)

        if not new_code:
            return {"success": False, "error": "LLM ei tuottanut koodia"}

        # Tarkista onko kirjoitus sallittu
        write_result = self.write_project_file(file_path, new_code)

        if write_result["success"]:
            await self.memory.store_memory(
                content=f"Muokkasin tiedostoa {file_path}: {instruction[:200]}",
                agent_id=self.id,
                memory_type="observation",
                importance=0.7
            )

        return write_result

    # ── Core Coding Abilities ─────────────────────────────────

    async def analyze_code(self, code: str = None, file_path: str = None) -> str:
        """Analysoi koodi ja anna palaute."""
        if file_path:
            # Yritä ensin projektin sisältä
            read = self.read_project_file(file_path)
            if read["success"] and read["type"] == "file":
                code = read["content"]
                context = f"Tiedosto: {file_path}\n"
            else:
                path = Path(file_path).expanduser()
                if not path.exists():
                    return f"❌ Tiedostoa ei löydy: {file_path}"
                code = path.read_text(errors="replace")
                context = f"Tiedosto: {file_path}\n"
        else:
            context = "Annettu koodi:\n"

        if not code or not code.strip():
            return "❌ Ei koodia analysoitavaksi"

        prompt = f"""{context}
```
{code[:8000]}
```

Analysoi tämä koodi ja anna raportti suomeksi:
1. BUGIT: Mahdolliset virheet
2. TURVALLISUUS: Riskit
3. SUORITUSKYKY: Optimointimahdollisuudet
4. PARANNUKSET: Konkreettiset ehdotukset"""

        result = await self.think(prompt)

        await self.memory.store_memory(
            content=f"Koodianalyysi {'tiedostolle ' + file_path if file_path else 'snippetille'}: "
                    f"{result[:300]}",
            agent_id=self.id,
            memory_type="insight",
            importance=0.6
        )

        return result

    async def write_code(self, task: str, language: str = "python",
                         output_path: str = None) -> dict:
        """Kirjoita koodi tehtävänannon perusteella."""
        memories = await self.memory.recall(task, limit=5)
        memory_context = "\n".join(
            f"- {m['content'][:200]}" for m in memories
        ) if memories else "Ei aiempaa kontekstia."

        prompt = f"""Kirjoita {language}-koodia:

TEHTÄVÄ: {task}

KONTEKSTI:
{memory_context}

Vastaa muodossa:
SELITYS: [mitä koodi tekee]

KOODI:
```{language}
[koodi]
```"""

        response = await self.think(prompt)
        code = self._extract_code(response, language)

        if code and output_path:
            path = Path(output_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(code)
            self.lines_written += code.count("\n") + 1
        elif code and not output_path:
            ext = {"python": "py", "javascript": "js", "typescript": "ts",
                   "bash": "sh", "html": "html", "sql": "sql"}.get(language, "txt")
            filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{language}.{ext}"
            path = self.workspace / filename
            path.write_text(code)
            output_path = str(path)
            self.lines_written += code.count("\n") + 1

        await self.memory.store_memory(
            content=f"Koodia kirjoitettu: {task[:200]}. Tiedosto: {output_path}",
            agent_id=self.id,
            memory_type="observation",
            importance=0.5,
            metadata={"language": language, "file": output_path}
        )

        return {
            "code": code,
            "explanation": response,
            "file_path": output_path,
            "language": language,
            "lines": code.count("\n") + 1 if code else 0
        }

    async def execute_code(self, code: str = None, file_path: str = None,
                           language: str = "python", timeout: int = 30) -> dict:
        """Aja koodi turvallisesti."""
        if file_path:
            path = Path(file_path).expanduser()
            if not path.exists():
                return {"success": False, "error": f"Tiedostoa ei löydy: {file_path}"}
            code = path.read_text()

        if not code:
            return {"success": False, "error": "Ei koodia ajettavaksi"}

        safety_check = self._safety_check(code)
        if not safety_check["safe"]:
            return {
                "success": False,
                "error": f"⛔ Turvallisuusriski: {safety_check['reason']}",
                "blocked": True
            }

        ext = {"python": ".py", "javascript": ".js", "bash": ".sh"}.get(language, ".txt")

        with tempfile.NamedTemporaryFile(mode="w", suffix=ext, dir=str(self.workspace),
                                          delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            if language == "python":
                cmd = ["python", temp_path]
            elif language == "javascript":
                cmd = ["node", temp_path]
            elif language == "bash":
                cmd = ["bash", temp_path]
            else:
                return {"success": False, "error": f"Tuntematon kieli: {language}"}

            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, cwd=str(self.workspace)
            )

            execution_result = {
                "success": result.returncode == 0,
                "stdout": result.stdout[:5000],
                "stderr": result.stderr[:2000],
                "return_code": result.returncode,
                "language": language
            }

        except subprocess.TimeoutExpired:
            execution_result = {
                "success": False, "error": f"⏰ Aikakatkaisu ({timeout}s)", "language": language
            }
        except Exception as e:
            execution_result = {
                "success": False, "error": str(e), "language": language
            }
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

        self.execution_history.append({
            "time": datetime.now().isoformat(),
            "language": language,
            "success": execution_result["success"],
            "lines": code.count("\n") + 1
        })

        return execution_result

    async def fix_code(self, code: str = None, file_path: str = None,
                       error_message: str = "") -> dict:
        """Korjaa buginen koodi iteratiivisesti (max 5 kierrosta)."""
        if file_path:
            path = Path(file_path).expanduser()
            if not path.exists():
                return {"success": False, "error": f"Tiedostoa ei löydy: {file_path}"}
            code = path.read_text()
            language = self._detect_language(file_path)
        else:
            language = "python"

        if not code:
            return {"success": False, "error": "Ei koodia korjattavaksi"}

        max_iterations = 5
        current_code = code
        history = []

        for iteration in range(max_iterations):
            result = await self.execute_code(current_code, language=language)

            if result.get("success"):
                self.bugs_fixed += 1
                if file_path:
                    Path(file_path).expanduser().write_text(current_code)

                await self.memory.store_memory(
                    content=f"Bugi korjattu ({iteration + 1} iteraatiota): "
                            f"{'tiedosto ' + file_path if file_path else 'snippet'}",
                    agent_id=self.id,
                    memory_type="insight",
                    importance=0.7
                )

                return {
                    "success": True, "fixed_code": current_code,
                    "iterations": iteration + 1,
                    "output": result.get("stdout", ""), "history": history
                }

            error = result.get("stderr", "") or result.get("error", "Tuntematon virhe")
            history.append({"iteration": iteration + 1, "error": error[:500]})

            prompt = f"""Korjaa seuraava {language}-koodi.

```{language}
{current_code}
```

VIRHE:
```
{error[:2000]}
```

Vastaa VAIN korjatulla koodilla:
```{language}
[korjattu koodi]
```"""

            response = await self.llm.generate(prompt, system=HACKER_SYSTEM_PROMPT)
            fixed = self._extract_code(response.content, language)

            if fixed and fixed != current_code:
                current_code = fixed
            else:
                break

        return {
            "success": False, "last_code": current_code,
            "iterations": max_iterations,
            "last_error": history[-1]["error"] if history else "Tuntematon",
            "history": history
        }

    async def improve_openclaw(self, target_file: str = None,
                                improvement_goal: str = "") -> dict:
        """Paranna OpenClaw-järjestelmän omaa koodia."""
        if target_file:
            files_to_analyze = [target_file]
        else:
            files_to_analyze = sorted(
                str(f.relative_to(self.project_root))
                for f in self.project_root.rglob("*.py")
                if "__pycache__" not in str(f)
            )

        results = []
        for filepath in files_to_analyze[:10]:
            try:
                read = self.read_project_file(filepath)
                if not read["success"]:
                    continue
                code = read["content"]
                if len(code) < 50:
                    continue

                analysis = await self.analyze_code(code=code, file_path=filepath)
                results.append({
                    "file": filepath,
                    "analysis": analysis[:500],
                    "improved": False
                })
            except Exception as e:
                results.append({"file": filepath, "error": str(e)})

        summary = f"Analysoitu {len(results)} tiedostoa."

        await self.memory.store_memory(
            content=f"OpenClaw-katsaus: {summary}",
            agent_id=self.id,
            memory_type="reflection",
            importance=0.8
        )

        return {
            "summary": summary,
            "files_analyzed": len(results),
            "details": results
        }

    # ── Utility Methods ─────────────────────────────────────────

    def _extract_code(self, text: str, language: str = "") -> Optional[str]:
        """Poimi koodi LLM-vastauksesta."""
        patterns = [
            rf"```{language}\s*\n(.*?)```",
            r"```\w*\s*\n(.*?)```",
            r"```\n(.*?)```",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                return match.group(1).strip()

        lines = text.strip().split("\n")
        if lines and (lines[0].startswith("import ") or
                      lines[0].startswith("def ") or
                      lines[0].startswith("class ") or
                      lines[0].startswith("#!")):
            return text.strip()

        return None

    def _detect_language(self, file_path: str) -> str:
        """Tunnista ohjelmointikieli tiedostopäätteestä."""
        ext = Path(file_path).suffix.lower()
        return {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".sh": "bash", ".html": "html", ".css": "css",
            ".sql": "sql", ".rs": "rust", ".go": "go",
        }.get(ext, "python")

    def _safety_check(self, code: str) -> dict:
        """Tarkista koodin turvallisuus ennen suoritusta."""
        dangerous_patterns = [
            (r"rm\s+-rf\s+/", "Yrittää poistaa juurihakemiston"),
            (r":(){ :\|:& };:", "Fork-pommi havaittu"),
            (r"dd\s+if=.*of=/dev/", "Levy-ylikirjoitus havaittu"),
            (r"mkfs\.", "Levyn formatointi havaittu"),
            (r">\s*/dev/sd", "Suora kirjoitus levylle"),
            (r"chmod\s+-R\s+777\s+/", "Vaaralliset oikeudet juuressa"),
            (r"curl.*\|\s*bash", "Remote code execution"),
            (r"wget.*\|\s*sh", "Remote code execution"),
            (r"eval\s*\(\s*base64", "Obfuskoitu koodi"),
        ]

        for pattern, reason in dangerous_patterns:
            if re.search(pattern, code, re.IGNORECASE):
                return {"safe": False, "reason": reason}

        return {"safe": True}

    def get_stats(self) -> dict:
        """Laajat tilastot HackerAgentille."""
        base = super().get_stats()
        base.update({
            "bugs_fixed": self.bugs_fixed,
            "lines_written": self.lines_written,
            "improvements_made": self.improvements_made,
            "executions": len(self.execution_history),
            "files_modified": len(self.files_modified),
            "success_rate": (
                sum(1 for e in self.execution_history if e["success"]) /
                max(len(self.execution_history), 1) * 100
            ),
            "workspace": str(self.workspace)
        })
        return base
