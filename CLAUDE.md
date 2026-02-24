# WaggleDance Swarm AI — Consciousness Architecture v3.0 🐝
# Master Reference Document for Claude Code

## WHO IS THE DEVELOPER
Jani Korpi / JKH Service (Y-tunnus: 2828492-2), Finnish beekeeper with ~300 colonies.
Location: Kuopio/Helsinki/Kouvola, Finland.
Primary language: Finnish. All user-facing output MUST be in Finnish.

## PROJECT OVERVIEW
WaggleDance is a local-first multi-agent AI system for beekeeping (mehiläishoito).
50+ specialized agents (tarhaaja, tautivahti, meteorologi, hortonomi, tutkija, etc.)
communicate through a HiveMind orchestrator with translation pipeline and consciousness layer.

Version: v0.0.3 → upgrading to v0.1.0 (consciousness rewrite)

## HARDWARE: RTX A2000 8GB VRAM
This is the ONLY hardware. Every design decision must fit within 8GB.
```
GPU ALLOCATION (6 models simultaneously):
  phi4-mini          2.5G   Chat responses (English internally)
  llama3.2:1b        0.7G   Heartbeat/learning/consolidation (ALL background)
  nomic-embed-text   0.3G   SEARCH embeddings (ChromaDB retrieval)
  all-minilm         0.2G   EVAL embeddings (hallucination, dedup) ← NEW
  Opus-MT FI→EN      0.3G   Translation in (HuggingFace PyTorch)
  Opus-MT EN→FI      0.3G   Translation out
  TOTAL:             4.3G / 8.0G (54%) | FREE: 3.7G

OLLAMA_MAX_LOADED_MODELS=4
OLLAMA_KEEP_ALIVE=24h
```

## CORE ARCHITECTURE
```
User (Finnish) → HiveMind
  ├─ PRIORITY GATE: Chat? → ALL GPU to chat, pause learning
  ├─ SmartRouter (confidence-based):
  │   >0.90 → ChromaDB direct (0ms, no LLM)
  │   >0.70 → llama1b + context (500ms)
  │   >0.50 → phi4-mini + context (3000ms)
  │   web   → llama1b + web results (1500ms)
  │   none  → Active Learning: ask user
  ├─ Translation: Opus-MT chat (quality) / dict heartbeat (speed)
  ├─ Consciousness Layer:
  │   ├─ MathSolver pre-filter (0ms, no LLM)
  │   ├─ ChromaDB vector memory (persistent)
  │   ├─ Dual embedding: nomic (search) + minilm (eval)
  │   ├─ Batch operations (embed, translate, store)
  │   ├─ Hallucination detection (contrastive + keyword + embedding)
  │   ├─ Active Learning (ask when uncertain)
  │   ├─ Agent levels 1-5 (earned by performance)
  │   └─ Embedding augmentation (domain synonyms)
  ├─ Swarm Learning (background):
  │   ├─ YAML Scanner: bulk import (no LLM, embed only, 600 facts/30s)
  │   ├─ Heartbeat: guided tasks via llama1b ONLY
  │   ├─ Round Table: 6 agents discuss + cross-validate + synthesize
  │   ├─ Batch Pipeline: GPU at 60-80% utilization
  │   ├─ Theater Pipe: batch hidden, stream results with 300ms gaps
  │   └─ Night Mode: aggressive learning when user idle
  ├─ Adaptive Throttle:
  │   ├─ Startup benchmark (optimal batch sizes for THIS hardware)
  │   ├─ Runtime VRAM monitor + user activity detection
  │   ├─ Chat active → pause learning
  │   └─ Duplicate high → slow down / Novel high → speed up
  └─ Dashboard: FastAPI + WebSocket (live stream)
```

## KEY DESIGN PRINCIPLES

### 1. Speed + Memory Replaces Model Size
phi4-mini (3.8B) with good memory outperforms 32B without memory.
LLM role shrinks over time: 100% day1 → 15% month6 as memory grows.
llama1b handles 80% of responses (memory lookup + formatting).

### 2. Batch Background, Stream Foreground (Theater Pipe)
GPU does batch work invisibly (6 agents in 2 seconds).
Dashboard shows results one-by-one with 300ms delays.
Looks like agents chatting in real-time.

### 3. Cross-Validation Over Model Intelligence
6 agents checking each other > 1 large model guessing alone.
Round Table consensus (confidence 0.85) > single answer (0.50).

### 4. Chat Always Wins (Priority Lock)
User types → ALL background pauses → phi4-mini answers → learning resumes.

### 5. Learn From Everything
YAML files (no LLM, just embed), conversations, corrections,
heartbeats (guided tasks), Round Tables, web search, night scanning.

## EXISTING WORKING CODE (PRESERVE)

### hivemind.py (~1400 lines) — EXTEND, don't rewrite
- HiveMind class with chat(), _delegate_to_agent()
- Heartbeat: _agent_proactive_think(), _master_generate_insight()
- AdaptiveThrottle (EXTEND with batch benchmark)
- WebSocket: _notify_ws()
- Translation: self.translation_proxy

### consciousness.py (~500 lines, v2) — REWRITE to v3
Working: MathSolver, ChromaDB, nomic-embed, embedding cache
Broken: hallucination detection (false positives, wrong weights)
Missing: batch ops, dual embed, smart router, round table, agent levels

### translation_proxy.py (~400 lines) — ADD batch methods
Working: fi_to_en(), en_to_fi() with force_opus
Missing: batch_fi_to_en(), batch_en_to_fi()
CRITICAL: Returns TranslationResult objects — always use .text attribute

### Other: core/yaml_bridge.py, core/llm_provider.py, core/en_validator.py,
web/dashboard.py, configs/settings.yaml, main.py

## BUILD PLAN — 4 PHASES

### PHASE 1: Foundation (do this FIRST, test before continuing)
Files: consciousness.py (rewrite), hivemind.py (extend), tools/scan_knowledge.py (new)

1a. YAML Knowledge Scanner (tools/scan_knowledge.py):
    - Parse ALL yaml in knowledge/ and agents/ directories
    - Extract facts as natural language Finnish sentences
    - Store via consciousness.learn() — NO LLM, just embed+store
    - Track progress in data/scan_progress.json (idempotent)
    - Target: 600 facts in <5 minutes
    - Auto-run on first startup if consciousness.memory.count < 100

1b. Smart Router (in consciousness.py or hivemind.py):
    - Confidence-based model selection (see architecture above)
    - >0.90 validated → direct answer, no LLM
    - >0.70 → llama1b formats answer with context
    - >0.50 → phi4-mini with full context
    - <0.50 → active learning ("En tiedä, kerro minulle?")

1c. Split Heartbeat Roles (hivemind.py):
    - phi4-mini: ONLY user chat (never heartbeat)
    - llama3.2:1b: ALL heartbeat/learning/consolidation
    - Modify heartbeat loop to always use llama1b model

1d. Chat Priority Lock (hivemind.py):
    - PriorityLock class with chat_enters/chat_exits/batch_checkpoint
    - Chat request → set flag → batch workers pause at checkpoints
    - Chat done → clear flag → workers resume

1e. Fix Hallucination Detection (consciousness.py):
    - Weights: 0.3 * embedding_similarity + 0.7 * keyword_overlap
    - Threshold: 0.45 (was 0.30)
    - Empty q_words → return 0.0 (was 1.0 — free pass bug)
    - Hard gate: overlap==0 AND similarity<0.65 → suspicious

1f. Seed ChromaDB (automatic):
    - Run scan_knowledge.py on first startup
    - Ensure data/chroma_db/ has domain knowledge

TEST PHASE 1:
  python consciousness.py  → math 9/9, hallucination 4/4, search 4/5+
  Ask: "mikä on varroa-kynnys" → correct answer from memory
  Ask: "laske 2+2" → "4" (0ms, math pre-filter)
  Ask: "kauanko kuningatar elää" → answer from YAML knowledge

### PHASE 2: Batch Pipeline (build on Phase 1)
Files: consciousness.py, translation_proxy.py, hivemind.py

2a. Batch Embedding (consciousness.py):
    - embed_batch(texts, mode) → Ollama /api/embed with list input
    - eval_embed_batch(texts) → all-minilm batch
    - dual_embed_batch(texts) → both models via asyncio.to_thread
    - Max batch: 50 (chunk larger)

2b. Batch Translation (translation_proxy.py):
    - batch_fi_to_en(texts) → HuggingFace batch tokenize+generate
    - batch_en_to_fi(texts) → same
    - Apply dict corrections to each result
    - Max batch: 20 (8GB memory constraint)

2c. Learn Queue with Batch Flush (consciousness.py):
    - self._learn_queue: list of (text, metadata) tuples
    - learn() appends to queue
    - _flush_learn_queue() when queue >= 10: batch translate, embed, store
    - flush() at shutdown or explicit call

2d. ChromaDB Batch Insert:
    - collection.add(ids, embeddings, documents, metadatas) — one call
    - Pre-compute vectors, then single insert

2e. Adaptive Throttle Benchmark (extend existing AdaptiveThrottle):
    - On startup: benchmark embed batch 1/5/10/20/50
    - Benchmark translate batch 1/5/10/20
    - Benchmark LLM parallel 1/2/3/4/6
    - Store optimal values: self.optimal = {embed_batch, translate_batch, llm_parallel}
    - Runtime: adjust based on VRAM, user activity, duplicate rate

2f. Dual Embedding Setup:
    - Auto-pull all-minilm if not installed
    - self.eval_embed using all-minilm (symmetric similarity)
    - Use for: hallucination check, dedup, future clustering
    - Warmup both models at startup

TEST PHASE 2:
  600 YAML facts imported in <30 seconds (was >3 minutes)
  Embedding batch 10: <400ms (was 3300ms)
  Startup benchmark prints optimal batch sizes
  all-minilm responding for eval tasks

### PHASE 3: Social Learning (build on Phase 2)
Files: consciousness.py, hivemind.py, core/agent_levels.py (new), web/dashboard.py

3a. Round Table Protocol (hivemind.py):
    - 6 agents selected by topic relevance + rotation
    - Each agent sees previous agents' responses (sequential context)
    - Consolidator (Queen) synthesizes consensus
    - Store as high-confidence wisdom (0.85)
    - Run every 20th heartbeat cycle (≈ once per hour)

3b. Theater Pipe (hivemind.py):
    - Round Table computed in batch (all 6 answers at once internally)
    - Results streamed to dashboard with 300ms gaps
    - Looks like live conversation
    - Quality indicator per message: ✅ validated / ⚠️ unchecked / ❌ flagged

3c. Agent Level System (core/agent_levels.py):
    Level 1 NOVICE:      Memory-only, all checked, max 200 tok
    Level 2 APPRENTICE:  +LLM+memory, can read swarm_facts
      Earn: 50 correct, halluc <15%, trust >0.3
    Level 3 JOURNEYMAN:  +write swarm_facts, consult 1 agent
      Earn: 200 correct, halluc <8%, trust >0.6
    Level 4 EXPERT:      +consult 3 agents, web search, skills
      Earn: 500 correct, halluc <3%, trust >0.8
    Level 5 MASTER:      Full autonomy, teach others
      Earn: 1000 correct, halluc <1%, trust >0.95
    DEMOTION: halluc exceeds threshold over 50-window → drop 1 level
    Stats in ChromaDB "agent_stats" collection

3d. Guided Heartbeat Tasks (consciousness.py LearningTaskQueue):
    Priority: 1) unread YAML sections, 2) low-coverage topics,
    3) recent user questions with low confidence, 4) cross-agent gaps,
    5) seasonal topics, 6) random exploration
    Each heartbeat gets specific task, not "think about anything"

3e. Swarm Facts Collection:
    - Shared ChromaDB collection "swarm_facts"
    - Level 3+ agents can write validated knowledge
    - All agents can read for cross-domain context

3f. Night Mode (hivemind.py):
    - Detect user idle >30 minutes
    - Heartbeat interval → 10-15 seconds
    - Focus: systematic YAML scan, consolidation every 100 facts
    - Track progress in data/learning_progress.json
    - Stop when: all files processed OR user returns OR 8 hours

3g. Dashboard Round Table View (web/dashboard.py):
    - WebSocket event "round_table_start/insight/end"
    - Display as conversation thread with agent icons
    - Show synthesis separately with 💾 stored indicator

TEST PHASE 3:
  Round Table produces cross-validated wisdom
  Agent levels increment after correct responses
  Night mode processes knowledge files systematically
  Dashboard shows live Round Table conversations

### PHASE 4: Advanced Learning (build on Phase 3)
Files: consciousness.py, hivemind.py, tools/distill_from_opus.py (new)

4a. Contrastive Learning:
    - Detect user correction: "ei", "väärin", "wrong", or correction text
    - Store in "corrections" collection: {query, bad_answer, good_answer, agent_id}
    - On future similar query: inject "Previously wrong: X. Correct: Y."
    - Agent trust -= 0.05 on correction

4b. Active Learning:
    - Memory search best score < 0.50 → don't let LLM hallucinate
    - Return: "En ole varma. Tiedätkö mikä on [topic]?"
    - Detect user teaching (previous was active_learning + current is informative)
    - Store with confidence=0.9, validated=True, source="user_teaching"
    - Respond: "Kiitos! Opin juuri: [fact]. Muistan tämän jatkossa."

4c. Embedding Augmentation:
    - On learn(): load dict_fi_en.json
    - Append English synonyms to Finnish domain terms
    - "toukkamätä" → embed as "Foulbrood | AFB | Paenibacillus larvae"
    - Stronger vectors for domain-specific search

4d. Multi-hop RAG:
    - First search → top 3 results
    - Extract entities from results
    - Second search with new entities → top 2 per entity
    - Deduplicate and rank → return top 5
    - Only activate when first search confidence < 0.7
    - Max 2 hops, cache intermediate results

4e. Episodic Memory (ChromaDB "episodes" collection):
    - Each conversation turn stored as episode
    - Linked by session_id and prev_episode_id
    - When similar topic found: return entire conversation chain
    - Quality score and resolution status per episode

4f. Seasonal/Curriculum Learning:
    - SEASONAL_BOOST dict: month → relevant Finnish+English keywords
    - Search results matching seasonal keywords get 1.2x score boost
    - Jan: talvehtiminen, Feb: kevättarkastus, Jul: linkous, Sep: varroa

4g. Knowledge Distillation Prep (tools/distill_from_opus.py):
    - Collect failed queries from data/failed_queries.jsonl
    - Format as prompts for expert answers
    - Import function: load expert answers into ChromaDB (confidence=0.95)
    - Pipeline ready, actual API call left as placeholder

4h. Feedback Dashboard Endpoint:
    - GET /api/consciousness → JSON stats
    - memory_count, cache_hit_rate, hallucination_rate
    - agent_levels, recent_corrections, seasonal_focus
    - learning_trend, top_queries, weakest_agent

TEST PHASE 4:
  User correction → stored, prevents same mistake
  Active learning → asks user, stores answer
  Full system running 24/7 autonomously
  Dashboard shows comprehensive learning metrics

## PHASE 1 COMPLETE (2026-02-23)
Files modified: consciousness.py, hivemind.py, main.py, start.bat
Files created: tools/scan_knowledge.py

1a. Hallucination fix: weights 0.3/0.7, threshold 0.45, empty→0.0, hard gate (3/4 pass)
1b. Smart Router: 4-tier confidence routing (memory_direct >0.90, memory_fast >0.70, memory_context >0.50, none)
1c. Heartbeat split: all background uses self.llm_heartbeat (no fallback to phi4-mini)
1d. PriorityLock: chat_enters/chat_exits/wait_if_chat integrated in chat() and _guarded()
1e. YAML Scanner: 5522 facts from 103 files, --dry-run/--count flags, idempotent
1f. Auto-seed: triggers on startup if memory.count < 100
1g. OLLAMA_KEEP_ALIVE=24h, OLLAMA_MAX_LOADED_MODELS=4 in main.py + start.bat

## PHASE 3 COMPLETE (2026-02-24)
Files modified: consciousness.py, hivemind.py, web/dashboard.py, configs/settings.yaml
Files created: core/agent_levels.py

3a. AgentLevelManager: L1-L5 levels, ChromaDB "agent_stats" collection, promotion/demotion, trust EMA
3b. LearningTaskQueue: guided heartbeat tasks (unread YAML, low-confidence, seasonal, random)
3c. SEASONAL_BOOST: month→keywords for 12 months (FI+EN)
3d. Swarm facts collection: ChromaDB "swarm_facts" in MemoryStore
3e. Round Table: 6 agents discuss → Queen synthesizes → confidence 0.85 → store
3f. Theater Pipe: 300ms streaming to dashboard via WebSocket
3g. Night Mode: user idle >30min → 10-15s interval, guided learning, max 8h
3h. Dashboard: Round Table card, L1-L5 badges, night mode indicator, /api/agent_levels
3i. Heartbeat integration: Round Table every 20th cycle, guided tasks for proactive think
3j. Agent level recording in _delegate_to_agent() and _round_table()
3k. settings.yaml: agent_levels, round_table, night_mode config sections

## CRITICAL RULES
1. All LLM internally in ENGLISH — Finnish only for user I/O
2. Opus-MT for translation (force_opus=True for chat)
3. nomic-embed-text: "search_document:" / "search_query:" prefixes
4. all-minilm: NO prefix (symmetric model)
5. ChromaDB cosine distance, score = 1.0 - (dist / 2.0)
6. TranslationResult.text — never concatenate TranslationResult directly
7. phi4-mini ONLY for chat — never heartbeat/learning
8. llama3.2:1b for ALL background — fast and cheap
9. Batch where possible — GPU likes batches not single calls
10. Chat ALWAYS wins — PriorityLock pauses all background work
11. UTF-8 everywhere — Finnish characters (ä, ö, å)
12. Windows 11 compatibility
13. Test every phase before proceeding to next
14. Log everything — learning_metrics.jsonl for analysis
