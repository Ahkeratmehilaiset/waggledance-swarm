#!/usr/bin/env python3
"""
OpenClaw v1.4 — Build Script (Windows-yhteensopiva)
=====================================================
Korvaa build.sh — toimii suoraan: python build.py

Vaiheet:
  1. Generoi 50 agenttia (batch 1-7)
  2. Validoi perustaso
  3. Depth patch (metriikat + kaudet)
  4. Schema normalize (yhtenäinen 11-avaimen schema)
  5. Strict-validointi
  6. Kompiloi markdown + YAML zip
  7. Generoi finetuning JSONL
"""

import subprocess
import sys
import os
from pathlib import Path

# Värit Windowsissa
try:
    os.system("")  # Enable ANSI on Windows
except:
    pass

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"

def run(desc: str, cmd: list[str]) -> bool:
    """Aja komento ja näytä tulos."""
    print(f"\n{BLUE}{'─'*50}{RESET}")
    print(f"{BLUE}▶ {desc}{RESET}")
    print(f"  {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        print(f"{RED}❌ EPÄONNISTUI: {desc}{RESET}")
        return False
    print(f"{GREEN}✅ {desc}{RESET}")
    return True


def main():
    print(f"""
{GREEN}╔══════════════════════════════════════════╗
║   OpenClaw v1.4 — Full Build (Python)    ║
╚══════════════════════════════════════════╝{RESET}
""")

    # Varmista että olemme projektin juuressa
    if not Path("tools").exists():
        print(f"{RED}❌ tools/-kansiota ei löydy! Aja projektin juuresta.{RESET}")
        sys.exit(1)

    py = sys.executable  # Käytä samaa Pythonia kuin tämä skripti

    # Luo output-kansio
    Path("output").mkdir(exist_ok=True)
    Path("agents").mkdir(exist_ok=True)

    steps = [
        # 1-7: Generoi agentit
        ("Batch 1: Agentit 1-3 (core, luontokuvaaja, ornitologi)", [py, "tools/gen_batch1.py"]),
        ("Batch 2: Agentit 4-6 (riista, hortonomi, metsä)", [py, "tools/gen_batch2.py"]),
        ("Batch 3: Agentit 7-10 (fenologi, tuholais, entomologi, tähtitiede)", [py, "tools/gen_batch3.py"]),
        ("Batch 4: Agentit 11-18 (mehiläis-spesialistit)", [py, "tools/gen_batch4.py"]),
        ("Batch 5: Agentit 19-28 (vesi, sää, maaperä)", [py, "tools/gen_batch5.py"]),
        ("Batch 5b: Agentit 29-30 (sähkö, LVI) — korjatut", [py, "tools/gen_batch5b.py"]),
        ("Batch 6: Agentit 31-39 (rakennus, turva)", [py, "tools/gen_batch6.py"]),
        ("Batch 7: Agentit 40-50 (ruoka, viihde, logistiikka, tiede)", [py, "tools/gen_batch7.py"]),

        # Validointi
        ("Perusvalidointi", [py, "tools/validate.py"]),

        # Depth patch
        ("Depth patch (metriikat + kaudet + padding)", [py, "tools/depth_patch.py"]),

        # Schema normalize
        ("Schema normalize (PROCESS_FLOWS + KNOWLEDGE_TABLES + SOURCE_REGISTRY)", [py, "tools/normalize_schema.py"]),

        # Strict validation
        ("Strict-validointi (M≥5 A≥3 N≥3 S=4 F≥2 Q=40)", [py, "tools/validate_strict.py"]),

        # Compile
        ("Kompiloi markdown-raportit", [py, "tools/compile_final.py"]),

        # Finetuning
        ("Generoi finetuning JSONL (1500 QA-paria)", [py, "tools/gen_finetuning.py"]),
    ]

    failed = []
    for i, (desc, cmd) in enumerate(steps, 1):
        print(f"\n{YELLOW}[{i}/{len(steps)}]{RESET}", end="")
        if not run(desc, cmd):
            failed.append(desc)
            # Jatka silti (paitsi jos batch-generointi epäonnistuu)
            if "Batch" in desc:
                print(f"{RED}Kriittinen virhe — keskeytetään.{RESET}")
                sys.exit(1)

    # YAML zip
    print(f"\n{BLUE}▶ Pakkaa YAML-agentit...{RESET}")
    try:
        import zipfile
        yaml_zip = Path("output/openclaw_50agents_yaml.zip")
        with zipfile.ZipFile(yaml_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for d in sorted(os.listdir("agents")):
                for fname in ["core.yaml", "sources.yaml"]:
                    fpath = Path(f"agents/{d}/{fname}")
                    if fpath.exists():
                        zf.write(fpath, f"{d}/{fname}")
        print(f"{GREEN}✅ {yaml_zip} ({yaml_zip.stat().st_size // 1024} KB){RESET}")
    except Exception as e:
        print(f"{YELLOW}⚠️  YAML zip: {e}{RESET}")

    # Yhteenveto
    print(f"""
{GREEN}{'═'*50}
BUILD VALMIS
{'═'*50}{RESET}
""")

    if failed:
        print(f"{RED}Epäonnistuneet vaiheet:{RESET}")
        for f in failed:
            print(f"  {RED}❌ {f}{RESET}")
    else:
        # Laske tilastot
        agent_count = len([d for d in os.listdir("agents")
                          if (Path("agents") / d / "core.yaml").exists()])
        jsonl = Path("output/finetuning_data.jsonl")
        md = Path("output/openclaw_50agents_complete.md")
        print(f"  Agentit:    {agent_count}/50")
        print(f"  JSONL:      {jsonl.stat().st_size // 1024} KB" if jsonl.exists() else "  JSONL: puuttuu")
        print(f"  Markdown:   {md.stat().st_size // 1024} KB" if md.exists() else "  Markdown: puuttuu")
        print(f"\n  {GREEN}✅ Kaikki vaiheet onnistuivat!{RESET}")


if __name__ == "__main__":
    main()
