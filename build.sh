#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# OpenClaw v1.4 — 50-Agent Knowledge Base: BUILD ALL
# ═══════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")"

echo "═══ OpenClaw v1.4 Build ═══"
echo ""

# 1. Varmista PyYAML
python3 -c "import yaml" 2>/dev/null || {
    echo "⚠️  PyYAML puuttuu, asennetaan..."
    pip install pyyaml --break-system-packages -q
}

# 2. Tyhjennä vanhat
rm -rf agents/ output/
mkdir -p agents output tools

# 3. Generoi agentit
echo "📦 Batch 1: Agentit 1-3"
python3 tools/gen_batch1.py
echo "📦 Batch 2: Agentit 4-6"
python3 tools/gen_batch2.py
echo "📦 Batch 3: Agentit 7-10"
python3 tools/gen_batch3.py
echo "📦 Batch 4: Agentit 11-18"
python3 tools/gen_batch4.py
echo "📦 Batch 5: Agentit 19-28"
python3 tools/gen_batch5.py
echo "📦 Batch 5b: Agentit 29-30"
python3 tools/gen_batch5b.py
echo "📦 Batch 6: Agentit 31-39"
python3 tools/gen_batch6.py
echo "📦 Batch 7: Agentit 40-50"
python3 tools/gen_batch7.py

# 4. Perusvalidointi
echo ""
echo "🔍 Perusvalidointi..."
python3 tools/validate.py

# 5. Syvyyskorjaus
echo ""
echo "🔧 Depth patch..."
python3 tools/depth_patch.py

# 5b. Schema-normalisointi (yhtenäinen avainjoukko)
echo ""
echo "📐 Schema normalize..."
python3 tools/normalize_schema.py

# 6. Strict-validointi
echo ""
echo "🔍 STRICT-validointi..."
python3 tools/validate_strict.py

# 7. Kompiloi
echo ""
echo "📄 Kompiloidaan..."
python3 tools/compile_final.py

# 8. Pakkaa YAML
echo ""
echo "📦 Pakataan YAML:t..."
cd agents && zip -r ../output/openclaw_50agents_yaml.zip */core.yaml */sources.yaml -q && cd ..

# 9. Finetuning JSONL
echo ""
echo "📊 Generoidaan finetuning JSONL..."
python3 tools/gen_finetuning.py

echo ""
echo "═══════════════════════════════════════"
echo "✅ BUILD VALMIS"
echo "═══════════════════════════════════════"
echo ""
echo "Tuotokset:"
ls -lh output/
