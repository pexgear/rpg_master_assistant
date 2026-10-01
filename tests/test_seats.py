"""Seat tokens: how a machine sits in somebody's chair without being them.

A character handed to autopilot needs something to log in with, and after
invitations there is nothing to use -- nobody can make a login and the DM does
not know their player's password. That is the design working, not a gap in it.

So the host mints a token scoped to one seat. The claim worth testing is not
that it lets a machine in; it is **what it does not buy**. A seat token is a
player's chair, not a key to the campaign: the same projection, the same
authority, and gone the moment the character is taken back.
"""

from __future__ import annotations

import pytest

from canon_keeper.net.client import SessionClient
from canon_keeper.net.server import SessionServer
from canon_keeper_core.repo.entities import KIND_NPC, KIND_PC, Entity
from canon_keeper_protocol.messages import MessageType, encode


@pytest.fixture
def table(qtbot, repos):
    """Marla, played by Elsa; a goblin nobody has been shown; and a secret."""
    campaign = repos.campaigns.ensure_default("Seats")
    marla = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Marla",
               data={"hp": 20, "max_hp": 20, "sheet": {"schema": 1, "level": 3}}))
    brok = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Brok",
               data={"hp": 24, "max_hp": 24, "sheet": {"schema": 1, "level": 3}}))
    ambush = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_NPC, name="The ambusher",
               summary="Waiting in the rafters.",
               data={"secrets": "Strikes when the fire is lit."}))

    elsa = repos.accounts.create(campaign.id, "elsa", "goblin-teeth",
                                 character_entity_id=marla.id)
    marco = repos.accounts.create(campaign.id, "marco", "goblin-teeth",
                                  character_entity_id=brok.id)
    repos.entities.set_owner(marla.id, elsa.id)
    repos.entities.set_owner(brok.id, marco.id)
    repos.shares.share(campaign.id, marla.id)

    enc = repos.encounters.create(campaign.id, "The cave", width=12, height=12)
    tokens = {
        "marla": repos.encounters.add(enc.id, marla.id, initiative=20, x=0, y=0),
        "brok": repos.encounters.add(enc.id, brok.id, initiative=10, x=1, y=0),
    }
    repos.encounters.begin(enc.id)

    server = SessionServer(repos, campaign.id, "Seats")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    yield server, repos, campaign, enc, tokens, marla, brok, ambush
    server.stop()


def _sit(qtbot, server, seat: str) -> SessionClient:
    """Connect the way a stand-in does: a seat token instead of a password."""
    client = SessionClient()
    client._url = f"ws://127.0.0.1:{server.port}"
    original = client._on_connected

    def hello() -> None:
        client._socket.sendTextMessage(encode(MessageType.HELLO, seat=seat))

    client._on_connected = hello  # type: ignore[method-assign]
    client._socket.connected.disconnect()
    client._socket.connected.connect(hello)
    client._wanted = True
    client._open()
    return client


# ------------------------------------------------------------------ the token


def test_a_seat_is_minted_for_a_character_with_a_player(table):
    server, _repos, _c, _enc, _tokens, marla, _brok, _ambush = table
    assert server.mint_seat(marla.id) != ""


def test_a_character_nobody_plays_gets_no_seat(table):
    """There is no chair to sit in, so there is nothing to mint."""
    server, _repos, _c, _enc, _tokens, _marla, _brok, ambush = table
    assert server.mint_seat(ambush.id) == ""


def test_minting_again_retires_the_first_token(table):
    """Handing a character over twice must not leave two stand-ins able to act."""
    server, _repos, _c, _enc, _tokens, marla, _brok, _ambush = table
    first = server.mint_seat(marla.id)
    second = server.mint_seat(marla.id)

    assert first != second
    assert server._seat_holder(first) == (None, None)
    assert server._seat_holder(second)[1] == marla.id


def test_taking_the_character_back_kills_the_token(table):
    server, _repos, _c, _enc, _tokens, marla, _brok, _ambush = table
    token = server.mint_seat(marla.id)

    server.revoke_seat(marla.id)

    assert server._seat_holder(token) == (None, None)


def test_a_token_is_never_written_down(table):
    """It lives for the handover and not a moment longer.

    A token in the campaign file would be a stored credential for a seat, and
    a stolen file would then carry a way into somebody's character.
    """
    server, repos, campaign, _enc, _tokens, marla, _brok, _ambush = table
    token = server.mint_seat(marla.id)

    rows = repos.conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    for (name,) in rows:
        found = repos.conn.execute(f"SELECT * FROM {name}").fetchall()
        assert token not in str(found), f"the token reached {name}"


# ------------------------------------------------------------- what it buys


def test_a_seat_gets_in(qtbot, table):
    server, _repos, _c, _enc, _tokens, marla, _brok, _ambush = table
    token = server.mint_seat(marla.id)

    client = _sit(qtbot, server, token)
    try:
        qtbot.waitUntil(lambda: client.me is not None, timeout=10000)
        assert client.me.name == "elsa"
        assert client.me.character == "Marla"
    finally:
        client.leave()


def test_a_seat_is_not_a_dungeon_master(qtbot, table):
    """The whole reason for doing it this way.

    Autopilot playing a character used to be the DM's agent, which sees every
    secret -- so a handed-over character knew where the ambush was and walked
    around it. That looks like good play and it is cheating.
    """
    server, _repos, _c, _enc, _tokens, marla, _brok, ambush = table
    token = server.mint_seat(marla.id)

    client = _sit(qtbot, server, token)
    try:
        qtbot.waitUntil(lambda: client.me is not None, timeout=10000)
        qtbot.wait(300)

        seen = {e.get("name") for e in client.state.all()}
        assert "The ambusher" not in seen, "a stand-in was shown an unshared NPC"
        blob = str(client.state.all())
        assert "Strikes when the fire is lit" not in blob
    finally:
        client.leave()


def test_a_seat_does_not_see_the_other_players_character(qtbot, table):
    """One seat, one view. Two stand-ins must not pool what they know."""
    server, _repos, _c, _enc, _tokens, marla, brok, _ambush = table
    token = server.mint_seat(marla.id)

    client = _sit(qtbot, server, token)
    try:
        qtbot.waitUntil(lambda: client.me is not None, timeout=10000)
        qtbot.wait(300)

        sheets = {
            e.get("name"): e for e in client.state.all() if isinstance(e, dict)
        }
        assert "Brok" not in sheets or not (sheets["Brok"].get("data") or {}).get(
            "sheet"
        ), "a stand-in was sent another player's sheet"
    finally:
        client.leave()


def test_a_dead_token_is_refused(qtbot, table):
    server, _repos, _c, _enc, _tokens, marla, _brok, _ambush = table
    token = server.mint_seat(marla.id)
    server.revoke_seat(marla.id)

    client = _sit(qtbot, server, token)
    try:
        qtbot.wait(600)
        assert client.me is None
    finally:
        client.leave()


def test_a_made_up_token_is_refused(qtbot, table):
    server, *_rest = table

    client = _sit(qtbot, server, "not-a-real-seat-token")
    try:
        qtbot.wait(600)
        assert client.me is None
    finally:
        client.leave()


def test_taking_the_character_back_disconnects_the_stand_in(qtbot, table):
    """Dropping the token stops the next connection. This stops the one open."""
    server, _repos, _c, _enc, _tokens, marla, _brok, _ambush = table
    token = server.mint_seat(marla.id)

    client = _sit(qtbot, server, token)
    try:
        qtbot.waitUntil(lambda: client.me is not None, timeout=10000)
        client._wanted = False  # do not let it reconnect while we watch

        server.revoke_seat(marla.id)

        qtbot.waitUntil(lambda: not client.is_connected, timeout=5000)
    finally:
        client.leave()


# --------------------------------------------------------- handing over, live


def test_handing_a_character_over_mints_a_seat(qtbot, table):
    server, repos, _c, _enc, tokens, marla, _brok, _ambush = table
    player = SessionClient()
    try:
        player.join(f"ws://127.0.0.1:{server.port}", "elsa", "goblin-teeth")
        qtbot.waitUntil(lambda: player.me is not None, timeout=10000)

        player.send_simulate(tokens["marla"].id, True)
        qtbot.waitUntil(
            lambda: repos.encounters.combatant(tokens["marla"].id).simulated,
            timeout=5000,
        )
        assert any(e == marla.id for _a, e in server._seats.values())
    finally:
        player.leave()


# --------------------------------------------------- what a seat may do, and not


def _seated(qtbot, server, repos, entity_id, combatant_id):
    """A stand-in, connected, with the character actually handed over."""
    repos.encounters.set_simulated(combatant_id, True)
    client = _sit(qtbot, server, server.mint_seat(entity_id))
    qtbot.waitUntil(lambda: client.me is not None, timeout=10000)
    return client


def test_a_seat_may_move_its_own_character(qtbot, table):
    server, repos, _c, enc, tokens, marla, _brok, _ambush = table
    assert repos.encounters.get(enc.id).turn_combatant_id == tokens["marla"].id
    client = _seated(qtbot, server, repos, marla.id, tokens["marla"].id)
    try:
        client.send_move(tokens["marla"].id, 0, 3)
        qtbot.waitUntil(
            lambda: repos.encounters.combatant(tokens["marla"].id).y == 3,
            timeout=5000,
        )
    finally:
        client.leave()


def test_a_seat_may_not_move_anybody_else(qtbot, table):
    """One chair. Reaching past it is the thing this must never allow."""
    server, repos, _c, _enc, tokens, marla, _brok, _ambush = table
    client = _seated(qtbot, server, repos, marla.id, tokens["marla"].id)
    was = repos.encounters.combatant(tokens["brok"].id)
    try:
        client.send_move(tokens["brok"].id, 5, 5)
        qtbot.wait(500)

        now = repos.encounters.combatant(tokens["brok"].id)
        assert (now.x, now.y) == (was.x, was.y)
    finally:
        client.leave()


def test_a_seat_may_not_act_when_it_is_not_their_turn(qtbot, table):
    server, repos, _c, enc, tokens, marla, _brok, _ambush = table
    client = _seated(qtbot, server, repos, marla.id, tokens["marla"].id)
    try:
        server.run_turn("next")  # now it is Brok's
        assert repos.encounters.get(enc.id).turn_combatant_id == tokens["brok"].id

        client.send_move(tokens["marla"].id, 0, 4)
        qtbot.wait(500)

        assert repos.encounters.combatant(tokens["marla"].id).y != 4
    finally:
        client.leave()


def test_a_seat_may_not_act_once_the_character_is_taken_back(qtbot, table):
    """The flag is what makes it a handover; without it there is no standing in."""
    server, repos, _c, _enc, tokens, marla, _brok, _ambush = table
    client = _seated(qtbot, server, repos, marla.id, tokens["marla"].id)
    try:
        # The flag goes off without the token being revoked -- the narrower of
        # the two ways this can end, and the one the authority check must catch.
        repos.encounters.set_simulated(tokens["marla"].id, False)

        client.send_move(tokens["marla"].id, 0, 4)
        qtbot.wait(500)

        assert repos.encounters.combatant(tokens["marla"].id).y != 4
    finally:
        client.leave()


def test_a_seat_cannot_run_the_fight(qtbot, table):
    """It plays a character. It does not get to run the table."""
    server, repos, _c, enc, tokens, marla, _brok, _ambush = table
    client = _seated(qtbot, server, repos, marla.id, tokens["marla"].id)
    up = repos.encounters.get(enc.id).turn_combatant_id
    try:
        client.send_turn("next")
        client.send_initiative(tokens["brok"].id, 99)
        qtbot.wait(500)

        assert repos.encounters.get(enc.id).turn_combatant_id == up
        assert repos.encounters.combatant(tokens["brok"].id).initiative != 99
    finally:
        client.leave()


def test_a_seat_may_end_its_own_turn(qtbot, table):
    server, repos, _c, enc, tokens, marla, _brok, _ambush = table
    client = _seated(qtbot, server, repos, marla.id, tokens["marla"].id)
    try:
        client.send_done()
        qtbot.waitUntil(
            lambda: repos.encounters.get(enc.id).turn_combatant_id
            == tokens["brok"].id,
            timeout=5000,
        )
    finally:
        client.leave()
