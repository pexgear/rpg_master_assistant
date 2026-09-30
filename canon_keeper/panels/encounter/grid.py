"""The battle grid: squares, tokens, and dragging one to another square.

Deliberately knows nothing about encounters, repositories or the wire. It is
handed a size and a list of tokens, and it emits what the person did. Both the
DM's map and a player's read-only one are this same widget with ``read_only``
set differently, so the two cannot drift apart in how they draw a fight.

Five feet to the square, like the books, and no pixel coordinates anywhere: a
token is at (3, -2) or it is not on the map at all. Half-squares are a rendering
idea, and reach and range are not rendering questions.

**0,0 is the middle**, x to the right and y downwards, per
:mod:`canon_keeper_protocol.grid`. The rulers along the top and left are what
make that usable out loud: "the one at minus three, two" is a square everyone
can find, and it is still that square after the map grows.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from canon_keeper_protocol import grid

#: Below this a token is a coloured dot with no room for initials or pips.
#: Fitting stops here and the map is panned instead, which is the better of the
#: two: a board you scroll is usable, and a board of specks is not.
MIN_CELL = 18
#: Above it a map that fits the panel stops looking like a battlefield and
#: starts looking like a chessboard for giants. Only the *automatic* size is
#: held to it -- somebody who zooms in has said what they want.
MAX_CELL = 64

#: How far a person may zoom, either way. Wider than the fitting range at both
#: ends on purpose: zooming out past legibility is how you find the far corner
#: of a big map, and zooming in past comfort is how you sort out four creatures
#: standing on top of each other.
ZOOM_MIN = 8
ZOOM_MAX = 200
#: One notch. Multiplicative, because a fixed number of pixels is a huge step
#: when the squares are small and an imperceptible one when they are large.
ZOOM_STEP = 1.2

#: Which way each arrow key walks the view, in squares. A square at a time
#: rather than a fixed pixel nudge, because the thing being looked for on a map
#: is always "two along and one down".
_ARROWS = {
    Qt.Key.Key_Left: (-1, 0),
    Qt.Key.Key_Right: (1, 0),
    Qt.Key.Key_Up: (0, -1),
    Qt.Key.Key_Down: (0, 1),
}

#: Room along the top and left for the coordinate rulers. Everyone gets these,
#: players included: naming a square is how a table talks about a map.
RULER = 22

#: What a combatant being dragged out of the initiative list looks like on the
#: clipboard. Its own type rather than Qt's item-model format, because the map
#: needs one number and parsing an item model to find it would be work in
#: exchange for nothing.
COMBATANT_MIME = "application/x-canonkeeper-combatant"

#: Player characters and everything else. Two hues, not a palette per kind: at
#: a glance the only question is "is that us or them".
_OURS = QColor(70, 130, 200)
_THEIRS = QColor(180, 80, 70)
_TURN = QColor(240, 190, 60)
#: Rock, pillar, overturned cart. Grey on purpose: terrain is scenery, and it
#: must not compete with the two colours that mean "us" and "them".
_STONE = QColor(120, 120, 124)

#: A turn that has been offered and not yet accepted. Distinct from both sides'
#: colours, because the whole point of it is that it has not happened.
_PLAN = QColor(150, 150, 160)
_BLADE = QColor(210, 90, 60)

#: How long each part of an action takes to show. Constants rather than numbers
#: on the wire: every client at a table runs the same build -- the protocol
#: version says so at the door -- so they agree without being told.
STEP_MS = 220      #: one square of walking
LUNGE_MS = 300     #: leaning in for a swing, and back
FLOAT_MS = 1100    #: the damage rising off a token
DOWN_MS = 650      #: a creature going down where it stands
FRAME_MS = 33      #: about thirty a second, which is enough for a token


#: Damage, and the word for when there is none. Red for what it costs; grey for
#: a miss, which is still worth showing -- it is half of what happened.
_HURT = QColor(215, 75, 65)
_MISSED = QColor(150, 150, 155)

#: The three things a creature spends. Green for movement and blue for the
#: action, because they are two halves of one turn and want telling apart at a
#: glance; red for a reaction already spent, because that one is a warning
#: rather than an allowance -- it is what somebody walking past has to know.
_MOVE_LEFT = QColor(110, 190, 120)
_ACTION_LEFT = QColor(120, 170, 230)
_REACTED = QColor(215, 95, 85)

#: How solid a body on the floor is drawn. Faint enough to read as "not in
#: this any more" at a glance, solid enough that nobody has to hunt for it --
#: where somebody fell is a thing the party is trying to get to.
GHOST_OPACITY = 0.35
#: And drained of its side's colour, because a body is not fighting for anybody.
_GHOST = QColor(140, 140, 145)


def eased(fraction: float) -> float:
    """Smoothstep: out of a standstill and back into one.

    A walk drawn at a flat speed reads as a token being *dragged* across the
    board by something outside the fight -- it starts at full pace and stops
    dead. A creature leans into a run and settles out of it, and half a
    second of that is the whole difference between a piece sliding and
    somebody walking.

    Over the whole walk rather than each square, so the middle of a long one
    is brisk and only the ends are gentle. Easing every square would be a
    creature stopping to think between each of them.
    """
    fraction = min(1.0, max(0.0, fraction))
    return fraction * fraction * (3.0 - 2.0 * fraction)


def when_eased(distance: float) -> float:
    """The other way round: how far through a walk a given square is reached.

    A swing provoked four squares along has to be *drawn* four squares along,
    and the token is not four squares along at four-tenths of the time -- it
    was still getting going. Smoothstep inverts in closed form, so this is the
    exact moment rather than a guess at it.
    """
    distance = min(1.0, max(0.0, distance))
    return 0.5 - math.sin(math.asin(1.0 - 2.0 * distance) / 3.0)


@dataclass(frozen=True)
class Token:
    """One creature on the map, ready to draw."""

    id: int
    label: str
    x: int
    y: int
    #: True for a player character. Drawn in the party's colour.
    ours: bool = False
    #: Whose turn it is. Ringed, so the answer to "who is up" is visible from
    #: across the table rather than read off a list.
    is_turn: bool = False
    #: DM only: on the map, but not shared, so no player can see it. Drawn
    #: dotted -- the DM should not have to guess which of the two states a
    #: token is in, and asking the party is not a way to find out.
    unseen: bool = False
    #: At zero hit points and lying where they fell. Drawn as a ghost: grey,
    #: faint, and still on its square. Taking it away hid the thing a party
    #: most wants to see, which is how far away their friend is.
    down: bool = False
    #: Squares this creature can still walk, and whether its action is still
    #: there. Only meaningful for whoever is up, which is the only token they
    #: are drawn on: what is left of *your* turn is the question a turn is
    #: about, and a pip on every token would be sixteen answers to it.
    squares_left: int = 0
    acted: bool = False
    #: Already swung at somebody walking past this round. The opposite
    #: polarity to the two above, deliberately: still having your reaction is
    #: everybody's default state and drawing it everywhere says nothing, while
    #: having spent it is the exception and it is exactly what somebody
    #: deciding whether to walk past this creature needs to know.
    reacted: bool = False

    @property
    def initials(self) -> str:
        parts = [word for word in self.label.split() if word]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][:1] + parts[1][:1]).upper()


@dataclass(frozen=True)
class Preview:
    """A turn somebody has been offered and has not answered yet.

    Drawn rather than described, because "move to 0,4 and attack the orc" is a
    sentence you have to translate back into the map you are looking at. A
    dotted line and a ghost is the same information with the translation
    already done.
    """

    #: The combatant about to act.
    token: int
    #: Where they would end up, or None if they are staying put.
    to: tuple[int, int] | None = None
    #: The square of whoever they would attack.
    target: tuple[int, int] | None = None


@dataclass(frozen=True)
class Choice:
    """One wedge of the wheel: something this creature could do now."""

    #: "move", or "attack" with a weapon named.
    kind: str
    label: str
    weapon: str = ""


@dataclass
class TurnPlan:
    """What a creature is about to do, as a thing rather than as a side effect.

    Right now each choice is carried out the moment it is picked, and there is
    no going back -- which is the same deal a person gets at a table once the
    die is on the felt. But a turn is a *sequence*, and the difference between
    "do it now" and "line it up and confirm" should be a change to when this is
    handed over, not a rewrite of how the map works.

    So the map builds one of these even though it currently posts it
    immediately. The future -- previewing a whole turn, taking a step back
    before anything is committed -- is a matter of holding it a little longer.
    """

    combatant: int
    #: Where they would end up, if the plan includes moving.
    move: tuple[int, int] | None = None
    #: Who they would hit, and with what.
    target: int | None = None
    weapon: str = ""

    @property
    def is_empty(self) -> bool:
        return self.move is None and self.target is None


def budget_marks(token: Token) -> list[tuple[QColor, bool]]:
    """The pips under a token: which colour, and whether it is still there.

    See :meth:`GridMap._draw_budget` for why only these, and only here.
    """
    marks: list[tuple[QColor, bool]] = []
    if token.is_turn:
        # Filled while it is still there, hollow once it is gone: a spent
        # move should read as an outline of the thing you no longer have.
        marks.append((_MOVE_LEFT, token.squares_left > 0))
        marks.append((_ACTION_LEFT, not token.acted))
    if token.reacted:
        marks.append((_REACTED, True))
    return marks


@dataclass(frozen=True)
class Placement:
    """Where a token is being drawn this frame, in squares."""

    x: float
    y: float
    opacity: float
    #: How much smaller than a square, per side, as a fraction of one: a body on
    #: the floor is drawn a size down.
    shrink: float
    #: Going down right now, as opposed to already lying there.
    falling: bool = False


@dataclass(frozen=True)
class Floating:
    """A number rising off a token, part-way through its second on screen."""

    square: tuple[int, int]
    text: str
    colour: QColor
    #: How far it has risen, in squares.
    rise: float
    alpha: float


#: How big the wheel is, as a fraction of the map's smaller side, and where its
#: ring sits. Kept in one place because a wedge that is drawn and a wedge that
#: is hit-tested disagreeing is the sort of bug nobody sees until they misclick.
RADIAL_INNER = 0.9
RADIAL_OUTER = 2.6


@dataclass
class _Effect:
    """One thing being shown, and how far through it is.

    Time-based rather than frame-based: a laptop that drops frames should show
    a shorter animation, not a slower one, or four people watching the same
    fight fall out of step within a round.
    """

    kind: str
    combatant: int
    duration: float
    started: float = 0.0
    #: Held back this long before it begins. A swing provoked partway along
    #: somebody's walk waits until they get there.
    delay: float = 0.0
    path: list[tuple[int, int]] = field(default_factory=list)
    toward: tuple[int, int] | None = None
    text: str = ""
    colour: QColor = field(default_factory=lambda: QColor(_HURT))

    def progress(self, now: float) -> float:
        if self.duration <= 0:
            return 1.0
        return min(1.0, max(0.0, (now - self.started - self.delay) / self.duration))

    def waiting(self, now: float) -> bool:
        """Not started yet. Nothing of it is drawn while this is true."""
        return now < self.started + self.delay

    def done(self, now: float) -> bool:
        return not self.waiting(now) and self.progress(now) >= 1.0


class GridMap(QWidget):
    """A grid of squares with tokens standing on them."""

    #: A token was clicked. -1 when the click landed on empty floor.
    picked = Signal(int)
    #: An empty square was clicked: (x, y).
    square_clicked = Signal(int, int)
    #: Right-click: (combatant id or -1, global position).
    menu_requested = Signal(int, QPoint)
    #: (combatant id, x, y) -- someone dragged a row of the initiative order
    #: onto a square. The commonest way a token gets onto the map at all.
    dropped = Signal(int, int, int)
    #: (x, y) -- ctrl-click: put something in the way here, or take it away.
    obstacle_toggled = Signal(int, int)
    #: (columns, rows) to add -- negative to take away. The buttons on the edge
    #: of the map itself, because "the room is bigger than that" is a thought
    #: you have while looking at the room.
    zoom_changed = Signal()
    #: A turn somebody lined up on the map: see :class:`TurnPlan`. Emitted when
    #: it is complete -- a move with a square, an attack with a target.
    planned = Signal(object)
    #: The wheel opened or closed, so a panel can say what is being asked.
    radial_changed = Signal(bool)
    #: Space was pressed on a token. The panel knows what that creature can do
    #: -- its weapons come off a sheet this widget has never seen -- so it
    #: answers with :meth:`offer`.
    radial_wanted = Signal(int)
    #: Enter was pressed: carry out whatever is staged. The map does not know
    #: whether anything is, which is the panel's business -- this only reports
    #: the keypress, so there is one place that decides what it means.
    commit_wanted = Signal()
    #: Escape was pressed with no wheel open, which is the other thing Escape
    #: means once a turn can be held: forget it.
    staging_cancelled = Signal()
    #: Something that is drawn changed -- a token, a wall, a frame of a walk.
    #: For the 3D view, which draws this map's state rather than keeping its
    #: own: see :meth:`update`.
    shown = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._width = 20
        self._height = 15
        self._tokens: list[Token] = []
        #: Squares nobody may stand in. Terrain, so everyone sees the same ones.
        self._obstacles: set[tuple[int, int]] = set()
        #: A turn on offer, drawn over the map until it is answered.
        self._preview: Preview | None = None
        #: Held while a turn is waiting on an answer: the map still reads
        #: normally -- it is showing the thing being decided -- but it stops
        #: taking clicks, so fiddling with it cannot be mistaken for answering.
        self.frozen = False
        self._read_only = False
        #: The one creature a read-only map may still take a turn for: a
        #: player's own character, while it is its turn. Read-only was never
        #: quite the question -- a player may not build terrain or move the
        #: fight about, and may absolutely move *themselves*.
        self.acts_for: int | None = None
        #: How small and how large the grid may get. Set by whoever owns the
        #: data; this widget only needs it to grey the right button out.
        #: The size of a square in pixels when somebody has chosen one, and
        #: None while the map is fitting itself to the panel. None rather than
        #: a number equal to the fitted size, because "fit" has to survive the
        #: panel being resized -- and a number would not.
        self._zoom: int | None = None
        #: How far the board has been dragged from the middle, in pixels. Only
        #: ever non-zero when the board is bigger than the room for it.
        self._pan = QPoint(0, 0)
        #: Where a pan started, while one is happening.
        self._panning_from: QPoint | None = None
        self._selected: int | None = None
        #: The wheel, when it is open: the combatant it belongs to and what it
        #: is offering. None when it is not.
        self._radial: int | None = None
        self._choices: list[Choice] = []
        #: Which wedge the mouse is over, so the wheel answers the pointer.
        self._hovering: int | None = None
        #: What the map is waiting for after a wedge was picked: "move" for a
        #: square, "attack" for a creature, or None.
        self._awaiting: Choice | None = None
        #: The walk to wherever the pointer is, while a move is awaited. Shown
        #: rather than left to the imagination, because "how many squares is
        #: that corner" is exactly the question a grid is supposed to answer
        #: without anybody counting on their fingers.
        self._hover_path: list[tuple[int, int]] = []
        #: The square something is being dragged over, outlined so the drop
        #: lands where the pointer says it will.
        self._hover: tuple[int, int] | None = None
        self.setMinimumSize(220, 180)
        self.setMouseTracking(False)
        self.setAcceptDrops(True)
        #: The map takes the keyboard, because the wheel opens on Space and the
        #: whole idea is that your attention never leaves the battlefield. Click
        #: focus rather than tab focus: nobody tabs their way onto a map.
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        #: What is being shown right now. Empty almost always, and the timer
        #: only runs while it is not.
        self._effects: list[_Effect] = []
        #: Tokens the state has already dropped but that are still being shown
        #: leaving. Cleared when their effect ends.
        self._leaving: dict[int, Token] = {}
        self._frames = QTimer(self)
        self._frames.setInterval(FRAME_MS)
        self._frames.timeout.connect(self._next_frame)

    def update(self, *args) -> None:
        """Repaint, and tell anyone else drawing this map that it changed.

        Every change to what is on the map already ends here, because the flat
        map has to repaint for it. Hanging the notice on the same call means the
        3D view cannot miss a change that somebody adds later and only thinks to
        repaint for -- which is how two views of one fight would come to
        disagree.
        """
        super().update(*args)
        self.shown.emit()

    # ------------------------------------------------------------------ input

    @property
    def read_only(self) -> bool:
        return self._read_only

    @read_only.setter
    def read_only(self, value: bool) -> None:
        self._read_only = bool(value)
        self.update()

    def set_grid(self, width: int, height: int) -> None:
        changed = (width, height) != (self._width, self._height)
        self._width = max(1, int(width))
        self._height = max(1, int(height))
        if changed:
            # A different map is a different thing to be looking at, and the
            # corner you had scrolled to is not a place on it.
            self._pan = QPoint(0, 0)
        self.update()

    def set_tokens(self, tokens: list[Token]) -> None:
        self._tokens = list(tokens)
        # A token that has left the fight cannot stay selected: the next click
        # on an empty square would try to place something that is not there.
        if self._selected is not None and not any(t.id == self._selected for t in tokens):
            self._selected = None
        self.update()

    def set_obstacles(self, squares) -> None:
        self._obstacles = {(int(x), int(y)) for x, y in squares}
        self.update()

    def set_preview(self, preview: Preview | None) -> None:
        self._preview = preview
        self.update()

    # ------------------------------------------------------------- showing it

    def play(self, event: dict) -> None:
        """Show what the host says happened.

        The host describes it -- the whole walk, whether the swing landed, how
        much it cost -- so this only has to draw it. Working the same thing out
        from two states would give every screen its own version of the fight,
        started at its own moment.
        """
        kind = str(event.get("kind", ""))
        combatant = event.get("combatant")
        if not isinstance(combatant, int):
            return

        if kind == "move":
            path = [
                (int(square[0]), int(square[1]))
                for square in event.get("path") or ()
                if isinstance(square, (list, tuple)) and len(square) == 2
            ]
            if len(path) > 1:
                self._begin(
                    _Effect(
                        kind="move",
                        combatant=combatant,
                        duration=STEP_MS * (len(path) - 1) / 1000,
                        path=path,
                    )
                )
        elif kind == "attack":
            hit = bool(event.get("hit"))
            damage = int(event.get("damage") or 0)
            target = event.get("target")
            # An opportunity attack lands partway along somebody's walk, and
            # the host says how far along. Held until they get there: a swing
            # thrown as they set off looks like the watcher knew where they
            # were going before they went.
            wait = self._wait_for_step(target, event.get("after"))
            self._begin(
                _Effect(
                    kind="lunge",
                    combatant=combatant,
                    duration=LUNGE_MS / 1000,
                    delay=wait,
                    toward=self._square_of(target),
                )
            )
            if isinstance(target, int):
                self._begin(
                    _Effect(
                        kind="float",
                        combatant=target,
                        duration=FLOAT_MS / 1000,
                        delay=wait,
                        text=f"-{damage}" if hit and damage else "miss",
                        colour=QColor(_HURT if hit and damage else _MISSED),
                    )
                )
        elif kind == "down":
            # They stay on the map now, so this is a fall rather than an exit:
            # the token sinks and fades to a ghost and then stops there. Kept in
            # `_leaving` all the same, for the one case where they really do go
            # -- a DM taking a body out of the fight while it is still falling.
            for token in self._tokens:
                if token.id == combatant:
                    self._leaving[combatant] = token
                    break
            self._begin(
                _Effect(kind="down", combatant=combatant, duration=DOWN_MS / 1000)
            )

    def _wait_for_step(self, walker, step) -> float:
        """How long until ``walker`` is that many squares into its walk.

        Nothing to wait for when there is no walk running -- an ordinary swing
        on somebody standing still, or a walk that was never sent because the
        blow that provoked it stopped them leaving. Timed against the walk
        itself rather than counted in squares, because the walk eases in and
        out: four squares along is not four-tenths of the way through the time.
        """
        if not isinstance(walker, int) or not isinstance(step, int) or step <= 0:
            return 0.0
        walking = self._effect_on(walker, "move")
        if walking is None or len(walking.path) < 2:
            return 0.0
        squares = len(walking.path) - 1
        return when_eased(min(1.0, step / squares)) * walking.duration

    def _begin(self, effect: _Effect) -> None:
        effect.started = time.monotonic()
        # One of each kind per token: a second walk replaces the first rather
        # than drawing the token in two places at once.
        self._effects = [
            other
            for other in self._effects
            if not (other.kind == effect.kind and other.combatant == effect.combatant)
        ]
        self._effects.append(effect)
        if not self._frames.isActive():
            self._frames.start()
        self.update()

    def _next_frame(self) -> None:
        now = time.monotonic()
        self._effects = [effect for effect in self._effects if not effect.done(now)]
        still_going = {effect.combatant for effect in self._effects}
        self._leaving = {
            combatant: token
            for combatant, token in self._leaving.items()
            if combatant in still_going
        }
        if not self._effects:
            self._frames.stop()
        self.update()

    def _drawable(self) -> list[Token]:
        """What to draw: what is there, plus what is still on its way out."""
        showing = {token.id for token in self._tokens}
        return self._tokens + [
            token for combatant, token in self._leaving.items() if combatant not in showing
        ]

    def _effect_on(self, combatant_id: int, kind: str) -> _Effect | None:
        for effect in self._effects:
            if effect.combatant == combatant_id and effect.kind == kind:
                return effect
        return None

    def _square_of(self, combatant_id) -> tuple[int, int] | None:
        for token in self._drawable():
            if token.id == combatant_id:
                return token.x, token.y
        return None


    # ------------------------------------------------------------- the wheel
    #
    # Space opens a ring of choices around whoever is up. The point is that
    # your hand is already on the map: the alternative is a dialog somewhere
    # else, which means looking away from the thing you are deciding about.

    def offer(self, combatant_id: int, choices: list[Choice]) -> None:
        """Open the wheel around one token. No choices means no wheel."""
        if not choices or not any(t.id == combatant_id for t in self._drawable()):
            return
        self._radial = combatant_id
        self._choices = list(choices)
        self._hovering = None
        self._awaiting = None
        # Only while the wheel is up: the rest of the time a move event per
        # pixel is work for nothing.
        self.setMouseTracking(True)
        self.radial_changed.emit(True)
        self.update()

    def close_radial(self) -> None:
        was = self._radial is not None or self._awaiting is not None
        self._radial = None
        self._choices = []
        self._hovering = None
        self._awaiting = None
        self._hover_path = []
        self.setMouseTracking(False)
        if was:
            self.radial_changed.emit(False)
        self.update()

    def _may_act(self) -> bool:
        """Whether this map may take a turn for whatever is selected."""
        if not self.read_only:
            return True
        return self.acts_for is not None and self._selected == self.acts_for

    @property
    def radial_open(self) -> bool:
        return self._radial is not None

    @property
    def awaiting(self) -> Choice | None:
        """What the map is waiting to be pointed at, having been asked."""
        return self._awaiting

    def _radial_centre(self, cell: int, origin: QPoint) -> QPoint | None:
        for token in self._drawable():
            if token.id == self._radial:
                square = self._at(token.x, token.y, cell, origin)
                return square.center()
        return None

    def _wedge_at(self, point: QPoint, cell: int, origin: QPoint) -> int | None:
        """Which wedge the point falls in, or None for outside the ring."""
        centre = self._radial_centre(cell, origin)
        if centre is None or not self._choices:
            return None
        dx = point.x() - centre.x()
        dy = point.y() - centre.y()
        away = math.hypot(dx, dy)
        if not (cell * RADIAL_INNER <= away <= cell * RADIAL_OUTER):
            return None
        # Straight up is the first wedge, and they run clockwise, because that
        # is the order the labels are read in.
        angle = (math.degrees(math.atan2(dx, -dy)) + 360.0) % 360.0
        return int(angle // (360.0 / len(self._choices)))

    def select(self, combatant_id: int | None) -> None:
        self._selected = combatant_id
        self.update()

    @property
    def selected(self) -> int | None:
        return self._selected

    # ------------------------------------------------------ for the 3D view
    #
    # The 3D view is a second way of drawing *this* map, not a second map. It
    # reads what is here and sends what the person did back through the same
    # doors a click on the flat map goes through, so a rule about what a click
    # means is written once -- and a table where the DM looks at the fight in
    # 3D and a player looks at it flat is still one fight.

    @property
    def grid_size(self) -> tuple[int, int]:
        return self._width, self._height

    @property
    def obstacles(self) -> frozenset[tuple[int, int]]:
        return frozenset(self._obstacles)

    @property
    def preview(self) -> Preview | None:
        return self._preview

    @property
    def choices(self) -> list[Choice]:
        """What the open wheel is offering. Empty when it is shut."""
        return list(self._choices) if self._radial is not None else []

    def tokens_shown(self) -> list[Token]:
        """Everything drawn, including whoever is still on their way out."""
        return self._drawable()

    def choose(self, index: int) -> None:
        """Pick one wedge of the open wheel, as a click on it would."""
        if self._radial is None or not 0 <= index < len(self._choices):
            return
        self._picked_wedge(self._choices[index])

    def point_at(self, square: tuple[int, int] | None) -> None:
        """The pointer is over ``square`` -- or over nothing, for None.

        Only matters while a move is waiting for its square: that is when the
        walk to wherever the pointer is gets drawn.
        """
        if self._awaiting is None or self._awaiting.kind != "move":
            return
        path = self._path_to(square)
        if path != self._hover_path:
            self._hover_path = path
            self.update()

    def hover_walk(self) -> list[tuple[tuple[int, int], bool]]:
        """The walk being pointed at, square by square, and whether each is in reach.

        The first square is where the walker stands. Worked out here rather
        than by whoever draws it, so the flat line and the 3D one turn red at
        the same square.
        """
        if len(self._hover_path) < 2:
            return []
        mover = next((t for t in self._drawable() if t.id == self._selected), None)
        if mover is None:
            return []
        steps = len(self._hover_path) - 1
        reach = mover.squares_left if mover.is_turn else steps
        return [(square, index <= reach) for index, square in enumerate(self._hover_path)]

    def press_square(
        self,
        square: tuple[int, int] | None,
        button: Qt.MouseButton,
        modifiers: Qt.KeyboardModifier,
        global_position: QPoint,
    ) -> None:
        """What a click on ``square`` means, wherever it was clicked.

        Everything a press does once it is known which square it landed on.
        The wheel and the middle-button pan are not here: both are about
        pixels, and each view has its own.
        """
        if self.frozen:
            return
        token = self._token_at(*square) if square else None

        # Having been asked for a square or a creature, the next click answers.
        if self._awaiting is not None and button == Qt.MouseButton.LeftButton:
            self._answer_with(square, token)
            return

        # Ctrl-click builds the room: something in the way, or no longer. Held
        # rather than moded, because a DM adding one rock should not have to
        # remember to turn a tool off before moving the next goblin.
        if (
            not self.read_only
            and square is not None
            and button == Qt.MouseButton.LeftButton
            and modifiers & Qt.KeyboardModifier.ControlModifier
        ):
            self.obstacle_toggled.emit(square[0], square[1])
            return

        if button == Qt.MouseButton.RightButton:
            self.menu_requested.emit(token.id if token else -1, global_position)
            return

        if token is not None:
            self._selected = token.id
            self.picked.emit(token.id)
            self.update()
            return

        self.picked.emit(-1)
        if square is not None:
            self.square_clicked.emit(square[0], square[1])

    def drop_on(self, combatant_id: int, square: tuple[int, int] | None) -> None:
        """A row of the initiative order let go of over ``square``."""
        if self.read_only or square is None:
            return
        self.dropped.emit(combatant_id, square[0], square[1])

    # ---------------------------------------------------------------- drawing

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        return QSize(RULER + self._width * 28, RULER + self._height * 28)

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        return grid.bounds(self._width, self._height)

    def _fitted(self) -> int:
        """The size of a square that would show the whole map at once."""
        by_width = (self.width() - RULER) // max(1, self._width)
        by_height = (self.height() - RULER) // max(1, self._height)
        return max(MIN_CELL, min(MAX_CELL, min(by_width, by_height)))

    def _cell(self) -> int:
        if self._zoom is None:
            return self._fitted()
        return max(ZOOM_MIN, min(ZOOM_MAX, self._zoom))

    def _origin(self) -> QPoint:
        """Where the top-left *square* is drawn, in pixels.

        Centred when the board fits, panned when it does not. The rulers do not
        move: they are pinned to the edges and the board slides under them, the
        way a spreadsheet keeps its column letters. Scrolling the coordinates
        off the screen would take away the thing that makes a square sayable
        out loud, which is the whole reason they are drawn.
        """
        cell = self._cell()
        spare_x = self.width() - RULER - cell * self._width
        spare_y = self.height() - RULER - cell * self._height
        pan = self._clamped_pan(cell)
        return QPoint(
            RULER + max(0, spare_x // 2) + pan.x(),
            RULER + max(0, spare_y // 2) + pan.y(),
        )

    def _clamped_pan(self, cell: int) -> QPoint:
        """The pan, cut down to one that still leaves the board on screen.

        Held here rather than at the point of dragging, because the panel gets
        resized too -- and a pan that was legal in a wide dock must not strand
        the board off the edge of a narrow one.
        """
        room_x = self.width() - RULER - cell * self._width
        room_y = self.height() - RULER - cell * self._height
        # Nothing to pan when it all fits: centred is the only sensible answer,
        # and a map that could be nudged around inside spare room would be a
        # map that never sits still.
        x = 0 if room_x >= 0 else max(room_x, min(0, self._pan.x()))
        y = 0 if room_y >= 0 else max(room_y, min(0, self._pan.y()))
        return QPoint(x, y)

    # ------------------------------------------------------------------- zoom

    @property
    def zoom(self) -> int:
        """The size of a square, in pixels, whether chosen or worked out."""
        return self._cell()

    @property
    def fitting(self) -> bool:
        """True while the map is sizing itself to the panel."""
        return self._zoom is None

    def zoom_by(self, notches: int, toward: QPoint | None = None) -> None:
        """Zoom in or out, keeping ``toward`` over the same square.

        Anchoring matters more than it sounds: zooming about the centre means
        the thing you were looking at slides away as you close in on it, and
        you chase it with two more gestures. Anchoring on the pointer -- or on
        the middle when there is no pointer, as from a menu -- is what makes
        one notch enough.
        """
        if not notches:
            return
        before = self._cell()
        wanted = before * (ZOOM_STEP ** notches)
        after = max(ZOOM_MIN, min(ZOOM_MAX, int(round(wanted))))
        # Rounding can eat a whole notch when the squares are small, which
        # reads as a wheel that does nothing.
        if after == before:
            after = max(ZOOM_MIN, min(ZOOM_MAX, before + (1 if notches > 0 else -1)))
        if after == before:
            return

        anchor = toward if toward is not None else self.rect().center()
        origin = self._origin()
        # Where the anchor sits on the board, in squares -- a fraction, so the
        # square stays under the pointer rather than jumping to its corner.
        away_x = (anchor.x() - origin.x()) / before
        away_y = (anchor.y() - origin.y()) / before

        self._zoom = after
        # Solve for the pan that puts that same spot back under the anchor.
        spare_x = self.width() - RULER - after * self._width
        spare_y = self.height() - RULER - after * self._height
        self._pan = QPoint(
            int(anchor.x() - away_x * after - RULER - max(0, spare_x // 2)),
            int(anchor.y() - away_y * after - RULER - max(0, spare_y // 2)),
        )
        self.zoom_changed.emit()
        self.update()

    def fit(self) -> None:
        """Go back to showing the whole map, and stay fitted as the panel moves."""
        self._zoom = None
        self._pan = QPoint(0, 0)
        self.zoom_changed.emit()
        self.update()

    def pan_by(self, dx: int, dy: int) -> None:
        """Slide the board a square at a time. Does nothing when it all fits."""
        cell = self._cell()
        self._pan = self._clamped_pan(cell) + QPoint(-dx * cell, -dy * cell)
        self._pan = self._clamped_pan(cell)
        self.update()

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """The wheel zooms. There is nothing else on a map for it to do."""
        if self.frozen:
            return
        notches = event.angleDelta().y() / 120.0
        if not notches:
            return
        self.zoom_by(
            1 if notches > 0 else -1, event.position().toPoint()
        )
        event.accept()

    def _square_at(self, point: QPoint) -> tuple[int, int] | None:
        """Which square a pixel is in, in map coordinates."""
        cell = self._cell()
        origin = self._origin()
        left, top, _right, _bottom = self.bounds
        column = (point.x() - origin.x()) // cell
        row = (point.y() - origin.y()) // cell
        if 0 <= column < self._width and 0 <= row < self._height:
            return int(left + column), int(top + row)
        return None

    def _at(self, x: int, y: int, cell: int, origin: QPoint) -> QRect:
        """The pixels of one square, from its map coordinates."""
        left, top, _right, _bottom = self.bounds
        return QRect(
            origin.x() + (x - left) * cell,
            origin.y() + (y - top) * cell,
            cell,
            cell,
        )

    def _path_to(self, square: tuple[int, int] | None) -> list[tuple[int, int]]:
        """The walk from whoever is moving to ``square``, or nothing.

        The same walk the host works out when the move actually happens --
        :func:`grid.route_between`, round whatever is in the way rather than
        through it -- so what is previewed is what would be taken. Both ends
        run the one function for exactly this reason: a client that found its
        own way round the same rock would draw a walk that never happened.

        Empty when there is no way at all, which draws nothing: a line to
        somewhere unreachable would be a promise the host is about to break.
        """
        if square is None or self._selected is None:
            return []
        mover = next((t for t in self._drawable() if t.id == self._selected), None)
        if mover is None or (mover.x, mover.y) == square:
            return []
        return grid.route_between(
            (mover.x, mover.y), square, self._in_the_way(mover, square), self.bounds
        )

    def _in_the_way(self, mover: Token, destination) -> set[tuple[int, int]]:
        """Squares the walk cannot go through: bodies and terrain alike.

        The same set the host builds, down to the two exceptions -- the fallen
        are walked over, and the destination is left out so that walking onto
        an occupied square is refused as a taken square rather than reported as
        unreachable.
        """
        blocked = set(self._obstacles)
        for token in self._drawable():
            if token.id != mover.id and not token.down:
                blocked.add((token.x, token.y))
        blocked.discard(destination)
        return blocked

    def _token_at(self, x: int, y: int) -> Token | None:
        for token in self._tokens:
            if token.x == x and token.y == y:
                return token
        return None

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        cell = self._cell()
        origin = self._origin()
        board = QRect(origin.x(), origin.y(), cell * self._width, cell * self._height)

        # Everything about the fight is kept out of the ruler strips. Once the
        # board can be panned it runs under them, and a coordinate you cannot
        # read is a square nobody can name out loud -- which is the one thing
        # the rulers are for.
        painter.save()
        painter.setClipRect(self._viewport())

        painter.fillRect(board, palette.base())

        left, top, right, bottom = self.bounds

        # Under the grid lines: terrain is the floor, and lines drawn over it
        # keep the squares countable across a rock the way they are elsewhere.
        for x, y in self._obstacles:
            if left <= x <= right and top <= y <= bottom:
                painter.fillRect(self._at(x, y, cell, origin), _STONE)

        # Lines faint enough to read tokens over. The two through 0,0 are
        # heaviest, because they are what "minus three, two" is counted from;
        # every fifth after that, since five squares is twenty-five feet and
        # that is the distance anyone at a table actually counts.
        text = palette.text().color()
        faint = QColor(text)
        faint.setAlpha(45)
        strong = QColor(text)
        strong.setAlpha(95)
        axis = QColor(text)
        axis.setAlpha(150)

        for column in range(self._width + 1):
            here = left + column
            painter.setPen(
                QPen(axis if here == 0 else strong if here % 5 == 0 else faint)
            )
            x = origin.x() + column * cell
            painter.drawLine(x, board.top(), x, board.bottom())
        for row in range(self._height + 1):
            here = top + row
            painter.setPen(
                QPen(axis if here == 0 else strong if here % 5 == 0 else faint)
            )
            y = origin.y() + row * cell
            painter.drawLine(board.left(), y, board.right(), y)

        if self._hover is not None:
            painter.setPen(QPen(self.palette().highlight().color(), max(2, cell // 8)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._at(self._hover[0], self._hover[1], cell, origin))

        now = time.monotonic()
        for token in self._drawable():
            self._draw_token(painter, token, None, cell, origin, now)

        self._draw_preview(painter, cell, origin)
        self._draw_move_preview(painter, cell, origin)
        self._draw_radial(painter, cell, origin, now)
        self._draw_numbers(painter, cell, origin, now)

        painter.restore()
        self._draw_rulers(painter, cell, origin)
        painter.end()

    def _viewport(self) -> QRect:
        """The part of the widget the board is allowed into."""
        return QRect(
            RULER, RULER, max(0, self.width() - RULER), max(0, self.height() - RULER)
        )

    def _draw_radial(
        self, painter: QPainter, cell: int, origin: QPoint, now: float
    ) -> None:
        """The wheel of choices, around whoever it belongs to.

        Drawn over the map on purpose. It covers a few squares while it is
        open, and it is open for about a second: the alternative is putting the
        choices somewhere with room for them, which means somewhere that is not
        where you are looking.
        """
        centre = self._radial_centre(cell, origin)
        if centre is None or not self._choices:
            return

        inner = cell * RADIAL_INNER
        outer = cell * RADIAL_OUTER
        box = QRect(
            int(centre.x() - outer),
            int(centre.y() - outer),
            int(outer * 2),
            int(outer * 2),
        )
        hole = QRect(
            int(centre.x() - inner),
            int(centre.y() - inner),
            int(inner * 2),
            int(inner * 2),
        )
        span = 360.0 / len(self._choices)

        palette = self.palette()
        font = QFont(painter.font())
        font.setPixelSize(max(9, min(14, cell // 3)))
        painter.setFont(font)
        metrics = painter.fontMetrics()

        for index, choice in enumerate(self._choices):
            # Qt measures from three o'clock anticlockwise in sixteenths of a
            # degree; the wheel reads from twelve o'clock clockwise. Hence 90.
            start = 90.0 - (index + 1) * span
            fill = QColor(palette.window().color())
            fill.setAlpha(235)
            if index == self._hovering:
                fill = QColor(palette.highlight().color())
                fill.setAlpha(235)
            painter.setBrush(fill)
            painter.setPen(QPen(QColor(palette.text().color()), 1))
            painter.drawPie(box, int(start * 16), int(span * 16) - 12)

            middle = math.radians(start + span / 2.0)
            reach = (inner + outer) / 2.0
            label = metrics.elidedText(
                choice.label,
                Qt.TextElideMode.ElideRight,
                int((outer - inner) * 1.6),
            )
            where = QPoint(
                int(centre.x() + math.cos(middle) * reach),
                int(centre.y() - math.sin(middle) * reach),
            )
            painter.setPen(
                QPen(
                    palette.highlightedText().color()
                    if index == self._hovering
                    else palette.text().color()
                )
            )
            painter.drawText(
                QRect(where.x() - 60, where.y() - 10, 120, 20),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )

        # Punch the token back out of the middle, so the wheel never hides the
        # creature it belongs to: whose turn this is has to stay readable while
        # you are deciding what they do.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(palette.base())
        painter.drawEllipse(hole)
        for token in self._drawable():
            if token.id == self._radial:
                self._draw_token(painter, token, None, cell, origin, now)
                break

    def _draw_numbers(
        self, painter: QPainter, cell: int, origin: QPoint, now: float
    ) -> None:
        """Damage rising off whoever took it, and fading.

        Drawn last, over everything: it is the one thing on the map that is
        about a moment rather than a state, and it has a second to be read.
        """
        font = QFont(painter.font())
        font.setPixelSize(max(11, cell // 2))
        font.setBold(True)
        painter.setFont(font)

        for number in self.floating(now):
            box = self._at(number.square[0], number.square[1], cell, origin)
            colour = QColor(number.colour)
            colour.setAlpha(int(255 * number.alpha))
            painter.setPen(QPen(colour))
            painter.drawText(
                box.translated(0, int(-cell * number.rise)),
                Qt.AlignmentFlag.AlignCenter,
                number.text,
            )

    def _draw_preview(self, painter: QPainter, cell: int, origin: QPoint) -> None:
        """The turn on offer: where they would go, and who they would hit."""
        if self._preview is None:
            return
        actor = next(
            (t for t in self._tokens if t.id == self._preview.token), None
        )
        if actor is None:
            return

        start = self._at(actor.x, actor.y, cell, origin).center()

        if self._preview.to is not None:
            end = self._at(*self._preview.to, cell, origin).center()
            painter.setPen(
                QPen(_PLAN, max(2, cell // 12), Qt.PenStyle.DashLine)
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(start, end)

            ghost = QColor(_OURS if actor.ours else _THEIRS)
            ghost.setAlpha(110)
            box = self._at(*self._preview.to, cell, origin).adjusted(
                cell // 8, cell // 8, -cell // 8, -cell // 8
            )
            painter.setPen(QPen(_PLAN, max(1, cell // 16), Qt.PenStyle.DashLine))
            painter.setBrush(ghost)
            painter.drawEllipse(box)

        if self._preview.target is not None:
            self._draw_sword(
                painter, self._at(*self._preview.target, cell, origin), cell
            )

    def _draw_move_preview(self, painter: QPainter, cell: int, origin: QPoint) -> None:
        """The walk to wherever the pointer is, while a move is being lined up.

        Drawn rather than left to be counted: "how many squares is that
        corner" is exactly the question a grid exists to answer, and making
        the DM count it by eye is the grid failing at its one job. The line
        follows :func:`grid.steps_between` -- the same walk the host would
        actually send -- so what is previewed is never a different line from
        what gets animated once the square is clicked.

        The part beyond what this turn has left turns the warning colour, the
        same one a spent reaction is marked in: a DM should see a move refused
        before clicking it, not after. Going round something rather than
        through it is what makes that worth drawing at all -- the long way
        costs the long way, and the line is where you find that out.
        """
        walk = self.hover_walk()
        if not walk:
            return
        mover = next(t for t in self._drawable() if t.id == self._selected)

        reach = max(
            (index for index, (_square, near) in enumerate(walk) if near), default=0
        )
        centres = [self._at(x, y, cell, origin).center() for (x, y), _near in walk]

        def _segment(points: list[QPoint], colour: QColor) -> None:
            if len(points) < 2:
                return
            painter.setPen(QPen(colour, max(2, cell // 12), Qt.PenStyle.DashLine))
            for a, b in zip(points, points[1:]):
                painter.drawLine(a, b)

        _segment(centres[: reach + 1], _PLAN)
        _segment(centres[reach:], _HURT)

        # A dot on every square walked through, not only the ends -- the count
        # of them is the distance, and a distance is easier to see than to add.
        painter.setPen(Qt.PenStyle.NoPen)
        for index, point in enumerate(centres[1:-1], start=1):
            painter.setBrush(_PLAN if index <= reach else _HURT)
            radius = max(2, cell // 10)
            painter.drawEllipse(point, radius, radius)

        end = self._hover_path[-1]
        ghost = QColor(_OURS if mover.ours else _THEIRS)
        ghost.setAlpha(110)
        box = self._at(*end, cell, origin).adjusted(
            cell // 8, cell // 8, -cell // 8, -cell // 8
        )
        ring = _PLAN if walk[-1][1] else _HURT
        painter.setPen(QPen(ring, max(1, cell // 16), Qt.PenStyle.DashLine))
        painter.setBrush(ghost)
        painter.drawEllipse(box)

    @staticmethod
    def _draw_sword(painter: QPainter, square, cell: int) -> None:
        """A sword over whoever is about to be hit.

        Drawn rather than written: at this size a word is unreadable and a
        shape is not, and the only question it has to answer is "that one?".
        """
        pen = QPen(_BLADE, max(2, cell // 9))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        middle = square.center()
        reach = cell // 3
        # The blade, running corner to corner, and a crossguard across it.
        painter.drawLine(
            middle.x() - reach, middle.y() + reach,
            middle.x() + reach, middle.y() - reach,
        )
        guard = cell // 6
        painter.drawLine(
            middle.x() - reach + guard // 2, middle.y() + reach - guard * 2,
            middle.x() - reach + guard * 2, middle.y() + reach - guard // 2,
        )

    def _draw_rulers(self, painter: QPainter, cell: int, origin: QPoint) -> None:
        """The numbers along the top and left.

        This is what makes a square something people can say out loud. Every
        square is labelled when there is room; when there is not, every second
        or fifth, and 0 always -- the one anybody counts from.

        Pinned to the edges of the panel rather than to the board, so panning
        slides the columns along under a ruler that stays put -- the way a
        spreadsheet keeps its column letters. Scrolling the coordinates off the
        screen would take away the one thing they are for.
        """
        left, top, _right, _bottom = self.bounds
        step = 1 if cell >= 30 else 2 if cell >= 20 else 5
        viewport = self._viewport()

        # An opaque strip, because the board now runs underneath it.
        painter.fillRect(QRect(0, 0, self.width(), RULER), self.palette().window())
        painter.fillRect(QRect(0, 0, RULER, self.height()), self.palette().window())

        font = QFont(painter.font())
        font.setPixelSize(max(8, min(12, cell // 2)))
        painter.setFont(font)
        ink = QColor(self.palette().text().color())
        ink.setAlpha(190)
        painter.setPen(QPen(ink))

        for column in range(self._width):
            here = left + column
            if here != 0 and here % step:
                continue
            x = origin.x() + column * cell
            if x + cell <= viewport.left() or x >= viewport.right():
                continue  # that column is off the side; its number would be too
            painter.drawText(
                QRect(x, 0, cell, RULER), Qt.AlignmentFlag.AlignCenter, str(here)
            )
        for row in range(self._height):
            here = top + row
            if here != 0 and here % step:
                continue
            y = origin.y() + row * cell
            if y + cell <= viewport.top() or y >= viewport.bottom():
                continue
            painter.drawText(
                QRect(0, y, RULER - 3, cell),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                str(here),
            )

    def _where_to_draw(
        self, token: Token, cell: int, origin: QPoint, now: float
    ) -> tuple[QRect, float]:
        """The token's square right now, and how solid it is.

        While something is being shown for a token, the animation owns where it
        is drawn -- the state underneath has already moved on, and drawing from
        that would put it at its destination before it has walked there.
        """
        where = self.placement(token, now)
        left, top, _right, _bottom = self.bounds
        square = QRect(
            int(origin.x() + (where.x - left) * cell),
            int(origin.y() + (where.y - top) * cell),
            cell,
            cell,
        )
        shrink = int(cell * where.shrink)
        return square.adjusted(shrink, shrink, -shrink, -shrink), where.opacity

    def placement(self, token: Token, now: float | None = None) -> "Placement":
        """Where a token is right now, in squares, and how it looks there.

        In squares rather than pixels so that both views move a creature along
        the same walk at the same moment: the 3D view has no pixels to share,
        and two copies of the easing would be two walks.
        """
        now = time.monotonic() if now is None else now
        x, y = float(token.x), float(token.y)
        opacity = 1.0
        shrink = 0.0

        walking = self._effect_on(token.id, "move")
        if walking and len(walking.path) > 1:
            done = eased(walking.progress(now))
            steps = len(walking.path) - 1
            exact = done * steps
            index = min(steps - 1, int(exact))
            (ax, ay), (bx, by) = walking.path[index], walking.path[index + 1]
            between = exact - index
            x = ax + (bx - ax) * between
            y = ay + (by - ay) * between

        lunging = self._effect_on(token.id, "lunge")
        if lunging and lunging.toward is not None and not lunging.waiting(now):
            done = lunging.progress(now)
            # Out and back, so it reads as a swing rather than a step.
            reach = (1.0 - abs(done * 2 - 1.0)) * 0.4
            x += (lunging.toward[0] - x) * reach
            y += (lunging.toward[1] - y) * reach

        falling = self._effect_on(token.id, "down")
        if falling:
            # Down to the ghost and no further. A token that faded to nothing
            # would say "gone", and they are not gone -- they are on the floor,
            # on that square, and somebody can reach them.
            done = falling.progress(now)
            opacity = 1.0 - done * (1.0 - GHOST_OPACITY)
            shrink = done * 0.2
        elif token.down:
            opacity = GHOST_OPACITY
            shrink = 0.2

        return Placement(x, y, opacity, shrink, falling is not None)

    def floating(self, now: float | None = None) -> list["Floating"]:
        """The numbers rising off whoever was just hit, as they stand now."""
        now = time.monotonic() if now is None else now
        shown = []
        for effect in self._effects:
            if effect.kind != "float" or effect.waiting(now):
                continue
            square = self._square_of(effect.combatant) or effect.toward
            if square is None:
                continue
            done = effect.progress(now)
            # Rises a square's height over its life, and fades over the last
            # third, so it is readable before it starts going.
            shown.append(
                Floating(
                    square=square,
                    text=effect.text,
                    colour=QColor(effect.colour),
                    rise=done,
                    alpha=min(1.0, (1.0 - done) * 3),
                )
            )
        return shown

    def _draw_token(
        self,
        painter: QPainter,
        token: Token,
        dragged_to: tuple[int, int] | None,
        cell: int,
        origin: QPoint,
        now: float,
    ) -> None:
        if dragged_to is not None:
            square = self._at(dragged_to[0], dragged_to[1], cell, origin)
            opacity = 1.0
        else:
            square, opacity = self._where_to_draw(token, cell, origin, now)

        margin = max(2, cell // 10)
        box = square.adjusted(margin, margin, -margin, -margin)
        going_down = self._effect_on(token.id, "down") is not None
        fill = QColor(
            _GHOST if (token.down or going_down)
            else (_OURS if token.ours else _THEIRS)
        )
        fill.setAlphaF(fill.alphaF() * opacity)

        if token.unseen:
            pen = QPen(fill.darker(160), max(1, cell // 14), Qt.PenStyle.DotLine)
        else:
            pen = QPen(fill.darker(160), max(1, cell // 16))
        painter.setPen(pen)
        painter.setBrush(fill)
        painter.drawEllipse(box)

        if token.is_turn:
            ring = QPen(_TURN, max(2, cell // 9))
            painter.setPen(ring)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(box.adjusted(-margin, -margin, margin, margin))

        if token.id == self._selected:
            painter.setPen(QPen(self.palette().highlight().color(), max(1, cell // 14)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(square)

        if cell >= MIN_CELL + 4:
            font = QFont(painter.font())
            font.setPixelSize(max(8, cell // 3))
            font.setBold(True)
            painter.setFont(font)
            initials = QColor(255, 255, 255)
            initials.setAlphaF(opacity)
            painter.setPen(QPen(initials))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, token.initials)

        if not (token.down or going_down):
            self._draw_budget(painter, token, square, cell, opacity)

    def _draw_budget(
        self, painter: QPainter, token: Token, square: QRect, cell: int, opacity: float
    ) -> None:
        """What this creature has left, under its feet.

        Two questions, and they want opposite answers. *What is left of my
        turn* is asked about one creature -- whoever is up -- so its two pips
        are drawn only there; a boot and a blade on every token would be
        sixteen answers to a question about one of them. *Can that thing swing
        at me as I go past* is asked about everybody else, and its answer is
        interesting only when it is no: a mark that appears once the reaction
        is spent, rather than one that sits on every token saying "still".

        Beneath the token rather than over it, so nothing covers the initials
        that say who this is.
        """
        if cell < MIN_CELL + 6:
            return  # no room for a pip that anybody could tell from a speck

        size = max(5, cell // 5)
        gap = max(2, size // 3)
        marks = budget_marks(token)
        if not marks:
            return

        width = len(marks) * size + (len(marks) - 1) * gap
        x = square.center().x() - width // 2
        # Straddling the bottom of the token, like a badge. Wholly inside it
        # and the initials fight for the space; wholly below and it lands in
        # the next creature's square.
        y = square.bottom() - size

        # A ring in the board's own colour behind every pip, so green on blue
        # and red on red are both still a pip rather than a smudge.
        backing = QPen(self.palette().base().color(), max(1, size // 3))
        for colour, filled in marks:
            shade = QColor(colour)
            shade.setAlphaF(shade.alphaF() * opacity)
            spot = QRect(x, y, size, size)
            painter.setPen(backing)
            painter.setBrush(shade if filled else self.palette().base())
            painter.drawEllipse(spot)
            if not filled:
                # Spent: an outline where the thing used to be.
                painter.setPen(QPen(shade, max(1, size // 4)))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(spot.adjusted(1, 1, -1, -1))
            x += size + gap

    # ------------------------------------------------------------------ mouse


    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """Space opens the wheel on whoever is selected; Escape puts it away.

        Only where there is something to act with. The DM's map can act for
        anybody; a player's can act for their own character on its own turn and
        for nothing else. A wheel that offered choices it could not carry out
        would be a worse lie than no wheel.

        Enter carries out a staged turn and Escape forgets one. Escape does the
        nearer thing first: with a wheel open it closes the wheel, because that
        is what the person just opened and what they are looking at.
        """
        if event.key() == Qt.Key.Key_Escape:
            if self._radial is not None or self._awaiting is not None:
                self.close_radial()
            else:
                self.staging_cancelled.emit()
            return

        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.commit_wanted.emit()
            return

        # Zoom from the keyboard too, since a laptop trackpad is a poor wheel.
        # Both the main-row and the keypad keys, and Equal as well as Plus,
        # because "the plus key" is three different keys depending on layout.
        if event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_by(1)
            return
        if event.key() in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            self.zoom_by(-1)
            return
        if event.key() == Qt.Key.Key_0:
            self.fit()
            return
        if event.key() in _ARROWS:
            self.pan_by(*_ARROWS[event.key()])
            return

        if event.key() == Qt.Key.Key_Space and self._may_act():
            if self._radial is not None or self._awaiting is not None:
                self.close_radial()
            elif self._selected is not None:
                self.radial_wanted.emit(self._selected)
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if self.frozen:
            return

        # The wheel gets the click before the board does, so picking a wedge
        # over a token does not also select that token.
        if self._radial is not None:
            if event.button() == Qt.MouseButton.LeftButton:
                chosen = self._wedge_at(
                    event.position().toPoint(), self._cell(), self._origin()
                )
                if chosen is not None:
                    self._picked_wedge(self._choices[chosen])
                    return
            self.close_radial()
            return

        # The middle button drags the board about. Its own button rather than a
        # modifier on the left one, so panning can never be mistaken for the
        # gesture that moves a creature.
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning_from = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        self.press_square(
            self._square_at(event.position().toPoint()),
            event.button(),
            event.modifiers(),
            event.globalPosition().toPoint(),
        )


    def _picked_wedge(self, choice: Choice) -> None:
        """A wedge was chosen. Now the map waits to be pointed at something."""
        self._radial = None
        self._choices = []
        self._hovering = None
        self._awaiting = choice
        self._hover_path = []
        # Kept on only for a move: that is the one answer worth previewing
        # before it is clicked, and tracking costs nothing while the map is
        # otherwise idle waiting for the pointer.
        self.setMouseTracking(choice.kind == "move")
        self.radial_changed.emit(False)
        self.update()

    def _answer_with(self, square, token) -> None:
        """The click that completes what a wedge started.

        Nothing is asked twice: a plan goes out, and whether it is carried out
        immediately or held for confirmation is the panel's to decide. Clicking
        nothing useful puts the wheel away rather than leaving the map in a
        mode somebody has to guess their way out of.
        """
        choice, combatant = self._awaiting, self._selected
        self._awaiting = None
        self._hover_path = []
        self.setMouseTracking(False)
        if choice is None or combatant is None:
            self.update()
            return

        if choice.kind == "move" and square is not None:
            self.planned.emit(TurnPlan(combatant=combatant, move=square))
        elif choice.kind == "attack" and token is not None and token.id != combatant:
            self.planned.emit(
                TurnPlan(combatant=combatant, target=token.id, weapon=choice.weapon)
            )
        self.radial_changed.emit(False)
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if self._panning_from is not None:
            here = event.position().toPoint()
            self._pan += here - self._panning_from
            self._panning_from = here
            # Written back clamped, so a drag that runs past the edge does not
            # bank a pan you then have to unwind before the board moves again.
            self._pan = self._clamped_pan(self._cell())
            self.update()
            return
        if self._radial is not None:
            over = self._wedge_at(
                event.position().toPoint(), self._cell(), self._origin()
            )
            if over != self._hovering:
                self._hovering = over
                self.update()
            return
        self.point_at(self._square_at(event.position().toPoint()))

    def leaveEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        # The pointer left without landing on a square, so there is nothing
        # left to preview a walk toward.
        if self._hover_path:
            self._hover_path = []
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if self._panning_from is not None:
            self._panning_from = None
            self.unsetCursor()

    # ----------------------------------------------------------------- dropping

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if self._will_take(event):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt's name
        if not self._will_take(event):
            return
        square = self._square_at(event.position().toPoint())
        if square != self._hover:
            self._hover = square
            self.update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        self._hover = None
        self.update()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt's name
        square = self._square_at(event.position().toPoint())
        self._hover = None
        self.update()
        if not self._will_take(event) or square is None:
            return
        combatant_id = dragged_combatant(event.mimeData())
        if combatant_id is None:
            return
        event.acceptProposedAction()
        self.drop_on(combatant_id, square)

    def _will_take(self, event) -> bool:
        return not self.read_only and event.mimeData().hasFormat(COMBATANT_MIME)


def dragged_combatant(mime) -> int | None:
    """Which combatant a drag is carrying, or None if it is not carrying one."""
    try:
        return int(bytes(mime.data(COMBATANT_MIME)).decode())
    except (TypeError, ValueError):
        return None
