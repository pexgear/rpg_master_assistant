"""Something happened; tell whoever asked. A Qt signal, without Qt.

The host announced itself through ``Signal``, which meant the thing that owns
every rule in the game could not be imported without 660 MB of Qt. It needs
almost none of what a signal offers: no types, no threads, no connection types,
no auto-disconnect. It needs "call these, in the order they asked".

Deliberately API-shaped like a signal -- ``connect`` and ``emit`` -- so the panels
that listen did not have to change. Connecting one of these *to* a Qt signal is
the one thing that does not carry over: a signal is emitted, not called, so that
reads ``hook.connect(lambda x: sig.emit(x))``.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

log = logging.getLogger("canonkeeper.hooks")


class Hook:
    """A list of things to call. Nothing more."""

    __slots__ = ("_name", "_listeners")

    def __init__(self, name: str = "") -> None:
        #: Only for the log line when a listener raises, so the message names
        #: something a person can find.
        self._name = name
        self._listeners: list[Callable[..., Any]] = []

    def connect(self, listener: Callable[..., Any]) -> None:
        self._listeners.append(listener)

    def disconnect(self, listener: Callable[..., Any] | None = None) -> None:
        """Stop calling one listener, or all of them."""
        if listener is None:
            self._listeners.clear()
        elif listener in self._listeners:
            self._listeners.remove(listener)

    def emit(self, *args: Any) -> None:
        """Call everyone, and survive any of them failing.

        A listener that raises is a panel with a bug in it, and the host is in
        the middle of resolving a turn. Letting that escape would roll the
        failure back into the rules -- a swing that happened, a hit point taken
        off, and an exception out of the function that did it. So it is logged
        and the rest are still told.
        """
        for listener in list(self._listeners):
            try:
                listener(*args)
            except Exception:  # noqa: BLE001 - one bad listener is not the host's problem
                log.exception("a listener for %s raised", self._name or "a hook")

    def __bool__(self) -> bool:
        return bool(self._listeners)
