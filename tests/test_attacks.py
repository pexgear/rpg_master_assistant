"""Taking a turn: the arithmetic, and the three parties who have to agree.

A player says "I get behind the orc and hit it with my axe". Three things then
happen, and none of them can skip the others.

* The **agent** turns words into rules and *proposes*. It never moves anybody.
* The **host** decides whether that is even legal, and does all the rolling.
* The **player** owns their own turn. Nothing touches their character until
  they accept, and refusing costs one click.

That last one is the difference between an app that offers to move your
character into a fire and one that does it.
"""

from __future__ import annotations

import pytest

from canon_keeper_core.content import Content
from canon_keeper.net.client import SessionClient
from canon_keeper.net.server import SessionServer
from canon_keeper_core.repo.entities import KIND_NPC, KIND_PC, Entity
from canon_keeper_core.rules import attack
from canon_keeper_protocol import MessageType


def _sheet(**overrides) -> dict:
    sheet = {
        "schema": 1,
        "species": "human",
        "class_index": "fighter",
        "level": 3,
        "abilities": {"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 10, "cha": 10},
        "equipment": ["battleaxe", "shortbow", "chain-mail"],
        "hp_current": 28,
    }
    sheet.update(overrides)
    return sheet


@pytest.fixture
def content(repos) -> Content:
    return Content(repos.settings)


class _Rolls:
    """Dice the test decides, in the shape the host's roller returns."""

    def __init__(self, *totals: int) -> None:
        self._totals = list(totals)

    def __call__(self, notation: str):
        value = self._totals.pop(0) if self._totals else 1

        class _Result:
            total = value
            rolls = [value]

        return _Result()


# ------------------------------------------------------------------ the weapon


def test_a_weapon_is_read_off_the_sheet(content):
    weapon = attack.find_weapon(_sheet(), content, "battleaxe")
    assert weapon.name == "Battleaxe"
    assert weapon.dice == "1d8"
    assert weapon.ranged is False


def test_a_loose_name_still_finds_it(content):
    """It comes out of a sentence somebody typed, not a dropdown."""
    assert attack.find_weapon(_sheet(), content, "axe").index == "battleaxe"
    assert attack.find_weapon(_sheet(), content, "Bow").index == "shortbow"


def test_asking_for_something_they_do_not_have_says_what_they_do(content):
    with pytest.raises(attack.NoAttack) as raised:
        attack.find_weapon(_sheet(), content, "greatsword")
    assert "Battleaxe" in str(raised.value)


def test_no_weapon_at_all_is_a_refusal_not_a_crash(content):
    with pytest.raises(attack.NoAttack):
        attack.find_weapon(_sheet(equipment=["chain-mail"]), content, "axe")


def test_armour_and_rope_are_not_weapons(content):
    carried = {w.index for w in attack.weapons_of(_sheet(), content)}
    assert carried == {"battleaxe", "shortbow"}


# ------------------------------------------------------------------ the numbers


def test_a_melee_attack_uses_strength(content):
    weapon = attack.find_weapon(_sheet(), content, "battleaxe")
    # +3 strength, +2 proficiency at level 3.
    assert attack.attack_bonus(_sheet(), content, weapon) == 5


def test_a_ranged_attack_uses_dexterity(content):
    weapon = attack.find_weapon(_sheet(), content, "shortbow")
    assert attack.attack_bonus(_sheet(), content, weapon) == 4


def test_a_finesse_weapon_takes_the_better_of_the_two(content):
    sheet = _sheet(
        equipment=["dagger"],
        abilities={"str": 8, "dex": 18, "con": 12, "int": 10, "wis": 10, "cha": 10},
    )
    weapon = attack.find_weapon(sheet, content, "dagger")
    assert attack.attack_bonus(sheet, content, weapon) == 6, "dexterity, not strength"


def test_reach_is_one_square_including_diagonals(content):
    axe = attack.find_weapon(_sheet(), content, "battleaxe")
    assert attack.within_reach(axe, 1)
    assert not attack.within_reach(axe, 2)

    bow = attack.find_weapon(_sheet(), content, "shortbow")
    assert attack.within_reach(bow, 10)


def test_distance_is_measured_the_way_a_table_measures_it():
    assert attack.squares_between((0, 0), (1, 1)) == 1
    assert attack.squares_between((0, 0), (3, -1)) == 3


# ------------------------------------------------------------------ the swing


def test_a_hit_takes_the_modifier_and_the_dice(content):
    weapon = attack.find_weapon(_sheet(), content, "battleaxe")
    result = attack.resolve(_sheet(), content, weapon, 13, _Rolls(12, 5))

    assert result.total == 17
    assert result.hit
    assert result.damage == 8, "5 on the die plus 3 strength"


def test_a_miss_rolls_no_damage(content):
    weapon = attack.find_weapon(_sheet(), content, "battleaxe")
    result = attack.resolve(_sheet(), content, weapon, 18, _Rolls(4))
    assert not result.hit
    assert result.damage == 0


def test_a_natural_twenty_always_hits(content):
    weapon = attack.find_weapon(_sheet(), content, "battleaxe")
    result = attack.resolve(_sheet(), content, weapon, 40, _Rolls(20, 6))
    assert result.hit
    assert result.critical


def test_a_natural_one_always_misses(content):
    weapon = attack.find_weapon(_sheet(), content, "battleaxe")
    result = attack.resolve(_sheet(), content, weapon, 2, _Rolls(1))
    assert not result.hit


def test_a_critical_doubles_the_dice_and_not_the_modifier(content):
    """The rule people most often get wrong in the generous direction."""
    weapon = attack.find_weapon(_sheet(), content, "battleaxe")
    rolled: list[str] = []

    def watching(notation):
        rolled.append(notation)
        return _Rolls(20 if len(rolled) == 1 else 9)(notation)

    result = attack.resolve(_sheet(), content, weapon, 10, watching)
    assert rolled[1] == "2d8"
    assert result.damage == 12, "9 on the dice plus 3, not plus 6"


def test_damage_is_never_less_than_one(content):
    weak = _sheet(abilities={"str": 1, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10})
    weapon = attack.find_weapon(weak, content, "battleaxe")
    result = attack.resolve(weak, content, weapon, 1, _Rolls(15, 1))
    assert result.hit
    assert result.damage == 1


def test_it_reads_as_a_sentence(content):
    weapon = attack.find_weapon(_sheet(), content, "battleaxe")
    said = attack.resolve(_sheet(), content, weapon, 13, _Rolls(12, 5)).describe(
        "Brok", "Yeemik"
    )
    assert "Brok attacks Yeemik with Battleaxe" in said
    assert "AC 13" in said
    assert "hit for 8" in said


# ------------------------------------------------------- the whole thing, live


@pytest.fixture
def fight(qtbot, repos):
    """A player with an axe, a goblin, and a fight already running."""
    campaign = repos.campaigns.ensure_default("Combat")
    hero = repos.entities.create(
        Entity(
            id=None,
            campaign_id=campaign.id,
            kind=KIND_PC,
            name="Brok",
            data={"hp": 28, "max_hp": 28, "sheet": _sheet()},
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
                "sheet": {
                    "schema": 1,
                    "level": 1,
                    "abilities": {"str": 8, "dex": 14, "con": 10, "int": 10,
                                  "wis": 8, "cha": 8},
                    # It needs something to swing, or every attack it makes is
                    # refused for having nothing on its sheet.
                    "equipment": ["scimitar", "shortbow"],
                    "overrides": {"ac": 10, "hp_max": 12},
                },
            },
        )
    )
    marco = repos.accounts.create(
        campaign.id, "marco", "goblin-teeth", display_name="Marco",
        character_entity_id=hero.id,
    )
    repos.accounts.create(
        campaign.id, "autopilot", "let-me-run-it", role="agent", display_name="Autopilot"
    )
    repos.accounts.create(
        campaign.id, "gm", "run-the-game", role="dm", display_name="The DM"
    )
    repos.entities.set_owner(hero.id, marco.id)
    repos.shares.share(campaign.id, goblin.id)

    # Sixteen square, so x and y both run -8..7 -- big enough that a speed of
    # six squares does not reach every corner from every corner.
    encounter = repos.encounters.create(campaign.id, "The cave", width=16, height=16)
    tokens = {
        "hero": repos.encounters.add(encounter.id, hero.id, initiative=18, x=-3, y=0),
        "goblin": repos.encounters.add(encounter.id, goblin.id, initiative=9, x=1, y=0),
    }
    repos.encounters.begin(encounter.id)

    server = SessionServer(repos, campaign.id, "Combat session")
    assert server.start(0, announce=False), "could not bind an ephemeral port"
    server.set_autopilot(True, by="The DM")
    yield server, repos, encounter, tokens, hero, goblin
    server.stop()


def _join(qtbot, server, username, password) -> SessionClient:
    client = SessionClient()
    with qtbot.waitSignal(client.connected, timeout=10000):
        client.join(f"ws://127.0.0.1:{server.port}", username, password)
    return client


def _propose(agent, tokens, **overrides) -> None:
    payload = {
        "combatant": tokens["hero"].id,
        "move": [0, 0],
        "target": tokens["goblin"].id,
        "weapon": "battleaxe",
        "text": "Move to 0,0 and attack Yeemik with a battleaxe.",
    }
    payload.update(overrides)
    agent._send(MessageType.PROPOSE, **payload)


def test_a_proposal_reaches_the_player_whose_turn_it_is(qtbot, fight):
    server, _repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)

        action = offered[0]
        assert action["who"] == "Brok"
        assert action["move"] == [0, 0]
        assert action["target_name"] == "Yeemik"
        assert "battleaxe" in action["text"]
    finally:
        agent.leave()
        player.leave()


def test_nothing_happens_until_they_accept(qtbot, fight):
    """The whole point. A proposal is not a move."""
    server, repos, _encounter, tokens, _hero, goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)
        qtbot.wait(200)

        assert repos.encounters.combatant(tokens["hero"].id).x == -3
        assert repos.entities.get(goblin.id).data["hp"] == 12
    finally:
        agent.leave()
        player.leave()


def test_accepting_moves_and_swings(qtbot, fight):
    server, repos, _encounter, tokens, _hero, goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)

        player.send_answer(offered[0]["id"], True)
        qtbot.waitUntil(
            lambda: repos.encounters.combatant(tokens["hero"].id).x == 0, timeout=5000
        )
        # AC 10 against +5 to hit: it lands far more often than not, and either
        # way the swing happened and is in the log.
        qtbot.waitUntil(
            lambda: any(
                "attacks Yeemik" in m.get("text", "") for m in server.history()
            ),
            timeout=5000,
        )
    finally:
        agent.leave()
        player.leave()


def test_refusing_leaves_everything_alone(qtbot, fight):
    server, repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)

        player.send_answer(offered[0]["id"], False, "I stay where I am and throw a rock")
        qtbot.wait(400)

        assert repos.encounters.combatant(tokens["hero"].id).x == -3
        assert any(
            "throw a rock" in m.get("text", "") for m in server.history()
        ), "what they said instead has to reach the agent"
    finally:
        agent.leave()
        player.leave()


def test_only_the_player_whose_turn_it_is_may_answer(qtbot, fight):
    """Somebody else accepting your turn is somebody else playing your character."""
    server, repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)

        # The agent is not the player, and holds a DM-shaped view. It still
        # cannot answer for them.
        with qtbot.waitSignal(agent.failed, timeout=5000):
            agent.send_answer(offered[0]["id"], True)
        qtbot.wait(200)
        assert repos.encounters.combatant(tokens["hero"].id).x == -3
    finally:
        agent.leave()
        player.leave()


def test_a_player_cannot_propose_their_own_turn(qtbot, fight):
    """Otherwise the confirmation is a client asking itself."""
    server, _repos, _encounter, tokens, _hero, _goblin = fight
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(player.failed, timeout=5000):
            _propose(player, tokens)
    finally:
        player.leave()


def test_an_impossible_square_is_refused_before_anyone_is_asked(qtbot, fight):
    server, _repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        with qtbot.waitSignal(agent.failed, timeout=5000) as blocker:
            _propose(agent, tokens, move=[99, 99])
        assert "off the map" in " ".join(str(a) for a in blocker.args)
        qtbot.wait(200)
        assert offered == [], "a player must never be shown an illegal turn"
    finally:
        agent.leave()
        player.leave()


def test_a_square_somebody_is_standing_on_is_refused(qtbot, fight):
    server, _repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        with qtbot.waitSignal(agent.failed, timeout=5000) as blocker:
            _propose(agent, tokens, move=[1, 0])
        assert "already at" in " ".join(str(a) for a in blocker.args)
    finally:
        agent.leave()


def test_nobody_attacks_themselves(qtbot, fight):
    server, _repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        with qtbot.waitSignal(agent.failed, timeout=5000):
            _propose(agent, tokens, target=tokens["hero"].id)
    finally:
        agent.leave()


def test_a_second_proposal_takes_the_first_one_back(qtbot, fight):
    """A player answering a stale offer would be acting on an older map."""
    server, _repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    withdrawn: list[str] = []
    player.action_withdrawn.connect(withdrawn.append)
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)
        first = offered[0]["id"]

        _propose(agent, tokens, move=[-1, 0], text="Move to -1,0 instead.")
        qtbot.waitUntil(lambda: first in withdrawn, timeout=5000)
    finally:
        agent.leave()
        player.leave()


# --------------------------------------------------- the chat box, in place of Send
#
# The confirmation is not a dialog somewhere else. It is where Send was, and it
# holds the box: there are exactly three ways past it -- do it, say more, or
# refuse -- and none of them is carrying on chatting and never coming back.


@pytest.fixture
def chat(qtbot, repos):
    """A player's Table panel, with nothing connected."""
    import logging

    from canon_keeper.bus import Bus
    from canon_keeper.net.state import SharedState
    from canon_keeper.panels.table.widget import TableWidget
    from canon_keeper.plugin import AppContext

    campaign = repos.campaigns.ensure_default("Turn")
    ctx = AppContext(
        repos=repos,
        bus=Bus(),
        log=logging.getLogger("canonkeeper.test"),
        campaign_id=campaign.id,
        role="player",
        shared=SharedState(),
    )
    widget = TableWidget(ctx)
    qtbot.addWidget(widget)
    return widget, ctx


def _offered(**overrides) -> dict:
    action = {
        "id": "abc123",
        "combatant": 11,
        "who": "Brok",
        "move": [0, 0],
        "target": 12,
        "target_name": "Yeemik",
        "weapon": "battleaxe",
        "text": "Move to 0,0 and attack Yeemik with a battleaxe.",
    }
    action.update(overrides)
    return action


def test_the_offer_replaces_sending(chat):
    widget, _ctx = chat
    widget._on_turn_offered(_offered())

    assert not widget._offer_bar.isHidden()
    assert "battleaxe" in widget._offer_label.text()
    assert not widget._entry.isEnabled(), "you cannot carry on chatting"


def test_doing_it_answers_and_gives_the_box_back(chat, qtbot):
    widget, ctx = chat
    widget._on_turn_offered(_offered())

    with qtbot.waitSignal(ctx.bus.action_answered, timeout=1000) as blocker:
        widget._accept_turn()

    assert blocker.args == ["abc123", True, ""]
    assert widget._offer_bar.isHidden()


def test_refusing_answers_and_gives_the_box_back(chat, qtbot):
    widget, ctx = chat
    widget._on_turn_offered(_offered())

    with qtbot.waitSignal(ctx.bus.action_answered, timeout=1000) as blocker:
        widget._refuse_turn()

    assert blocker.args == ["abc123", False, ""]
    assert widget._offer_bar.isHidden()


def test_saying_more_unlocks_the_box_without_answering(chat):
    """The offer stands while you explain -- the map is still showing it."""
    widget, _ctx = chat
    widget._on_turn_offered(_offered())

    widget._unlock_to_say_more()

    assert widget._offer_bar.isHidden()
    assert widget._offered is not None, "not answered, only being corrected"
    assert "What did you mean" in widget._entry.placeholderText()


def test_the_offer_comes_back_after_one_more_message(chat, monkeypatch):
    """One message, then it waits again rather than leaving the turn open."""
    widget, _ctx = chat
    widget._on_turn_offered(_offered())
    widget._unlock_to_say_more()

    monkeypatch.setattr(widget._client, "send_chat", lambda _text: True)
    widget._entry.setText("no, I stay put and shoot it")
    widget._send()

    assert not widget._offer_bar.isHidden()


def test_the_dms_copy_does_not_hold_their_box(chat):
    widget, _ctx = chat
    widget._on_turn_offered(_offered(watching=True))

    assert widget._offer_bar.isHidden()
    assert widget._offered is None


def test_a_withdrawn_offer_gives_the_box_back(chat):
    widget, _ctx = chat
    widget._on_turn_offered(_offered())
    widget._on_turn_withdrawn("abc123")

    assert widget._offer_bar.isHidden()
    assert widget._offered is None


def test_your_turn_is_said_in_the_chat(chat):
    """The map has already said it, in a colour, on a fingernail."""
    widget, _ctx = chat
    widget._ctx.shared.replace_all(
        [{"id": 7, "kind": "pc", "name": "Brok", "data": {}, "own": True}]
    )
    widget._on_encounter_received(
        {"turn": 11, "combatants": [{"id": 11, "entity": 7, "x": 0, "y": 0}]}
    )

    assert any(line.kind == "turn" for line in widget._entries)


def test_it_is_said_once(chat):
    widget, _ctx = chat
    widget._ctx.shared.replace_all(
        [{"id": 7, "kind": "pc", "name": "Brok", "data": {}, "own": True}]
    )
    fight = {"turn": 11, "combatants": [{"id": 11, "entity": 7, "x": 0, "y": 0}]}
    widget._on_encounter_received(fight)
    widget._on_encounter_received(fight)

    assert sum(1 for line in widget._entries if line.kind == "turn") == 1


def test_somebody_elses_turn_says_nothing(chat):
    widget, _ctx = chat
    widget._ctx.shared.replace_all(
        [{"id": 7, "kind": "pc", "name": "Brok", "data": {}, "own": True}]
    )
    widget._on_encounter_received(
        {"turn": 99, "combatants": [{"id": 11, "entity": 7, "x": 0, "y": 0}]}
    )

    assert not any(line.kind == "turn" for line in widget._entries)


# --------------------------------------------------- anything else, or done?
#
# The clock runs on the host, not on the player's machine. It is a promise made
# to four other people, and it must not depend on one person's laptop staying
# awake.


def test_after_acting_they_are_asked_if_there_is_more(qtbot, fight):
    server, _repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    asked: list[tuple] = []
    player.action_proposed.connect(offered.append)
    player.still_your_turn.connect(lambda on, secs: asked.append((on, secs)))
    try:
        _propose(agent, tokens, target=None)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)
        player.send_answer(offered[0]["id"], True)

        qtbot.waitUntil(lambda: bool(asked), timeout=5000)
        waiting, seconds = asked[0]
        assert waiting is True
        assert seconds == 30
    finally:
        agent.leave()
        player.leave()


def test_saying_done_passes_the_turn_on(qtbot, fight):
    server, repos, encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        assert repos.encounters.get(encounter.id).turn_combatant_id == tokens["hero"].id
        _propose(agent, tokens, target=None)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)
        player.send_answer(offered[0]["id"], True)
        qtbot.wait(300)

        player.send_done()
        qtbot.waitUntil(
            lambda: repos.encounters.get(encounter.id).turn_combatant_id
            == tokens["goblin"].id,
            timeout=5000,
        )
    finally:
        agent.leave()
        player.leave()


def test_nobody_else_can_end_your_turn(qtbot, fight):
    server, repos, encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        with qtbot.waitSignal(agent.failed, timeout=5000):
            agent.send_done()
        assert (
            repos.encounters.get(encounter.id).turn_combatant_id == tokens["hero"].id
        )
    finally:
        agent.leave()


def test_the_clock_runs_out_and_the_turn_moves_on(qtbot, fight, monkeypatch):
    """The whole point: everybody else stops waiting on one person."""
    import canon_keeper.net.server as server_module

    monkeypatch.setattr(server_module, "STILL_YOUR_TURN_MS", 300)

    server, repos, encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens, target=None)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)
        player.send_answer(offered[0]["id"], True)

        qtbot.waitUntil(
            lambda: repos.encounters.get(encounter.id).turn_combatant_id
            == tokens["goblin"].id,
            timeout=5000,
        )
        assert any(
            "no further action" in m.get("text", "") for m in server.history()
        ), "it has to say why the turn moved"
    finally:
        agent.leave()
        player.leave()


def test_typing_stops_the_clock(qtbot, fight, monkeypatch):
    """Somebody talking is somebody who has not stopped reading."""
    import canon_keeper.net.server as server_module

    monkeypatch.setattr(server_module, "STILL_YOUR_TURN_MS", 400)

    server, repos, encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens, target=None)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)
        player.send_answer(offered[0]["id"], True)
        qtbot.wait(150)

        player.send_chat("wait, I want to throw a dagger too")
        qtbot.wait(700)

        assert (
            repos.encounters.get(encounter.id).turn_combatant_id == tokens["hero"].id
        ), "the turn moved on while they were still talking"
    finally:
        agent.leave()
        player.leave()


def test_a_new_proposal_stops_the_clock(qtbot, fight, monkeypatch):
    import canon_keeper.net.server as server_module

    monkeypatch.setattr(server_module, "STILL_YOUR_TURN_MS", 400)

    server, repos, encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens, target=None)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)
        player.send_answer(offered[0]["id"], True)
        qtbot.wait(150)

        _propose(agent, tokens, move=[-2, 0], target=None, text="Step across.")
        qtbot.waitUntil(lambda: len(offered) == 2, timeout=5000)
        qtbot.wait(500)

        assert (
            repos.encounters.get(encounter.id).turn_combatant_id == tokens["hero"].id
        ), "it moved on while a turn was sitting in front of them"
    finally:
        agent.leave()
        player.leave()


def test_the_dm_passing_the_turn_stops_the_clock(qtbot, fight, monkeypatch):
    """It must not fire again a round later and skip somebody at random."""
    import canon_keeper.net.server as server_module

    monkeypatch.setattr(server_module, "STILL_YOUR_TURN_MS", 400)

    server, repos, encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens, target=None)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)
        player.send_answer(offered[0]["id"], True)
        qtbot.wait(150)

        # The DM's path: autopilot may not pass a person's turn, and this
        # is only moving the turn onto the goblin to set the scene.
        server.run_turn("next")
        qtbot.waitUntil(
            lambda: repos.encounters.get(encounter.id).turn_combatant_id
            == tokens["goblin"].id,
            timeout=5000,
        )
        qtbot.wait(600)

        # Still the goblin's: the clock did not fire a second time.
        assert (
            repos.encounters.get(encounter.id).turn_combatant_id == tokens["goblin"].id
        )
    finally:
        agent.leave()
        player.leave()


# ----------------------------------------------------------------- how far
#
# A turn is a move up to your speed and an action. Without the first half a
# proposal could walk somebody from one corner of the room to the other and the
# host would carry it out.


def test_speed_is_read_off_the_sheet(content):
    from canon_keeper_core.rules import derive

    assert derive.speed_in_squares(_sheet(), content) == 6, "thirty feet"
    assert derive.speed_in_squares(_sheet(species="dwarf"), content) == 5


def test_a_monster_with_no_species_still_gets_a_speed(content):
    from canon_keeper_core.rules import derive

    assert derive.speed_in_squares({"schema": 1, "abilities": {}}, content) == 6


def test_a_statblock_can_state_its_own(content):
    from canon_keeper_core.rules import derive

    slow = {"schema": 1, "abilities": {}, "overrides": {"speed": 20}}
    assert derive.speed_in_squares(slow, content) == 4


def test_too_far_is_not_put_to_the_player(qtbot, fight):
    """It goes to the DM instead -- see the rule-bending section below."""
    server, _repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    dm = _join(qtbot, server, "gm", "run-the-game")
    offered: list[dict] = []
    asked: list[dict] = []
    player.action_proposed.connect(offered.append)
    dm.bend_requested.connect(asked.append)
    try:
        # Brok is at -3,0 with a speed of six squares. 4,4 is seven away.
        _propose(agent, tokens, move=[4, 4], target=None)
        qtbot.waitUntil(lambda: bool(asked), timeout=5000)

        why = asked[0]["why"]
        assert "6 squares" in why and "30 feet" in why
        assert "7 away" in why, "it should say how far they actually asked for"
        assert offered == [], "a player must never be shown a turn the rules refuse"
    finally:
        agent.leave()
        player.leave()
        dm.leave()


def test_as_far_as_they_can_go_is_allowed(qtbot, fight):
    server, _repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        # Exactly six squares away, diagonally, which counts as six.
        _propose(agent, tokens, move=[3, 0], target=None)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)
        assert offered[0]["move"] == [3, 0]
    finally:
        agent.leave()
        player.leave()


def test_coming_onto_the_map_is_not_moving_across_it(qtbot, fight):
    """There is nowhere to measure from, and arriving is not running."""
    server, repos, _encounter, tokens, _hero, _goblin = fight
    repos.encounters.place(tokens["hero"].id, None, None)
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens, move=[4, 4], target=None)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)
    finally:
        agent.leave()
        player.leave()


def test_a_move_that_became_too_far_is_refused_on_the_way_out(qtbot, fight):
    """The map moves while a player is deciding.

    They were five squares from the destination when it was proposed and nine
    by the time they pressed Do it, because the DM dragged them. Checking only
    at proposal time would carry out a move the rules never allowed.
    """
    server, repos, _encounter, tokens, hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        # From -3,0 to -5,-5 is five squares: legal when it was proposed.
        _propose(agent, tokens, move=[-5, -5], target=None)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)

        # The DM hauls them to the far corner while they are reading it, which
        # puts the destination nine squares away.
        repos.encounters.place(tokens["hero"].id, 4, 4)

        player.send_answer(offered[0]["id"], True)
        qtbot.wait(400)
        assert repos.encounters.combatant(tokens["hero"].id).x == 4, (
            "the move was carried out from a square they were no longer on"
        )
    finally:
        agent.leave()
        player.leave()


# --------------------------------------------------------- the agent rolling
#
# It could move a goblin, and could talk about it hitting somebody, and had no
# way to actually swing -- so it narrated outcomes instead of asking for them,
# which is the one thing it is told never to do.


def test_the_agent_can_roll_an_attack(qtbot, fight):
    server, repos, _encounter, tokens, _hero, goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        # Next to each other, and it is the goblin's turn to swing.
        repos.encounters.place(tokens["goblin"].id, -2, 0)
        server.repos.encounters.begin(_encounter.id)
        # The DM's path: autopilot may not pass a person's turn, and this
        # is only moving the turn onto the goblin to set the scene.
        server.run_turn("next")
        qtbot.waitUntil(
            lambda: repos.encounters.get(_encounter.id).turn_combatant_id
            == tokens["goblin"].id,
            timeout=5000,
        )

        agent._send(
            MessageType.SWING,
            combatant=tokens["goblin"].id,
            target=tokens["hero"].id,
            weapon="scimitar",
        )
        qtbot.waitUntil(
            lambda: any("attacks Brok" in m.get("text", "") for m in server.history()),
            timeout=5000,
        )
    finally:
        agent.leave()


def test_a_swing_uses_up_the_action(qtbot, fight):
    server, repos, encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        repos.encounters.place(tokens["goblin"].id, -2, 0)
        agent._send(
            MessageType.SWING,
            combatant=tokens["hero"].id,
            target=tokens["goblin"].id,
            weapon="battleaxe",
        )
        qtbot.waitUntil(
            lambda: repos.encounters.get(encounter.id).action_used, timeout=5000
        )
    finally:
        agent.leave()


def test_a_weapon_they_do_not_have_is_refused_not_bent(qtbot, fight):
    """Not on the sheet is not a rule anybody waives."""
    server, repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    dm = _join(qtbot, server, "gm", "run-the-game")
    asked: list[dict] = []
    dm.bend_requested.connect(asked.append)
    try:
        repos.encounters.place(tokens["goblin"].id, -2, 0)
        with qtbot.waitSignal(agent.failed, timeout=5000):
            agent._send(
                MessageType.SWING,
                combatant=tokens["hero"].id,
                target=tokens["goblin"].id,
                weapon="trebuchet",
            )
        qtbot.wait(200)
        assert asked == []
    finally:
        agent.leave()
        dm.leave()


def test_swinging_from_too_far_is_put_to_the_dm(qtbot, fight):
    """"Just let him reach" is a thing a DM says."""
    server, _repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    dm = _join(qtbot, server, "gm", "run-the-game")
    asked: list[dict] = []
    dm.bend_requested.connect(asked.append)
    try:
        # Brok is at -3,0 and the goblin at 1,0: four squares, with an axe.
        agent._send(
            MessageType.SWING,
            combatant=tokens["hero"].id,
            target=tokens["goblin"].id,
            weapon="battleaxe",
        )
        qtbot.waitUntil(lambda: bool(asked), timeout=5000)
        assert "too far" in asked[0]["why"]
    finally:
        agent.leave()
        dm.leave()


def test_a_player_cannot_swing_for_themselves(qtbot, fight):
    server, _repos, _encounter, tokens, _hero, _goblin = fight
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(player.failed, timeout=5000):
            player._send(
                MessageType.SWING,
                combatant=tokens["hero"].id,
                target=tokens["goblin"].id,
                weapon="battleaxe",
            )
    finally:
        player.leave()


# ------------------------------------------------------------- the empty chair


def test_a_player_can_hand_their_character_over(qtbot, fight):
    server, repos, _encounter, tokens, _hero, _goblin = fight
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        player.send_simulate(tokens["hero"].id, True)
        qtbot.waitUntil(
            lambda: repos.encounters.combatant(tokens["hero"].id).simulated,
            timeout=5000,
        )
        # Named rather than "autopilot": there is one stand-in per character,
        # and a table with three of them cannot tell them apart by a word they
        # all share.
        told = [m.get("text", "") for m in server.history()]
        assert any(
            "is playing Brok" in line for line in told
        ), "the table has to be told a machine is playing somebody"
        assert any(
            server.stand_in_name(_hero.id) in line for line in told
        ), "and told which stand-in it is"

        player.send_simulate(tokens["hero"].id, False)
        qtbot.waitUntil(
            lambda: not repos.encounters.combatant(tokens["hero"].id).simulated,
            timeout=5000,
        )
    finally:
        player.leave()


def test_a_handed_over_turn_ends_itself(qtbot, fight, monkeypatch):
    """The bug this was reported for.

    Nothing ever ended a turn except a person pressing Done, so a character
    handed to autopilot took its turn and then held the whole table.
    """
    import canon_keeper.net.server as server_module

    monkeypatch.setattr(server_module, "MACHINE_TURN_MS", 300)

    server, repos, encounter, tokens, _hero, _goblin = fight
    repos.encounters.set_simulated(tokens["hero"].id, True)
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        assert repos.encounters.get(encounter.id).turn_combatant_id == tokens["hero"].id

        agent.send_move(tokens["hero"].id, -2, 0)
        qtbot.waitUntil(
            lambda: repos.encounters.get(encounter.id).turn_combatant_id
            == tokens["goblin"].id,
            timeout=5000,
        )
    finally:
        agent.leave()


def test_a_monsters_turn_ends_itself_too(qtbot, fight, monkeypatch):
    """Same hole, and it was there before anybody could be handed over."""
    import canon_keeper.net.server as server_module

    monkeypatch.setattr(server_module, "MACHINE_TURN_MS", 300)

    server, repos, encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        # The DM's path: autopilot may not pass a person's turn, and this
        # is only moving the turn onto the goblin to set the scene.
        server.run_turn("next")
        qtbot.waitUntil(
            lambda: repos.encounters.get(encounter.id).turn_combatant_id
            == tokens["goblin"].id,
            timeout=5000,
        )
        agent.send_move(tokens["goblin"].id, 2, 0)

        qtbot.waitUntil(
            lambda: repos.encounters.get(encounter.id).round == 2, timeout=5000
        )
    finally:
        agent.leave()


def test_a_second_action_does_not_split_the_turn(qtbot, fight, monkeypatch):
    """Move then swing is one turn, not two turns with a gap in the middle."""
    import canon_keeper.net.server as server_module

    monkeypatch.setattr(server_module, "MACHINE_TURN_MS", 600)

    server, repos, encounter, tokens, _hero, _goblin = fight
    repos.encounters.set_simulated(tokens["hero"].id, True)
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        # Up next to the goblin at 1,0, so the axe actually reaches.
        agent.send_move(tokens["hero"].id, 0, 0)
        qtbot.wait(400)
        agent._send(
            MessageType.SWING,
            combatant=tokens["hero"].id,
            target=tokens["goblin"].id,
            weapon="battleaxe",
        )
        qtbot.waitUntil(
            lambda: repos.encounters.get(encounter.id).action_used, timeout=5000
        )
        assert (
            repos.encounters.get(encounter.id).turn_combatant_id == tokens["hero"].id
        ), "the turn passed while it was still acting"
    finally:
        agent.leave()


def test_a_person_still_gets_the_long_clock(qtbot, fight, monkeypatch):
    """The short clock is for machines. A person is asked, and gets longer."""
    import canon_keeper.net.server as server_module

    monkeypatch.setattr(server_module, "MACHINE_TURN_MS", 200)

    server, repos, encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    asked: list[tuple] = []
    player.action_proposed.connect(offered.append)
    player.still_your_turn.connect(lambda on, secs: asked.append((on, secs)))
    try:
        _propose(agent, tokens, target=None)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)
        player.send_answer(offered[0]["id"], True)

        qtbot.waitUntil(lambda: bool(asked), timeout=5000)
        assert asked[0] == (True, 30)
        qtbot.wait(500)
        assert (
            repos.encounters.get(encounter.id).turn_combatant_id == tokens["hero"].id
        ), "a person was cut off by the machine clock"
    finally:
        agent.leave()
        player.leave()


def test_nobody_hands_over_somebody_elses_character(qtbot, fight):
    server, repos, _encounter, tokens, _hero, goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        # Not even the agent, which otherwise has a DM's view: it does not get
        # to decide which characters it plays.
        with qtbot.waitSignal(agent.failed, timeout=5000):
            agent._send(MessageType.SIMULATE, combatant=tokens["hero"].id, on=True)
        assert not repos.encounters.combatant(tokens["hero"].id).simulated
    finally:
        agent.leave()


def test_being_played_by_a_machine_is_on_the_wire(qtbot, fight):
    """The same rule the roster follows: a table deserves to know."""
    server, repos, _encounter, tokens, _hero, _goblin = fight
    repos.encounters.set_simulated(tokens["hero"].id, True)
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        qtbot.waitUntil(lambda: player.state.encounter is not None, timeout=5000)
        mine = [
            c for c in player.state.encounter["combatants"]
            if c["id"] == tokens["hero"].id
        ]
        assert mine and mine[0]["simulated"] is True
    finally:
        player.leave()


# ------------------------------------------------------------- picking things up


def test_something_found_goes_onto_the_sheet(qtbot, fight):
    """Describing the find is not recording it."""
    server, repos, _encounter, _tokens, hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        agent._send(MessageType.GIVE, entity=hero.id, item="a longsword")
        qtbot.waitUntil(
            lambda: "a longsword" in (repos.entities.get(hero.id).data.get("inventory") or ""),
            timeout=5000,
        )
        assert any(
            "Brok picks up: a longsword" in m.get("text", "")
            for m in server.history()
        )
    finally:
        agent.leave()


def test_a_second_thing_does_not_replace_the_first(qtbot, fight):
    server, repos, _encounter, _tokens, hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    try:
        for item in ("a longsword", "3 torches"):
            agent._send(MessageType.GIVE, entity=hero.id, item=item)
            qtbot.waitUntil(
                lambda i=item: i in (repos.entities.get(hero.id).data.get("inventory") or ""),
                timeout=5000,
            )
        held = repos.entities.get(hero.id).data["inventory"]
        assert held.splitlines() == ["a longsword", "3 torches"]
    finally:
        agent.leave()


def test_what_they_were_already_carrying_is_kept(qtbot, fight, repos):
    server, _repos, _encounter, _tokens, hero, _goblin = fight
    entity = repos.entities.get(hero.id)
    entity.data = dict(entity.data or {}, inventory="a rope")
    repos.entities.update(entity)

    server.give(repos.entities.get(hero.id), "a lantern")

    assert repos.entities.get(hero.id).data["inventory"] == "a rope\na lantern"


def test_a_player_cannot_give_themselves_things(qtbot, fight):
    """Otherwise inventory is a wish list."""
    server, repos, _encounter, _tokens, hero, _goblin = fight
    player = _join(qtbot, server, "marco", "goblin-teeth")
    try:
        with qtbot.waitSignal(player.failed, timeout=5000):
            player._send(MessageType.GIVE, entity=hero.id, item="a +5 holy avenger")
        assert not repos.entities.get(hero.id).data.get("inventory")
    finally:
        player.leave()


# ---------------------------------------------------------- bending the rules
#
# A DM can always overrule the rules; that is most of what being a DM is. So a
# rule the agent runs into is not a flat no, and is certainly not the machine
# quietly doing it anyway. It goes back to the DM.


def test_a_rule_the_agent_hits_is_put_to_the_dm(qtbot, fight):
    server, repos, _encounter, tokens, _hero, goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    dm = _join(qtbot, server, "gm", "run-the-game")
    asked: list[dict] = []
    dm.bend_requested.connect(asked.append)
    try:
        # Brok is up, so moving the goblin at all is out of turn.
        agent.send_move(tokens["goblin"].id, -7, 3)
        qtbot.waitUntil(lambda: bool(asked), timeout=5000)

        assert "Yeemik" in asked[0]["what"]
        assert "not Yeemik's turn" in asked[0]["why"]
        assert repos.encounters.combatant(tokens["goblin"].id).x == 1, (
            "nothing may happen until the DM answers"
        )
    finally:
        agent.leave()
        dm.leave()


def test_going_too_far_is_put_to_the_dm_as_well(qtbot, fight):
    """The other rule a DM might waive: "just let him get there"."""
    server, repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    dm = _join(qtbot, server, "gm", "run-the-game")
    asked: list[dict] = []
    dm.bend_requested.connect(asked.append)
    try:
        # It *is* Brok's turn, so the only rule left is the speed.
        agent.send_move(tokens["hero"].id, 5, 5)
        qtbot.waitUntil(lambda: bool(asked), timeout=5000)

        assert "6 squares" in asked[0]["why"]
        assert "8 away" in asked[0]["why"]
        assert repos.encounters.combatant(tokens["hero"].id).x == -3
    finally:
        agent.leave()
        dm.leave()


def test_allowing_it_carries_it_out(qtbot, fight):
    server, repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    dm = _join(qtbot, server, "gm", "run-the-game")
    asked: list[dict] = []
    dm.bend_requested.connect(asked.append)
    try:
        agent.send_move(tokens["goblin"].id, -7, 3)
        qtbot.waitUntil(lambda: bool(asked), timeout=5000)

        dm.send_allow(asked[0]["id"], True)
        qtbot.waitUntil(
            lambda: repos.encounters.combatant(tokens["goblin"].id).x == -7,
            timeout=5000,
        )
        assert any(
            "DM allows it" in m.get("text", "") for m in server.history()
        ), "the table should know a rule was waived"
    finally:
        agent.leave()
        dm.leave()


def test_saying_no_leaves_it_alone(qtbot, fight):
    server, repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    dm = _join(qtbot, server, "gm", "run-the-game")
    asked: list[dict] = []
    dm.bend_requested.connect(asked.append)
    try:
        agent.send_move(tokens["goblin"].id, -7, 3)
        qtbot.waitUntil(lambda: bool(asked), timeout=5000)

        dm.send_allow(asked[0]["id"], False)
        qtbot.wait(400)
        assert repos.encounters.combatant(tokens["goblin"].id).x == 1
    finally:
        agent.leave()
        dm.leave()


def test_the_agent_cannot_bless_its_own_request(qtbot, fight):
    """It holds a DM-shaped view. It is not the DM, and this is the difference."""
    server, repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    dm = _join(qtbot, server, "gm", "run-the-game")
    asked: list[dict] = []
    dm.bend_requested.connect(asked.append)
    try:
        agent.send_move(tokens["goblin"].id, -7, 3)
        qtbot.waitUntil(lambda: bool(asked), timeout=5000)

        with qtbot.waitSignal(agent.failed, timeout=5000):
            agent.send_allow(asked[0]["id"], True)
        assert repos.encounters.combatant(tokens["goblin"].id).x == 1
    finally:
        agent.leave()
        dm.leave()


def test_a_square_that_does_not_exist_is_not_a_rule(qtbot, fight):
    """Off the map is not a rule the DM can waive. There is no such square."""
    server, _repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    dm = _join(qtbot, server, "gm", "run-the-game")
    asked: list[dict] = []
    dm.bend_requested.connect(asked.append)
    try:
        with qtbot.waitSignal(agent.failed, timeout=5000):
            agent.send_move(tokens["goblin"].id, 99, 99)
        qtbot.wait(200)
        assert asked == [], "the DM was asked to waive arithmetic"
    finally:
        agent.leave()
        dm.leave()


def test_once_the_fight_is_running_even_the_dm_is_walking(qtbot, fight):
    """There is no teleport left on a live battlefield, not for anybody.

    Arranging the board used to have no speed limit whoever did it, which
    made the DM's own drag the last way to put a creature anywhere at all --
    through walls, through bodies, for free, on nobody's turn. While a fight
    is running a move is a walk, and it is held to the same rules as one.
    """
    server, repos, _encounter, tokens, _hero, _goblin = fight
    dm = _join(qtbot, server, "gm", "run-the-game")
    asked: list[dict] = []
    dm.bend_requested.connect(asked.append)
    try:
        with qtbot.waitSignal(dm.failed, timeout=5000):
            dm.send_move(tokens["goblin"].id, -7, 3)

        assert repos.encounters.combatant(tokens["goblin"].id).x == 1
        assert asked == [], "the DM does not ask themselves for a ruling"
    finally:
        dm.leave()



def test_a_waived_turn_is_not_refused_again_on_the_way_out(qtbot, fight):
    """The player still confirms it -- and the rule must not bite a second time."""
    server, repos, _encounter, tokens, _hero, _goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    dm = _join(qtbot, server, "gm", "run-the-game")
    asked: list[dict] = []
    offered: list[dict] = []
    dm.bend_requested.connect(asked.append)
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens, move=[4, 4], target=None)
        qtbot.waitUntil(lambda: bool(asked), timeout=5000)

        dm.send_allow(asked[0]["id"], True)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)
        assert offered[0]["move"] == [4, 4]

        player.send_answer(offered[0]["id"], True)
        qtbot.waitUntil(
            lambda: repos.encounters.combatant(tokens["hero"].id).x == 4, timeout=5000
        )
    finally:
        agent.leave()
        player.leave()
        dm.leave()


def test_swinging_from_across_the_room_is_refused(qtbot, fight):
    """Melee reaches one square. Accepting must not quietly teleport the axe."""
    server, repos, _encounter, tokens, _hero, goblin = fight
    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens, move=None, text="Attack Yeemik from here.")
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)

        player.send_answer(offered[0]["id"], True)
        qtbot.waitUntil(
            lambda: any("too far" in m.get("text", "") for m in server.history()),
            timeout=5000,
        )
        assert repos.entities.get(goblin.id).data["hp"] == 12
    finally:
        agent.leave()
        player.leave()


# ------------------------------------------------- whose character is this


def test_a_turn_is_offered_to_whoever_plays_the_character(qtbot, fight, repos):
    """Two facts have to agree, and they are written in different places.

    An account says which character it *plays*; an entity says who *owns* it.
    A character whose owner was never set looks, to the host, exactly like a
    monster: no accept bar for the player, and autopilot takes the turn. This
    is the case that broke it -- a campaign whose accounts arrived without the
    ownership half.
    """
    server, repos, encounter, tokens, hero, _goblin = fight
    marco = repos.accounts.by_username(server.campaign_id, "marco")
    # The half that goes missing.
    repos.entities.set_owner(hero.id, None)

    agent = _join(qtbot, server, "autopilot", "let-me-run-it")
    player = _join(qtbot, server, "marco", "goblin-teeth")
    offered: list[dict] = []
    player.action_proposed.connect(offered.append)
    try:
        _propose(agent, tokens, target=None)
        qtbot.waitUntil(lambda: bool(offered), timeout=5000)
        assert not offered[0].get("watching"), (
            "the player was sent the DM's copy, which cannot be answered"
        )
    finally:
        agent.leave()
        player.leave()


def test_a_character_with_a_player_is_not_played_by_the_machine(fight, repos):
    """The same drift, seen from the other side: their turn taken from them."""
    server, repos, _encounter, tokens, hero, _goblin = fight
    repos.entities.set_owner(hero.id, None)

    combatant = repos.encounters.combatant(tokens["hero"].id)
    assert server._machine_plays(combatant) is False
