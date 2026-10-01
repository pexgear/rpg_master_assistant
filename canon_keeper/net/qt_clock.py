"""The host's clock, kept by Qt, for the host embedded in the app.

One of two: see :mod:`canon_keeper_core.clock` for the shape, and the asyncio one
for a host running on its own. This exists so the rules can stop importing a
desktop toolkit to find out what time it is.

Every timer is parented, which is not decoration: an unparented ``QTimer`` whose
last Python reference goes out of scope is collected, and a turn clock that was
collected is a turn nobody is waiting on any more. That has been the shape of
more than one disappearing-timer bug in Qt code.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QTimer


class _QtLater:
    """One scheduled call, cancellable."""

    __slots__ = ("_timer",)

    def __init__(self, timer: QTimer) -> None:
        self._timer = timer

    def cancel(self) -> None:
        self._timer.stop()


class QtClock:
    """Later, by ``QTimer``. Needs something to parent its timers to."""

    def __init__(self, owner: QObject) -> None:
        self._owner = owner

    def later(self, seconds: float, call: Callable[[], None]) -> _QtLater:
        timer = QTimer(self._owner)
        timer.setSingleShot(True)
        timer.timeout.connect(call)
        # Also stopped when it fires, so a handle kept and cancelled afterwards
        # is harmless rather than a second firing.
        timer.start(max(0, int(seconds * 1000)))
        return _QtLater(timer)
