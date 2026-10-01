"""Joining a campaign for the first time.

A campaign starts with characters and nobody to play them. The DM makes an
invite for a character, sends the code, and the person holding it makes their
own account -- so the DM never types somebody else's password and never learns
it.

Most of this file is about what must *not* happen, because an invite is a way
into somebody's campaign and the interesting cases are all adversarial: the
wrong code, a recorded exchange replayed, two people racing with the same code,
a code that was replaced before it was used.
"""

from __future__ import annotations

import time

import pytest

from canon_keeper.net.server import SessionServer
from canon_keeper_core.repo.entities import KIND_PC, Entity
from canon_keeper_protocol import auth, enrol


@pytest.fixture
def table(qtbot, repos):
    """A campaign with two characters and nobody playing either."""
    campaign = repos.campaigns.ensure_default("Invites")
    marla = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Marla")
    )
    brok = repos.entities.create(
        Entity(id=None, campaign_id=campaign.id, kind=KIND_PC, name="Brok")
    )
    repos.accounts.create(
        campaign.id, "gm", "run-the-game", role="dm", display_name="The DM"
    )

    server = SessionServer(repos, campaign.id, "Invite session")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    yield server, repos, campaign, marla, brok
    server.stop()


# ------------------------------------------------------------------ the crypto


def test_a_sealed_verifier_opens_with_the_right_code():
    code = enrol.new_code()
    nonce = auth.new_nonce()
    salt, verifier = auth.make_credentials("a good password")

    sent = enrol.seal(code, nonce, "marco", salt, verifier)
    opened = enrol.unseal(
        code, nonce, "marco", salt, bytes.fromhex(sent["sealed"]), sent["tag"]
    )

    assert opened == verifier


def test_the_wrong_code_opens_nothing():
    nonce = auth.new_nonce()
    salt, verifier = auth.make_credentials("a good password")
    sent = enrol.seal(enrol.new_code(), nonce, "marco", salt, verifier)

    with pytest.raises(enrol.EnrolError):
        enrol.unseal(
            enrol.new_code(), nonce, "marco", salt,
            bytes.fromhex(sent["sealed"]), sent["tag"],
        )


def test_the_password_is_not_in_what_is_sent():
    """The point of the whole exercise."""
    password = "correct-horse-battery-staple"
    code = enrol.new_code()
    nonce = auth.new_nonce()
    salt, verifier = auth.make_credentials(password)

    sent = enrol.seal(code, nonce, "marco", salt, verifier)

    blob = "".join(str(v) for v in sent.values())
    assert password not in blob
    assert password.encode("utf-8").hex() not in blob


def test_the_code_is_not_in_what_is_sent():
    """Nor the invite itself -- a sniffer must not be able to reuse it."""
    code = enrol.new_code()
    nonce = auth.new_nonce()
    salt, verifier = auth.make_credentials("a good password")

    sent = enrol.seal(code, nonce, "marco", salt, verifier)

    blob = "".join(str(v) for v in sent.values())
    assert code not in blob
    assert code.replace("-", "") not in blob


def test_the_verifier_is_not_in_the_clear_either():
    """It is login-equivalent, so on a LAN it is as good as the password."""
    code = enrol.new_code()
    nonce = auth.new_nonce()
    salt, verifier = auth.make_credentials("a good password")

    sent = enrol.seal(code, nonce, "marco", salt, verifier)

    assert verifier.hex() != sent["sealed"]
    assert verifier.hex() not in "".join(str(v) for v in sent.values())


def test_the_username_cannot_be_swapped():
    """Otherwise a recorded enrolment could be replayed under another name.

    Which would hand the recorder an account whose password the real player
    chose -- and the real player would never know they were sharing it.
    """
    code = enrol.new_code()
    nonce = auth.new_nonce()
    salt, verifier = auth.make_credentials("a good password")
    sent = enrol.seal(code, nonce, "marco", salt, verifier)

    with pytest.raises(enrol.EnrolError):
        enrol.unseal(
            code, nonce, "somebody-else", salt,
            bytes.fromhex(sent["sealed"]), sent["tag"],
        )


def test_a_different_nonce_will_not_open_it():
    """Which is what stops a recorded exchange being replayed at all."""
    code = enrol.new_code()
    salt, verifier = auth.make_credentials("a good password")
    sent = enrol.seal(code, auth.new_nonce(), "marco", salt, verifier)

    with pytest.raises(enrol.EnrolError):
        enrol.unseal(
            code, auth.new_nonce(), "marco", salt,
            bytes.fromhex(sent["sealed"]), sent["tag"],
        )


def test_the_sealed_bytes_cannot_be_edited():
    code = enrol.new_code()
    nonce = auth.new_nonce()
    salt, verifier = auth.make_credentials("a good password")
    sent = enrol.seal(code, nonce, "marco", salt, verifier)

    tampered = bytearray(bytes.fromhex(sent["sealed"]))
    tampered[0] ^= 0xFF

    with pytest.raises(enrol.EnrolError):
        enrol.unseal(code, nonce, "marco", salt, bytes(tampered), sent["tag"])


def test_a_code_is_read_the_way_people_retype_it():
    code = enrol.new_code()
    assert enrol.clean_code(code.lower()) == code
    assert enrol.clean_code(code.replace("-", " ")) == code
    assert enrol.clean_code(f"  {code}  ") == code


def test_every_failure_says_the_same_thing():
    """A guess must not learn whether a real invite exists for this campaign."""
    assert "not valid" in enrol.explain()
    assert "expired" not in enrol.explain()


# ------------------------------------------------------------- the invite itself


def test_making_a_second_invite_kills_the_first(table):
    """The rule that was asked for: an unused code stops working when replaced."""
    _server, repos, campaign, marla, _brok = table
    first = repos.invites.create(campaign.id, marla.id)
    second = repos.invites.create(campaign.id, marla.id)

    live = repos.invites.live(campaign.id)

    assert [i.id for i in live] == [second.id]
    assert repos.invites.get(first.id).state == "replaced"


def test_an_invite_for_another_character_is_untouched(table):
    _server, repos, campaign, marla, brok = table
    hers = repos.invites.create(campaign.id, marla.id)
    repos.invites.create(campaign.id, brok.id)

    assert repos.invites.get(hers.id).live() is True


def test_an_invite_expires(table):
    _server, repos, campaign, marla, _brok = table
    invite = repos.invites.create(campaign.id, marla.id)

    assert invite.live(now=invite.expires_at - 1) is True
    assert invite.live(now=invite.expires_at + 1) is False


def test_an_expired_invite_is_not_offered_for_matching(table, monkeypatch):
    _server, repos, campaign, marla, _brok = table
    repos.invites.create(campaign.id, marla.id)
    later = time.time() + enrol.INVITE_LIFETIME_SECONDS + 1
    monkeypatch.setattr(time, "time", lambda: later)

    assert repos.invites.live(campaign.id) == []


def test_only_one_arrival_can_claim_one(table):
    """Two people racing with the same code. Exactly one gets an account."""
    _server, repos, campaign, marla, _brok = table
    invite = repos.invites.create(campaign.id, marla.id)

    assert repos.invites.claim(invite.id) is True
    assert repos.invites.claim(invite.id) is False


def test_a_character_with_a_player_can_still_be_invited(table):
    """This is the password reset, and the way a character changes hands.

    A DM who could not do this would have to know what their player typed,
    which is the arrangement the whole feature exists to avoid.
    """
    server, repos, campaign, marla, _brok = table
    repos.accounts.create(
        campaign.id, "marco", "goblin-teeth", character_entity_id=marla.id
    )

    assert server.invite_for(marla.id) != ""


def test_inviting_again_does_not_break_the_old_login_yet(qtbot, table):
    """Until the code is used, the person playing them is still playing them.

    Otherwise making a code by accident would throw somebody out mid-session.
    """
    from canon_keeper.net.client import SessionClient

    server, repos, campaign, marla, _brok = table
    repos.accounts.create(
        campaign.id, "marco", "goblin-teeth", character_entity_id=marla.id
    )
    server.invite_for(marla.id)

    client = SessionClient()
    try:
        client.join(f"ws://127.0.0.1:{server.port}", "marco", "goblin-teeth")
        qtbot.waitUntil(lambda: client.me is not None, timeout=10000)
        assert client.me.name == "marco"
    finally:
        client.leave()


def test_the_dm_gets_a_code_to_send(table):
    server, repos, campaign, marla, _brok = table
    code = server.invite_for(marla.id)

    assert enrol.clean_code(code) == code
    assert repos.invites.waiting_for(marla.id).code == code


# ---------------------------------------------------------- over the wire


def test_an_account_made_from_an_invite_can_log_in(qtbot, table):
    """The whole flow, end to end, through a real socket."""
    from canon_keeper.net.client import SessionClient

    server, repos, campaign, marla, _brok = table
    code = server.invite_for(marla.id)

    client = SessionClient()
    made: list[tuple] = []
    client.enrolled.connect(lambda *a: made.append(a))
    try:
        client.enrol(f"ws://127.0.0.1:{server.port}", code, "marco", "goblin-teeth")
        qtbot.waitUntil(lambda: bool(made), timeout=10000)

        assert made[0] == ("marco", "Marla")
        account = repos.accounts.by_username(campaign.id, "marco")
        assert account is not None
        assert account.character_entity_id == marla.id

        # And the credentials they chose actually work.
        qtbot.waitUntil(lambda: client.me is not None, timeout=10000)
        assert client.me.name == "marco"
    finally:
        client.leave()


def test_the_host_never_sees_the_password(qtbot, table):
    """It is not on the wire, so it cannot be stored -- only the verifier is."""
    server, repos, campaign, marla, _brok = table
    code = server.invite_for(marla.id)
    from canon_keeper.net.client import SessionClient

    client = SessionClient()
    made: list[tuple] = []
    client.enrolled.connect(lambda *a: made.append(a))
    try:
        client.enrol(f"ws://127.0.0.1:{server.port}", code, "marco", "goblin-teeth")
        qtbot.waitUntil(lambda: bool(made), timeout=10000)

        account = repos.accounts.by_username(campaign.id, "marco")
        assert account.verifier == auth.derive_verifier("goblin-teeth", account.salt)
        assert b"goblin-teeth" not in account.verifier
    finally:
        client.leave()


def test_a_wrong_code_makes_nothing(qtbot, table):
    from canon_keeper.net.client import SessionClient

    server, repos, campaign, marla, _brok = table
    server.invite_for(marla.id)

    client = SessionClient()
    refused: list[str] = []
    client.failed.connect(refused.append)
    try:
        client.enrol(
            f"ws://127.0.0.1:{server.port}", enrol.new_code(), "marco", "goblin-teeth"
        )
        qtbot.waitUntil(lambda: bool(refused), timeout=10000)

        assert repos.accounts.by_username(campaign.id, "marco") is None
        assert "not valid" in refused[0]
    finally:
        client.leave()


def test_a_replaced_code_makes_nothing(qtbot, table):
    """The rule asked for, proved through the socket rather than the table."""
    from canon_keeper.net.client import SessionClient

    server, repos, campaign, marla, _brok = table
    stale = server.invite_for(marla.id)
    server.invite_for(marla.id)  # and now the first one is dead

    client = SessionClient()
    refused: list[str] = []
    client.failed.connect(refused.append)
    try:
        client.enrol(f"ws://127.0.0.1:{server.port}", stale, "marco", "goblin-teeth")
        qtbot.waitUntil(lambda: bool(refused), timeout=10000)

        assert repos.accounts.by_username(campaign.id, "marco") is None
    finally:
        client.leave()


def test_an_invite_is_spent_once(qtbot, table):
    from canon_keeper.net.client import SessionClient

    server, repos, campaign, marla, _brok = table
    code = server.invite_for(marla.id)

    first = SessionClient()
    made: list[tuple] = []
    first.enrolled.connect(lambda *a: made.append(a))
    try:
        first.enrol(f"ws://127.0.0.1:{server.port}", code, "marco", "goblin-teeth")
        qtbot.waitUntil(lambda: bool(made), timeout=10000)
    finally:
        first.leave()

    second = SessionClient()
    refused: list[str] = []
    second.failed.connect(refused.append)
    try:
        second.enrol(f"ws://127.0.0.1:{server.port}", code, "impostor", "other-word")
        qtbot.waitUntil(lambda: bool(refused), timeout=10000)

        assert repos.accounts.by_username(campaign.id, "impostor") is None
    finally:
        second.leave()


def test_a_taken_username_is_said_plainly(qtbot, table):
    """Whoever holds a live code was invited. Being vague at them is unkind."""
    from canon_keeper.net.client import SessionClient

    server, repos, campaign, marla, brok = table
    repos.accounts.create(campaign.id, "marco", "goblin-teeth")
    code = server.invite_for(brok.id)

    client = SessionClient()
    refused: list[str] = []
    client.failed.connect(refused.append)
    try:
        client.enrol(f"ws://127.0.0.1:{server.port}", code, "marco", "another-word")
        qtbot.waitUntil(lambda: bool(refused), timeout=10000)

        assert "already taken" in refused[0]
    finally:
        client.leave()


# ----------------------------------------------------------- handing a seat on


def test_a_returning_player_gets_back_in_with_a_new_code(qtbot, table):
    """The password reset. They lost it; the DM sends another code."""
    from canon_keeper.net.client import SessionClient

    server, repos, campaign, marla, _brok = table
    first = server.invite_for(marla.id)

    client = SessionClient()
    made: list[tuple] = []
    client.enrolled.connect(lambda *a: made.append(a))
    try:
        client.enrol(f"ws://127.0.0.1:{server.port}", first, "marco", "first-word")
        qtbot.waitUntil(lambda: bool(made), timeout=10000)
    finally:
        client.leave()
    was = repos.accounts.by_username(campaign.id, "marco")

    again = server.invite_for(marla.id)
    back = SessionClient()
    returned: list[tuple] = []
    back.enrolled.connect(lambda *a: returned.append(a))
    try:
        back.enrol(f"ws://127.0.0.1:{server.port}", again, "marco", "second-word")
        qtbot.waitUntil(lambda: bool(returned), timeout=10000)
    finally:
        back.leave()

    now = repos.accounts.by_username(campaign.id, "marco")
    assert now.id == was.id, "the same seat, not a second one"
    assert now.verifier == auth.derive_verifier("second-word", now.salt)
    assert now.verifier != was.verifier, "the old password still works"


def test_a_seat_handed_over_is_not_duplicated(qtbot, table):
    """Somebody new takes the character on. One character, one player."""
    from canon_keeper.net.client import SessionClient

    server, repos, campaign, marla, _brok = table
    first = SessionClient()
    made: list[tuple] = []
    first.enrolled.connect(lambda *a: made.append(a))
    try:
        first.enrol(
            f"ws://127.0.0.1:{server.port}", server.invite_for(marla.id),
            "marco", "first-word",
        )
        qtbot.waitUntil(lambda: bool(made), timeout=10000)
    finally:
        first.leave()

    second = SessionClient()
    taken: list[tuple] = []
    second.enrolled.connect(lambda *a: taken.append(a))
    try:
        second.enrol(
            f"ws://127.0.0.1:{server.port}", server.invite_for(marla.id),
            "elsa", "another-word",
        )
        qtbot.waitUntil(lambda: bool(taken), timeout=10000)
    finally:
        second.leave()

    playing = [
        a for a in repos.accounts.list(campaign.id)
        if a.character_entity_id == marla.id
    ]
    assert [a.username for a in playing] == ["elsa"]
    assert repos.accounts.by_username(campaign.id, "marco") is None


def test_the_character_changes_owner_with_the_seat(qtbot, table):
    """Ownership follows the seat, or the new player cannot edit their sheet."""
    from canon_keeper.net.client import SessionClient

    server, repos, campaign, marla, _brok = table
    client = SessionClient()
    made: list[tuple] = []
    client.enrolled.connect(lambda *a: made.append(a))
    try:
        client.enrol(
            f"ws://127.0.0.1:{server.port}", server.invite_for(marla.id),
            "marco", "first-word",
        )
        qtbot.waitUntil(lambda: bool(made), timeout=10000)
    finally:
        client.leave()

    account = repos.accounts.by_username(campaign.id, "marco")
    assert repos.entities.get(marla.id).owner_account_id == account.id


def test_somebody_elses_username_is_still_refused(qtbot, table):
    """Coming back to your own seat lets you keep your name. Not theirs."""
    from canon_keeper.net.client import SessionClient

    server, repos, campaign, marla, brok = table
    repos.accounts.create(campaign.id, "elsa", "goblin-teeth", character_entity_id=brok.id)
    code = server.invite_for(marla.id)

    client = SessionClient()
    refused: list[str] = []
    client.failed.connect(refused.append)
    try:
        client.enrol(f"ws://127.0.0.1:{server.port}", code, "elsa", "another-word")
        qtbot.waitUntil(lambda: bool(refused), timeout=10000)

        assert "already taken" in refused[0]
        # And Brok's player is untouched.
        assert repos.accounts.by_username(campaign.id, "elsa").character_entity_id == brok.id
    finally:
        client.leave()


# ------------------------------------------------------------ one thing to send


def test_an_invite_carries_where_to_connect():
    """Two things to copy is two things to get wrong."""
    code = enrol.new_code()
    whole = enrol.wrap("ws://192.168.1.10:8765", code)

    assert enrol.unwrap(whole) == ("ws://192.168.1.10:8765", code)


def test_an_invite_survives_being_sent_through_a_chat_app():
    """It arrives with a scheme, a stray space, or a full stop on the end."""
    code = enrol.new_code()
    whole = enrol.wrap("wss://table.tailnet.ts.net", code)

    assert enrol.unwrap(f"  {whole}  ") == ("wss://table.tailnet.ts.net", code)
    assert enrol.unwrap(f"{whole}.") == ("wss://table.tailnet.ts.net", code)
    assert enrol.unwrap(f"canonkeeper://{whole}") == (
        "wss://table.tailnet.ts.net", code
    )


def test_half_an_invite_is_still_read():
    """A DM not hosting yet sends only a code; somebody with an account, only
    an address."""
    code = enrol.new_code()

    assert enrol.unwrap(code) == ("", code)
    assert enrol.unwrap(code.lower().replace("-", " ")) == ("", code)
    assert enrol.unwrap("ws://192.168.1.10:8765") == ("ws://192.168.1.10:8765", "")


def test_a_code_with_no_address_wraps_to_just_the_code():
    code = enrol.new_code()
    assert enrol.wrap("", code) == code


def test_pasting_a_whole_invite_fills_the_address_in(qtbot):
    """What a player actually does: one paste, and the dialog sorts it out."""
    from canon_keeper.panels.table.dialogs import JoinDialog

    dialog = JoinDialog()
    qtbot.addWidget(dialog)
    code = enrol.new_code()

    dialog._code.setText(enrol.wrap("ws://10.0.0.4:8765", code))

    assert dialog._url.text() == "ws://10.0.0.4:8765"
    assert dialog.invite_code() == code


# ----------------------------------------------------- arriving for the first time


def test_the_chooser_takes_an_invite(qtbot):
    """Where most people meet the app: they have a line, not a login."""
    from canon_keeper.shell.startup import ONLINE_TAB, CampaignDialog

    dialog = CampaignDialog()
    qtbot.addWidget(dialog)
    dialog._tabs.setCurrentIndex(ONLINE_TAB)
    code = enrol.new_code()

    dialog._code.setText(enrol.wrap("ws://10.0.0.4:8765", code))

    assert dialog._url.text() == "ws://10.0.0.4:8765", (
        "the address the invite carried was not filled in"
    )
    assert enrol.clean_code(dialog._code.text()) == code


def test_the_chooser_carries_the_invite_into_the_app(qtbot):
    from canon_keeper.shell.startup import ONLINE_TAB, CampaignDialog

    dialog = CampaignDialog()
    qtbot.addWidget(dialog)
    dialog._tabs.setCurrentIndex(ONLINE_TAB)
    code = enrol.new_code()
    dialog._code.setText(enrol.wrap("ws://10.0.0.4:8765", code))
    dialog._username.setText("marco")
    dialog._password.setText("goblin-teeth")

    dialog._accept()

    made = dialog.launch()
    assert made is not None
    assert made.invite == code
    assert made.url == "ws://10.0.0.4:8765"


def test_joining_with_a_login_carries_no_invite(qtbot):
    """Everybody after the first evening. Nothing changed for them."""
    from canon_keeper.shell.startup import ONLINE_TAB, CampaignDialog

    dialog = CampaignDialog()
    qtbot.addWidget(dialog)
    dialog._tabs.setCurrentIndex(ONLINE_TAB)
    dialog._url.setText("ws://10.0.0.4:8765")
    dialog._username.setText("marco")
    dialog._password.setText("goblin-teeth")

    dialog._accept()

    assert dialog.launch().invite == ""


# ------------------------------------------------- a bad login opens nothing


def test_a_good_join_answers_the_shell(qtbot, table):
    """The Table panel tells the shell how the launch join went, once."""
    from canon_keeper.bus import Bus
    from canon_keeper.plugin import PendingJoin

    server, repos, campaign, marla, _brok = table
    repos.accounts.create(
        campaign.id, "marco", "goblin-teeth", character_entity_id=marla.id
    )

    answers: list[tuple] = []
    bus = Bus()
    bus.session_ready.connect(lambda ok, why: answers.append((ok, why)))
    client = _launched(qtbot, bus, server.port, "marco", "goblin-teeth")
    try:
        qtbot.waitUntil(lambda: bool(answers), timeout=10000)
        assert answers[0][0] is True
    finally:
        client.leave()


def test_a_bad_password_answers_with_a_reason(qtbot, table):
    from canon_keeper.bus import Bus

    server, repos, campaign, marla, _brok = table
    repos.accounts.create(
        campaign.id, "marco", "goblin-teeth", character_entity_id=marla.id
    )

    answers: list[tuple] = []
    bus = Bus()
    bus.session_ready.connect(lambda ok, why: answers.append((ok, why)))
    client = _launched(qtbot, bus, server.port, "marco", "wrong-word")
    try:
        qtbot.waitUntil(lambda: bool(answers), timeout=10000)
        assert answers[0][0] is False
        assert answers[0][1], "a refusal with no reason is a blank dialog"
    finally:
        client.leave()


def test_the_answer_comes_once(qtbot, table):
    """The client retries by itself, and reconnecting is not a second launch.

    Without the guard, a session that dropped and came back an hour into the
    evening would answer a question the shell stopped listening to before the
    window was even shown.
    """
    from canon_keeper.bus import Bus

    server, repos, campaign, marla, _brok = table
    repos.accounts.create(
        campaign.id, "marco", "goblin-teeth", character_entity_id=marla.id
    )

    answers: list[tuple] = []
    bus = Bus()
    bus.session_ready.connect(lambda ok, why: answers.append((ok, why)))
    panel = _panel(qtbot, bus, server.port, "marco", "goblin-teeth")
    try:
        qtbot.waitUntil(lambda: bool(answers), timeout=10000)

        # What a reconnect looks like from the panel's side, and what a later
        # refusal looks like. Neither is the launch resolving again.
        panel._on_connected()
        panel._on_failed("dropped, then refused")

        assert len(answers) == 1
    finally:
        panel._client.leave()


def _panel(qtbot, bus, port: int, username: str, password: str):
    """A Table panel built the way the shell builds one for a launch join."""
    from canon_keeper.panels.table.widget import TableWidget
    from canon_keeper.plugin import AppContext, PendingJoin

    ctx = AppContext(repos=None, bus=bus, log=None, campaign_id=1, role="player")
    ctx.pending_join = PendingJoin(
        f"ws://127.0.0.1:{port}", username, password
    )
    panel = TableWidget(ctx)
    qtbot.addWidget(panel)
    return panel


def _launched(qtbot, bus, port: int, username: str, password: str):
    return _panel(qtbot, bus, port, username, password)._client
