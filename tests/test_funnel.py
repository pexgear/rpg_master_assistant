"""Publishing a session through Tailscale Funnel.

The CLI is stubbed throughout: these tests are about how we drive it and what we
do with what it says, not about Tailscale working. The outputs below are the
shapes the real command produces.
"""

from __future__ import annotations

import json

import pytest

from canon_keeper.net import funnel

SUCCESS_OUTPUT = """Available on the internet:

https://desk.tail1a2b3.ts.net/
|-- proxy http://127.0.0.1:8765

Funnel started and running in the background.
Press Ctrl+C to exit.
"""

# Verbatim from tailscale 1.102.3. It prints this in under a second and then
# blocks, polling, until the tailnet setting changes -- which is the hang this
# module exists to avoid.
NOT_ENABLED_OUTPUT = """
Funnel is not enabled on your tailnet.
To enable, visit:

         https://login.tailscale.com/f/funnel?node=nmwNkFutkA11CNTRL
"""

STATUS_WITH_FUNNEL = {
    "Self": {
        "DNSName": "desk.tail1a2b3.ts.net.",
        "ID": "nmwNkFutkA11CNTRL",
        "CapMap": {"https://tailscale.com/cap/funnel": None, "ssh": None},
    }
}

STATUS_WITHOUT_FUNNEL = {
    "Self": {
        "DNSName": "alienshere.tail6fa344.ts.net.",
        "ID": "nmwNkFutkA11CNTRL",
        "CapMap": {"ssh": None, "default-auto-update": None},
    }
}


@pytest.fixture
def cli(monkeypatch):
    """Stand in for the tailscale command; records calls, returns queued replies.

    `replies` feeds short commands, `long_replies` feeds the one that may block.
    """
    calls: list[list[str]] = []
    replies: list[tuple[int, str]] = []
    long_replies: list[tuple[int | None, str]] = []

    def fake_run(args, timeout=funnel.QUERY_TIMEOUT):
        calls.append(list(args))
        return replies.pop(0) if replies else (0, "")

    def fake_capture(args, timeout):
        calls.append(list(args))
        return long_replies.pop(0) if long_replies else (0, "")

    monkeypatch.setattr(funnel, "_run", fake_run)
    monkeypatch.setattr(funnel, "_run_capturing", fake_capture)
    monkeypatch.setattr(funnel, "is_installed", lambda: True)
    monkeypatch.setattr(funnel, "executable", lambda: "/usr/bin/tailscale")
    return type(
        "Cli",
        (),
        {"calls": calls, "replies": replies, "long_replies": long_replies},
    )()


def _status(payload) -> tuple[int, str]:
    return 0, json.dumps(payload)


# ------------------------------------------------------------------------ urls


def test_https_becomes_wss():
    """Funnel serves HTTPS; a player needs a WebSocket address."""
    assert (
        funnel.to_websocket_url("https://desk.tail1a2b3.ts.net/")
        == "wss://desk.tail1a2b3.ts.net"
    )


def test_an_empty_url_stays_empty():
    assert funnel.to_websocket_url("") == ""


# ----------------------------------------------------------------------- start


def test_starting_publishes_the_port_and_reports_the_address(cli):
    cli.replies.append(_status(STATUS_WITH_FUNNEL))
    cli.long_replies.append((0, SUCCESS_OUTPUT))

    result = funnel.start(8765)

    assert result.ok
    assert result.url == "https://desk.tail1a2b3.ts.net"
    assert result.websocket_url == "wss://desk.tail1a2b3.ts.net"
    assert ["funnel", "--bg", "8765"] in cli.calls


def test_a_tailnet_without_funnel_is_caught_before_the_command_blocks(cli):
    """The bug this module was rewritten for.

    `tailscale funnel` prints its complaint and then waits indefinitely for the
    setting to change, so we must never reach it in this state.
    """
    cli.replies.append(_status(STATUS_WITHOUT_FUNNEL))

    result = funnel.start(8765)

    assert result.ok is False
    assert ["funnel", "--bg", "8765"] not in cli.calls, "ran the command that hangs"
    assert "not switched on" in result.message
    assert result.enable_url == (
        "https://login.tailscale.com/f/funnel?node=nmwNkFutkA11CNTRL"
    )


def test_the_enable_link_is_offered_even_from_an_older_cli(cli):
    """Older versions report no capabilities, so we only learn from the output."""
    cli.replies.append((0, json.dumps({"Self": {"ID": "nmwNkFutkA11CNTRL"}})))
    cli.long_replies.append((None, NOT_ENABLED_OUTPUT))
    cli.replies.append((0, json.dumps({"Self": {"ID": "nmwNkFutkA11CNTRL"}})))

    result = funnel.start(8765)

    assert result.ok is False
    assert "nmwNkFutkA11CNTRL" in result.enable_url


def test_unknown_capabilities_do_not_block_a_working_setup(cli):
    """Absent capability data must mean "ask Tailscale", not "refuse"."""
    cli.replies.append((0, json.dumps({"Self": {"DNSName": "desk.tail1a2b3.ts.net."}})))
    cli.long_replies.append((0, SUCCESS_OUTPUT))

    result = funnel.start(8765)

    assert result.ok is True


def test_the_tailnet_refusing_is_reported_in_tailscales_own_words(cli):
    cli.replies.append(_status(STATUS_WITH_FUNNEL))
    cli.long_replies.append((1, "something went wrong in tailscaled"))

    result = funnel.start(8765)

    assert result.ok is False
    assert "tailscaled" in result.message


def test_without_tailscale_the_user_is_told_what_to_install(monkeypatch):
    monkeypatch.setattr(funnel, "executable", lambda: None)

    result = funnel.start(8765)

    assert result.ok is False
    assert "tailscale.com/download" in result.message
    assert "only you" in result.message, "players should be told they need nothing"


def test_the_address_is_recovered_when_the_output_does_not_carry_one(cli):
    """Output wording differs between releases, so fall back to status."""
    cli.replies.append(_status(STATUS_WITH_FUNNEL))
    cli.long_replies.append((0, "Funnel started."))
    cli.replies.append(_status(STATUS_WITH_FUNNEL))

    result = funnel.start(8765)

    assert result.ok
    assert result.websocket_url == "wss://desk.tail1a2b3.ts.net"


def test_a_command_that_never_returns_is_killed_and_reported(cli):
    cli.replies.append(_status(STATUS_WITH_FUNNEL))
    cli.long_replies.append((None, "waiting for something"))
    cli.replies.append((1, "no"))

    result = funnel.start(8765)

    assert result.ok is False
    assert "did not answer" in result.message
    assert "waiting for something" in result.message, "keep what it managed to say"


def test_a_hung_command_is_killed_and_its_output_kept(monkeypatch):
    """Partial output is the whole point: the useful line comes out first."""
    import subprocess

    class _Hanging:
        returncode = None
        killed = False

        def communicate(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd="tailscale", timeout=timeout)
            return ("Funnel is not enabled on your tailnet.", "")

        def kill(self):
            _Hanging.killed = True

    monkeypatch.setattr(funnel, "executable", lambda: "/usr/bin/tailscale")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _Hanging())

    code, output = funnel._run_capturing(["funnel", "--bg", "8765"], timeout=1)

    assert code is None
    assert _Hanging.killed is True
    assert "not enabled" in output


def test_commands_never_wait_on_input(monkeypatch):
    """A prompt we did not anticipate must fail, not hang forever."""
    import subprocess

    seen = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs)

        class _Done:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Done()

    monkeypatch.setattr(funnel, "executable", lambda: "/usr/bin/tailscale")
    monkeypatch.setattr(subprocess, "run", fake_run)

    funnel._run(["funnel", "status"])
    assert seen["stdin"] == subprocess.DEVNULL


# ------------------------------------------------------------------------ stop


def test_stopping_turns_the_tunnel_off(cli):
    cli.replies.append((0, ""))

    assert funnel.stop().ok
    assert cli.calls == [["funnel", "--https=443", "off"]]


def test_stopping_falls_back_for_older_releases(cli):
    cli.replies.append((1, "unknown flag: --https"))
    cli.replies.append((0, ""))

    assert funnel.stop().ok
    assert cli.calls == [["funnel", "--https=443", "off"], ["funnel", "reset"]]


def test_stopping_without_tailscale_is_not_an_error(monkeypatch):
    """Nothing of ours is running, so there is nothing to complain about."""
    monkeypatch.setattr(funnel, "executable", lambda: None)
    assert funnel.stop().ok is True


def test_a_failure_to_stop_is_surfaced(cli):
    cli.replies.append((1, "tailscaled is not running"))
    cli.replies.append((1, "tailscaled is not running"))

    result = funnel.stop()

    assert result.ok is False
    assert "tailscaled" in result.message


# ---------------------------------------------------------------------- status


def test_running_is_detected_from_the_status_output(cli):
    cli.replies.append((0, "https://desk.tail1a2b3.ts.net/ proxy 127.0.0.1:8765"))
    assert funnel.is_running() is True


def test_no_serve_config_means_not_running(cli):
    cli.replies.append((0, "No serve config"))
    assert funnel.is_running() is False


def test_machine_url_comes_from_status_json(cli):
    cli.replies.append(_status(STATUS_WITH_FUNNEL))
    assert funnel.machine_url() == "https://desk.tail1a2b3.ts.net"


def test_node_info_reads_the_funnel_capability(cli):
    cli.replies.append(_status(STATUS_WITH_FUNNEL))
    info = funnel.node_info()
    assert info.funnel_enabled is True
    assert info.capabilities_known is True
    assert info.node_id == "nmwNkFutkA11CNTRL"


def test_node_info_notices_the_capability_is_absent(cli):
    cli.replies.append(_status(STATUS_WITHOUT_FUNNEL))
    info = funnel.node_info()
    assert info.funnel_enabled is False
    assert info.capabilities_known is True


def test_unreadable_status_json_is_survivable(cli):
    cli.replies.append((0, "not json at all"))
    assert funnel.machine_url() == ""


# ------------------------------------------------------------------ the panel


@pytest.fixture(autouse=True)
def own_data_dir(monkeypatch, tmp_path):
    """Keep the published marker out of the real application data directory.

    The panel reads it while it is being built, so without this the suite
    would behave differently on a machine that happens to have a funnel left
    over -- and would write to the developer's own app data to find out.
    """
    monkeypatch.setenv("CANONKEEPER_DATA_DIR", str(tmp_path))


@pytest.fixture
def table(ctx, qtbot):
    from canon_keeper.panels.table.widget import TableWidget

    widget = TableWidget(ctx)
    qtbot.addWidget(widget)
    return widget


def test_publishing_without_a_session_does_nothing(table):
    """Reachable only as a stray call now that hosting drives it."""
    started = len(table._entries)

    table._start_funnel()

    assert table._funnel_url == ""
    assert len(table._entries) == started, "nothing to say about nothing"


def test_a_published_address_is_shown_and_copyable(table, qtbot):
    table._on_funnel_started(funnel.Result(True, url="https://desk.tail1a2b3.ts.net/"))

    assert table._funnel_url == "wss://desk.tail1a2b3.ts.net"
    assert "wss://desk.tail1a2b3.ts.net" in table._log.toPlainText()

    table._copy_invite()
    from PySide6.QtWidgets import QApplication

    assert QApplication.clipboard().text() == "wss://desk.tail1a2b3.ts.net"


def test_a_refusal_leaves_the_session_up(table, monkeypatch):
    """Failing to reach the wider internet is not failing to host.

    Publishing is the half that depends on someone else's service being set
    up. The table on your own network must not go down with it.
    """
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)

    table._on_funnel_started(funnel.Result(False, message="something broke"))

    assert table._funnel_url == ""
    assert any(
        "network still works" in line.text for line in table._entries
    ), "the DM should be told the session is still up"
    assert not any(
        line.kind == "error" for line in table._entries
    ), "an unpublished session is not an error"


def test_the_enable_link_is_offered_as_a_button(table, monkeypatch):
    """A URL in a message box is a URL nobody clicks."""
    from PySide6.QtWidgets import QMessageBox

    labels = []
    original_add = QMessageBox.addButton

    def spy_add(self, *args):
        if args and isinstance(args[0], str):
            labels.append(args[0])
        return original_add(self, *args)

    monkeypatch.setattr(QMessageBox, "addButton", spy_add)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)

    table._on_funnel_started(
        funnel.Result(
            False,
            message="Funnel is not switched on",
            enable_url="https://login.tailscale.com/f/funnel?node=abc",
        )
    )

    assert any("Tailscale settings" in label for label in labels)


def test_stopping_clears_the_published_address(table):
    table._on_funnel_started(funnel.Result(True, url="https://desk.tail1a2b3.ts.net/"))
    table._stop_funnel()
    table._on_funnel_stopped(funnel.Result(True))

    assert table._funnel_url == ""


# ----------------------------------------------- what reaches a player live


def test_every_change_signal_reaches_the_host(table):
    """A path that changes data and forgets to publish fails silently.

    Deletion was exactly that: characters removed mid-session stayed on every
    player's screen until they reconnected, because entity_deleted was never
    wired to the host.
    """
    published = []

    class _Listening:
        is_running = True

        def publish_entity(self, entity_id):
            published.append(entity_id)

        def refuse_conflicting(self, entity_id):
            return 0

        def stop(self):
            pass

    table._server = _Listening()

    table._ctx.bus.entity_changed.emit(1)
    table._ctx.bus.entity_deleted.emit(2)
    table._ctx.bus.share_changed.emit(3)

    assert published == [1, 2, 3], "a change signal is not reaching the host"


# ------------------------------------------------- leaving it on by accident
#
# `tailscale funnel --bg` takes effect in the daemon, not in this process. So
# the app closing is not the funnel closing, and anything the app forgets to
# take down stays on the public internet -- across a restart, and across a
# reboot.


def test_closing_takes_down_a_publish_that_never_answered(table, monkeypatch):
    """The address is not the thing to check. The request is.

    A DM who presses Go online and closes the app before Tailscale answers had
    no address recorded, so the old test for one skipped the takedown -- and
    ``funnel --bg`` had already put the session on the internet by then. The
    next launch starts with an empty address and no idea it is there.
    """
    stopped: list[bool] = []
    monkeypatch.setattr(funnel, "stop", lambda *_a: stopped.append(True))
    table._server = None
    table._publishing = True  # asked for, no answer yet
    assert table._funnel_url == ""

    table.shutdown()

    assert stopped, "the session was left published"
    assert table._publishing is False


def test_closing_does_not_ask_tailscale_about_a_session_never_published(
    table, monkeypatch
):
    """The other half: no publish, no command. Every call spawns a process."""
    stopped: list[bool] = []
    monkeypatch.setattr(funnel, "stop", lambda *_a: stopped.append(True))
    table._server = None

    table.shutdown()

    assert stopped == []


def test_a_funnel_left_over_from_last_time_is_said_out_loud(ctx, qtbot):
    """The case the app could not see at all: `funnel --bg` outlives it.

    The configuration lives in the tailscaled daemon, so an unclean exit --
    a crash, a kill, the power going -- leaves the machine published with
    nothing here holding its address. A note beside the campaigns records it,
    because the next run starts with an empty one and no way to know.
    """
    from canon_keeper.panels.table.widget import TableWidget

    funnel.remember(8765)
    widget = TableWidget(ctx)
    qtbot.addWidget(widget)

    assert widget._anything_published is True
    assert any("8765" in line.text for line in widget._entries), (
        "the DM was not told the machine is still published"
    )
    assert widget._leave_button.isEnabled(), "nothing on screen could take it down"


def test_nothing_is_said_when_the_last_session_shut_down_cleanly(table):
    """The other half. A note that cried wolf would stop being read."""
    assert table._anything_published is False
    assert not any("still published" in line.text for line in table._entries)


def test_going_offline_clears_the_note_as_well_as_the_funnel(table, monkeypatch):
    """A note outliving the funnel means being told again on the next launch."""
    monkeypatch.setattr(funnel, "stop", lambda *_a: funnel.Result(True))
    funnel.remember(8765)
    table._publishing = True

    table._stop_funnel()

    assert funnel.left_over() is None


def test_a_publish_that_never_ran_leaves_no_note(table, monkeypatch):
    """Funnel switched off for the tailnet: the command is never run.

    Nothing reaches the internet in that case, so claiming otherwise on the
    next launch would be a false alarm -- and the whole value of the note is
    that it is worth acting on.
    """
    monkeypatch.setattr(
        type(table), "_report_funnel_problem", lambda self, result: None
    )
    funnel.remember(8765)
    table._publishing = True

    table._on_funnel_started(
        funnel.Result(False, message="Funnel is not switched on", attempted=False)
    )

    assert funnel.left_over() is None
    assert table._anything_published is False


def test_a_publish_that_ran_and_then_failed_keeps_the_note(table, monkeypatch):
    """Because by then it may well be published, and nobody here can tell."""
    monkeypatch.setattr(
        type(table), "_report_funnel_problem", lambda self, result: None
    )
    funnel.remember(8765)
    table._publishing = True

    table._on_funnel_started(
        funnel.Result(False, message="timed out", attempted=True)
    )

    assert funnel.left_over() == 8765
    assert table._anything_published is True
