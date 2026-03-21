PHASE 2 PROMPT — BATCH PIPELINE
=================================
Give this to Claude Code AFTER Phase 1 is complete and tested.

---

Read CLAUDE.md for full context. You are implementing PHASE 2: Batch Pipeline.
Phase 1 is complete. Do not modify Phase 1 code unless fixing a bug.

## TASK 2a: Batch Embedding (consciousness.py)

Add batch embedding methods:

embed_batch(texts: list[str], mode="document"|"query") -> list[list[float]]:
  Uses Ollama /api/embed endpoint with list input:
  POST http://localhost:11434/api/embed
  {"model": "nomic-embed-text", "input": ["search_document: text1", "search_document: text2", ...]}
  Returns all vectors in one GPU call. Max batch: 50, chunk larger.
  Check embedding cache first — skip cached texts.

eval_embed_batch(texts: list[str]) -> list[list[float]]:
  Same but uses all-minilm model. NO prefix (symmetric model).

dual_embed_batch(texts: list[str]) -> tuple[list, list]:
  Run both models concurrently via asyncio.to_thread:
  nomic_task = asyncio.to_thread(self.embed_batch, texts, "document")
  minilm_task = asyncio.to_thread(self.eval_embed_batch, texts)
  return await asyncio.gather(nomic_task, minilm_task)

## TASK 2b: Batch Translation (translation_proxy.py)

Add batch methods to the existing translation proxy:

batch_fi_to_en(texts: list[str]) -> list[str]:
  HuggingFace batch: tokenizer(texts, padding=True, return_tensors="pt").to(device)
  outputs = model.generate(**inputs)
  batch_decode → apply dict corrections to each
  Max batch: 20. Returns list of strings (not TranslationResult).

batch_en_to_fi(texts: list[str]) -> list[str]:
  Same for en→fi direction.

## TASK 2c: Learn Queue with Batch Flush (consciousness.py)

Modify learn() to queue and batch-flush:

self._learn_queue: list = []

def learn(self, text, **kwargs):
    self._learn_queue.append((text, kwargs))
    if len(self._learn_queue) >= self._batch_size:
        self._flush_learn_queue()

def _flush_learn_queue(self):
    texts = [t for t, k in self._learn_queue]
    metas = [k for t, k in self._learn_queue]
    # Batch translate FI→EN
    en_texts = self.opus.batch_fi_to_en(texts)
    # Batch embed (both models)
    nomic_vecs, minilm_vecs = self.dual_embed_batch(en_texts)
    # Batch dedup check using minilm_vecs
    # Batch ChromaDB insert
    self.memory.add(ids=[...], embeddings=nomic_vecs, documents=texts, metadatas=[...])
    self._learn_queue.clear()

def flush(self):
    if self._learn_queue:
        self._flush_learn_queue()

CRITICAL: Call flush() at shutdown and when queue must be emptied immediately.

## TASK 2d: Dual Embedding Setup (consciousness.py)

Add all-minilm as evaluation embedding model:
- Auto-pull on startup: subprocess.run(["ollama", "pull", "all-minilm"])
- self.eval_embed_model = "all-minilm"
- Warmup both models at startup
- Use eval_embed for: hallucination check (update check_hallucination to use it)
- Use eval_embed for: dedup check in learn

## TASK 2e: Adaptive Throttle Benchmark (extend AdaptiveThrottle in hivemind.py)

Add startup benchmark to existing AdaptiveThrottle:

async def benchmark_batch_sizes(self):
    """Run once at startup. Measures optimal sizes for THIS hardware."""
    for batch in [1, 5, 10, 20, 50]:
        time embed_batch(["test"] * batch)
    for batch in [1, 5, 10, 20]:
        time batch_fi_to_en(["testi"] * batch)
    for parallel in [1, 2, 3, 4, 6]:
        time asyncio.gather(*[generate("llama3.2:1b", "hi") for _ in range(parallel)])
    
    self.optimal_embed_batch = find_sweet_spot(embed_results)
    self.optimal_translate_batch = find_sweet_spot(translate_results)
    self.optimal_llm_parallel = find_sweet_spot(llm_results)
    
    log.info(f"Benchmark: embed={self.optimal_embed_batch}, "
             f"translate={self.optimal_translate_batch}, "
             f"llm_parallel={self.optimal_llm_parallel}")

Update scan_knowledge.py to use these optimal batch sizes.

## TASK 2f: Update scan_knowledge.py to Use Batching

Modify the YAML scanner to use batch operations:
- Collect 50 facts → batch_fi_to_en → batch_embed → batch ChromaDB insert
- Use optimal batch sizes from throttle benchmark
- Target: entire knowledge/ directory in <30 seconds

## FINAL TEST — PHASE 2
1. python consciousness.py → all previous tests still pass
2. python tools/scan_knowledge.py → completes in <30 seconds
3. Startup logs show benchmark results
4. Verify batch operations work: embed_batch, batch translate
Report as table. When all pass: "PHASE 2 COMPLETE"


================================================================
PHASE 3 PROMPT — SOCIAL LEARNING
================================================================
Give this to Claude Code AFTER Phase 2 is complete and tested.

---

Read CLAUDE.md. Implementing PHASE 3: Social Learning.
Phases 1-2 complete. Build on existing batch infrastructure.

## TASK 3a: Agent Level System (new file: core/agent_levels.py)

Create AgentLevelManager class. Stores stats in ChromaDB "agent_stats" collection.

Per agent: {agent_id, level, total_responses, correct_responses,
  hallucination_count, user_corrections, trust_score,
  last_promoted, last_demoted, specialties[]}

Levels 1-5 as defined in CLAUDE.md.
check_promotion(agent_id) → bool (promotes if criteria met)
check_demotion(agent_id) → bool (demotes if hallucination too high)
record_response(agent_id, was_correct, was_hallucination) → updates stats

Integrate: call record_response after every agent answer in hivemind.py

## TASK 3b: Round Table Protocol (hivemind.py)

New method: async def _round_table(self, topic, agent_count=6)

Phase 1 — Selection: Pick agents by topic relevance + level (higher = priority)
Phase 2 — Discussion: Each agent gets prompt with previous agents' responses
Phase 3 — Synthesis: Queen agent summarizes consensus
Phase 4 — Storage: Store as wisdom (confidence=0.85, source="round_table")

Use llama3.2:1b for all agents (fast).
Run every 20th heartbeat cycle.

## TASK 3c: Theater Pipe (hivemind.py)

Round Table computed in batch (all prompts prepared, sent efficiently).
Results queued and streamed to dashboard with 300ms delays:
await self._notify_ws("round_table", {agent, icon, text, round})
await asyncio.sleep(0.3)

## TASK 3d: Guided Heartbeat Tasks (consciousness.py)

LearningTaskQueue class:
  next_task() returns specific learning task:
  Priority: 1) unread YAML, 2) low-coverage topics, 3) recent user Qs,
  4) cross-agent gaps, 5) seasonal, 6) random

  Each heartbeat: agent gets task in prompt instead of "think freely"

## TASK 3e: Night Mode (hivemind.py)

Detect user idle >30 min (no WebSocket messages).
Switch to aggressive learning:
- Interval: 10 seconds
- Guided tasks: systematic YAML scanning
- Consolidation every 100 new facts
- Progress: data/learning_progress.json
- Stop: all processed OR user returns OR 8 hours

## TASK 3f: Dashboard Updates (web/dashboard.py)

WebSocket events for Round Table:
- "round_table_start": {topic, agents}
- "round_table_insight": {agent, icon, text, quality}
- "round_table_end": {topic, wisdom_stored}

Agent levels visible in dashboard.

## FINAL TEST — PHASE 3
1. python main.py → heartbeat runs with guided tasks
2. Wait for Round Table trigger → 6 agents discuss in dashboard
3. Agent levels increment after correct responses
4. Night mode activates when idle >30 min
Report as table. When all pass: "PHASE 3 COMPLETE"


================================================================
PHASE 4 PROMPT — ADVANCED LEARNING
================================================================
Give this to Claude Code AFTER Phase 3 is complete and tested.

---

Read CLAUDE.md. Implementing PHASE 4: Advanced Learning.
Phases 1-3 complete. This is the final phase.

## TASK 4a: Contrastive Learning
Detect user corrections. Store in "corrections" collection.
On future similar query: inject correction context into LLM prompt.
Update agent trust on correction.

## TASK 4b: Active Learning
Low confidence (<0.50) → ask user instead of hallucinating.
Detect user teaching → store as high-confidence validated fact.
Respond with confirmation: "Kiitos! Opin juuri: [fact]"

## TASK 4c: Embedding Augmentation
On learn(): append English synonyms from dict_fi_en.json.
Domain terms get richer embeddings.

## TASK 4d: Multi-hop RAG
First search → extract entities → second search → deduplicate → top 5.
Only when first search < 0.70. Max 2 hops.

## TASK 4e: Episodic Memory
Store conversation turns as linked episodes.
Search returns entire conversation chains when relevant.

## TASK 4f: Seasonal Learning
Month-based keyword boosting (1.2x) in search results.
See SEASONAL_BOOST dict in CLAUDE.md.

## TASK 4g: Knowledge Distillation Prep
Collect failed queries → data/failed_queries.jsonl
Import function for expert answers → ChromaDB confidence=0.95

## TASK 4h: Feedback Dashboard
GET /api/consciousness → comprehensive stats JSON.
memory_count, cache_hit_rate, hallucination_rate, agent_levels, etc.

## FINAL TEST — PHASE 4 (COMPREHENSIVE)
Run a full integration test that exercises ALL features:
1. YAML scan → verify fact count
2. Chat with memory → verify smart routing
3. Correct an answer → verify contrastive learning stores it
4. Ask unknown question → verify active learning asks
5. Wait for Round Table → verify cross-validation
6. Check agent levels → verify promotion logic
7. Trigger night mode → verify accelerated learning
8. Check /api/consciousness → verify all stats
9. Memory stress test: learn 500 facts rapidly, verify no crashes
10. Verify embeddings: cache hit rate, batch performance

Report comprehensive results.
When all pass: "PHASE 4 COMPLETE — v0.1.0 READY"

Update CLAUDE.md with final status and performance metrics.
