"""IR adapters — Phase 9 §G.

Convert per-session outputs (Session A curiosity, Session B
self-model, Session C dream artifacts, Session D hive proposals)
into the unified Cognition IR.

Each adapter is pure: takes a parsed dict, returns a list of
IRObject. Adapters never call back into runtime, never write disk
state, and never trigger live LLM/network calls.
"""
