"""The battle map in 3D: the same fight, stood up, and nothing else.

The 3D view is a second way of drawing the flat map, not a second map. The
claims worth holding are the ones that would let the two drift apart: a square
in the room is the square of the same name on the flat map; a figure part-way
along a walk is where the flat token is at that moment; and a click in the room
means exactly what the same click on the flat map means -- including what a
player is not allowed to do with it.

Nothing here looks at pixels. Qt Quick 3D does not draw without a GPU, and a
headless run has none; what can be held is everything the scene is *told*.
"""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent

from canon_keeper.panels.encounter.grid import Choice, GridMap, Preview, Token, TurnPlan
from canon_keeper.panels.encounter.view3d import (
    SQUARE,
    WALL_HIGH,
    WALL_LOW,
    Map3D,
    MapView,
    Scene3D,
    to_scene,
    to_square,
)

LEFT = Qt.MouseButton.LeftButton.value
RIGHT = Qt.MouseButton.RightButton.value
CTRL = Qt.KeyboardModifier.ControlModifier.value


@pytest.fixture
def room(qtbot):
    """A ten-square room, a fighter whose turn it is, a goblin, and a pillar."""
    source = GridMap()
    qtbot.addWidget(source)
    source.set_grid(10, 10)
    source.set_obstacles([(2, 2)])
    source.set_tokens(
        [
            Token(id=1, label="Brok", x=0, y=0, ours=True, is_turn=True, squares_left=2),
            Token(id=2, label="Yeemik", x=3, y=0),
        ]
    )
    source.select(1)
    return source


@pytest.fixture
def seen(qtbot, room):
    """The room, in the 3D view, showing."""
    view = MapView(room)
    qtbot.addWidget(view)
    view.resize(500, 400)
    view.show()
    assert view.in_3d, view.problem
    return view


def _floor(x: int, y: int, source: GridMap) -> tuple[float, float]:
    return to_scene(x, y, *source.grid_size)


def _row(scene: Scene3D, tid: int) -> dict:
    return next(row for row in scene.tokens.rows() if row["tid"] == tid)


# --------------------------------------------------------------- the squares


@pytest.mark.parametrize("width,height", [(10, 10), (7, 5), (20, 15), (1, 1)])
def test_every_square_in_the_room_is_the_square_of_the_same_name(width, height):
    """A spot anywhere inside a square finds that square, on odd maps and even.

    Even-sided maps put their extra square right and down, so they are where a
    conversion that assumed a symmetric board would be off by one.
    """
    from canon_keeper_protocol import grid

    left, top, right, bottom = grid.bounds(width, height)
    for x in range(left, right + 1):
        for y in range(top, bottom + 1):
            sx, sz = to_scene(x, y, width, height)
            for nudge_x, nudge_z in ((0, 0), (-0.49, -0.49), (0.49, 0.49)):
                spot = (sx + nudge_x * SQUARE, sz + nudge_z * SQUARE)
                assert to_square(*spot, width, height) == (x, y)


def test_the_room_is_centred_on_the_middle_of_the_board():
    """The camera turns about the scene's origin, so the board has to sit on it."""
    left_edge, _ = to_scene(-5, 0, 10, 10)
    right_edge, _ = to_scene(4, 0, 10, 10)
    assert left_edge - SQUARE / 2 == -(right_edge + SQUARE / 2)


def test_north_is_away_from_the_viewer_as_it_is_at_the_top_of_the_flat_map():
    _, north = to_scene(0, -3, 10, 10)
    _, south = to_scene(0, 3, 10, 10)
    assert north < south


def test_the_floor_beyond_the_board_is_no_square():
    assert to_square(-6 * SQUARE, 0, 10, 10) is None
    assert to_square(0, 5 * SQUARE, 10, 10) is None


# ------------------------------------------------------------ what is shown


def test_the_room_shows_the_walls_and_creatures_the_flat_map_has(qtbot, room):
    scene = Scene3D()
    scene.read(room, room.palette())

    assert scene.walls == [dict(zip(("sx", "sz"), _floor(2, 2, room)))]
    assert [row["tid"] for row in scene.tokens.rows()] == [1, 2]
    brok = _row(scene, 1)
    assert (brok["sx"], brok["sz"]) == _floor(0, 0, room)
    assert brok["initials"] == "BR"
    assert brok["turn"] is True


def test_a_wall_can_be_knocked_down_to_see_behind_it_and_put_back(qtbot):
    scene = Scene3D()
    assert scene.wallHeight == WALL_HIGH
    scene.toggleWalls()
    assert scene.wallHeight == WALL_LOW
    scene.toggleWalls()
    assert scene.wallHeight == WALL_HIGH


def test_a_walking_figure_is_where_the_flat_token_is_at_the_same_moment(qtbot, room):
    """One walk, drawn twice. Two copies of the easing would be two walks."""
    room.play({"kind": "move", "combatant": 2, "path": [[3, 0], [3, 1], [3, 2], [3, 3]]})
    qtbot.wait(250)

    scene = Scene3D()
    now = time.monotonic()
    scene.read(room, room.palette())
    goblin = next(t for t in room.tokens_shown() if t.id == 2)
    where = room.placement(goblin, now)
    row = _row(scene, 2)

    assert 0 < where.y < 3, "the walk should be under way, not finished or unstarted"
    expected = to_scene(where.x, where.y, 10, 10)
    assert row["sx"] == pytest.approx(expected[0], abs=SQUARE * 0.05)
    assert row["sz"] == pytest.approx(expected[1], abs=SQUARE * 0.05)


def test_a_creature_the_party_cannot_see_is_see_through_to_the_dm(qtbot, room):
    room.set_tokens([Token(id=1, label="Brok", x=0, y=0), Token(id=2, label="Yeemik", x=3, y=0, unseen=True)])
    scene = Scene3D()
    scene.read(room, room.palette())
    assert _row(scene, 1)["alpha"] == 1.0
    assert _row(scene, 2)["alpha"] < 1.0


def test_a_body_on_the_floor_has_no_pips(qtbot, room):
    """What is left of a turn is about somebody standing up."""
    room.set_tokens([Token(id=1, label="Brok", x=0, y=0, is_turn=True, squares_left=3, down=True)])
    scene = Scene3D()
    scene.read(room, room.palette())
    assert _row(scene, 1)["pips"] == []
    assert _row(scene, 1)["size"] < 1.0


def test_a_turn_on_offer_is_drawn_as_a_ghost_and_a_mark_on_the_target(qtbot, room):
    room.set_preview(Preview(token=1, to=(0, 3), target=(3, 0)))
    scene = Scene3D()
    scene.read(room, room.palette())
    kinds = {mark["kind"]: (mark["sx"], mark["sz"]) for mark in scene.marks}
    assert kinds["ghost"] == _floor(0, 3, room)
    assert kinds["target"] == _floor(3, 0, room)


# ----------------------------------------------------------- what is done


def test_clicking_a_figure_picks_that_creature_not_the_floor_behind_it(qtbot, room):
    """A tall figure stands over the square behind it. Its head is still it."""
    view = Map3D(room)
    qtbot.addWidget(view)
    behind = _floor(3, -1, room)

    with qtbot.waitSignal(room.picked) as picked:
        view.scene.click(behind[0], behind[1], 2, LEFT, 0, 10, 10)
    assert picked.args == [2]
    assert room.selected == 2


def test_clicking_empty_floor_is_a_click_on_that_square(qtbot, room):
    view = Map3D(room)
    qtbot.addWidget(view)
    spot = _floor(-2, 3, room)
    with qtbot.waitSignal(room.square_clicked) as clicked:
        view.scene.click(spot[0], spot[1], -1, LEFT, 0, 10, 10)
    assert clicked.args == [-2, 3]


def test_ctrl_click_in_the_room_builds_a_wall_for_the_dm(qtbot, room):
    view = Map3D(room)
    qtbot.addWidget(view)
    spot = _floor(4, 4, room)
    with qtbot.waitSignal(room.obstacle_toggled) as toggled:
        view.scene.click(spot[0], spot[1], -1, LEFT, CTRL, 10, 10)
    assert toggled.args == [4, 4]


def test_a_player_cannot_build_a_wall_from_the_3d_view_either(qtbot, room):
    """Read-only is decided by the map, so a second view cannot route round it."""
    room.read_only = True
    view = Map3D(room)
    qtbot.addWidget(view)
    spot = _floor(4, 4, room)
    with qtbot.assertNotEmitted(room.obstacle_toggled):
        view.scene.click(spot[0], spot[1], -1, LEFT, CTRL, 10, 10)


def test_right_click_on_a_figure_asks_for_its_menu(qtbot, room):
    view = Map3D(room)
    qtbot.addWidget(view)
    with qtbot.waitSignal(room.menu_requested) as asked:
        view.scene.click(0.0, 0.0, 2, RIGHT, 0, 10, 10)
    assert asked.args[0] == 2


def test_a_combatant_dropped_into_the_room_lands_on_that_square(qtbot, room):
    view = Map3D(room)
    qtbot.addWidget(view)
    spot = _floor(-3, 2, room)
    with qtbot.waitSignal(room.dropped) as dropped:
        view.scene.drop(spot[0], spot[1], "7")
    assert dropped.args == [7, -3, 2]


def test_a_player_cannot_drop_a_combatant_into_the_room(qtbot, room):
    room.read_only = True
    view = Map3D(room)
    qtbot.addWidget(view)
    with qtbot.assertNotEmitted(room.dropped):
        view.scene.drop(0.0, 0.0, "7")


# ------------------------------------------------------------ taking a turn


def test_a_turn_can_be_taken_from_the_room_start_to_finish(qtbot, seen, room):
    """Space, pick Move, point at a square, click it: a plan comes out.

    The wheel is a menu in 3D, and the same choices reach the same map.
    """
    map3d = seen.map3d
    room.radial_wanted.connect(
        lambda who: room.offer(who, [Choice("move", "Move"), Choice("attack", "Club", "Club")])
    )
    map3d._quick.setFocus()
    space = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier)
    map3d.eventFilter(map3d._quick, space)

    assert room.radial_open
    menu = map3d.menu
    assert [action.text() for action in menu.actions()] == ["Move", "Club"]
    menu.actions()[0].trigger()
    menu.hide()
    qtbot.wait(20)
    assert room.awaiting is not None and room.awaiting.kind == "move"

    target = _floor(0, 3, room)
    map3d.scene.hover(target[0], target[1], -1)
    map3d.sync_now()
    steps = [mark for mark in map3d.scene.marks if mark["kind"] in ("step", "ghost")]
    assert len(steps) == 3

    with qtbot.waitSignal(room.planned) as planned:
        map3d.scene.click(target[0], target[1], -1, LEFT, 0, 10, 10)
    assert planned.args == [TurnPlan(combatant=1, move=(0, 3))]


def test_a_walk_past_what_is_left_of_the_turn_goes_red_at_the_same_square(qtbot, seen, room):
    """The flat line and the 3D dots come from one list, so they cannot disagree."""
    room.offer(1, [Choice("move", "Move")])
    room.choose(0)
    room.point_at((0, 4))
    seen.map3d.sync_now()

    near = [mark["near"] for mark in seen.map3d.scene.marks if mark["kind"] in ("step", "ghost")]
    assert near == [flag for _square, flag in room.hover_walk()[1:]]
    assert near == [True, True, False, False]


def test_putting_the_menu_away_puts_the_wheel_away(qtbot, seen, room):
    room.offer(1, [Choice("move", "Move")])
    menu = seen.map3d.menu
    assert menu is not None
    menu.hide()
    qtbot.waitUntil(lambda: not room.radial_open, timeout=500)
    assert room.awaiting is None


# ------------------------------------------------------------------ the view


def test_the_scene_loads(qtbot, room):
    """The QML is read and built. Whether it *draws* needs a GPU; this does not."""
    view = Map3D(room)
    qtbot.addWidget(view)
    assert view.ok, view.problem


def test_the_map_is_seen_in_3d_and_the_flat_one_is_never_shown(qtbot, seen, room):
    """The flat map still holds the fight; nobody looks at it."""
    assert seen._stack.currentWidget() is seen.map3d
    assert not room.isVisible()


@pytest.fixture
def flat_only(qtbot, room, monkeypatch, tmp_path):
    """The room on a machine whose Qt cannot build the 3D scene.

    A fixture rather than a local so the view outlives the test body: it owns
    the map, and dropping it early would take the map with it before pytest-qt
    gets to close either.
    """
    from canon_keeper.panels.encounter import view3d

    broken = tmp_path / "broken.qml"
    broken.write_text("this is not QML", encoding="utf-8")
    monkeypatch.setattr(view3d, "QML", broken)
    view = MapView(room)
    qtbot.addWidget(view)
    view.show()
    return view


def test_a_machine_that_cannot_build_the_scene_still_gets_a_map(qtbot, flat_only, room):
    """Flat is better than nothing, and the reason is kept rather than swallowed."""
    assert not flat_only.in_3d
    assert flat_only.problem
    assert flat_only._stack.currentWidget() is room
    assert room.isVisible()


def test_the_camera_buttons_reach_the_flat_map_when_that_is_all_there_is(qtbot, flat_only, room):
    """Zoom and whole-map from the menu have to do something whichever is showing."""
    before = room.zoom
    flat_only.zoom_by(2)
    assert room.zoom > before
    flat_only.fit()
    assert room.fitting


# ------------------------------------------------------------------ the camera


def _drag(view, button, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    from PySide6.QtTest import QTest

    quick = view.map3d._quick
    QTest.mousePress(quick, button, modifiers, QPoint(250, 200))
    for step in range(1, 11):
        QTest.mouseMove(quick, QPoint(250 + step * 8, 200 + step * 4))
    QTest.mouseRelease(quick, button, modifiers, QPoint(330, 240))


def _camera(view) -> dict:
    root = view.map3d._quick.rootObject()
    return {name: root.property(name) for name in ("yaw", "pitch", "tx", "tz")}


def test_the_middle_button_slides_the_room_without_turning_it(qtbot, seen):
    before = _camera(seen)
    _drag(seen, Qt.MouseButton.MiddleButton)
    after = _camera(seen)
    assert (after["tx"], after["tz"]) != (before["tx"], before["tz"])
    assert (after["yaw"], after["pitch"]) == (before["yaw"], before["pitch"])


def test_shift_and_the_left_button_slide_it_too_for_a_touchpad(qtbot, seen):
    before = _camera(seen)
    _drag(seen, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier)
    after = _camera(seen)
    assert (after["tx"], after["tz"]) != (before["tx"], before["tz"])
    assert after["yaw"] == before["yaw"]


def test_the_left_button_turns_the_room_about_where_it_is(qtbot, seen):
    before = _camera(seen)
    _drag(seen, Qt.MouseButton.LeftButton)
    after = _camera(seen)
    assert after["yaw"] != before["yaw"]
    assert (after["tx"], after["tz"]) == (before["tx"], before["tz"])
