"""Pressing Autopilot should hand over the table, not set homework.

The first version of this told the DM to open a terminal and run a command with
their campaign's file path in it. These tests are about the version that does
the work instead: makes the login, keeps the password, starts the process.

What must survive the convenience is the arrangement it makes convenient. The
agent is still a separate process with a socket and a login. Nothing here hands
it a database, and nothing here can.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from canon_keeper import agent_runner, credentials


@pytest.fixture
def keyring_in_memory(monkeypatch):
    """A credential store that works, without touching the real one."""
    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(credentials, "is_available", lambda: True)
    monkeypatch.setattr(
        credentials,
        "save",
        lambda url, user, password: store.__setitem__((url, user), password) or True,
    )
    monkeypatch.setattr(credentials, "load", lambda url, user: store.get((url, user)))
    return store


# ------------------------------------------------------------------- the login


def test_it_creates_the_login_the_first_time(ctx, keyring_in_memory):
    username, password = agent_runner.ensure_account(ctx.repos, ctx.campaign_id)

    account = ctx.repos.accounts.by_username(ctx.campaign_id, username)
    assert account is not None
    assert account.role == "agent"
    assert password


def test_the_password_is_generated_not_asked_for(ctx, keyring_in_memory):
    _username, password = agent_runner.ensure_account(ctx.repos, ctx.campaign_id)
    assert len(password) >= 24, "nobody types this, so it may as well be long"


def test_pressing_it_twice_reuses_one_login(ctx, keyring_in_memory):
    first_user, first_password = agent_runner.ensure_account(
        ctx.repos, ctx.campaign_id
    )
    second_user, second_password = agent_runner.ensure_account(
        ctx.repos, ctx.campaign_id
    )

    assert (first_user, first_password) == (second_user, second_password)
    assert len(ctx.repos.accounts.list(ctx.campaign_id)) == 1


def test_the_password_goes_to_the_credential_store(ctx, keyring_in_memory):
    _username, password = agent_runner.ensure_account(ctx.repos, ctx.campaign_id)
    assert password in keyring_in_memory.values()


def test_a_login_whose_password_was_lost_is_reset(ctx, keyring_in_memory):
    """A cleared keychain must not leave a campaign unable to use autopilot."""
    agent_runner.ensure_account(ctx.repos, ctx.campaign_id)
    keyring_in_memory.clear()

    _username, password = agent_runner.ensure_account(ctx.repos, ctx.campaign_id)

    assert password
    assert len(ctx.repos.accounts.list(ctx.campaign_id)) == 1, "reset, not duplicated"


def test_the_new_password_actually_works(ctx, keyring_in_memory):
    """A reset that produced a password the host rejects would be worse than none."""
    from canon_keeper_protocol import auth

    agent_runner.ensure_account(ctx.repos, ctx.campaign_id)
    keyring_in_memory.clear()
    username, password = agent_runner.ensure_account(ctx.repos, ctx.campaign_id)

    account = ctx.repos.accounts.by_username(ctx.campaign_id, username)
    assert auth.derive_verifier(password, account.salt) == account.verifier


def test_without_a_credential_store_it_says_what_to_do(ctx, monkeypatch):
    monkeypatch.setattr(credentials, "is_available", lambda: False)

    with pytest.raises(agent_runner.AgentUnavailable) as exc:
        agent_runner.ensure_account(ctx.repos, ctx.campaign_id)

    assert "--add-agent" in str(exc.value), "the fallback has to be spelled out"


def test_each_campaign_gets_its_own_password(ctx, keyring_in_memory, repos):
    other = repos.campaigns.create("Another Campaign")

    _u1, first = agent_runner.ensure_account(ctx.repos, ctx.campaign_id)
    _u2, second = agent_runner.ensure_account(repos, other.id)

    assert first != second


# ----------------------------------------------------------------- the process


def test_it_finds_the_agent(monkeypatch):
    """Installed alongside the app, so it should simply be found."""
    assert agent_runner.find_executable() is not None


def test_a_missing_agent_says_how_to_get_one():
    assert "pip install" in agent_runner.explain_missing()


def test_secrets_go_through_the_environment_not_the_command_line(monkeypatch):
    """Arguments are visible to anyone who can list processes."""
    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs.get("env", {})
        return _FakeProcess()

    monkeypatch.setattr(agent_runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(agent_runner, "find_executable", lambda: ["canonkeeper-agent"])

    agent_runner.start(
        "ws://127.0.0.1:8766", "autopilot", "the-password", "sk-the-key"
    )

    assert "the-password" not in " ".join(captured["command"])
    assert "sk-the-key" not in " ".join(captured["command"])
    assert captured["env"]["CANONKEEPER_AGENT_PASSWORD"] == "the-password"
    assert captured["env"]["ANTHROPIC_API_KEY"] == "sk-the-key"


def test_the_url_and_user_do_go_on_the_command_line(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        agent_runner.subprocess,
        "Popen",
        lambda command, **kwargs: captured.update(command=command) or _FakeProcess(),
    )
    monkeypatch.setattr(agent_runner, "find_executable", lambda: ["canonkeeper-agent"])

    agent_runner.start("ws://127.0.0.1:8766", "autopilot", "pw")

    assert "--url" in captured["command"]
    assert "ws://127.0.0.1:8766" in captured["command"]
    assert "autopilot" in captured["command"]


def test_starting_without_the_agent_installed_raises(monkeypatch):
    monkeypatch.setattr(agent_runner, "find_executable", lambda: None)

    with pytest.raises(agent_runner.AgentUnavailable) as exc:
        agent_runner.start("ws://127.0.0.1:8766", "autopilot", "pw")

    assert "pip install" in str(exc.value)


def test_stopping_an_already_dead_process_is_fine():
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    agent_runner.stop(process)  # must not raise


def test_stopping_nothing_is_fine():
    agent_runner.stop(None)


def test_a_stubborn_agent_is_killed(monkeypatch):
    class Stubborn:
        def __init__(self):
            self.killed = False

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("agent", timeout)

        def kill(self):
            self.killed = True

    process = Stubborn()
    agent_runner.stop(process, timeout=0.01)
    assert process.killed is True


# --------------------------------------------------------------------- the key


def test_the_environment_wins_for_the_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-the-environment")
    assert agent_runner.api_key() == "sk-from-the-environment"


def test_otherwise_it_comes_from_the_credential_store(monkeypatch, keyring_in_memory):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    agent_runner.remember_api_key("sk-remembered")
    assert agent_runner.api_key() == "sk-remembered"


def test_no_key_anywhere_is_an_empty_string(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(credentials, "load", lambda *_args: None)
    assert agent_runner.api_key() == ""


# ------------------------------------------------------------------ the button
#
# The rule worth protecting: the app starts an agent only when nothing is
# already answering. Someone running one themselves -- on this machine or on a
# spare box -- should not have a second one started underneath them.


@pytest.fixture
def table(ctx, qtbot, keyring_in_memory):
    from canon_keeper.panels.table.widget import TableWidget

    widget = TableWidget(ctx)
    qtbot.addWidget(widget)
    return widget


class _FakeServer:
    is_running = True
    port = 8766

    def __init__(self, members=()):
        self.members = list(members)
        self.autopilot = False
        self.by = ""

    def set_autopilot(self, on, by=""):
        self.autopilot = on
        self.by = by

    def stop(self):
        """Called when the widget is closed at teardown."""
        self.is_running = False


class _Member:
    def __init__(self, role):
        self.role = role
        self.name = role


class _FakeProcess:
    """A process that has already exited, so stopping it is a no-op.

    A bare object() is not enough: teardown closes the widget, which calls
    shutdown, which stops the agent -- after monkeypatch has been undone.
    """

    returncode = 0
    stdout = None

    def poll(self):
        return 0

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    def wait(self, timeout=None):
        return 0

    def why_it_stopped(self, lines: int = 4) -> str:
        return "for testing"


def test_it_does_not_start_one_over_an_agent_already_connected(table, monkeypatch):
    started = []
    monkeypatch.setattr(agent_runner, "start", lambda *a, **k: started.append(a))
    table._server = _FakeServer(members=[_Member("agent")])

    assert table._ensure_agent_running() is True
    assert started == [], "someone else's agent must be left alone"


def test_it_does_start_one_when_only_players_are_connected(table, monkeypatch):
    started = []
    # Say the requirements are met. Without this the answer depends on whether
    # `anthropic` happens to be installed, which is exactly the difference
    # between a developer's machine and CI.
    monkeypatch.setattr(agent_runner, "missing_requirement", lambda: "")
    monkeypatch.setattr(agent_runner, "find_executable", lambda: ["canonkeeper-agent"])
    monkeypatch.setattr(agent_runner, "api_key", lambda: "sk-test")
    monkeypatch.setattr(
        agent_runner, "start", lambda *a, **k: started.append(a) or _FakeProcess()
    )
    table._server = _FakeServer(members=[_Member("player")])

    assert table._ensure_agent_running() is True
    assert len(started) == 1


def test_turning_it_off_stops_the_one_we_started(table, monkeypatch):
    stopped = []
    monkeypatch.setattr(agent_runner, "stop", lambda p, **k: stopped.append(p))
    table._server = _FakeServer()
    table._agent_process = _FakeProcess()
    table._autopilot_button.setChecked(False)

    table._toggle_autopilot()

    assert table._server.autopilot is False
    assert len(stopped) == 1
    assert table._agent_process is None


def test_it_is_switched_off_before_the_agent_is_stopped(table, monkeypatch):
    """Otherwise there is a window where the agent is live and ungated."""
    order = []
    server = _FakeServer()
    server.set_autopilot = lambda on, by="": order.append(f"switch:{on}")
    monkeypatch.setattr(agent_runner, "stop", lambda p, **k: order.append("stop"))
    table._server = server
    table._agent_process = _FakeProcess()
    table._autopilot_button.setChecked(False)

    table._toggle_autopilot()

    assert order == ["switch:False", "stop"]


def test_closing_the_app_stops_the_agent(table, monkeypatch):
    stopped = []
    monkeypatch.setattr(agent_runner, "stop", lambda p, **k: stopped.append(p))
    table._agent_process = _FakeProcess()

    table.shutdown()

    assert len(stopped) == 1, "an agent left against a dead session is unkillable"


# ------------------------------------------------------------- noticing it died
#
# The whole reason this section exists: the agent was started, exited a
# millisecond later because `anthropic` was not installed, and the app said
# "Starting the agent..." and then nothing. Ever. The table sat waiting for a
# machine that was not there.


def test_a_missing_model_package_is_caught_before_starting(monkeypatch):
    """A sentence beats a process that dies in the first millisecond."""
    monkeypatch.setattr(agent_runner, "find_executable", lambda: ["canonkeeper-agent"])
    monkeypatch.setattr(agent_runner.importlib.util, "find_spec", lambda _name: None)

    problem = agent_runner.missing_requirement()

    assert "anthropic" in problem
    assert "pip install" in problem


def test_nothing_missing_is_an_empty_string(monkeypatch):
    monkeypatch.setattr(agent_runner, "find_executable", lambda: ["canonkeeper-agent"])
    monkeypatch.setattr(agent_runner.importlib.util, "find_spec", lambda _name: object())

    assert agent_runner.missing_requirement() == ""


def test_a_missing_agent_is_reported_first(monkeypatch):
    monkeypatch.setattr(agent_runner, "find_executable", lambda: None)
    assert "pip install" in agent_runner.missing_requirement()


def test_it_keeps_what_the_agent_said(tmp_path):
    """So a failure can be shown to a person instead of vanishing."""
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; print('could not log in: nope'); sys.exit(1)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    agent = agent_runner.RunningAgent(process)
    agent.wait(timeout=15)
    agent._reader.join(timeout=5)

    assert "could not log in" in agent.why_it_stopped()
    assert agent.returncode == 1


def test_a_silent_death_still_says_something():
    process = subprocess.Popen(
        [sys.executable, "-c", "raise SystemExit(1)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    agent = agent_runner.RunningAgent(process)
    agent.wait(timeout=15)
    agent._reader.join(timeout=5)

    assert agent.why_it_stopped(), "an empty explanation is not an explanation"


def test_only_the_tail_is_kept():
    """The failure is on the way out; the first lines are just it starting up."""
    process = subprocess.Popen(
        [sys.executable, "-c", "[print(f'line {i}') for i in range(50)]"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    agent = agent_runner.RunningAgent(process)
    agent.wait(timeout=15)
    agent._reader.join(timeout=5)

    said = agent.why_it_stopped(lines=3)
    assert "line 49" in said
    assert "line 0" not in said


def test_the_watchdog_reports_a_dead_agent(table, monkeypatch):
    table._server = _FakeServer()
    table._server.autopilot = True
    table._agent_process = _FakeProcess()
    table._autopilot_button.setChecked(True)

    table._check_agent()

    assert any("stopped" in line.text.lower() for line in table._entries)
    assert table._agent_process is None


def test_and_switches_autopilot_off(table):
    """Autopilot with no agent is a table waiting on nothing."""
    server = _FakeServer()
    server.autopilot = True
    table._server = server
    table._agent_process = _FakeProcess()
    table._autopilot_button.setChecked(True)

    table._check_agent()

    assert server.autopilot is False
    assert table._autopilot_button.isChecked() is False


def test_a_living_agent_is_left_alone(table):
    class _Alive(_FakeProcess):
        def poll(self):
            return None

    server = _FakeServer()
    server.autopilot = True
    table._server = server
    table._agent_process = _Alive()

    table._check_agent()

    assert table._agent_process is not None
    assert server.autopilot is True


# ------------------------------------------------- one campaign, one password
#
# campaign.id is not a unique id. Every campaign is its own SQLite file, so
# almost all of them are campaign 1 -- keying the credential store by that made
# two campaigns share an entry, and opening the second silently overwrote the
# first's password. The symptom was an agent that could no longer log in to a
# campaign nobody had touched.


def test_two_campaigns_do_not_share_a_password(ctx, keyring_in_memory, repos):
    """Both are campaign 1 on disk. They must not be campaign 1 in the keychain."""
    from canon_keeper_core.db.connection import connect
    from canon_keeper_core.db.migrate import migrate
    from canon_keeper_core.repo import Repos

    conn = connect(":memory:")
    migrate(conn)
    second = Repos(conn)
    second_campaign = second.campaigns.ensure_default("A Different Campaign")
    assert second_campaign.id == ctx.campaign_id, "both really are campaign 1"

    _u1, first = agent_runner.ensure_account(ctx.repos, ctx.campaign_id)
    _u2, later = agent_runner.ensure_account(second, second_campaign.id)

    # Opening the second must not have clobbered the first.
    _u3, first_again = agent_runner.ensure_account(ctx.repos, ctx.campaign_id)
    assert first_again == first
    assert later != first
    conn.close()


def test_a_saved_password_the_host_would_refuse_is_replaced(ctx, keyring_in_memory):
    """Handing over a password that fails at the door helps nobody."""
    from canon_keeper_protocol import auth

    agent_runner.ensure_account(ctx.repos, ctx.campaign_id)

    # Something put the store and the campaign out of step: a restored backup,
    # a copied campaign, or the id collision above.
    key = agent_runner._password_key(ctx.repos)
    keyring_in_memory[(key, agent_runner.AGENT_USERNAME)] = "a-password-from-elsewhere"

    _username, password = agent_runner.ensure_account(ctx.repos, ctx.campaign_id)

    account = ctx.repos.accounts.by_username(
        ctx.campaign_id, agent_runner.AGENT_USERNAME
    )
    assert password != "a-password-from-elsewhere"
    assert auth.derive_verifier(password, account.salt) == account.verifier


def test_a_working_password_is_left_alone(ctx, keyring_in_memory):
    """Resetting on every press would be churn, and would break a running agent."""
    _u1, first = agent_runner.ensure_account(ctx.repos, ctx.campaign_id)
    account_before = ctx.repos.accounts.by_username(
        ctx.campaign_id, agent_runner.AGENT_USERNAME
    )

    _u2, second = agent_runner.ensure_account(ctx.repos, ctx.campaign_id)
    account_after = ctx.repos.accounts.by_username(
        ctx.campaign_id, agent_runner.AGENT_USERNAME
    )

    assert first == second
    assert account_before.verifier == account_after.verifier


def test_the_password_check_is_not_fooled_by_a_near_miss(ctx, keyring_in_memory):
    from canon_keeper_protocol import auth

    _username, password = agent_runner.ensure_account(ctx.repos, ctx.campaign_id)
    account = ctx.repos.accounts.by_username(
        ctx.campaign_id, agent_runner.AGENT_USERNAME
    )

    assert agent_runner._password_works(account, password) is True
    assert agent_runner._password_works(account, password + "x") is False
    assert agent_runner._password_works(account, "") is False
