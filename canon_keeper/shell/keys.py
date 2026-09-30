"""Which keys clash, and which only look as though they do.

Scoping a key to its panel changes what a clash *is*, and the change is worth
stating because the obvious answer is wrong. Two panels both using ``Ctrl+N`` for
their own version of "new thing" is **not** a clash: only the panel with the
focus is listening, exactly as two applications both using Ctrl+N is not a clash.
Reporting those would bury the real ones.

A real clash is a key that will not do what somebody reading the menu expects:

* **Two claims in the same reach.** Two window-wide keys, or two application-wide
  keys. One of them wins and Qt does not promise which.
* **A wider claim over a narrower one.** A window-wide key beats a panel's key
  everywhere, including inside that panel, so the panel's menu shows a shortcut
  that never fires. This is the one that actually happens, and the one nobody
  works out from the symptom.
* **The same panel claiming a key twice.** Its own two menu items, one of which
  is unreachable.

The keys the app handles itself count as claims too. A panel taking ``Space``
window-wide would stop the map responding to Space, and the map would simply go
quiet -- no error, nothing in the log, just a grid that ignores the key it
documents.
"""

from __future__ import annotations

from dataclasses import dataclass

from canon_keeper.plugin import REACH_EVERYWHERE, REACH_PANEL, REACH_WINDOW

#: How far each reach carries, for comparing two of them. A bigger number wins.
_DISTANCE = {REACH_PANEL: 0, REACH_WINDOW: 1, REACH_EVERYWHERE: 2}


@dataclass(frozen=True)
class Claim:
    """Somebody wanting a key: a panel's menu item, or the app itself."""

    key: str
    owner: str
    reach: str
    #: What it does, for a report a person reads rather than a log line.
    what: str = ""

    @property
    def carries(self) -> int:
        return _DISTANCE.get(self.reach, 0)


@dataclass(frozen=True)
class Clash:
    """Two claims on one key that cannot both be honoured."""

    key: str
    #: The one that will win, as far as anything here can tell.
    wins: Claim
    #: The one that will not fire, or not reliably.
    loses: Claim
    why: str

    def __str__(self) -> str:
        return (
            f"{self.key}: {self.loses.owner} wants it "
            f"({self.loses.what or 'no description'}) but {self.wins.owner} "
            f"has it {self.wins.reach}-wide. {self.why}"
        )


def normalise(key: str) -> str:
    """One spelling per key, so ``ctrl+n`` and ``Ctrl+N`` are the same claim.

    Deliberately not Qt's ``QKeySequence`` parsing: this module is about
    comparing what panels *declared*, runs before any of it reaches Qt, and is
    worth being able to test without a running application.
    """
    parts = [bit.strip().lower() for bit in str(key).replace("-", "+").split("+")]
    parts = [bit for bit in parts if bit]
    if not parts:
        return ""
    # Modifiers in a fixed order, so Ctrl+Shift+N and Shift+Ctrl+N agree.
    order = ("ctrl", "control", "alt", "shift", "meta", "cmd")
    mods = sorted((p for p in parts if p in order), key=order.index)
    rest = [p for p in parts if p not in order]
    return "+".join(["ctrl" if m == "control" else m for m in mods] + rest)


def clashes(claims) -> list[Clash]:
    """Every real clash among these claims, worst first.

    Sorted so a report reads top-down in the order somebody should care: an
    application-wide key taken from under everything else matters more than two
    window keys arguing with each other.
    """
    by_key: dict[str, list[Claim]] = {}
    for claim in claims:
        if not claim.key:
            continue
        by_key.setdefault(normalise(claim.key), []).append(claim)

    found: list[Clash] = []
    for key, wanting in by_key.items():
        if len(wanting) < 2:
            continue
        for i, one in enumerate(wanting):
            for other in wanting[i + 1 :]:
                clash = _between(key, one, other)
                if clash is not None:
                    found.append(clash)

    found.sort(key=lambda c: -c.wins.carries)
    return found


def _between(key: str, one: Claim, other: Claim) -> Clash | None:
    """What goes wrong between two claims on the same key, if anything."""
    if one.carries == other.carries:
        if one.reach == REACH_PANEL and one.owner != other.owner:
            # The case that looks like a clash and is not. Only the panel with
            # the focus is listening.
            return None
        if one.owner == other.owner:
            return Clash(
                key=key,
                wins=one,
                loses=other,
                why="One panel has claimed it twice; one of the two will not fire.",
            )
        return Clash(
            key=key,
            wins=one,
            loses=other,
            why=(
                f"Both want it {one.reach}-wide. One of them wins and it is not "
                "defined which."
            ),
        )

    wider, narrower = (one, other) if one.carries > other.carries else (other, one)
    return Clash(
        key=key,
        wins=wider,
        loses=narrower,
        why=(
            f"{wider.owner} takes it {wider.reach}-wide, so {narrower.owner}'s "
            "never fires -- not even inside its own panel."
        ),
    )
