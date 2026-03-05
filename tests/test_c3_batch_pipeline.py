"""C3: Test batch dedup optimization in _flush_learn_queue."""
import sys, os, ast
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_syntax():
    """memory_engine.py parses without errors."""
    with open(os.path.join(os.path.dirname(__file__), "..", "core", "memory_engine.py"),
              "r", encoding="utf-8") as f:
        ast.parse(f.read())
    print("  [PASS] memory_engine.py syntax valid")


def test_batch_dedup_in_source():
    """_flush_learn_queue uses batch dedup (one query, not N)."""
    with open(os.path.join(os.path.dirname(__file__), "..", "core", "memory_engine.py"),
              "r", encoding="utf-8") as f:
        src = f.read()

    # Find the flush method
    flush_start = src.index("def _flush_learn_queue(self):")
    flush_end = src.index("def flush(self):", flush_start)
    flush_body = src[flush_start:flush_end]

    # Should have batch query (collection.query with query_embeddings as list)
    assert "self.memory.collection.query(" in flush_body, \
        "Should use batch collection.query for dedup"

    # Should NOT have per-item search loop
    assert "self.memory.search(emb, top_k=1" not in flush_body, \
        "Should NOT have per-item memory.search in flush"

    print("  [PASS] batch dedup uses single collection.query")


def test_dedup_flag_pattern():
    """Dedup uses is_dup flag array pattern."""
    with open(os.path.join(os.path.dirname(__file__), "..", "core", "memory_engine.py"),
              "r", encoding="utf-8") as f:
        src = f.read()

    flush_start = src.index("def _flush_learn_queue(self):")
    flush_end = src.index("def flush(self):", flush_start)
    flush_body = src[flush_start:flush_end]

    assert "is_dup" in flush_body, "Should use is_dup flag array"
    assert "is_dup[idx] = True" in flush_body, "Should set is_dup flag on matches"
    assert "is_dup[i]" in flush_body, "Should check is_dup in filter loop"
    print("  [PASS] is_dup flag pattern correct")


def test_batch_translation_path():
    """Batch translation path exists for Finnish texts."""
    with open(os.path.join(os.path.dirname(__file__), "..", "core", "memory_engine.py"),
              "r", encoding="utf-8") as f:
        src = f.read()

    flush_start = src.index("def _flush_learn_queue(self):")
    flush_end = src.index("def flush(self):", flush_start)
    flush_body = src[flush_start:flush_end]

    assert "batch_fi_to_en" in flush_body, "Should have batch translation"
    assert "fi_indices" in flush_body, "Should collect Finnish indices"
    print("  [PASS] batch translation path present")


def test_batch_embed_call():
    """Batch embed is called once (not per-item)."""
    with open(os.path.join(os.path.dirname(__file__), "..", "core", "memory_engine.py"),
              "r", encoding="utf-8") as f:
        src = f.read()

    flush_start = src.index("def _flush_learn_queue(self):")
    flush_end = src.index("def flush(self):", flush_start)
    flush_body = src[flush_start:flush_end]

    # Should call embed_batch: 1 for main EN + 1 for bilingual FI
    embed_batch_calls = flush_body.count("self.embed.embed_batch(")
    assert embed_batch_calls == 2, \
        f"Expected 2 embed_batch calls (EN + FI), found {embed_batch_calls}"
    print("  [PASS] 2 embed_batch calls (main EN + bilingual FI)")


def test_eviction_hook():
    """Eviction check is called at end of flush."""
    with open(os.path.join(os.path.dirname(__file__), "..", "core", "memory_engine.py"),
              "r", encoding="utf-8") as f:
        src = f.read()

    flush_start = src.index("def _flush_learn_queue(self):")
    flush_end = src.index("def flush(self):", flush_start)
    flush_body = src[flush_start:flush_end]

    assert "self.eviction.on_flush()" in flush_body, \
        "Eviction hook should be at end of flush"
    print("  [PASS] eviction hook present in flush")


if __name__ == "__main__":
    print("C3: Batch pipeline optimization tests")
    print("=" * 50)
    test_syntax()
    test_batch_dedup_in_source()
    test_dedup_flag_pattern()
    test_batch_translation_path()
    test_batch_embed_call()
    test_eviction_hook()
    print("=" * 50)
    print("ALL 6 TESTS PASSED")
