"""Token Economy core module — test suite (~10 tests).

Tests: TokenEconomy init, get_balance, reward, spend, leaderboard,
rank_emoji, ORACLE_PRICES constants, REWARD_RULES constants.
No live DB needed — uses in-memory aiosqlite mock.
"""
import sys, os, ast, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Helpers ─────────────────────────────────────────────────────────────

def _run(coro):
    """Run async coroutine in sync test."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeDB:
    """Minimal async DB stub — stores nothing, never fails."""
    async def execute(self, *a, **kw):
        return _FakeCursor()
    async def commit(self):
        pass


class _FakeCursor:
    async def fetchall(self):
        return []


class _FakeMemory:
    """Minimal memory stub with _db attribute."""
    def __init__(self):
        self._db = _FakeDB()


# ── 1. Syntax ────────────────────────────────────────────────────────────

def test_syntax_token_economy():
    path = os.path.join(os.path.dirname(__file__), "..", "core", "token_economy.py")
    with open(path, "r", encoding="utf-8") as f:
        ast.parse(f.read())
    print("  [PASS] core/token_economy.py syntax valid")


# ── 2. Constants ─────────────────────────────────────────────────────────

def test_oracle_prices_exist():
    from core.token_economy import ORACLE_PRICES
    assert "web_search" in ORACLE_PRICES
    assert "claude_question" in ORACLE_PRICES
    assert "deep_research" in ORACLE_PRICES
    assert ORACLE_PRICES["web_search"] > 0
    assert ORACLE_PRICES["claude_question"] > ORACLE_PRICES["web_search"]
    print("  [PASS] ORACLE_PRICES constants correct")


def test_reward_rules_exist():
    from core.token_economy import REWARD_RULES
    assert "task_completed" in REWARD_RULES
    assert "insight_generated" in REWARD_RULES
    assert "reflection_done" in REWARD_RULES
    assert "message_sent" in REWARD_RULES
    assert "question_answered" in REWARD_RULES
    assert all(v > 0 for v in REWARD_RULES.values())
    print("  [PASS] REWARD_RULES constants correct")


def test_rank_emojis_exist():
    from core.token_economy import RANK_EMOJIS
    assert 0 in RANK_EMOJIS
    assert 100 in RANK_EMOJIS
    # All keys should be non-negative integers
    assert all(isinstance(k, int) and k >= 0 for k in RANK_EMOJIS)
    print("  [PASS] RANK_EMOJIS constants correct")


# ── 3. Init + balance ────────────────────────────────────────────────────

def test_token_economy_init():
    from core.token_economy import TokenEconomy
    mem = _FakeMemory()
    te = TokenEconomy(mem)
    assert te.memory is mem
    assert te._balances == {}
    assert te._history == []
    print("  [PASS] TokenEconomy init OK")


def test_get_balance_unknown_agent():
    from core.token_economy import TokenEconomy
    te = TokenEconomy(_FakeMemory())
    assert te.get_balance("unknown_agent") == 0
    print("  [PASS] get_balance returns 0 for unknown agent")


# ── 4. Reward ────────────────────────────────────────────────────────────

def test_reward_increases_balance():
    from core.token_economy import TokenEconomy, REWARD_RULES
    te = TokenEconomy(_FakeMemory())

    async def _test():
        new_balance = await te.reward("agent_bee", "task_completed")
        assert new_balance == REWARD_RULES["task_completed"]
        assert te.get_balance("agent_bee") == REWARD_RULES["task_completed"]
        # Second reward accumulates
        new_balance2 = await te.reward("agent_bee", "message_sent")
        expected = REWARD_RULES["task_completed"] + REWARD_RULES["message_sent"]
        assert new_balance2 == expected
        assert te.get_balance("agent_bee") == expected

    _run(_test())
    print("  [PASS] reward increases balance correctly")


def test_reward_custom_amount():
    from core.token_economy import TokenEconomy
    te = TokenEconomy(_FakeMemory())

    async def _test():
        balance = await te.reward("agent_bee", "special", custom_amount=42)
        assert balance == 42
        assert te.get_balance("agent_bee") == 42

    _run(_test())
    print("  [PASS] reward with custom_amount works")


def test_reward_history_appended():
    from core.token_economy import TokenEconomy
    te = TokenEconomy(_FakeMemory())

    async def _test():
        await te.reward("agent_bee", "task_completed")
        assert len(te._history) == 1
        entry = te._history[0]
        assert entry["agent_id"] == "agent_bee"
        assert entry["reason"] == "task_completed"
        assert "balance" in entry
        assert "time" in entry

    _run(_test())
    print("  [PASS] reward history entry recorded")


# ── 5. Spend ─────────────────────────────────────────────────────────────

def test_spend_reduces_balance():
    from core.token_economy import TokenEconomy
    te = TokenEconomy(_FakeMemory())

    async def _test():
        await te.reward("agent_bee", "task_completed", custom_amount=50)
        success = await te.spend("agent_bee", 20, "web_search")
        assert success is True
        assert te.get_balance("agent_bee") == 30

    _run(_test())
    print("  [PASS] spend reduces balance correctly")


def test_spend_insufficient_funds():
    from core.token_economy import TokenEconomy
    te = TokenEconomy(_FakeMemory())

    async def _test():
        await te.reward("agent_bee", "message_sent", custom_amount=5)
        success = await te.spend("agent_bee", 100, "deep_research")
        assert success is False
        assert te.get_balance("agent_bee") == 5  # unchanged

    _run(_test())
    print("  [PASS] spend with insufficient funds returns False")


# ── 6. Rank emoji ────────────────────────────────────────────────────────

def test_get_rank_emoji():
    from core.token_economy import TokenEconomy, RANK_EMOJIS
    te = TokenEconomy(_FakeMemory())
    # Zero balance -> lowest rank emoji
    emoji = te.get_rank_emoji("nobody")
    assert isinstance(emoji, str)
    assert len(emoji) > 0
    # Should be the emoji at threshold 0
    assert emoji == RANK_EMOJIS[0]
    print("  [PASS] get_rank_emoji returns emoji string")


# ── 7. Leaderboard ───────────────────────────────────────────────────────

def test_get_leaderboard():
    from core.token_economy import TokenEconomy
    te = TokenEconomy(_FakeMemory())

    async def _test():
        await te.reward("agent_alpha", "task_completed", custom_amount=50)
        await te.reward("agent_beta", "task_completed", custom_amount=20)
        board = te.get_leaderboard()
        assert len(board) == 2
        # Sorted descending by balance
        assert board[0]["agent_id"] == "agent_alpha"
        assert board[0]["balance"] == 50
        assert board[1]["agent_id"] == "agent_beta"
        assert board[1]["balance"] == 20
        # Each entry has required fields
        for entry in board:
            assert "agent_id" in entry
            assert "balance" in entry
            assert "rank" in entry

    _run(_test())
    print("  [PASS] get_leaderboard sorted by balance")


# ── Runner ──────────────────────────────────────────────────────────────

ALL_TESTS = [
    test_syntax_token_economy,
    test_oracle_prices_exist,
    test_reward_rules_exist,
    test_rank_emojis_exist,
    test_token_economy_init,
    test_get_balance_unknown_agent,
    test_reward_increases_balance,
    test_reward_custom_amount,
    test_reward_history_appended,
    test_spend_reduces_balance,
    test_spend_insufficient_funds,
    test_get_rank_emoji,
    test_get_leaderboard,
]


if __name__ == "__main__":
    passed = 0
    failed = 0
    errors = []

    print("\n" + "=" * 60)
    print("core/token_economy.py -- {0} tests".format(len(ALL_TESTS)))
    print("=" * 60 + "\n")

    for test in ALL_TESTS:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((test.__name__, str(e)))
            print("  [FAIL] {0}: {1}".format(test.__name__, e))

    print("\n" + "=" * 60)
    print("Result: {0}/{1} passed, {2} failed".format(passed, passed + failed, failed))
    if errors:
        print("\nFailed tests:")
        for name, err in errors:
            print("  - {0}: {1}".format(name, err))
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)
