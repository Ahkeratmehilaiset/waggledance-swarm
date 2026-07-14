"""Port for optional forward and inverse game-theory advice."""
from __future__ import annotations

from typing import Protocol

from waggledance.core.game_theory_contracts import (
    ForwardGameRequest,
    ForwardGameResult,
    InverseGameRequest,
    InverseGameResult,
)


class GameTheoryPort(Protocol):
    """Shared opt-in service available to current and generated solvers.

    Implementations return advice and evidence only. This port deliberately
    has no method for selecting runtime authority or applying external writes.
    """

    def solve_forward(self, request: ForwardGameRequest) -> ForwardGameResult: ...

    def infer_inverse(self, request: InverseGameRequest) -> InverseGameResult: ...
