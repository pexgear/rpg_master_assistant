"""Everyone watching the same fight.

A map that only ever showed the latest state would be correct and unreadable:
tokens teleport, hit points change, and nobody sees anything happen. So the
host *describes* what happened -- the whole walk, whether the swing landed, how
much it cost -- and every client draws that.

Described rather than inferred, and that is the whole design. A client working
it out from two states it was sent would invent its own line, at its own
moment, and four people would watch four different fights.

The other half is that an animation is projected like everything else. A token
you were never sent must not acquire one: that would be the thing projection
exists to prevent, arriving as a moving dot.
"""

from __future__ import annotations

import time

import pytest

from canon_keeper.net.client import SessionClient
from canon_keeper.net.server import SessionServer
from canon_keeper.panels.encounter.grid import (
    DOWN_MS,
    STEP_MS,
    GridMap,
    Token,
    eased,
    when_eased,
)
from canon_keeper_core.repo.entities import KIND_NPC, KIND_PC, Entity
from canon_keeper_protocol import Played, grid


# ------------------------------------------------------------------ the walk


def test_a_walk_is_worked_out_once_and_sent():
    """Every client walks the same line rather than inventing its own."""
    assert grid.steps_between((0, 0), (3, 0)) == [(0, 0), (1, 0), (2, 0), (3, 0)]


def test_diagonals_are_one_step_each():
    """The same way the distance is measured, so six squares is six steps."""
    assert grid.steps_between((0, 0), (2, 2)) == [(0, 0), (1, 1), (2, 2)]
    assert len(grid.steps_between((0, 0), (3, 1))) == 4


def test_going_nowhere_is_one_square():
    assert grid.steps_between((2, 2), (2, 2)) == [(2, 2)]


def test_a_bad_pair_cannot_loop_forever():
    assert len(grid.steps_between((0, 0), (10_000, 0))) <= 121


# ---------------------------------------------------------------- the drawing


@pytest.fixture
def map_widget(qtbot) -> GridMap:
    made = GridMap()
    made.resize(400, 400)
    made.set_grid(10, 10)
    made.set_tokens(
        [
            Token(id=1, label="Brok", x=-3, y=0, ours=True),
            Token(id=2, label="Yeemik", x=1, y=0),
        ]
    )
    qtbot.addWidget(made)
    return made


def test_nothing_is_animating_to_begin_with(map_widget):
    assert map_widget._effects == []
    assert not map_widget._frames.isActive()


def test_a_walk_starts_the_clock(map_widget):
    map_widget.play(
        {"kind": Played.MOVE.value, "combatant": 1, "path": [[-3, 0], [-2, 0], [-1, 0]]}
    )
    assert map_widget._frames.isActive()
    assert map_widget._effect_on(1, "move") is not None


def test_a_longer_walk_takes_longer(map_widget):
    """Time is per square, so crossing the room is not the same as one step."""
    map_widget.play({"kind": Played.MOVE.value, "combatant": 1, "path": [[0, 0], [1, 0]]})
    short = map_widget._effect_on(1, "move").duration

    map_widget.play(
        {
            "kind": Played.MOVE.value,
            "combatant": 1,
            "path": [[0, 0], [1, 0], [2, 0], [3, 0]],
        }
    )
    assert map_widget._effect_on(1, "move").duration == pytest.approx(short * 3)
    assert short == pytest.approx(STEP_MS / 1000)


def test_a_walk_of_one_square_is_not_animated(map_widget):
    """There is nothing between the two squares to show."""
    map_widget.play({"kind": Played.MOVE.value, "combatant": 1, "path": [[0, 0]]})
    assert map_widget._effects == []


def test_a_step_is_slow_enough_to_watch(map_widget):
    """Fast enough and a walk is a teleport with extra steps."""
    assert STEP_MS >= 200


# ------------------------------------------------------------------ easing


def test_the_walk_starts_and_ends_at_a_standstill():
    """A creature leans into a run and settles out of it -- it is not dragged."""
    assert eased(0.0) == pytest.approx(0.0)
    assert eased(1.0) == pytest.approx(1.0)
    # Barely moving at either end, and past halfway by the middle.
    assert eased(0.05) < 0.05
    assert eased(0.95) > 0.95
    assert eased(0.5) == pytest.approx(0.5)


def test_easing_never_goes_backwards():
    seen = [eased(step / 20) for step in range(21)]
    assert seen == sorted(seen)


def test_easing_stays_inside_the_walk():
    """Overshooting would draw a token past the square it was walking to."""
    assert eased(-1.0) == pytest.approx(0.0)
    assert eased(2.0) == pytest.approx(1.0)


def test_the_middle_of_a_walk_is_brisker_than_its_ends(map_widget):
    """Eased over the whole walk, not each square -- or it stutters between them."""
    early = eased(0.2) - eased(0.1)
    middle = eased(0.55) - eased(0.45)

    assert middle > early


# ------------------------------------------- a swing partway along a walk


def test_an_ordinary_swing_waits_for_nothing(map_widget):
    map_widget.play(
        {"kind": Played.ATTACK.value, "combatant": 1, "target": 2, "hit": True,
         "damage": 4}
    )
    assert map_widget._effect_on(1, "lunge").delay == 0.0


def test_a_swing_provoked_partway_along_a_walk_waits_for_it(map_widget):
    """It lands as they leave the reach, not as they set off across the room.

    The host says how far along it was provoked; the walk it belongs to is
    already running by the time the swing arrives, so the delay is measured
    against that walk rather than counted in squares.
    """
    walk = [[0, 0], [0, 1], [0, 2], [0, 3], [0, 4]]
    map_widget.play({"kind": Played.MOVE.value, "combatant": 2, "path": walk})
    map_widget.play(
        {"kind": Played.ATTACK.value, "combatant": 1, "target": 2, "hit": True,
         "damage": 4, "after": 3}
    )

    walking = map_widget._effect_on(2, "move")
    lunge = map_widget._effect_on(1, "lunge")
    assert 0.0 < lunge.delay < walking.duration
    # Three squares of four, through the easing the walk itself uses.
    assert lunge.delay == pytest.approx(when_eased(0.75) * walking.duration)


def test_the_damage_waits_with_it(map_widget):
    """The number floating off them is the same event as the blow."""
    map_widget.play(
        {"kind": Played.MOVE.value, "combatant": 2,
         "path": [[0, 0], [0, 1], [0, 2]]}
    )
    map_widget.play(
        {"kind": Played.ATTACK.value, "combatant": 1, "target": 2, "hit": True,
         "damage": 4, "after": 1}
    )

    assert map_widget._effect_on(2, "float").delay > 0.0


def test_nothing_of_a_waiting_swing_is_drawn_yet(map_widget):
    map_widget.play(
        {"kind": Played.MOVE.value, "combatant": 2,
         "path": [[1, 0], [1, 1], [1, 2], [1, 3]]}
    )
    map_widget.play(
        {"kind": Played.ATTACK.value, "combatant": 1, "target": 2, "hit": True,
         "damage": 4, "after": 3}
    )
    lunge = map_widget._effect_on(1, "lunge")

    assert lunge.waiting(time.monotonic()) is True
    assert lunge.progress(time.monotonic()) == 0.0
    # And it is not reaped as finished before it has even begun.
    assert lunge.done(time.monotonic()) is False


def test_a_waiting_swing_leans_in_once_its_moment_comes(map_widget):
    map_widget.play(
        {"kind": Played.MOVE.value, "combatant": 2,
         "path": [[1, 0], [1, 1], [1, 2], [1, 3]]}
    )
    map_widget.play(
        {"kind": Played.ATTACK.value, "combatant": 1, "target": 2, "hit": True,
         "damage": 4, "after": 3}
    )
    cell, origin = map_widget._cell(), map_widget._origin()
    lunge = map_widget._effect_on(1, "lunge")
    token = map_widget._tokens[0]
    home = map_widget._at(-3, 0, cell, origin)

    # Still standing where it was while the walk is only starting.
    waiting, _o = map_widget._where_to_draw(token, cell, origin, time.monotonic())
    assert waiting.x() == home.x()

    # Wound forward to halfway through the swing itself.
    lunge.started = time.monotonic() - lunge.delay - lunge.duration / 2
    leaning, _o = map_widget._where_to_draw(token, cell, origin, time.monotonic())
    assert leaning.x() != home.x()


def test_a_swing_with_no_walk_to_wait_for_happens_now(map_widget):
    """A creature dropped before it set off never walks, so there is no delay."""
    map_widget.play(
        {"kind": Played.ATTACK.value, "combatant": 1, "target": 2, "hit": True,
         "damage": 4, "after": 3}
    )

    assert map_widget._effect_on(1, "lunge").delay == 0.0


def test_the_token_is_drawn_along_the_way_not_at_the_end(map_widget):
    """The state has already moved on; the animation owns the drawing."""
    map_widget.play(
        {
            "kind": Played.MOVE.value,
            "combatant": 1,
            "path": [[-3, 0], [-2, 0], [-1, 0], [0, 0]],
        }
    )
    cell, origin = map_widget._cell(), map_widget._origin()
    effect = map_widget._effect_on(1, "move")
    token = map_widget._tokens[0]

    # A third of the way along is not where it started, nor where it ends.
    effect.started = time.monotonic() - effect.duration / 3
    square, _opacity = map_widget._where_to_draw(token, cell, origin, time.monotonic())
    start = map_widget._at(-3, 0, cell, origin)
    end = map_widget._at(0, 0, cell, origin)
    assert start.x() < square.x() < end.x()


def test_a_second_walk_replaces_the_first(map_widget):
    """Otherwise one token is drawn in two places at once."""
    for _ in range(2):
        map_widget.play(
            {"kind": Played.MOVE.value, "combatant": 1, "path": [[0, 0], [1, 0]]}
        )
    assert len([e for e in map_widget._effects if e.kind == "move"]) == 1


# ---------------------------------------------------------------- the swing


def test_an_attack_leans_in_and_floats_a_number(map_widget):
    map_widget.play(
        {"kind": Played.ATTACK.value, "combatant": 1, "target": 2, "hit": True,
         "damage": 7}
    )
    assert map_widget._effect_on(1, "lunge") is not None

    floating = map_widget._effect_on(2, "float")
    assert floating is not None
    assert floating.text == "-7"


def test_a_miss_is_shown_too(map_widget):
    """Half of what happened, and the half a table argues about."""
    map_widget.play(
        {"kind": Played.ATTACK.value, "combatant": 1, "target": 2, "hit": False,
         "damage": 0}
    )
    assert map_widget._effect_on(2, "float").text == "miss"


def test_the_lunge_goes_out_and_comes_back(map_widget):
    """A swing, not a step: it must not end up standing on the target."""
    map_widget.play(
        {"kind": Played.ATTACK.value, "combatant": 1, "target": 2, "hit": True,
         "damage": 3}
    )
    cell, origin = map_widget._cell(), map_widget._origin()
    effect = map_widget._effect_on(1, "lunge")
    token = map_widget._tokens[0]
    home = map_widget._at(-3, 0, cell, origin)

    effect.started = time.monotonic() - effect.duration / 2
    middle, _o = map_widget._where_to_draw(token, cell, origin, time.monotonic())
    assert middle.x() > home.x(), "it should have leaned toward the target"

    effect.started = time.monotonic() - effect.duration
    back, _o = map_widget._where_to_draw(token, cell, origin, time.monotonic())
    assert back.x() == home.x(), "and come back"


def test_an_attack_on_somebody_off_the_map_still_lunges(map_widget):
    """The target may be one this viewer was never sent."""
    map_widget.play(
        {"kind": Played.ATTACK.value, "combatant": 1, "hit": True, "damage": 4}
    )
    assert map_widget._effect_on(1, "lunge") is not None


# ----------------------------------------------------------------- going down


def test_a_token_is_kept_a_moment_longer_than_the_state(map_widget):
    """The host takes them off the map at once. Nobody would see them go."""
    map_widget.play({"kind": Played.DOWN.value, "combatant": 2})
    map_widget.set_tokens([Token(id=1, label="Brok", x=-3, y=0, ours=True)])

    assert 2 in map_widget._leaving
    assert {t.id for t in map_widget._drawable()} == {1, 2}


def test_and_fades_while_it_goes(map_widget):
    map_widget.play({"kind": Played.DOWN.value, "combatant": 2})
    cell, origin = map_widget._cell(), map_widget._origin()
    effect = map_widget._effect_on(2, "down")
    token = map_widget._tokens[1]

    effect.started = time.monotonic()
    _square, full = map_widget._where_to_draw(token, cell, origin, time.monotonic())
    effect.started = time.monotonic() - effect.duration * 0.75
    _square, nearly = map_widget._where_to_draw(token, cell, origin, time.monotonic())

    assert full > nearly
    assert effect.duration == pytest.approx(DOWN_MS / 1000)


def test_once_it_is_gone_it_is_gone(map_widget):
    map_widget.play({"kind": Played.DOWN.value, "combatant": 2})
    map_widget.set_tokens([Token(id=1, label="Brok", x=-3, y=0, ours=True)])

    for effect in map_widget._effects:
        effect.started = time.monotonic() - effect.duration - 1
    map_widget._next_frame()

    assert map_widget._leaving == {}
    assert {t.id for t in map_widget._drawable()} == {1}
    assert not map_widget._frames.isActive()


def test_the_clock_stops_when_there_is_nothing_to_show(map_widget):
    """A timer running over an idle map is a laptop fan for no reason."""
    map_widget.play({"kind": Played.MOVE.value, "combatant": 1, "path": [[0, 0], [1, 0]]})
    assert map_widget._frames.isActive()

    for effect in map_widget._effects:
        effect.started = time.monotonic() - effect.duration - 1
    map_widget._next_frame()

    assert not map_widget._frames.isActive()


def test_nonsense_is_ignored(map_widget):
    for rubbish in ({}, {"kind": "move"}, {"kind": "nope", "combatant": 1},
                    {"kind": "move", "combatant": 1, "path": "over there"}):
        map_widget.play(rubbish)
    assert map_widget._effects == []


# ------------------------------------------------- the host, over a real socket


@pytest.fixture
def live(qtbot, repos):
    """A fight with one shared goblin and one the party has never been told of."""
    campaign = repos.campaigns.ensure_default("Seen")
    marco = repos.accounts.create(
        campaign.id, "marco", "goblin-teeth", display_name="Marco"
    )
    repos.accounts.create(
        campaign.id, "autopilot", "let-me-run-it", role="agent", display_name="Auto"
    )
    hero = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Brok",
               data={"hp": 20, "max_hp": 20})
    )
    seen = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Yeemik",
               data={"hp": 7, "max_hp": 7})
    )
    hidden = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="The thing")
    )
    repos.entities.set_owner(hero.id, marco.id)
    repos.shares.share(campaign.id, seen.id)

    encounter = repos.encounters.create(campaign.id, "The cave", width=16, height=16)
    tokens = {
        "hero": repos.encounters.add(encounter.id, hero.id, initiative=18, x=-3, y=0),
        "seen": repos.encounters.add(encounter.id, seen.id, initiative=9, x=1, y=0),
        "hidden": repos.encounters.add(encounter.id, hidden.id, initiative=5, x=5, y=5),
    }
    server = SessionServer(repos, campaign.id, "Seen session")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    server.set_autopilot(True, by="The DM")
    yield server, repos, encounter, tokens
    server.stop()


def _join(qtbot, server, username, password) -> SessionClient:
    client = SessionClient()
    with qtbot.waitSignal(client.connected, timeout=10000):
        client.join(f"ws://127.0.0.1:{server.port}", username, password)
    return client


def test_a_move_is_described_to_the_table(qtbot, live):
    server, _repos, _encounter, tokens = live
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    shown: list[dict] = []
    player.play.connect(shown.append)
    try:
        agent.send_move(tokens["seen"].id, 3, 0)
        qtbot.waitUntil(lambda: bool(shown), timeout=5000)

        event = shown[0]
        assert event["kind"] == "move"
        assert event["combatant"] == tokens["seen"].id
        assert event["path"][0] == [1, 0]
        assert event["path"][-1] == [3, 0]
    finally:
        agent.leave()
        player.leave()


def test_a_token_they_cannot_see_does_not_animate_for_them(qtbot, live):
    """Otherwise projection is undone by a moving dot."""
    server, _repos, _encounter, tokens = live
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    shown: list[dict] = []
    player.play.connect(shown.append)
    try:
        agent.send_move(tokens["hidden"].id, 6, 5)
        qtbot.wait(400)
        assert shown == []
    finally:
        agent.leave()
        player.leave()


def test_the_dm_sees_it_even_so(qtbot, live, repos):
    server, _repos, _encounter, tokens = live
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    shown: list[dict] = []
    agent.play.connect(shown.append)
    try:
        agent.send_move(tokens["hidden"].id, 6, 5)
        qtbot.waitUntil(lambda: bool(shown), timeout=5000)
        assert shown[0]["combatant"] == tokens["hidden"].id
    finally:
        agent.leave()


def test_the_dm_dragging_a_token_is_shown_too(qtbot, live, repos):
    """It is still a token moving, and everybody is looking at that map."""
    server, _repos, _encounter, tokens = live
    player = _join(qtbot, server, "marco", "goblin-teeth")
    shown: list[dict] = []
    player.play.connect(shown.append)
    try:
        repos.encounters.place(tokens["seen"].id, 1, 0)
        server._do_move(tokens["seen"].id, 2, 2)
        qtbot.waitUntil(lambda: bool(shown), timeout=5000)
        assert shown[0]["path"][-1] == [2, 2]
    finally:
        player.leave()


def test_going_down_is_shown_and_the_body_stays(qtbot, live, repos):
    """The headline one.

    Going down is a fall, not an exit. The event still goes out -- every screen
    should show them dropping at the same moment rather than each noticing a
    changed flag -- but the token stays on the square it fell on, drawn as a
    ghost. Taking it away made the square the party most wants to reach the one
    square showing nothing.
    """
    server, _repos, _encounter, tokens = live
    player = _join(qtbot, server, "marco", "goblin-teeth")
    shown: list[dict] = []
    player.play.connect(shown.append)
    try:
        goblin = repos.entities.list(server.campaign_id)
        yeemik = next(e for e in goblin if e.name == "Yeemik")
        before = repos.encounters.combatant(tokens["seen"].id)

        server._take_damage(yeemik, 99)
        qtbot.waitUntil(
            lambda: any(e["kind"] == "down" for e in shown), timeout=5000
        )

        assert repos.entities.get(yeemik.id).data["hp"] == 0
        after = repos.encounters.combatant(tokens["seen"].id)
        assert after is not None, (
            "down, not out of the fight -- a DM may bring them round"
        )
        assert (after.x, after.y) == (before.x, before.y)
        assert after.down is True
    finally:
        player.leave()


def test_a_wound_short_of_death_is_not_a_death(qtbot, live, repos):
    server, _repos, _encounter, tokens = live
    player = _join(qtbot, server, "marco", "goblin-teeth")
    shown: list[dict] = []
    player.play.connect(shown.append)
    try:
        yeemik = next(
            e for e in repos.entities.list(server.campaign_id) if e.name == "Yeemik"
        )
        server._take_damage(yeemik, 3)
        qtbot.wait(400)

        assert not any(e["kind"] == "down" for e in shown)
        assert repos.encounters.combatant(tokens["seen"].id).on_map
    finally:
        player.leave()


def test_coming_onto_the_map_is_not_a_walk(qtbot, live, repos):
    """There is nowhere to walk from."""
    server, _repos, _encounter, tokens = live
    player = _join(qtbot, server, "marco", "goblin-teeth")
    shown: list[dict] = []
    player.play.connect(shown.append)
    try:
        repos.encounters.place(tokens["seen"].id, None, None)
        server._do_move(tokens["seen"].id, 2, 2)
        qtbot.wait(400)
        assert shown == []
    finally:
        player.leave()
