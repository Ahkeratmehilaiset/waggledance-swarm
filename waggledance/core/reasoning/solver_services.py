# SPDX-License-Identifier: BUSL-1.1
"""Explicit service hub for opt-in solver composition."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import inspect
from typing import Any, Callable, TypeVar

from waggledance.core.ports.game_theory_port import GameTheoryPort


class SolverServiceUnavailable(RuntimeError):
    """A solver requested a service that was not injected."""


_SOLVER_SERVICES_MARKER = "__wd_solver_services_opt_in__"
_CallableT = TypeVar("_CallableT", bound=Callable[..., Any])


@dataclass(frozen=True)
class SolverServices:
    """Capabilities a solver may use without gaining execution authority.

    Solvers receive this object through ordinary dependency injection. The
    field defaults to ``None``, so existing solvers and execution paths remain
    unchanged. A current or autonomously generated solver opts in by accepting
    ``SolverServices`` and calling ``require_game_theory()``.
    """

    game_theory: GameTheoryPort | None = None

    @property
    def has_game_theory(self) -> bool:
        return self.game_theory is not None

    def require_game_theory(self) -> GameTheoryPort:
        if self.game_theory is None:
            raise SolverServiceUnavailable(
                "game-theory service was not injected into this solver"
            )
        return self.game_theory


@lru_cache(maxsize=1)
def default_solver_services() -> SolverServices:
    """Return the pure built-in advisory service hub.

    The cached implementation has no mutable state or external resources. A
    runtime may instead inject another ``GameTheoryPort`` implementation, but
    all results must still satisfy the same advisory contracts.
    """

    from waggledance.core.game_theory_service import BoundedGameTheoryService

    return SolverServices(game_theory=BoundedGameTheoryService())


def solver_services_opt_in(function: _CallableT) -> _CallableT:
    """Mark a solver callable for explicit ``SolverServices`` injection.

    The marker is required at each execution boundary; parameter-name
    introspection alone never opts a solver in. Requiring a keyword-only
    parameter prevents payload data from occupying the service position.
    """

    try:
        parameter = inspect.signature(function).parameters.get("solver_services")
    except (TypeError, ValueError) as exc:
        raise TypeError("solver callable signature is not inspectable") from exc
    if parameter is None or parameter.kind is not inspect.Parameter.KEYWORD_ONLY:
        raise TypeError(
            "solver_services_opt_in requires a keyword-only "
            "solver_services parameter"
        )
    setattr(function, _SOLVER_SERVICES_MARKER, True)
    return function


def accepts_solver_services(function: Callable[..., Any]) -> bool:
    """Return whether a callable explicitly opted into service injection."""

    target = getattr(function, "__func__", function)
    return getattr(target, _SOLVER_SERVICES_MARKER, False) is True


def invoke_solver_callable(
    function: Callable[..., Any],
    *args: Any,
    injected_services: SolverServices | None = None,
    **kwargs: Any,
) -> Any:
    """Invoke one solver, injecting services only after explicit opt-in."""

    if not accepts_solver_services(function):
        return function(*args, **kwargs)
    if "solver_services" in kwargs:
        raise ValueError("solver_services is reserved for runtime injection")
    services = injected_services or default_solver_services()
    return function(*args, solver_services=services, **kwargs)
