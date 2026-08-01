"""Reusable state-machine validation.

Status transitions are defined as adjacency lists (dict[Enum, Set[Enum]]).
The validator is instantiated once per domain and reused by models, services,
and schemas so the rules live in a single place.
"""

from enum import Enum
from typing import Optional, Set, TypeVar
from typing import Set, TypeVar

StatusT = TypeVar("StatusT", bound=Enum)


class StateMachine:
    """Validates transitions for a finite state machine."""

    def __init__(
        self,
        transitions: dict[StatusT, Set[StatusT]],
        terminal: Optional[Set[StatusT]] = None,
    ):
        self._transitions = transitions
        self._terminal = terminal or set()

    def is_valid(self, current: StatusT, next_status: StatusT) -> bool:
        """Return True if ``next_status`` is a legal move from ``current``."""
        return next_status in self._transitions.get(current, set())

    def is_terminal(self, status: StatusT) -> bool:
        """Return True if ``status`` is a terminal (absorbing) state."""
        return status in self._terminal

    def allowed_next(self, current: StatusT) -> Set[StatusT]:
        """Return the set of statuses reachable from ``current``."""
        return set(self._transitions.get(current, set()))
