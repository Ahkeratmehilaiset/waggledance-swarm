PHASE 1 PROMPT FOR CLAUDE CODE
================================
Copy-paste this into Claude Code after replacing CLAUDE.md.

---

Read CLAUDE.md thoroughly — it contains the complete architecture for v0.1.0.

You are implementing PHASE 1: Foundation. This is the highest-impact, lowest-risk phase.
Work through each task sequentially. Test after each task. Do not proceed if tests fail.

IMPORTANT RULES:
- Read existing code before modifying. Understand the current structure.
- phi4-mini is ONLY for user chat. llama3.2:1b for ALL background tasks.
- TranslationResult objects from translation_proxy: always use .text attribute
- nomic-embed-text needs "search_document:" and "search_query:" prefixes
- UTF-8 encoding everywhere
- Log every change you make

## TASK 1: Fix Hallucination Detection (consciousness.py)

Read consciousness.py, find the check_hallucination method.
Fix these bugs:
- Empty q_words after stopword removal returns 1.0 (should return 0.0)
- Weights should be: 0.3 * similarity + 0.7 * keyword_overlap (keyword is primary signal)
- Threshold should be 0.45 (was 0.30)
- Add hard gate: overlap==0.0 AND similarity<0.65 → always suspicious

Test: python consciousness.py
Expected: hallucination tests improve to 3/4 or 4/4

## TASK 2: Split Heartbeat to llama1b Only (hivemind.py)

Read hivemind.py heartbeat methods: _agent_proactive_think(), _master_generate_insight(), _idle_research()
Change ALL heartbeat LLM calls to use "llama3.2:1b" model explicitly.
phi4-mini must NEVER be used for heartbeat — only for user chat responses.

This is critical: if heartbeat uses phi4-mini, it blocks chat and causes 5+ second latency.

Test: Start python main.py, verify in logs that heartbeat uses llama3.2:1b

## TASK 3: Chat Priority Lock (hivemind.py)

Create a PriorityLock that pauses background work when user chats:

```python
class PriorityLock:
    def __init__(self):
        self._chat_active = asyncio.Event()
        self._chat_active.set()  # Not active = workers can proceed
    
    async def chat_enters(self):
        self._chat_active.clear()  # Block workers
    
    async def chat_exits(self):
        self._chat_active.set()  # Release workers
    
    async def wait_if_chat(self):
        """Workers call this between steps. Blocks if chat is active."""
        await self._chat_active.wait()
```

Add to HiveMind.__init__: self.priority = PriorityLock()
Add to chat() start: await self.priority.chat_enters()
Add to chat() end (finally block): await self.priority.chat_exits()
Add to heartbeat loop: await self.priority.wait_if_chat() before each LLM call

## TASK 4: Smart Router (consciousness.py or hivemind.py)

Modify the chat flow to route based on memory confidence:

Before sending to LLM, check consciousness memory:
  results = self.consciousness.search(question)
  best = results[0] if results else None

  if best and best.score > 0.90 and best.metadata.get('validated'):
      # Direct answer from memory — no LLM needed at all
      return best.text
  
  if best and best.score > 0.70:
      # Good context — use fast llama1b to format answer
      context = format_results(results[:3])
      response = await generate("llama3.2:1b", context + "\nQuestion: " + question)
  
  elif best and best.score > 0.50:
      # Some context — use phi4-mini for reasoning
      context = format_results(results[:5])
      response = await generate("phi4-mini", context + "\nQuestion: " + question)
  
  else:
      # No good context — let phi4-mini try (existing behavior)
      # But consider: active learning in Phase 4 will handle this better
      response = await generate("phi4-mini", question)

## TASK 5: YAML Knowledge Scanner (new file: tools/scan_knowledge.py)

Create a script that parses ALL YAML files in knowledge/ and agents/ directories
and stores facts directly into ChromaDB via consciousness.learn().

NO LLM NEEDED — just parse YAML structure and format as natural language.

Example YAML entry:
```yaml
varroa:
  treatment_threshold: 3 per 100 bees
  timing: August-September
  methods: [oxalic acid, formic acid, thymol]
```

Becomes facts:
- "Varroa treatment threshold is 3 mites per 100 bees"
- "Varroa treatment timing is August to September"  
- "Varroa treatment methods include oxalic acid, formic acid, and thymol"

For each fact: consciousness.learn(fact, source_type="yaml_seed", confidence=0.90, validated=True)

Features:
- --dry-run flag: print facts without storing
- --count flag: just show how many facts would be extracted
- Track progress in data/scan_progress.json (skip already-processed files)
- Idempotent: consciousness.learn() has 0.93 similarity dedup built in

## TASK 6: Auto-Seed on First Startup (hivemind.py)

After consciousness initialization in startup:
```python
if self.consciousness and self.consciousness.memory.count() < 100:
    print("  🌱 First startup — seeding knowledge base...")
    # Import and run scan_knowledge
    from tools.scan_knowledge import scan_all
    count = scan_all(self.consciousness)
    print(f"  ✅ Seeded {count} facts from knowledge base")
```

## TASK 7: OLLAMA_KEEP_ALIVE + Environment (main.py + start.bat)

Ensure these are set at startup:
- main.py: os.environ.setdefault("OLLAMA_KEEP_ALIVE", "24h")
- main.py: os.environ.setdefault("OLLAMA_MAX_LOADED_MODELS", "4")
- start.bat: set OLLAMA_KEEP_ALIVE=24h and set OLLAMA_MAX_LOADED_MODELS=4

Check if this was already done by a previous Claude Code session.
If already present, skip and note "already implemented".

## FINAL TEST — PHASE 1 COMPLETE

Run ALL of these and report results:

1. python consciousness.py
   Expected: Math 9/9, Hallucination 3/4+, Search improved

2. python tools/scan_knowledge.py --dry-run
   Expected: Shows extracted facts from YAML files

3. python tools/scan_knowledge.py  
   Expected: Stores facts, shows count

4. python consciousness.py (again, after seeding)
   Expected: Search tests improve (more knowledge available)

5. python main.py (if Ollama is running)
   Expected: 
   - "🌱 First startup" message if DB was empty
   - Heartbeat uses llama3.2:1b (check logs)
   - Chat uses phi4-mini (test with a message)
   - "mikä on varroa-kynnys" gets answer from memory

Report results as a table:
| Test | Expected | Actual | Pass/Fail |

If any test fails, diagnose and fix before declaring Phase 1 complete.
When Phase 1 passes, update CLAUDE.md with results and say "PHASE 1 COMPLETE".
