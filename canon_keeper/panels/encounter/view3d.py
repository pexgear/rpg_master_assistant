"""The battle map in 3D: the same fight, stood up.

A second way of *drawing* :class:`~canon_keeper.panels.encounter.grid.GridMap`,
not a second map. It holds no state of its own about the fight: every frame is
read off the flat map, and every click is handed back to it through
:meth:`GridMap.press_square`. So the walk, the wheel, what ctrl-click means and
what a player may touch are decided in one place, and a DM looking at the fight
in 3D and a player looking at it flat are looking at the same fight.

Obstacles are walls and nothing more. There is no height in the rules -- a
square is empty or blocked, exactly as on the flat map -- so standing a rock up
is a way of seeing the room, not a new fact about it.

Qt Quick 3D does the drawing. It needs a GPU; a machine whose Qt cannot build
the scene gets the flat map drawn instead, which is why that choice lives in
:class:`MapView` rather than in either panel.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QObject,
    QPoint,
    QTimer,
    QUrl,
    Property,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QCursor, QPalette
from PySide6.QtWidgets import (
    QMenu,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from canon_keeper.panels.encounter import grid as flat
from canon_keeper.panels.encounter.grid import GridMap, Token
from canon_keeper_protocol import grid

#: One square, in the scene's own units. The size of Qt's built-in cube, so a
#: wall is that cube and nothing needs scaling to fit a square.
SQUARE = 100.0

#: How tall a wall stands, in squares. Taller than a person, because a wall you
#: can see over reads as a low fence and people start asking whether they can
#: climb it -- and the rules have no answer, because there is no height.
WALL_HIGH = 1.4
#: The same walls knocked down to a kerb, for looking at what is behind them.
WALL_LOW = 0.25

QML = Path(__file__).with_name("map3d.qml")

log = logging.getLogger(__name__)


def to_scene(x: float, y: float, width: int, height: int) -> tuple[float, float]:
    """The middle of square (x, y), in scene units on the floor.

    The board is centred on the scene's origin whatever its coordinates are, so
    the camera always turns about the middle of the room. Map y runs down the
    screen and scene z runs toward the viewer, which is the same direction: the
    tilted view keeps north at the top, as the flat one does.
    """
    left, top, _right, _bottom = grid.bounds(width, height)
    return (
        (x - left + 0.5 - width / 2) * SQUARE,
        (y - top + 0.5 - height / 2) * SQUARE,
    )


def to_square(sx: float, sz: float, width: int, height: int) -> tuple[int, int] | None:
    """Which square a point on the floor is in, or None for off the board."""
    left, top, _right, _bottom = grid.bounds(width, height)
    column = math.floor(sx / SQUARE + width / 2)
    row = math.floor(sz / SQUARE + height / 2)
    if 0 <= column < width and 0 <= row < height:
        return left + column, top + row
    return None


class TokenModel(QAbstractListModel):
    """The creatures, one row each, for the scene to stand up.

    A model rather than a list property because it changes thirty times a
    second while somebody walks: a list would rebuild every figure on every
    frame, and a model tells the scene which numbers moved.
    """

    ROLES = (
        "tid", "sx", "sz", "initials", "name", "fill", "alpha", "size",
        "turn", "down", "pips",
    )

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[dict] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802
        return {
            Qt.ItemDataRole.UserRole + index: QByteArray(name.encode())
            for index, name in enumerate(self.ROLES)
        }

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        offset = role - Qt.ItemDataRole.UserRole
        if not 0 <= offset < len(self.ROLES):
            return None
        return self._rows[index.row()][self.ROLES[offset]]

    def rows(self) -> list[dict]:
        return list(self._rows)

    def replace(self, rows: list[dict]) -> None:
        """Take a new set of rows, disturbing the scene as little as possible.

        The same creatures in the same order is a frame of a walk and changes
        numbers in place. Anybody joining or leaving is a new cast, and the
        figures are built again.
        """
        if [row["tid"] for row in rows] != [row["tid"] for row in self._rows]:
            self.beginResetModel()
            self._rows = rows
            self.endResetModel()
            return
        for number, (old, new) in enumerate(zip(self._rows, rows)):
            if old != new:
                self._rows[number] = new
                here = self.index(number)
                self.dataChanged.emit(here, here)


class Scene3D(QObject):
    """What the 3D scene shows, read off a :class:`GridMap`, and what was done to it.

    Kept apart from the widget so it can be tested without a GPU: everything
    here is numbers and signals, and the QML only draws what it is told.
    """

    #: A square was clicked: (square or None, button, modifiers, where in the view).
    pressed = Signal(object, int, int, QPoint)
    #: The pointer is over this square now -- or over nothing, for None.
    pointed = Signal(object)
    #: A combatant was let go of over this square.
    landed = Signal(int, object)

    shapeChanged = Signal()
    wallsChanged = Signal()
    marksChanged = Signal()
    floatsChanged = Signal()
    coloursChanged = Signal()
    wallHeightChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._columns = 1
        self._rows = 1
        self._left = 0
        self._top = 0
        self._read_only = False
        self._walls: list[dict] = []
        self._marks: list[dict] = []
        self._floats: list[dict] = []
        self._colours: dict[str, QColor] = {}
        self._wall_height = WALL_HIGH
        #: Where each creature really stands, for a click on its figure: a
        #: figure part-way along a walk is not on the square it is drawn over.
        self._squares: dict[int, tuple[int, int]] = {}
        self._tokens = TokenModel(self)

    # ------------------------------------------------------------- properties

    def _get_columns(self) -> int:
        return self._columns

    def _get_rows(self) -> int:
        return self._rows

    def _get_left(self) -> int:
        return self._left

    def _get_top(self) -> int:
        return self._top

    def _get_read_only(self) -> bool:
        return self._read_only

    columns = Property(int, _get_columns, notify=shapeChanged)
    rows = Property(int, _get_rows, notify=shapeChanged)
    left = Property(int, _get_left, notify=shapeChanged)
    top = Property(int, _get_top, notify=shapeChanged)
    readOnly = Property(bool, _get_read_only, notify=shapeChanged)  # noqa: N815

    def _get_walls(self) -> list:
        return self._walls

    def _get_marks(self) -> list:
        return self._marks

    def _get_floats(self) -> list:
        return self._floats

    walls = Property("QVariantList", _get_walls, notify=wallsChanged)
    marks = Property("QVariantList", _get_marks, notify=marksChanged)
    floats = Property("QVariantList", _get_floats, notify=floatsChanged)

    def _get_tokens(self) -> QObject:
        return self._tokens

    tokens = Property(QObject, _get_tokens, constant=True)

    # A default argument, because a function in a class body cannot see the
    # class's other names when it runs -- only when it is defined.
    def _colour_of(name: str, notify=coloursChanged):  # noqa: N805 - a factory
        return Property(
            QColor, lambda self: self._colours.get(name, QColor()), notify=notify
        )

    backdrop = _colour_of("backdrop")
    floor = _colour_of("floor")
    faint = _colour_of("faint")
    strong = _colour_of("strong")
    axis = _colour_of("axis")
    stone = _colour_of("stone")
    turn = _colour_of("turn")
    plan = _colour_of("plan")
    hurt = _colour_of("hurt")
    blade = _colour_of("blade")
    highlight = _colour_of("highlight")
    ink = _colour_of("ink")
    panel = _colour_of("panel")
    del _colour_of

    def _get_wall_height(self) -> float:
        return self._wall_height

    def _set_wall_height(self, value: float) -> None:
        if value != self._wall_height:
            self._wall_height = float(value)
            self.wallHeightChanged.emit()

    wallHeight = Property(  # noqa: N815
        float, _get_wall_height, _set_wall_height, notify=wallHeightChanged
    )

    @Slot()
    def toggleWalls(self) -> None:  # noqa: N802 - called from QML
        self._set_wall_height(WALL_LOW if self._wall_height > WALL_LOW else WALL_HIGH)

    # ---------------------------------------------------------------- reading

    def read(self, source: GridMap, palette: QPalette) -> None:
        """Take everything that is drawn off the flat map."""
        columns, rows = source.grid_size
        left, top, _right, _bottom = grid.bounds(columns, rows)
        shape = (columns, rows, left, top, source.read_only)
        if shape != (self._columns, self._rows, self._left, self._top, self._read_only):
            self._columns, self._rows, self._left, self._top, self._read_only = shape
            self.shapeChanged.emit()

        self._read_colours(palette)

        def at(x: float, y: float) -> dict:
            sx, sz = to_scene(x, y, columns, rows)
            return {"sx": sx, "sz": sz}

        walls = [
            at(x, y)
            for x, y in sorted(source.obstacles)
            if grid.holds(columns, rows, x, y)
        ]
        if walls != self._walls:
            self._walls = walls
            self.wallsChanged.emit()

        tokens = source.tokens_shown()
        self._squares = {token.id: (token.x, token.y) for token in tokens}
        self._tokens.replace([self._token_row(source, token, at) for token in tokens])

        marks = self._marks_of(source, tokens, at)
        if marks != self._marks:
            self._marks = marks
            self.marksChanged.emit()

        floats = [
            {
                **at(*number.square),
                "text": number.text,
                "colour": QColor(number.colour),
                "rise": number.rise,
                "alpha": number.alpha,
            }
            for number in source.floating()
        ]
        if floats or self._floats:
            self._floats = floats
            self.floatsChanged.emit()

    def _read_colours(self, palette: QPalette) -> None:
        text = palette.text().color()

        def faded(alpha: int) -> QColor:
            colour = QColor(text)
            colour.setAlpha(alpha)
            return colour

        # The same inks as the flat map, so the two read as one thing: a line
        # every square, a heavier one every fifth, and the heaviest through 0,0.
        colours = {
            "backdrop": palette.window().color(),
            "floor": palette.base().color(),
            "faint": faded(45),
            "strong": faded(95),
            "axis": faded(150),
            "stone": QColor(flat._STONE),
            "turn": QColor(flat._TURN),
            "plan": QColor(flat._PLAN),
            "hurt": QColor(flat._HURT),
            "blade": QColor(flat._BLADE),
            "highlight": palette.highlight().color(),
            "ink": text,
            "panel": palette.button().color(),
        }
        if colours != self._colours:
            self._colours = colours
            self.coloursChanged.emit()

    def _token_row(self, source: GridMap, token: Token, at) -> dict:
        where = source.placement(token)
        lying = token.down or where.falling
        fill = QColor(
            flat._GHOST if lying else (flat._OURS if token.ours else flat._THEIRS)
        )
        # Unseen is dotted on the flat map. There is no dotted in 3D, so it is
        # see-through instead -- still unmistakably a different state from a
        # creature the party can see, which is the only thing it has to say.
        opacity = where.opacity * (0.45 if token.unseen else 1.0)
        return {
            "tid": token.id,
            **at(where.x, where.y),
            "initials": token.initials,
            "name": token.label,
            "fill": fill,
            "alpha": opacity,
            "size": 1.0 - 2 * where.shrink,
            "turn": token.is_turn,
            "down": token.down,
            "pips": (
                []
                if lying
                else [
                    {"colour": QColor(colour), "filled": filled}
                    for colour, filled in flat.budget_marks(token)
                ]
            ),
        }

    def _marks_of(self, source: GridMap, tokens: list[Token], at) -> list[dict]:
        """Everything on the floor that is not a creature or a wall."""
        marks: list[dict] = []
        chosen = next((t for t in tokens if t.id == source.selected), None)
        if chosen is not None:
            marks.append({**at(chosen.x, chosen.y), "kind": "selected", "near": True})

        walk = source.hover_walk()
        for (x, y), near in walk[1:-1]:
            marks.append({**at(x, y), "kind": "step", "near": near})
        if walk:
            (x, y), near = walk[-1]
            marks.append({**at(x, y), "kind": "ghost", "near": near})

        offer = source.preview
        actor = next((t for t in tokens if offer and t.id == offer.token), None)
        if offer is not None and actor is not None:
            if offer.to is not None:
                line = grid.steps_between((actor.x, actor.y), tuple(offer.to))
                for x, y in line[1:-1]:
                    marks.append({**at(x, y), "kind": "step", "near": True})
                marks.append({**at(*offer.to), "kind": "ghost", "near": True})
            if offer.target is not None:
                marks.append({**at(*offer.target), "kind": "target", "near": True})
        return marks

    # ------------------------------------------------------------ from QML

    def _square_for(self, sx: float, sz: float, tid: int) -> tuple[int, int] | None:
        if tid >= 0 and tid in self._squares:
            return self._squares[tid]
        return to_square(sx, sz, self._columns, self._rows)

    @Slot(float, float, int, int, int, float, float)
    def click(self, sx, sz, tid, button, modifiers, px, py) -> None:  # noqa: PLR0913
        """A click the scene worked out landed at (sx, sz) on the floor.

        ``tid`` is the creature whose figure was hit, or -1. A figure counts as
        its square, because a tall figure stands over the square behind it and
        clicking somebody's head should not select the floor beyond them.
        """
        self.pressed.emit(
            self._square_for(sx, sz, tid), int(button), int(modifiers),
            QPoint(int(px), int(py)),
        )

    @Slot()
    def clickNothing(self) -> None:  # noqa: N802 - called from QML
        """A click on the sky: the floor was missed altogether."""
        self.pressed.emit(None, Qt.MouseButton.LeftButton.value, 0, QPoint())

    @Slot(float, float, int)
    def hover(self, sx, sz, tid) -> None:
        self.pointed.emit(self._square_for(sx, sz, tid))

    @Slot()
    def hoverNothing(self) -> None:  # noqa: N802 - called from QML
        self.pointed.emit(None)

    @Slot(float, float, str)
    def drop(self, sx, sz, carried) -> None:
        try:
            combatant = int(carried)
        except ValueError:
            return
        self.landed.emit(combatant, to_square(sx, sz, self._columns, self._rows))


class Map3D(QWidget):
    """The 3D scene in a widget, drawing whatever ``source`` holds."""

    def __init__(self, source: GridMap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source = source
        self._menu: QMenu | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Imported here so that a Qt without Quick 3D costs the flat map
        # nothing: the error is shown where the view would have been.
        from PySide6.QtQuickWidgets import QQuickWidget

        self._quick = QQuickWidget(self)
        # Owned by the Quick widget, so it is destroyed after the scene that
        # reads it rather than before: the other way round, every binding in
        # the scene re-reads a vanished object on the way out and says so.
        self.scene = Scene3D(self._quick)
        # Current from the start, not from the first time it is shown: a click
        # can only land on a room the scene already knows the size of.
        self.scene.read(source, self.palette())
        self._quick.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
        self._quick.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._quick.rootContext().setContextProperty("scene", self.scene)
        self._quick.setSource(QUrl.fromLocalFile(str(QML)))
        self.problem = "; ".join(error.toString() for error in self._quick.errors())
        layout.addWidget(self._quick)
        self._quick.installEventFilter(self)

        self.scene.pressed.connect(self._on_pressed)
        self.scene.pointed.connect(source.point_at)
        self.scene.landed.connect(source.drop_on)
        source.shown.connect(self._sync)
        source.radial_changed.connect(self._on_radial)

    @property
    def ok(self) -> bool:
        return not self.problem

    def showEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().showEvent(event)
        self._sync()

    def _sync(self) -> None:
        # Hidden is the flat map's turn, and a hidden scene reading thirty
        # frames a second of somebody else's walk would be work for no one.
        if self.isVisible():
            self.scene.read(self._source, self.palette())

    def sync_now(self) -> None:
        """Read the map whether or not this is showing. For tests."""
        self.scene.read(self._source, self.palette())

    def _on_pressed(self, square, button, modifiers, where: QPoint) -> None:
        self._source.press_square(
            square,
            Qt.MouseButton(button),
            Qt.KeyboardModifier(modifiers),
            self._quick.mapToGlobal(where),
        )

    # -------------------------------------------------------------- the wheel

    def _on_radial(self, opened: bool) -> None:
        """The wheel, as a menu.

        The flat map draws its wheel around the token; a wheel painted flat on
        the floor of a tilted room is an ellipse you have to read sideways. So
        the same choices come up as a menu at the pointer, which is where the
        person pressing Space is already looking.
        """
        if not opened or not self.isVisible():
            return
        choices = self._source.choices
        if not choices:
            return
        menu = QMenu(self)
        for index, choice in enumerate(choices):
            action = menu.addAction(choice.label)
            action.triggered.connect(lambda _checked=False, i=index: self._source.choose(i))
        # Dismissing the menu is dismissing the wheel. Deferred, because a pick
        # is reported after the menu has already begun to hide -- and closing
        # the wheel first would leave the pick nothing to pick from.
        menu.aboutToHide.connect(lambda: QTimer.singleShot(0, self._menu_gone))
        self._menu = menu
        menu.popup(QCursor.pos())

    def _menu_gone(self) -> None:
        self._menu = None
        if self._source.radial_open:
            self._source.close_radial()

    @property
    def menu(self) -> QMenu | None:
        return self._menu

    # ------------------------------------------------------------ the keyboard

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt's name
        """Keys belong to the map, wherever it is being looked at from.

        Space, Enter and Escape mean the same thing in either view, so they go
        to the flat map, which is where they are decided. Zoom and the arrows
        are about the camera, which only this view has.
        """
        from PySide6.QtCore import QEvent

        if watched is self._quick and event.type() == QEvent.Type.KeyPress:
            root = self._quick.rootObject()
            key = event.key()
            if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                root.zoomBy(1)
            elif key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                root.zoomBy(-1)
            elif key == Qt.Key.Key_0:
                root.tilted()
            elif key in flat._ARROWS:
                root.panBy(*flat._ARROWS[key])
            else:
                self._source.keyPressEvent(event)
            return True
        return super().eventFilter(watched, event)


class MapView(QWidget):
    """The battle map as people see it: in 3D.

    Both panels hold a :class:`GridMap` and talk only to it -- it is where the
    fight's state and the meaning of every click live. This is what is put in
    front of it. The flat map is still here, never shown, because it *is* the
    map; drawing it flat is only a fallback for a machine whose Qt cannot build
    the 3D scene, where a flat map is better than no map at all.
    """

    def __init__(self, source: GridMap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source = source
        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(source)
        #: Why the 3D view is not showing, when it is not. Empty otherwise.
        self.problem = ""

        self._map3d: Map3D | None = None
        try:
            built = Map3D(source)
        except ImportError as error:
            self.problem = str(error)
        else:
            if built.ok:
                self._map3d = built
                self._stack.addWidget(built)
                self._stack.setCurrentWidget(built)
            else:
                self.problem = built.problem
                built.deleteLater()
        if self.problem:
            log.warning("3D map unavailable, drawing it flat: %s", self.problem)

    @property
    def in_3d(self) -> bool:
        return self._map3d is not None

    @property
    def map3d(self) -> Map3D | None:
        return self._map3d

    def _camera(self):
        return self._map3d._quick.rootObject() if self._map3d is not None else None

    def zoom_by(self, notches: int) -> None:
        camera = self._camera()
        if camera is not None:
            camera.zoomBy(notches)
        else:
            self._source.zoom_by(notches)

    def fit(self) -> None:
        """The whole room, from the usual angle."""
        camera = self._camera()
        if camera is not None:
            camera.tilted()
        else:
            self._source.fit()

    def from_above(self) -> None:
        """The whole room, straight down. Flat already is."""
        camera = self._camera()
        if camera is not None:
            camera.above()
        else:
            self._source.fit()
