"""Keeping the running stand-ins matching the handed-over characters.

The supervisor is deliberately dumb: it compares two sets and starts or stops
the difference. What is worth testing is the edges -- a character handed back,
one whose process died, one nobody plays, and the seat being revoked whenever a
stand-in goes, because a live token with nothing sitting in it is a way into
somebody's character that nobody is watching.

No real processes are started here. Spawning is one function, and what matters
is *when* it is called.
"""

from __future__ import annotations

import pytest

from canon_keeper import stand_ins
from canon_keeper.net.server import SessionServer
from canon_keeper_core.repo.entities import KIND_NPC, KIND_PC, Entity


class _Fake:
    """A process that is running until told otherwise."""

    def __init__(self) -> None:
        self.stopped = False

    def poll(self):
        return 0 if self.stopped else None

    def terminate(self) -> None:
        self.stopped = True

    def wait(self, timeout=None) -> int:
        return 0

    def kill(self) -> None:
        self.stopped = True


@pytest.fixture
def started(monkeypatch):
    """Record what would have been spawned, and spawn nothing."""
    launched: list[tuple[str, str]] = []

    def fake_start(url, seat, pause=None):
        launched.append((url, seat))
        return _Fake()

    monkeypatch.setattr(stand_ins, "start", fake_start)
    return launched


@pytest.fixture
def table(qtbot, repos):
    campaign = repos.campaigns.ensure_default("Stand-ins")
    marla = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Marla"))
    brok = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Brok"))
    goblin = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="Yeemik"))

    elsa = repos.accounts.create(campaign.id, "elsa", "goblin-teeth",
                                 character_entity_id=marla.id)
    marco = repos.accounts.create(campaign.id, "marco", "goblin-teeth",
                                  character_entity_id=brok.id)
    repos.entities.set_owner(marla.id, elsa.id)
    repos.entities.set_owner(brok.id, marco.id)

    enc = repos.encounters.create(campaign.id, "The cave", width=12, height=12)
    tokens = {
        "marla": repos.encounters.add(enc.id, marla.id, initiative=20, x=0, y=0),
        "brok": repos.encounters.add(enc.id, brok.id, initiative=15, x=1, y=0),
        "goblin": repos.encounters.add(enc.id, goblin.id, initiative=10, x=2, y=0),
    }
    repos.encounters.begin(enc.id)

    server = SessionServer(repos, campaign.id, "Stand-ins")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    yield server, repos, enc, tokens, marla, brok, goblin
    server.stop()


def test_nothing_runs_until_a_character_is_handed_over(table, started):
    server, *_rest = table
    minder = stand_ins.StandIns()

    minder.look(server)

    assert started == []
    assert minder.playing == set()


def test_handing_one_over_starts_one(table, started):
    server, repos, _enc, tokens, marla, _brok, _goblin = table
    repos.encounters.set_simulated(tokens["marla"].id, True)
    minder = stand_ins.StandIns()

    minder.look(server)

    assert minder.playing == {marla.id}
    assert len(started) == 1
    url, seat = started[0]
    assert url == f"ws://127.0.0.1:{server.port}", "it connects over loopback"
    assert seat, "it was given a seat token"


def test_two_handed_over_are_two_processes(table, started):
    """One each. Two views, and neither knows what the other was told."""
    server, repos, _enc, tokens, marla, brok, _goblin = table
    repos.encounters.set_simulated(tokens["marla"].id, True)
    repos.encounters.set_simulated(tokens["brok"].id, True)
    minder = stand_ins.StandIns()

    minder.look(server)

    assert minder.playing == {marla.id, brok.id}
    assert len({seat for _url, seat in started}) == 2, "two different seats"


def test_looking_twice_does_not_start_a_second(table, started):
    """It is wired to every change to the fight, so it is called constantly."""
    server, repos, _enc, tokens, _marla, _brok, _goblin = table
    repos.encounters.set_simulated(tokens["marla"].id, True)
    minder = stand_ins.StandIns()

    minder.look(server)
    minder.look(server)
    minder.look(server)

    assert len(started) == 1


def test_handing_a_character_back_stops_it(table, started):
    server, repos, _enc, tokens, marla, _brok, _goblin = table
    repos.encounters.set_simulated(tokens["marla"].id, True)
    minder = stand_ins.StandIns()
    minder.look(server)

    repos.encounters.set_simulated(tokens["marla"].id, False)
    minder.look(server)

    assert minder.playing == set()


def test_handing_back_takes_the_seat_away_too(table, started):
    """A live token with nothing sitting in it is a way in nobody is watching."""
    server, repos, _enc, tokens, marla, _brok, _goblin = table
    repos.encounters.set_simulated(tokens["marla"].id, True)
    minder = stand_ins.StandIns()
    minder.look(server)
    assert any(e == marla.id for _a, e in server._seats.values())

    repos.encounters.set_simulated(tokens["marla"].id, False)
    minder.look(server)

    assert not any(e == marla.id for _a, e in server._seats.values())


def test_a_monster_gets_no_stand_in(table, started):
    """Nobody plays it, so there is no seat to sit in. Autopilot runs it."""
    server, repos, _enc, tokens, _marla, _brok, goblin = table
    repos.encounters.set_simulated(tokens["goblin"].id, True)
    minder = stand_ins.StandIns()

    minder.look(server)

    assert minder.playing == set()
    assert started == []


def test_one_that_died_is_started_again(table, started):
    """A crash must not leave the character sitting in an empty seat."""
    server, repos, _enc, tokens, marla, _brok, _goblin = table
    repos.encounters.set_simulated(tokens["marla"].id, True)
    minder = stand_ins.StandIns()
    minder.look(server)
    minder._running[marla.id].stopped = True

    minder.look(server)

    assert len(started) == 2
    assert minder.playing == {marla.id}


def test_hosting_stopping_stops_them_all(table, started):
    server, repos, _enc, tokens, _marla, _brok, _goblin = table
    repos.encounters.set_simulated(tokens["marla"].id, True)
    repos.encounters.set_simulated(tokens["brok"].id, True)
    minder = stand_ins.StandIns()
    minder.look(server)
    assert len(minder.playing) == 2

    server.stop()
    minder.look(server)

    assert minder.playing == set()


def test_a_stand_in_that_will_not_start_is_not_fatal(table, monkeypatch):
    """Without one the character is played by autopilot, as it was before."""
    server, repos, _enc, tokens, marla, _brok, _goblin = table

    def refuse(url, seat, pause=None):
        raise RuntimeError("canonkeeper-player is not installed")

    monkeypatch.setattr(stand_ins, "start", refuse)
    repos.encounters.set_simulated(tokens["marla"].id, True)
    minder = stand_ins.StandIns()

    minder.look(server)  # must not raise

    assert minder.playing == set()
    assert not any(e == marla.id for _a, e in server._seats.values()), (
        "a seat was minted for a stand-in that never started"
    )


# ------------------------------------------------------------------ names


def test_a_stand_in_has_a_name_of_its_own(table):
    """Three of these at once cannot all be called "autopilot"."""
    from canon_keeper_protocol import robots

    server, _repos, _enc, _tokens, marla, _brok, _goblin = table

    assert server.stand_in_name(marla.id) in robots.NAMES


def test_the_name_varies_with_the_character():
    """The regression this guards is every stand-in getting the same one.

    Asserted across a spread rather than on one pair, because the name is a
    hash into a list of twenty-four and any *given* pair can collide -- see
    :func:`test_two_characters_can_draw_the_same_name`.
    """
    from canon_keeper_protocol import robots

    drawn = {robots.name_for_character("a-campaign", i) for i in range(1, 25)}

    assert len(drawn) > 1


def test_two_characters_can_draw_the_same_name():
    """A known limit, written down so it is not mistaken for a broken test.

    The name is a pure function of the campaign and the character, which is
    what makes it the same in every process that works it out -- the host, the
    DM's panel, and the stand-in itself -- without any of them coordinating.
    The price is that two characters in one fight can land on the same word,
    about once in twenty-five. Cosmetic, and confusing at a table when it
    happens; fixing it means somebody deciding, which means coordination.
    """
    from canon_keeper_protocol import robots

    clashes = [
        key
        for key in (f"campaign-{n}" for n in range(200))
        if robots.name_for_character(key, 1) == robots.name_for_character(key, 2)
    ]

    assert clashes, "if this ever stops being true, the naming has been changed"


def test_the_name_is_the_same_every_time(table):
    """A name that changed when a process restarted would be a label."""
    server, _repos, _enc, _tokens, marla, _brok, _goblin = table

    assert server.stand_in_name(marla.id) == server.stand_in_name(marla.id)


def test_two_campaigns_do_not_share_a_name_by_accident():
    """Every campaign is its own file and starts counting at one, so most
    pairs of campaigns both have an entity 3."""
    from canon_keeper_protocol import robots

    seen = {
        robots.name_for_character(key, 3)
        for key in ("one", "two", "three", "four", "five")
    }
    assert len(seen) > 1


def test_the_name_reaches_the_table(table, started):
    server, repos, _enc, tokens, marla, _brok, _goblin = table
    repos.encounters.set_simulated(tokens["marla"].id, True)

    combatants = server._with_stand_ins(
        repos.encounters.combatants(_enc_id(repos, server))
    )
    mine = next(c for c in combatants if c.entity_id == marla.id)

    assert mine.stand_in_name == server.stand_in_name(marla.id)


def _enc_id(repos, server) -> int:
    return repos.encounters.running(server.campaign_id).id
