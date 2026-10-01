"""Zero hit points: the end for a monster, the start of something for a player.

Two rules meet here and they pull in opposite directions, which is why they are
tested together.

The turn must **pass over** whoever is out of the fight, or a long combat gets
slower the closer it comes to being over -- every round stopping to offer a turn
to each creature that died in it.

The turn must **not** pass over somebody who is dying, because the death save
happens at the start of their turn. Skip them and they neither die nor recover:
they simply stop, forever, which is worse than either ending.
"""

from __future__ import annotations

import pytest

import canon_keeper.net.server as server_module
from canon_keeper.net.server import SessionServer
from canon_keeper_core.repo.entities import KIND_NPC, KIND_PC, Entity
from canon_keeper_core.rules import death


class _Rolls:
    """Dice the test decides, in the shape the host's roller returns."""

    def __init__(self, *totals: int) -> None:
        self._totals = list(totals)

    def __call__(self, notation: str):
        value = self._totals.pop(0) if self._totals else 10

        class _Result:
            total = value
            rolls = [value]

        return _Result()


@pytest.fixture
def fight(qtbot, repos):
    """A player character, two goblins, and a fight already running."""
    campaign = repos.campaigns.ensure_default("Dying")
    hero = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_PC,
            name="Brok",
            data={"hp": 28, "max_hp": 28, "sheet": {"schema": 1, "level": 3}},
        )
    )
    goblins = [
        repos.entities.create(
            Entity(
                id=None,
                campaign_id=campaign.id,
                kind=KIND_NPC,
                name=name,
                data={"hp": 7, "max_hp": 7, "sheet": {"schema": 1, "level": 1}},
            )
        )
        for name in ("Yeemik", "Droop")
    ]

    encounter = repos.encounters.create(campaign.id, "The cave", width=12, height=12)
    tokens = {
        "hero": repos.encounters.add(encounter.id, hero.id, initiative=20, x=0, y=0),
        "first": repos.encounters.add(encounter.id, goblins[0].id, initiative=10, x=2, y=0),
        "second": repos.encounters.add(encounter.id, goblins[1].id, initiative=5, x=3, y=0),
    }
    repos.encounters.begin(encounter.id)

    server = SessionServer(repos, campaign.id, "Dying session")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    yield server, repos, encounter, tokens, hero, goblins
    server.stop()


def _drop(repos, entity) -> None:
    """Put somebody on zero hit points without going through a weapon."""
    data = dict(entity.data or {})
    data["hp"] = 0
    entity.data = data
    repos.entities.update(entity)


def _turn(repos, encounter) -> int | None:
    return repos.encounters.get(encounter.id).turn_combatant_id


# ------------------------------------------------------------- passing them by


def test_the_turn_passes_over_a_dead_goblin(fight):
    """The bug this file exists for: a fight that stops on its own corpses."""
    server, repos, encounter, tokens, _hero, goblins = fight
    _drop(repos, goblins[0])

    assert _turn(repos, encounter) == tokens["hero"].id
    server.run_turn("next")

    assert _turn(repos, encounter) == tokens["second"].id, (
        "the turn was handed to a goblin that died two rounds ago"
    )


def test_a_dead_goblin_stays_in_the_order(fight):
    """Passed over is not removed. A DM may still want to look at it."""
    server, repos, encounter, tokens, _hero, goblins = fight
    _drop(repos, goblins[0])
    server.run_turn("next")

    listed = [c.id for c in repos.encounters.combatants(encounter.id)]
    assert tokens["first"].id in listed


def test_a_round_still_ends_when_the_survivors_wrap(fight):
    server, repos, encounter, tokens, _hero, goblins = fight
    _drop(repos, goblins[0])
    _drop(repos, goblins[1])

    before = repos.encounters.get(encounter.id).round
    server.run_turn("next")

    assert _turn(repos, encounter) == tokens["hero"].id
    assert repos.encounters.get(encounter.id).round == before + 1


def test_when_everybody_is_down_nobody_is_up(fight):
    """Not "the fight is over" -- that is the DM's call, not a repository's."""
    server, repos, encounter, _tokens, hero, goblins = fight
    _drop(repos, hero)
    _drop(repos, goblins[0])
    _drop(repos, goblins[1])
    # The hero is dying rather than dead, so three failures first.
    repos.encounters.record_death_save(_tokens_of(repos, encounter, hero), failures=3)

    server.run_turn("next")
    assert _turn(repos, encounter) is None


def _tokens_of(repos, encounter, entity) -> int:
    return next(
        c.id
        for c in repos.encounters.combatants(encounter.id)
        if c.entity_id == entity.id
    )


# ------------------------------------------------------------------ dying well


def test_a_dying_character_is_still_handed_the_turn(fight, monkeypatch):
    """Skipping them would mean they never roll, and so never die or recover."""
    monkeypatch.setattr(server_module, "roll", _Rolls(15))
    server, repos, encounter, tokens, hero, _goblins = fight
    _drop(repos, hero)
    # Move the turn to the last goblin so the next step wraps onto the hero.
    server.run_turn("next")
    server.run_turn("next")

    server.run_turn("next")

    combatant = repos.encounters.combatant(tokens["hero"].id)
    assert combatant.death_successes == 1, "the dying character never rolled"


def test_the_death_save_is_the_whole_turn(fight, monkeypatch):
    """It resolves and the turn moves straight on -- there is nothing to do."""
    monkeypatch.setattr(server_module, "roll", _Rolls(15))
    server, repos, encounter, tokens, hero, _goblins = fight
    _drop(repos, hero)
    server.run_turn("next")
    server.run_turn("next")
    server.run_turn("next")

    assert _turn(repos, encounter) == tokens["first"].id, (
        "the turn stopped on somebody who cannot act"
    )


def test_three_failures_is_dead(fight, monkeypatch):
    monkeypatch.setattr(server_module, "roll", _Rolls(3, 4, 5))
    server, repos, encounter, tokens, hero, _goblins = fight
    _drop(repos, hero)

    for _ in range(9):  # three rounds of three combatants
        server.run_turn("next")

    combatant = repos.encounters.combatant(tokens["hero"].id)
    assert combatant.death_failures >= death.SAVES_NEEDED
    assert death.condition(0, KIND_PC, combatant.death_successes,
                           combatant.death_failures) == death.DEAD


def test_three_saves_is_stable_and_no_more_turns(fight, monkeypatch):
    monkeypatch.setattr(server_module, "roll", _Rolls(15, 15, 15))
    server, repos, encounter, tokens, hero, _goblins = fight
    _drop(repos, hero)

    for _ in range(9):
        server.run_turn("next")

    combatant = repos.encounters.combatant(tokens["hero"].id)
    assert combatant.death_successes >= death.SAVES_NEEDED
    # Stable, so the turn now passes them by like anybody else who is out.
    assert tokens["hero"].id in server._resting()


def test_a_natural_twenty_stands_them_back_up(fight, monkeypatch):
    monkeypatch.setattr(server_module, "roll", _Rolls(20))
    server, repos, encounter, tokens, hero, _goblins = fight
    _drop(repos, hero)
    server.run_turn("next")
    server.run_turn("next")

    server.run_turn("next")

    assert repos.entities.get(hero.id).data["hp"] == 1
    combatant = repos.encounters.combatant(tokens["hero"].id)
    assert combatant.death_failures == 0 and combatant.death_successes == 0
    assert _turn(repos, encounter) == tokens["hero"].id, (
        "back on their feet, and the turn is theirs"
    )


def test_a_natural_one_costs_two(fight, monkeypatch):
    monkeypatch.setattr(server_module, "roll", _Rolls(1))
    server, repos, encounter, tokens, hero, _goblins = fight
    _drop(repos, hero)
    server.run_turn("next")
    server.run_turn("next")
    server.run_turn("next")

    assert repos.encounters.combatant(tokens["hero"].id).death_failures == 2


# --------------------------------------------------------------- and monsters


def test_a_goblin_gets_no_saves(fight, monkeypatch):
    """Three more d20s to confirm the orc is dead is nobody's idea of fun."""
    monkeypatch.setattr(server_module, "roll", _Rolls(15, 15, 15))
    server, repos, encounter, tokens, _hero, goblins = fight
    _drop(repos, goblins[0])

    for _ in range(6):
        server.run_turn("next")

    combatant = repos.encounters.combatant(tokens["first"].id)
    assert combatant.death_successes == 0 and combatant.death_failures == 0


def test_a_monster_at_zero_is_dead_not_dying():
    assert death.condition(0, KIND_NPC, 0, 0) == death.DEAD
    assert death.condition(0, KIND_PC, 0, 0) == death.DYING


def test_nobody_with_unknown_hit_points_starts_rolling():
    """A token dropped on the map to stand for a crowd is not at zero."""
    assert death.condition(None, KIND_PC, 0, 0) == death.UP


# -------------------------------------------------------------- getting up again


def test_healing_and_dropping_again_starts_the_count_again(fight):
    """Two failures carried through a healing word would be a rule the game
    does not have, and a nasty one."""
    server, repos, encounter, tokens, hero, _goblins = fight
    repos.encounters.record_death_save(tokens["hero"].id, failures=2)

    server._heal_to(repos.entities.get(hero.id), 6)
    server._take_damage(repos.entities.get(hero.id), 6)

    combatant = repos.encounters.combatant(tokens["hero"].id)
    assert combatant.death_failures == 0, "the old count followed them down"


def test_being_hit_while_down_costs_a_save(fight):
    server, repos, encounter, tokens, hero, _goblins = fight
    _drop(repos, hero)

    server._take_damage(repos.entities.get(hero.id), 4)

    assert repos.encounters.combatant(tokens["hero"].id).death_failures == 1


def test_hitting_a_corpse_is_a_mood_not_a_rule(fight):
    server, repos, encounter, tokens, _hero, goblins = fight
    _drop(repos, goblins[0])

    server._take_damage(repos.entities.get(goblins[0].id), 4)

    assert repos.encounters.combatant(tokens["first"].id).death_failures == 0


# ------------------------------------------------------------- staying put


def test_a_body_stays_on_the_square_it_fell_on(fight):
    """Taking the token away hid the one square the party most wants to reach."""
    server, repos, _encounter, tokens, _hero, goblins = fight
    combatant = repos.encounters.combatant(tokens["first"].id)
    where = (combatant.x, combatant.y)

    server._take_damage(repos.entities.get(goblins[0].id), 99)

    after = repos.encounters.combatant(tokens["first"].id)
    assert (after.x, after.y) == where
    assert after.down is True


def test_a_body_does_not_hold_its_square(fight):
    """Stepping over one is a thing people do; walking around it is not."""
    server, repos, _encounter, tokens, hero, goblins = fight
    server._take_damage(repos.entities.get(goblins[0].id), 99)
    body = repos.encounters.combatant(tokens["first"].id)

    assert repos.encounters.place(tokens["hero"].id, body.x, body.y) is True


def test_getting_up_takes_the_ghost_off(fight):
    server, repos, _encounter, tokens, _hero, goblins = fight
    server._take_damage(repos.entities.get(goblins[0].id), 99)
    assert repos.encounters.combatant(tokens["first"].id).down is True

    server._heal_to(repos.entities.get(goblins[0].id), 4)

    assert repos.encounters.combatant(tokens["first"].id).down is False


def test_a_ghost_left_by_a_hand_edit_is_tidied_on_publish(fight):
    """A DM typing hit points into the Characters panel is not this code path.

    So the flag is re-derived every time the fight is published, which is the
    one moment everybody is about to be told what is true.
    """
    server, repos, _encounter, tokens, _hero, goblins = fight
    entity = repos.entities.get(goblins[0].id)
    data = dict(entity.data)
    data["hp"] = 0
    entity.data = data
    repos.entities.update(entity)
    assert repos.encounters.combatant(tokens["first"].id).down is False

    server.publish_encounter()

    assert repos.encounters.combatant(tokens["first"].id).down is True
