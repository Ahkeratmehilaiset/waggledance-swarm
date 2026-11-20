"""Small deterministic M02/M18 contracts; no 10GiB source or live owner."""
import hashlib
import io
import struct
from types import SimpleNamespace
import pytest
from tools import wd_bridge_mind_session as session
from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_absent_generation_prefix_readonly as prefix
from tools import wd_mind_vector_cache as cache

LIMIT = 10 * 1024**3
OWNER = 3 * LIMIT + 6 * (4 * 1024**2 + 1) + 3
PREWARM = 7 * LIMIT + 4 * 1024**2 + 1

@pytest.mark.parametrize("offset", [LIMIT - 1, LIMIT, LIMIT + 1])
@pytest.mark.parametrize("operation,expected", [
    ("startup", OWNER), ("query", OWNER), ("prewarm", PREWARM),
    ("prewarm_refresh", PREWARM)])
def test_exact_source_boundary_without_source_io(monkeypatch, offset, operation, expected):
    def no_io(*args, **kwargs):
        raise AssertionError("source I/O before metadata preflight")
    snapshot = {"messages": {"end_cursor": SimpleNamespace(offset=offset)}}
    functions = {
        "startup": runtime._startup_owner_source_budget,
        "query": runtime._local_query_source_budget,
        "prewarm": runtime._prewarm_source_budget,
        "prewarm_refresh": lambda snap: runtime._prewarm_source_budget(snap, extra_prefix_passes=2)}
    with monkeypatch.context() as guard:
        guard.setattr("builtins.open", no_io)
        guard.setattr(prefix.os, "open", no_io)
        if offset > LIMIT:
            with pytest.raises(ValueError, match="source_budget"):
                functions[operation](snapshot)
        else:
            result = functions[operation](snapshot)
            assert type(result) is prefix._SourceReadBudget
            assert result._remaining == expected

def test_shared_allowance_hash_and_selected_read():
    class Counting(io.BytesIO):
        def __init__(self, value):
            super().__init__(value)
            self.calls = []
        def read(self, size=-1):
            self.calls.append(size)
            return super().read(size)
    allowance = prefix._SourceReadBudget(6)
    initial = Counting(b"abcd")
    assert prefix._hash_prefix(initial, 4, source_budget=allowance) == hashlib.sha256(b"abcd").hexdigest()
    selected = Counting(b"efg")
    with pytest.raises(ValueError, match="source_budget"):
        session._bounded_source_read(selected, allowance, 3)
    assert selected.calls == []
    assert prefix._budgeted_read(selected, 2, allowance) == b"ef"
    with pytest.raises(ValueError, match="source_budget"):
        session._bounded_source_read(selected, allowance, 1)
    assert initial.calls == [4] and selected.calls == [2]

def test_full_prefix_primitive_sees_unselected_same_size_change():
    source = io.BytesIO(b"old row\nselected row\n")
    size = len(source.getvalue())
    before = prefix._hash_prefix(source, size, source_budget=prefix._SourceReadBudget(size))
    source.seek(0)
    source.write(b"new")
    after = prefix._hash_prefix(source, size, source_budget=prefix._SourceReadBudget(size))
    assert before != after
    # Primitive coverage only: this does not prove actual session wiring.

def test_more_than_old_cache_cap_valid_rows():
    vector = struct.pack("<384f", 1.0, *([0.0] * 383))
    rows = tuple((f"{number:064x}", "b" * 64, vector) for number in range(250001))
    assert cache._rows(rows) is rows

def test_new_cache_limit_refuses_before_inspecting_rows(monkeypatch):
    class NeverInspect:
        def fullmatch(self, value):
            raise AssertionError("row inspected beyond declared capacity")
    vector = struct.pack("<384f", 1.0, *([0.0] * 383))
    rows = (("1" * 64, "b" * 64, vector),) * 500001
    monkeypatch.setattr(cache, "_HEX", NeverInspect())
    with pytest.raises(cache.CacheRefused, match="rows"):
        cache._rows(rows)


@pytest.mark.parametrize("offset", [LIMIT - 1, LIMIT, LIMIT + 1])
def test_session_cap_is_distinct_from_total_read_allowance(monkeypatch, offset):
    total = 4 * LIMIT + (64 * 1024**2 + 1) + 1 + 6 * (4 * 1024**2 + 1)
    budget = session._SourceReadBudget(total)
    budget.reserve(64 * 1024**2 + 1)
    before = budget._remaining
    def no_io(*args, **kwargs):
        raise AssertionError("source I/O during session preflight")
    with monkeypatch.context() as guard:
        guard.setattr("builtins.open", no_io)
        if offset > LIMIT:
            with pytest.raises(session._SelectedSourcePrefixBudgetRefused):
                session._preflight_selected_source_prefix(budget, offset)
        else:
            session._preflight_selected_source_prefix(budget, offset)
    assert budget._remaining == before
