"""Doing something later, without saying which toolkit is counting.

The host waits for three things: a login that never arrives, a turn nobody
answers, and a moment before closing a socket it has just refused -- long enough
for the refusal to reach the far end rather than racing the close.

That is the whole of it: *call this in N seconds, and let me cancel it*. A
``QTimer`` does far more and costs a desktop toolkit, which is why the thing that
owns every rule in the game could not be imported without one.

Two implementations: one over ``QTimer`` for the app it is embedded in, one over
the event loop for a host running on its own. The host asks for neither by name.
"""

from __future__ import annotations

from typing import Callable, Protocol


class Later(Protocol):
    """Something scheduled, which may not have happened yet."""

    def cancel(self) -> None:
        """Stop it happening. Harmless if it already has."""


class Clock(Protocol):
    """Where the host goes when it wants something to happen later."""

    def later(self, seconds: float, call: Callable[[], None]) -> Later:
        ...


class _Cancelled:
    """A handle for something that was never scheduled. Cancel is a no-op."""

    def cancel(self) -> None:
        return None


NEVER = _Cancelled()
