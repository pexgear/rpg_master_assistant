"""Walking out of somebody's reach, and what it costs.

Without this rule a grid is a diagram. You stroll past the ogre to reach the
wizard behind it, and the ogre may only watch -- so standing next to something
means nothing, and neither does choosing where to stand. This is the rule that
makes the map a map.

Every square of the walk is checked, not only where it starts and ends --
found along the same route the move is animated along, round whatever is in
the way (`grid.route_between`). Checking only the ends missed a creature who cut
straight through an ogre's reach on the way to somewhere else, never adjacent
at either end of the move. Stepping around an enemy and back into its reach,
by contrast, has not really left it, and a rule that fired on every wobble
would punish moving at all -- "did you leave, at any point during the walk" is
the version people actually play, and that is the one checked here.
"""

from __future__ import annotations

import pytest

import canon_keeper.net.server as server_module
from canon_keeper.content import Content
from canon_keeper.net.server import SessionServer
from canon_keeper.repo.entities import KIND_NPC, KIND_PC, Entity
from canon_keeper.rules import attack


def _sheet(equipment, **overrides) -> dict:
    sheet = {
        "schema": 1,
        "species": "human",
        "class_index": "fighter",
        "level": 3,
        "abilities": {"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 10, "cha": 10},
        "equipment": equipment,
        "hp_current": 30,
    }
    sheet.update(overrides)
    return sheet


class _Rolls:
    def __init__(self, *totals: int) -> None:
        self._totals = list(totals)

    def __call__(self, notation: str):
        value = self._totals.pop(0) if self._totals else 18

        class _Result:
            total = value
            rolls = [value]

        return _Result()


@pytest.fixture
def content(repos) -> Content:
    return Content(repos.settings)


@pytest.fixture
def fight(qtbot, repos):
    """A hero standing next to a goblin, with room to walk away."""
    campaign = repos.campaigns.ensure_default("Reach")
    hero = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_PC,
            name="Brok",
            data={
                "hp": 30,
                "max_hp": 30,
                "sheet": _sheet(["battleaxe", "chain-mail"]),
            },
        )
    )
    goblin = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_NPC,
            name="Yeemik",
            data={
                "hp": 12,
                "max_hp": 12,
                "sheet": _sheet(["scimitar"], overrides={"ac": 10, "hp_max": 12}),
            },
        )
    )
    archer = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_NPC,
            name="Droop",
            data={
                "hp": 7,
                "max_hp": 7,
                "sheet": _sheet(["shortbow"], overrides={"ac": 10, "hp_max": 7}),
            },
        )
    )

    encounter = repos.encounters.create(campaign.id, "The cave", width=16, height=16)
    tokens = {
        # Standing on adjacent squares, so a step away leaves reach.
        "hero": repos.encounters.add(encounter.id, hero.id, initiative=20, x=0, y=0),
        "goblin": repos.encounters.add(encounter.id, goblin.id, initiative=10, x=1, y=0),
        "archer": repos.encounters.add(encounter.id, archer.id, initiative=5, x=-1, y=0),
    }
    repos.encounters.begin(encounter.id)

    server = SessionServer(repos, campaign.id, "Reach session")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    yield server, repos, encounter, tokens, hero, goblin, archer
    server.stop()


def _hp(repos, entity) -> int:
    return repos.entities.get(entity.id).data["hp"]


# ------------------------------------------------------------------- the rule


def test_walking_away_is_swung_at(fight, monkeypatch):
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5))
    server, repos, _encounter, tokens, hero, _goblin, _archer = fight
    before = _hp(repos, hero)

    server._do_move(tokens["hero"].id, 0, 4, spending=True)

    assert _hp(repos, hero) < before, "the goblin watched them walk away"


def test_a_walk_that_never_happens_is_not_swung_at(fight, monkeypatch):
    """You are swung at for leaving. Being refused is not leaving.

    An occupied destination is checked last, by ``place``, and deliberately so:
    it is left out of the route to keep the refusal specific. But the swings are
    provoked before that, so a creature that asked for an impossible square and
    was told no had already been swung at for a walk it never took -- and at low
    hit points that kills it where it stands.
    """
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5))
    server, repos, _encounter, tokens, hero, _goblin, _archer = fight
    # Standing exactly where test_walking_away_is_swung_at walks to, so the only
    # difference between that test and this one is whether the square is free.
    assert repos.encounters.place(tokens["archer"].id, 0, 4)
    before = _hp(repos, hero)

    assert server._do_move(tokens["hero"].id, 0, 4, spending=True) is False

    assert _hp(repos, hero) == before, "swung at for standing still"
    moved = repos.encounters.combatant(tokens["hero"].id)
    assert (moved.x, moved.y) == (0, 0)


def test_staying_in_reach_is_not(fight, monkeypatch):
    """Shuffling round somebody is not escaping them."""
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5))
    server, repos, _encounter, tokens, hero, _goblin, _archer = fight
    before = _hp(repos, hero)

    # 0,0 -> 1,1 is still one square from the goblin at 1,0.
    server._do_move(tokens["hero"].id, 1, 1, spending=True)

    assert _hp(repos, hero) == before


def test_an_archer_cannot_stop_you_leaving(fight, monkeypatch):
    """A bow does not hold ground, which is why archers want a friend in front."""
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5))
    server, repos, _encounter, tokens, hero, goblin, _archer = fight
    # Take the goblin out of it so only the archer is adjacent.
    repos.encounters.place(tokens["goblin"].id, 6, 6)
    before = _hp(repos, hero)

    server._do_move(tokens["hero"].id, 0, 4, spending=True)

    assert _hp(repos, hero) == before


def test_a_dm_arranging_the_board_provokes_nothing(fight, monkeypatch):
    """Dragging tokens about before anybody rolls is not somebody walking."""
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5))
    server, repos, _encounter, tokens, hero, _goblin, _archer = fight
    before = _hp(repos, hero)

    server._do_move(tokens["hero"].id, 0, 4, spending=False)

    assert _hp(repos, hero) == before


def test_cutting_through_reach_without_stopping_still_provokes(fight, monkeypatch):
    """Never adjacent at the start, never adjacent at the end -- and still hit.

    Start-and-end-only missed exactly this: a walk that enters an enemy's
    reach and leaves it again inside one move, with nothing at either end to
    show for it.
    """
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5))
    server, repos, _encounter, tokens, hero, _goblin, _archer = fight
    # The goblin stays at 1,0. A straight walk up column 0 passes within one
    # square of it at y=-1,0,1 and is nowhere near it at either end.
    repos.encounters.place(tokens["hero"].id, 0, -3)
    before = _hp(repos, hero)

    server._do_move(tokens["hero"].id, 0, 3, spending=True)

    assert _hp(repos, hero) < before, "walking straight through reach was free"


def test_never_getting_close_enough_provokes_nothing(fight, monkeypatch):
    """The other half of the same check: a walk that never enters is not a leave."""
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5))
    server, repos, _encounter, tokens, hero, _goblin, _archer = fight
    # Column -3 stays four squares from the goblin at 1,0 the whole way.
    repos.encounters.place(tokens["hero"].id, -3, -3)
    before = _hp(repos, hero)

    server._do_move(tokens["hero"].id, -3, 3, spending=True)

    assert _hp(repos, hero) == before


def test_friends_do_not_swing_at_each_other(fight, monkeypatch):
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5))
    server, repos, _encounter, tokens, _hero, goblin, _archer = fight
    # The goblin walks away from the other goblin's neighbour -- the hero is
    # the only thing adjacent, and a goblin is no enemy of a goblin.
    repos.encounters.place(tokens["hero"].id, 6, 6)
    repos.encounters.place(tokens["archer"].id, 2, 0)
    before = _hp(repos, goblin)

    server._do_move(tokens["goblin"].id, 1, 5, spending=True)

    assert _hp(repos, goblin) == before


# ---------------------------------------------------------------- one a round


def test_a_reaction_is_spent_once_a_round(fight, monkeypatch):
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5, 19, 5))
    server, repos, _encounter, tokens, hero, _goblin, _archer = fight

    server._do_move(tokens["hero"].id, 0, 4, spending=True)
    hurt_once = _hp(repos, hero)

    # Walk back into reach and out again in the same round.
    server._do_move(tokens["hero"].id, 0, 0, spending=True)
    server._do_move(tokens["hero"].id, 0, 4, spending=True)

    assert _hp(repos, hero) == hurt_once, "the goblin reacted twice in one round"


def test_the_reaction_comes_back_next_round(fight, monkeypatch):
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5, 19, 5))
    server, repos, encounter, tokens, hero, _goblin, _archer = fight

    server._do_move(tokens["hero"].id, 0, 4, spending=True)
    hurt_once = _hp(repos, hero)

    # Round the order, twice, so the round number moves on.
    for _ in range(4):
        server.run_turn("next")
    server._do_move(tokens["hero"].id, 0, 0, spending=True)
    server._do_move(tokens["hero"].id, 0, 4, spending=True)

    assert _hp(repos, hero) < hurt_once, "the reaction never came back"


# --------------------------------------------------------------- falling over


def _about_to_fall(repos, hero, monkeypatch) -> None:
    """One hit point, and a swing that will certainly land."""
    entity = repos.entities.get(hero.id)
    entity.data = {**dict(entity.data), "hp": 1}
    repos.entities.update(entity)
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 6))


def test_dropped_on_the_way_out_you_never_arrive(fight, monkeypatch):
    """Dropped leaving somebody's reach, you do not reach where you were going."""
    server, repos, _encounter, tokens, hero, _goblin, _archer = fight
    _about_to_fall(repos, hero, monkeypatch)

    moved = server._do_move(tokens["hero"].id, 0, 4, spending=True)

    assert moved is False
    assert _hp(repos, hero) == 0
    assert repos.encounters.combatant(tokens["hero"].id).y != 4


def test_you_fall_on_the_square_the_blow_caught_you_on(fight, monkeypatch):
    """Which is where you were standing, and where help will come looking."""
    server, repos, _encounter, tokens, hero, _goblin, _archer = fight
    _about_to_fall(repos, hero, monkeypatch)

    server._do_move(tokens["hero"].id, 0, 4, spending=True)

    fallen = repos.encounters.combatant(tokens["hero"].id)
    assert fallen.on_map, "they did not vanish -- they fell"
    # The goblin at 1,0 still reaches 0,1 diagonally, so the step that leaves
    # its reach is the one arriving at 0,2. Not four squares away, and not
    # back at the square they set off from.
    assert (fallen.x, fallen.y) == (0, 2)


def test_going_down_mid_walk_is_published_at_once(fight, monkeypatch):
    """Everyone has to see the ghost now, not at whatever happens next.

    This path used to return without telling anybody, so the creature stayed
    drawn on its feet until something unrelated refreshed the map.
    """
    server, repos, _encounter, tokens, hero, _goblin, _archer = fight
    _about_to_fall(repos, hero, monkeypatch)
    told: list[int] = []
    server.encounter_applied.connect(lambda: told.append(1))

    server._do_move(tokens["hero"].id, 0, 4, spending=True)

    assert told, "nobody was told the fight had changed"
    assert repos.encounters.combatant(tokens["hero"].id).down is True


def test_going_down_mid_walk_ends_your_turn(fight, monkeypatch):
    """There is nothing an unconscious creature does with the rest of it."""
    server, repos, encounter, tokens, hero, _goblin, _archer = fight
    assert repos.encounters.get(encounter.id).turn_combatant_id == tokens["hero"].id
    _about_to_fall(repos, hero, monkeypatch)

    server._do_move(tokens["hero"].id, 0, 4, spending=True)

    assert repos.encounters.get(encounter.id).turn_combatant_id != tokens["hero"].id


def test_the_walk_shown_is_only_as_far_as_they_got(fight, monkeypatch):
    """The whole walk had already been described. It has to be taken back."""
    server, repos, _encounter, tokens, hero, _goblin, _archer = fight
    _about_to_fall(repos, hero, monkeypatch)
    walks: list[dict] = []
    server.played.connect(
        lambda event: walks.append(event) if event["kind"] == "move" else None
    )

    server._do_move(tokens["hero"].id, 0, 4, spending=True)

    assert len(walks) == 2, "the full walk was described and never corrected"
    assert [tuple(square) for square in walks[-1]["path"]] == [(0, 0), (0, 1), (0, 2)]


def test_falling_is_not_reported_as_a_square_being_taken(fight, monkeypatch):
    """The square was empty. They were cut down on the way to it.

    Every door that reports a failed walk reported both its causes in the same
    words -- "taken, or something is in the way" -- so the DM's own status bar
    contradicted the chat line directly above it, which said what had actually
    happened. Nothing to add is nothing said.
    """
    server, repos, _encounter, tokens, hero, _goblin, _archer = fight
    _about_to_fall(repos, hero, monkeypatch)

    problem = server.take_turn(tokens["hero"].id, move=[0, 4])

    assert problem == "", f"the DM was told {problem!r} about an empty square"
    assert repos.encounters.combatant(tokens["hero"].id).down is True


def test_a_square_really_taken_still_says_so(fight, monkeypatch):
    """The other half of it: the ordinary refusal has not gone quiet."""
    server, repos, _encounter, tokens, hero, _goblin, archer = fight
    monkeypatch.setattr(server_module, "roll", _Rolls(1, 1))
    # The archer is standing at -1,0 and holds no ground, so walking onto it
    # is refused by the square being occupied rather than by anybody's reach.
    problem = server.take_turn(tokens["hero"].id, move=[-1, 0])

    assert "taken" in problem
    assert repos.encounters.combatant(tokens["hero"].id).down is False


def test_cut_down_walking_over_you_do_not_get_the_swing_in(fight, monkeypatch):
    """A proposal is a move and an action, carried out in that order.

    Nothing between the two halves asked whether the creature was still on its
    feet, and the one-action rule could not catch it: going down passes the
    turn, and an action is only ever spent by whoever's turn it is. So a hero
    dropped crossing the room still landed a blow from the floor.
    """
    from canon_keeper.net.projection import Viewer
    from canon_keeper.net.server import _Session
    from canon_keeper_protocol.messages import Member

    server, repos, _encounter, tokens, hero, goblin, archer = fight
    # Next to the square they fall on *and* next to the one they were walking
    # to, so what is being tested is whether they are conscious rather than
    # whether they are close enough.
    assert repos.encounters.place(tokens["archer"].id, 1, 3)
    _about_to_fall(repos, hero, monkeypatch)
    before = _hp(repos, archer)
    session = _Session(
        member=Member(id="", name="Marco", role="player"),
        account_id=None,
        viewer=Viewer.dungeon_master(),
    )

    server._carry_out(
        {
            "combatant": tokens["hero"].id,
            "who": "Brok",
            "move": [0, 4],
            "target": tokens["archer"].id,
            "weapon": "battleaxe",
        },
        session,
    )

    assert repos.encounters.combatant(tokens["hero"].id).down is True
    assert _hp(repos, archer) == before, "an unconscious hero swung an axe"


def test_the_unconscious_do_not_swing_at_passers_by(fight, monkeypatch):
    monkeypatch.setattr(server_module, "roll", _Rolls(19, 5))
    server, repos, _encounter, tokens, hero, goblin, _archer = fight
    entity = repos.entities.get(goblin.id)
    data = dict(entity.data)
    data["hp"] = 0
    entity.data = data
    repos.entities.update(entity)
    before = _hp(repos, hero)

    server._do_move(tokens["hero"].id, 0, 4, spending=True)

    assert _hp(repos, hero) == before


# ----------------------------------------------------------------- the rule alone


def test_only_melee_holds_ground(content):
    assert attack.threatens(_sheet(["battleaxe"]), content, 1) is True
    assert attack.threatens(_sheet(["shortbow"]), content, 1) is False
    assert attack.threatens(_sheet(["battleaxe"]), content, 2) is False
    assert attack.threatens(_sheet(["chain-mail"]), content, 1) is False


def test_the_weapon_taken_up_is_the_first_melee_one(content):
    weapon = attack.melee_weapon(_sheet(["shortbow", "battleaxe"]), content)
    assert weapon is not None and weapon.index == "battleaxe"
    assert attack.melee_weapon(_sheet(["shortbow"]), content) is None
