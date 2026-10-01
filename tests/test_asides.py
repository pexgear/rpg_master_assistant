"""What the DM says while a machine is answering, and who hears it.

While autopilot is on there is one voice at the table and it is the agent's. A
DM typing then is **directing** -- "there is something behind the door" -- and
if their words also went out, the party would hear two DMs, one of whom keeps
being contradicted by the other.

So the line goes to the back room: the DM, any co-DM, and the agent, which
works it into what it says next. Speaking to the party directly is what the
switch is for.

Following that rule turned up an older leak worth its own section below. The
chat log is handed to whoever logs in next, and everything the host had ever
said privately to the DM was sitting in it -- refusals, pending requests, and
the text of expired API keys.
"""

from __future__ import annotations

import pytest

from canon_keeper.net.client import SessionClient
from canon_keeper.net.server import SessionServer
from canon_keeper_core.repo.chat import DM_ONLY, EVERYONE, SYSTEM


@pytest.fixture
def live(qtbot, repos):
    campaign = repos.campaigns.ensure_default("Asides")
    repos.accounts.create(campaign.id, "marco", "goblin-teeth", display_name="Marco")
    repos.accounts.create(
        campaign.id, "autopilot", "let-me-run-it", role="agent", display_name="Autopilot"
    )
    repos.accounts.create(
        campaign.id, "gm", "run-the-game", role="dm", display_name="The DM"
    )
    server = SessionServer(repos, campaign.id, "Aside session")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    yield server, campaign.id
    server.stop()


def _join(qtbot, server, username, password) -> SessionClient:
    client = SessionClient()
    with qtbot.waitSignal(client.connected, timeout=10000):
        client.join(f"ws://127.0.0.1:{server.port}", username, password)
    return client


# ------------------------------------------------------------------ the rule


def test_with_autopilot_off_the_dm_speaks_to_the_table(qtbot, live):
    server, _campaign_id = live
    dm = _join(qtbot, server, "gm", "run-the-game")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(player.said, timeout=5000) as blocker:
            dm.send_chat("The innkeeper looks up.")
        _member, text, aside = blocker.args
        assert text == "The innkeeper looks up."
        assert aside is False
    finally:
        dm.leave()
        player.leave()


def test_with_autopilot_on_the_party_does_not_hear_the_dm(qtbot, live):
    server, _campaign_id = live
    dm = _join(qtbot, server, "gm", "run-the-game")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    heard: list[str] = []
    player.said.connect(lambda _member, text, _aside: heard.append(text))
    try:
        server.set_autopilot(True, by="The DM")
        dm.send_chat("There is something behind the door.")
        qtbot.wait(300)
        assert heard == [], "the party heard direction meant for the machine"
    finally:
        dm.leave()
        player.leave()


def test_but_the_agent_does(qtbot, live):
    """It is the whole point: the DM steers, the agent speaks."""
    server, _campaign_id = live
    dm = _join(qtbot, server, "gm", "run-the-game")
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        server.set_autopilot(True, by="The DM")
        with qtbot.waitSignal(agent.said, timeout=5000) as blocker:
            dm.send_chat("There is something behind the door.")
        _member, text, aside = blocker.args
        assert text == "There is something behind the door."
        assert aside is True
    finally:
        dm.leave()
        agent.leave()


def test_the_dm_sees_that_it_did_not_go_out(qtbot, live):
    """A line that looks public and was not is worse than one held back."""
    server, _campaign_id = live
    dm = _join(qtbot, server, "gm", "run-the-game")
    try:
        server.set_autopilot(True, by="The DM")
        with qtbot.waitSignal(dm.said, timeout=5000) as blocker:
            dm.send_chat("Make them nervous.")
        assert blocker.args[2] is True
    finally:
        dm.leave()


def test_the_agent_still_speaks_to_the_whole_table(qtbot, live):
    """Only the DM's lines are held back. The agent is the voice."""
    server, _campaign_id = live
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        server.set_autopilot(True, by="The DM")
        with qtbot.waitSignal(player.said, timeout=5000) as blocker:
            agent.send_chat("Something shifts behind the door.")
        assert blocker.args[1] == "Something shifts behind the door."
        assert blocker.args[2] is False
    finally:
        agent.leave()
        player.leave()


def test_a_players_line_is_never_an_aside(qtbot, live):
    server, _campaign_id = live
    player = _join(qtbot, server, "marco", "goblin-teeth")
    watcher = _join(qtbot, server, "gm", "run-the-game")
    try:
        server.set_autopilot(True, by="The DM")
        with qtbot.waitSignal(watcher.said, timeout=5000) as blocker:
            player.send_chat("I open the door.")
        assert blocker.args[2] is False
    finally:
        player.leave()
        watcher.leave()


def test_taking_the_table_back_makes_the_dm_public_again(qtbot, live):
    server, _campaign_id = live
    dm = _join(qtbot, server, "gm", "run-the-game")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        server.set_autopilot(True, by="The DM")
        server.set_autopilot(False, by="The DM")
        with qtbot.waitSignal(player.said, timeout=5000) as blocker:
            dm.send_chat("Right, where were we?")
        assert blocker.args[1] == "Right, where were we?"
    finally:
        dm.leave()
        player.leave()


# ---------------------------------------------------------------- the log
#
# The log is handed out on every login, so anything private in it is private
# only until the next person connects.


def test_an_aside_is_kept_but_marked(live, repos):
    server, campaign_id = live
    server.set_autopilot(True, by="The DM")
    server._record(
        "said", "There is something behind the door.", speaker="The DM",
        role="dm", audience=DM_ONLY,
    )

    kept = repos.chat.recent(campaign_id, audiences=(EVERYONE, DM_ONLY))
    assert any(m.text.startswith("There is something") for m in kept)
    assert all(m.is_private for m in kept if m.text.startswith("There is"))


def test_a_player_is_not_handed_the_dms_asides(live):
    server, _campaign_id = live
    server._record("said", "The ambush is at the bridge.", audience=DM_ONLY)
    server._record("said", "You are all in the inn.", audience=EVERYONE)

    public = [m["text"] for m in server.history(for_dm=False)]
    assert "You are all in the inn." in public
    assert "The ambush is at the bridge." not in public


def test_but_the_dm_is(live):
    server, _campaign_id = live
    server._record("said", "The ambush is at the bridge.", audience=DM_ONLY)
    assert "The ambush is at the bridge." in [
        m["text"] for m in server.history(for_dm=True)
    ]


def test_what_the_host_tells_the_dm_is_not_read_out_to_the_next_player(live):
    """The leak this was found by. "Autopilot could not answer: Error code 401
    ... your key" went into the log as an ordinary line, and the log is the
    first thing a joining player is handed."""
    server, _campaign_id = live
    server._tell_dms("Autopilot could not answer: your API key has expired.")

    assert not [
        m for m in server.history(for_dm=False) if "API key" in m.get("text", "")
    ]
    assert [m for m in server.history(for_dm=True) if "API key" in m.get("text", "")]


def test_the_default_audience_is_public(live, repos):
    """Everything already in the log stays what it was: something anyone heard."""
    server, campaign_id = live
    server._record(SYSTEM, "Marco joined")
    assert "Marco joined" in [m["text"] for m in server.history(for_dm=False)]


def test_history_defaults_to_the_public_view(live):
    """Forgetting the argument shows a DM too little, never a player too much."""
    server, _campaign_id = live
    server._record("said", "secret", audience=DM_ONLY)
    assert "secret" not in [m["text"] for m in server.history()]


def test_a_joining_player_gets_the_filtered_log(qtbot, live):
    """End to end, because the filter is only worth anything at the door."""
    server, _campaign_id = live
    server._tell_dms("Marco asks to change level to 6")
    server._record("said", "You are all in the inn.", audience=EVERYONE)

    # Connected before joining: HISTORY arrives moments after WELCOME, so a
    # listener attached afterwards has already missed it.
    player = SessionClient()
    got: list[list] = []
    player.history_received.connect(got.append)
    try:
        player.join(f"ws://127.0.0.1:{server.port}", "marco", "goblin-teeth")
        qtbot.waitUntil(lambda: bool(got), timeout=5000)
        texts = [m.get("text", "") for m in got[0]]
        assert "You are all in the inn." in texts
        assert not any("level to 6" in text for text in texts)
    finally:
        player.leave()
