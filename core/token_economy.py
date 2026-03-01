# WaggleDance Swarm AI • v0.0.1 • Built: 2026-02-22 14:37 EET
# Jani Korpi (Ahkerat Mehiläiset)
"""
WaggleDance Swarm AI Token Economy — Agentit ansaitsevat ja käyttävät tokeneita
"""
import json
from datetime import datetime


# Oracle-hinnat (kuiskaukset käyttävät whisper_protocol.py:n WHISPER_COSTS)
ORACLE_PRICES = {
    "web_search": 3,
    "claude_question": 10,
    "deep_research": 20,
}

# Palkitsemissäännöt
REWARD_RULES = {
    "task_completed": 10,
    "insight_generated": 5,
    "reflection_done": 3,
    "message_sent": 1,
    "question_answered": 8,
}

# Emoji-progressio
RANK_EMOJIS = {
    0: "😊",
    20: "😄",
    50: "🤩",
    80: "🔥",
    100: "👑",
}


class TokenEconomy:
    """Hallinnoi agenttien tokeni-taloutta."""

    def __init__(self, memory):
        self.memory = memory
        self._balances: dict[str, int] = {}
        self._history: list[dict] = []

    async def initialize(self):
        """Luo taulut ja lataa saldot."""
        await self.memory._db.execute("""
            CREATE TABLE IF NOT EXISTS token_economy (
                agent_id TEXT PRIMARY KEY,
                balance INTEGER DEFAULT 0,
                total_earned INTEGER DEFAULT 0,
                total_spent INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self.memory._db.execute("""
            CREATE TABLE IF NOT EXISTS token_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT,
                balance_after INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self.memory._db.commit()

        # Lataa olemassa olevat saldot
        cursor = await self.memory._db.execute(
            "SELECT agent_id, balance FROM token_economy"
        )
        for row in await cursor.fetchall():
            self._balances[row[0]] = row[1]

    def get_balance(self, agent_id: str) -> int:
        return self._balances.get(agent_id, 0)

    async def reward(self, agent_id: str, reason: str,
                     custom_amount: int = None) -> int:
        """Anna tokeneita agentille."""
        amount = custom_amount or REWARD_RULES.get(reason, 1)
        balance = self._balances.get(agent_id, 0) + amount
        self._balances[agent_id] = balance

        await self.memory._db.execute("""
            INSERT INTO token_economy (agent_id, balance, total_earned)
            VALUES (?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                balance = ?,
                total_earned = total_earned + ?,
                updated_at = CURRENT_TIMESTAMP
        """, (agent_id, balance, amount, balance, amount))

        await self.memory._db.execute(
            "INSERT INTO token_transactions (agent_id, amount, reason, balance_after) VALUES (?,?,?,?)",
            (agent_id, amount, reason, balance)
        )
        await self.memory._db.commit()

        self._history.append({
            "agent_id": agent_id, "amount": amount,
            "reason": reason, "balance": balance,
            "time": datetime.now().isoformat()
        })
        return balance

    async def spend(self, agent_id: str, amount: int, reason: str) -> bool:
        """Käytä tokeneita."""
        balance = self._balances.get(agent_id, 0)
        if balance < amount:
            return False
        new_balance = balance - amount
        self._balances[agent_id] = new_balance

        await self.memory._db.execute(
            "UPDATE token_economy SET balance = ?, total_spent = total_spent + ? WHERE agent_id = ?",
            (new_balance, amount, agent_id)
        )
        await self.memory._db.execute(
            "INSERT INTO token_transactions (agent_id, amount, reason, balance_after) VALUES (?,?,?,?)",
            (agent_id, -amount, reason, new_balance)
        )
        await self.memory._db.commit()
        return True

    def get_rank_emoji(self, agent_id: str) -> str:
        balance = self.get_balance(agent_id)
        emoji = "😊"
        for threshold, e in sorted(RANK_EMOJIS.items()):
            if balance >= threshold:
                emoji = e
        return emoji

    def get_leaderboard(self) -> list[dict]:
        board = []
        for aid, balance in sorted(self._balances.items(),
                                    key=lambda x: x[1], reverse=True):
            board.append({
                "agent_id": aid,
                "balance": balance,
                "rank": self.get_rank_emoji(aid)
            })
        return board
