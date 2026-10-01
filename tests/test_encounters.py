"""Fights: the order, the grid, and the two ways of leaving one.

The behaviour worth pinning down is not "a row can be inserted". It is that the
initiative order is a total, stable function of the rows, that off the map and
out of the fight stay two different things, and that the turn marker survives
the roster changing underneath it -- which is the case a real table hits every
time a goblin dies.
"""

from __future__ import annotations

import pytest

from canon_keeper_core.repo.encounters import MAX_SIZE, MIN_SIZE, order_of
from canon_keeper_core.repo.entities import Entity


@pytest.fixture
def fight(repos):
    campaign = repos.campaigns.ensure_default("Fight")
    encounter = repos.encounters.create(campaign.id, "The cave", width=10, height=8)
    return repos, campaign.id, encounter


def _creature(repos, campaign_id, name, kind="npc"):
    return repos.entities.create(
        Entity(id=None, campaign_id=campaign_id, kind=kind, name=name)
    )


# --------------------------------------------------------------------- the order


def test_the_order_is_initiative_then_dexterity_then_id(fight):
    repos, campaign_id, encounter = fight
    slow = repos.encounters.add(encounter.id, _creature(repos, campaign_id, "Slow").id,
                                initiative=8)
    quick = repos.encounters.add(encounter.id, _creature(repos, campaign_id, "Quick").id,
                                 initiative=18)
    tied_low = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "TiedLow").id,
        initiative=12, tiebreak=1
    )
    tied_high = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "TiedHigh").id,
        initiative=12, tiebreak=4
    )

    order = [c.id for c in repos.encounters.combatants(encounter.id)]
    assert order == [quick.id, tied_high.id, tied_low.id, slow.id]


def test_nobody_rolled_yet_goes_last(fight):
    """"Not rolled" is not "rolled a zero", and must not sort like one."""
    repos, campaign_id, encounter = fight
    unrolled = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "Waiting").id
    )
    terrible = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "Unlucky").id, initiative=-3
    )

    order = [c.id for c in repos.encounters.combatants(encounter.id)]
    assert order == [terrible.id, unrolled.id]


def test_the_order_is_a_pure_function(fight):
    """order_of is used by the panel and the wire. It must not need a database."""
    repos, campaign_id, encounter = fight
    for name, initiative in (("a", 5), ("b", 15), ("c", 10)):
        repos.encounters.add(
            encounter.id, _creature(repos, campaign_id, name).id, initiative=initiative
        )
    rows = repos.encounters.combatants(encounter.id)
    assert order_of(list(reversed(rows))) == rows


# ----------------------------------------------------------------- on and off


def test_off_the_map_is_still_in_the_fight(fight):
    repos, campaign_id, encounter = fight
    runner = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "Runner").id, initiative=10, x=2, y=2
    )

    assert repos.encounters.place(runner.id, None, None)
    still_there = repos.encounters.combatant(runner.id)
    assert still_there is not None, "taken off the map should not delete them"
    assert not still_there.on_map
    assert len(repos.encounters.combatants(encounter.id)) == 1


def test_out_of_the_fight_is_a_different_thing(fight):
    repos, campaign_id, encounter = fight
    goner = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "Goner").id, initiative=10
    )
    repos.encounters.remove(goner.id)
    assert repos.encounters.combatant(goner.id) is None
    assert repos.encounters.combatants(encounter.id) == []


def test_a_square_outside_the_grid_is_refused(fight):
    """0,0 is the middle, so a ten by eight map runs x -5..4 and y -4..3."""
    repos, campaign_id, encounter = fight
    who = repos.encounters.add(encounter.id, _creature(repos, campaign_id, "X").id)
    assert not repos.encounters.place(who.id, 5, 0)
    assert not repos.encounters.place(who.id, 0, 4)
    assert not repos.encounters.place(who.id, -6, 0)
    assert repos.encounters.place(who.id, -5, -4)
    assert repos.encounters.place(who.id, 4, 3)


def test_two_creatures_cannot_share_a_square(fight):
    repos, campaign_id, encounter = fight
    first = repos.encounters.add(encounter.id, _creature(repos, campaign_id, "A").id)
    second = repos.encounters.add(encounter.id, _creature(repos, campaign_id, "B").id)
    assert repos.encounters.place(first.id, 3, 3)
    assert not repos.encounters.place(second.id, 3, 3)
    # And standing still is not standing on yourself.
    assert repos.encounters.place(first.id, 3, 3)


def test_joining_a_fight_on_a_square_obeys_the_same_three_facts(fight):
    """Arriving already standing somewhere is still standing somewhere.

    ``place`` has refused an impossible square since there was a grid, and
    ``add`` wrote whatever it was given straight to the database -- so the one
    door that puts a creature on the map without moving it there was the one
    door with no check. A goblin enlisted onto a square the map does not have
    makes the fight unplayable rather than merely odd: every legal move it
    tries comes back "off the map", because the square it measures from is the
    one outside the room.
    """
    repos, campaign_id, encounter = fight
    standing = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "Standing").id, x=3, y=3
    )
    repos.encounters.toggle_obstacle(encounter.id, 2, 2)

    outside = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "Outside").id, x=50, y=50
    )
    crowding = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "Crowding").id, x=3, y=3
    )
    in_the_rock = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "Rock").id, x=2, y=2
    )

    assert standing.on_map, "a square that exists is still written down"
    # In the order, and the DM can drag them on. Not standing outside the room.
    assert not outside.on_map
    assert not crowding.on_map
    assert not in_the_rock.on_map
    assert repos.encounters.at(encounter.id, 3, 3).id == standing.id


def test_one_creature_joins_a_fight_once(fight):
    repos, campaign_id, encounter = fight
    goblin = _creature(repos, campaign_id, "Goblin")
    assert repos.encounters.add(encounter.id, goblin.id) is not None
    assert repos.encounters.add(encounter.id, goblin.id) is None
    assert len(repos.encounters.combatants(encounter.id)) == 1


def test_shrinking_the_grid_takes_the_stranded_off_the_map(fight):
    """Not deleted, and not left at a square that no longer exists."""
    repos, campaign_id, encounter = fight
    far = repos.encounters.add(encounter.id, _creature(repos, campaign_id, "Far").id)
    near = repos.encounters.add(encounter.id, _creature(repos, campaign_id, "Near").id)
    repos.encounters.place(far.id, 4, 3)
    repos.encounters.place(near.id, 1, 1)

    repos.encounters.resize(encounter.id, 6, 6)
    assert not repos.encounters.combatant(far.id).on_map
    assert repos.encounters.combatant(near.id).on_map


def test_the_grid_stays_a_size_a_person_can_read(fight):
    repos, _campaign_id, encounter = fight
    repos.encounters.resize(encounter.id, 1000, 0)
    resized = repos.encounters.get(encounter.id)
    assert resized.width == MAX_SIZE
    assert resized.height == MIN_SIZE


# ------------------------------------------------------------ what is in the way


def test_an_obstacle_goes_in_and_comes_out(fight):
    repos, _campaign_id, encounter = fight
    assert repos.encounters.toggle_obstacle(encounter.id, 3, 3) is True
    assert repos.encounters.obstacles(encounter.id) == {(3, 3)}

    assert repos.encounters.toggle_obstacle(encounter.id, 3, 3) is False
    assert repos.encounters.obstacles(encounter.id) == set()


def test_nobody_can_stand_in_one(fight):
    repos, campaign_id, encounter = fight
    who = repos.encounters.add(encounter.id, _creature(repos, campaign_id, "X").id)
    repos.encounters.toggle_obstacle(encounter.id, 3, 3)

    assert not repos.encounters.place(who.id, 3, 3)
    assert not repos.encounters.combatant(who.id).on_map


def test_one_cannot_be_dropped_on_somebody(fight):
    """The square would hold a creature and a rock at once."""
    repos, campaign_id, encounter = fight
    who = repos.encounters.add(encounter.id, _creature(repos, campaign_id, "X").id)
    repos.encounters.place(who.id, 2, 2)

    assert repos.encounters.toggle_obstacle(encounter.id, 2, 2) is False
    assert repos.encounters.obstacles(encounter.id) == set()


def test_one_outside_the_grid_is_refused(fight):
    repos, _campaign_id, encounter = fight
    assert repos.encounters.toggle_obstacle(encounter.id, 99, 0) is False
    assert repos.encounters.obstacles(encounter.id) == set()


def test_shrinking_the_grid_removes_the_terrain_outside_it(fight):
    repos, _campaign_id, encounter = fight
    repos.encounters.toggle_obstacle(encounter.id, 9, 7)
    repos.encounters.toggle_obstacle(encounter.id, 1, 1)

    repos.encounters.resize(encounter.id, 6, 6)

    assert repos.encounters.obstacles(encounter.id) == {(1, 1)}


def test_emptying_the_fight_keeps_the_room(fight):
    """The obstacles are the cave, not the goblins standing in it."""
    repos, campaign_id, encounter = fight
    repos.encounters.add(encounter.id, _creature(repos, campaign_id, "X").id)
    repos.encounters.toggle_obstacle(encounter.id, 3, 3)

    repos.encounters.clear(encounter.id)

    assert repos.encounters.combatants(encounter.id) == []
    assert repos.encounters.obstacles(encounter.id) == {(3, 3)}


def test_an_obstacle_bumps_the_version(fight):
    repos, _campaign_id, encounter = fight
    before = repos.encounters.get(encounter.id).version
    repos.encounters.toggle_obstacle(encounter.id, 3, 3)
    assert repos.encounters.get(encounter.id).version > before


# ---------------------------------------------------------------------- turns


def test_starting_puts_the_highest_initiative_up(fight):
    repos, campaign_id, encounter = fight
    slow = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "Slow").id, initiative=4
    )
    fast = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "Fast").id, initiative=20
    )
    repos.encounters.begin(encounter.id)

    started = repos.encounters.get(encounter.id)
    assert started.round == 1
    assert started.turn_combatant_id == fast.id
    assert started.turn_combatant_id != slow.id


def test_the_round_goes_up_when_the_order_wraps(fight):
    repos, campaign_id, encounter = fight
    first = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "First").id, initiative=20
    )
    second = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "Second").id, initiative=10
    )

    repos.encounters.begin(encounter.id)
    repos.encounters.advance(encounter.id)
    assert repos.encounters.get(encounter.id).turn_combatant_id == second.id
    assert repos.encounters.get(encounter.id).round == 1

    repos.encounters.advance(encounter.id)
    wrapped = repos.encounters.get(encounter.id)
    assert wrapped.turn_combatant_id == first.id
    assert wrapped.round == 2


def test_advancing_before_it_starts_starts_it(fight):
    repos, campaign_id, encounter = fight
    only = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "Only").id, initiative=10
    )
    repos.encounters.advance(encounter.id)
    started = repos.encounters.get(encounter.id)
    assert started.round == 1
    assert started.turn_combatant_id == only.id


def test_removing_whoever_is_up_passes_the_turn_on(fight):
    """The case every fight hits: the thing whose turn it is, dies.

    The marker must land on the next in order, not on nobody -- otherwise the
    next press restarts the round from the top and someone acts twice.
    """
    repos, campaign_id, encounter = fight
    first = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "First").id, initiative=20
    )
    second = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "Second").id, initiative=10
    )
    third = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "Third").id, initiative=5
    )

    repos.encounters.begin(encounter.id)
    repos.encounters.advance(encounter.id)
    assert repos.encounters.get(encounter.id).turn_combatant_id == second.id

    repos.encounters.remove(second.id)
    assert repos.encounters.get(encounter.id).turn_combatant_id == third.id
    assert repos.encounters.combatant(first.id) is not None


def test_removing_the_last_one_leaves_nobody_up(fight):
    repos, campaign_id, encounter = fight
    only = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "Only").id, initiative=10
    )
    repos.encounters.begin(encounter.id)
    repos.encounters.remove(only.id)
    assert repos.encounters.get(encounter.id).turn_combatant_id is None


def test_ending_stops_the_clock_and_keeps_the_fight(fight):
    repos, campaign_id, encounter = fight
    who = repos.encounters.add(
        encounter.id, _creature(repos, campaign_id, "Who").id, initiative=10, x=1, y=1
    )
    repos.encounters.begin(encounter.id)
    repos.encounters.end(encounter.id)

    over = repos.encounters.get(encounter.id)
    assert over.round == 0
    assert not over.running
    assert over.turn_combatant_id is None
    # Everything is still there to look at.
    assert repos.encounters.combatant(who.id).on_map


# ------------------------------------------------------------ which fight is on


def test_only_one_fight_runs_at_a_time(repos):
    campaign = repos.campaigns.ensure_default("Two fights")
    first = repos.encounters.create(campaign.id, "Ambush")
    second = repos.encounters.create(campaign.id, "The boss")

    assert repos.encounters.running(campaign.id).id == second.id
    assert not repos.encounters.get(first.id).running

    repos.encounters.set_running(first.id, True)
    assert repos.encounters.running(campaign.id).id == first.id
    assert not repos.encounters.get(second.id).running


def test_a_finished_fight_stays_on_screen_but_off_the_wire(repos):
    """`current` keeps showing it; `running` -- what players are sent -- does not."""
    campaign = repos.campaigns.ensure_default("One fight")
    encounter = repos.encounters.create(campaign.id, "Over")
    repos.encounters.end(encounter.id)

    assert repos.encounters.running(campaign.id) is None
    assert repos.encounters.current(campaign.id).id == encounter.id


def test_deleting_a_fight_takes_its_combatants_and_its_terrain(fight):
    repos, campaign_id, encounter = fight
    who = repos.encounters.add(encounter.id, _creature(repos, campaign_id, "Who").id)
    repos.encounters.toggle_obstacle(encounter.id, 3, 3)

    repos.encounters.delete(encounter.id)

    assert repos.encounters.get(encounter.id) is None
    assert repos.encounters.combatant(who.id) is None
    assert repos.encounters.obstacles(encounter.id) == set()


def test_every_change_bumps_the_version(fight):
    """Including changes to combatants, which is the point of one version."""
    repos, campaign_id, encounter = fight
    before = repos.encounters.get(encounter.id).version
    who = repos.encounters.add(encounter.id, _creature(repos, campaign_id, "Who").id)
    after_add = repos.encounters.get(encounter.id).version
    assert after_add > before

    repos.encounters.place(who.id, 1, 1)
    assert repos.encounters.get(encounter.id).version > after_add
