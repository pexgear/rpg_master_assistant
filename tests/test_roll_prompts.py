"""Turning "make a DC 14 Perception check" into something you can click.

Two halves worth testing separately. Reading a line for the rolls it asks for is
plain text work with no Qt in it, and it is where the interesting failure lives:
being too eager. A panel that underlines the word "perception" in "his
perception of the situation" teaches people to ignore underlining, which costs
more than the prompts it catches.

The other half is that clicking one asks the *host* for a number. The die in the
overlay is animation over a real round trip; it never decides anything.
"""

from __future__ import annotations

import logging

import pytest
from PySide6.QtCore import QUrl

from canon_keeper.bus import Bus
from canon_keeper_core.content import Content
from canon_keeper.net.state import SharedState
from canon_keeper.panels.table import rolls
from canon_keeper.panels.table.agent_settings import TRANSLATE_SETTING
from canon_keeper.panels.table.dice_overlay import FRAMES, AsciiDie, RollDialog
from canon_keeper.panels.table.widget import TableWidget
from canon_keeper.plugin import AppContext
from canon_keeper_core.repo.entities import KIND_PC
from canon_keeper_protocol.messages import Member


# ------------------------------------------------------------------- reading


def _one(text: str) -> rolls.Prompt:
    found = rolls.find(text)
    assert len(found) == 1, f"{text!r} found {[p.text for p in found]}"
    return found[0]


def test_a_skill_check_is_found():
    prompt = _one("Everyone make a Perception check.")
    assert prompt.kind == rolls.SKILL
    assert prompt.key == "perception"
    assert prompt.text.lower() == "perception check"


def test_a_dc_in_front_is_part_of_it():
    prompt = _one("That is a DC 14 Stealth check.")
    assert prompt.dc == 14
    assert prompt.key == "stealth"
    assert "DC 14" in prompt.text


def test_a_dc_behind_counts_too():
    prompt = _one("Give me an Athletics check (DC 18).")
    assert prompt.dc == 18


def test_the_long_way_of_writing_it_is_one_prompt():
    """"Wisdom (Perception) check" is one roll, not three overlapping ones."""
    prompt = _one("Roll me a Wisdom (Perception) check.")
    assert prompt.kind == rolls.SKILL
    assert prompt.key == "perception"


def test_two_word_skills_survive():
    assert _one("An Animal Handling check, please.").key == "animal-handling"
    assert _one("Sleight of Hand check.").key == "sleight-of-hand"


def test_a_saving_throw_is_a_save_not_a_check():
    prompt = _one("DC 15 Dexterity saving throw.")
    assert prompt.kind == rolls.SAVE
    assert prompt.key == "dex"
    assert prompt.dc == 15


def test_the_short_way_of_writing_a_save_works():
    assert _one("Con save, everyone.").kind == rolls.SAVE


def test_a_bare_ability_check_is_found():
    prompt = _one("Strength check to shift it.")
    assert prompt.kind == rolls.ABILITY
    assert prompt.key == "str"


def test_initiative_is_its_own_thing():
    assert _one("Roll initiative.").kind == rolls.INITIATIVE


def test_plain_dice_are_found():
    prompt = _one("Take 2d6 + 3 from the falling rock.")
    assert prompt.kind == rolls.DICE
    assert prompt.notation == "2d6+3"


def test_a_bare_d20_gets_its_one():
    assert _one("Just roll d20.").notation == "1d20"


@pytest.mark.parametrize(
    "line",
    [
        "His perception of the situation was poor.",
        "She had the stealth of a falling wardrobe.",
        "The nature of the problem is obvious.",
        "That takes real strength of character.",
        "The investigation had stalled.",
    ],
)
def test_words_that_are_not_asking_for_a_roll(line):
    """The failure that matters. Underline everything and nothing is underlined."""
    assert rolls.find(line) == []


def test_two_prompts_in_one_line_are_both_found():
    found = rolls.find("A Perception check, then a DC 12 Dexterity saving throw.")
    assert [p.kind for p in found] == [rolls.SKILL, rolls.SAVE]
    assert found[0].start < found[1].start


def test_prompts_never_overlap():
    for line in (
        "DC 14 Wisdom (Perception) check",
        "roll initiative, then a d20",
        "Strength (Athletics) check, DC 15",
    ):
        found = rolls.find(line)
        for earlier, later in zip(found, found[1:]):
            assert earlier.end <= later.start, line


def test_the_label_says_the_dc():
    assert "DC 14" in _one("DC 14 Perception check").label


# -------------------------------------------------------------- the arithmetic


@pytest.fixture
def content(repos) -> Content:
    return Content(repos.settings)


def _sheet() -> dict:
    return {
        "schema": 1,
        "species": "Halfling",
        "class_index": "rogue",
        "level": 4,
        "abilities": {"str": 8, "dex": 18, "con": 12, "int": 12, "wis": 14, "cha": 14},
        "skill_proficiencies": ["stealth"],
    }


def test_a_proficient_skill_adds_proficiency(content):
    stealth = rolls.bonus_for(_one("Stealth check"), _sheet(), content)
    perception = rolls.bonus_for(_one("Perception check"), _sheet(), content)
    assert stealth > perception, "proficiency has to count for something"


def test_an_ability_check_is_just_the_modifier(content):
    assert rolls.bonus_for(_one("Dexterity check"), _sheet(), content) == 4


def test_no_sheet_means_a_plain_d20(content):
    assert rolls.bonus_for(_one("Perception check"), {}, content) == 0
    assert rolls.notation_for(_one("Perception check"), 0) == "1d20"


def test_a_nonsense_sheet_does_not_raise(content):
    """A broken sheet costs you the bonus, not the evening."""
    assert rolls.bonus_for(_one("Perception check"), {"level": "yes"}, content) == 0


def test_plain_dice_keep_their_own_modifier(content):
    """"2d6+3" was written by someone who meant it."""
    prompt = _one("2d6+3 fire damage")
    assert rolls.notation_for(prompt, 5) == "2d6+3"


def test_a_bonus_goes_on_the_notation():
    assert rolls.notation_for(_one("Perception check"), 5) == "1d20+5"
    assert rolls.notation_for(_one("Perception check"), -1) == "1d20-1"


# ----------------------------------------------------------------- the overlay


def test_the_die_frames_all_take_a_face():
    """Every frame has to be able to show a number, or it flickers blank."""
    for frame in FRAMES:
        assert "{face" in frame
        assert frame.format(face="20")


def test_a_frame_keeps_its_shape_whatever_the_number():
    """Otherwise the die visibly jitters as it rolls through 7, 18 and 20."""
    for index, frame in enumerate(FRAMES):
        shapes = {
            tuple(len(line) for line in frame.format(face=face).splitlines())
            for face in ("1", "7", "18", "20")
        }
        assert len(shapes) == 1, f"frame {index} changes width with the number"


def test_the_die_is_symmetrical():
    """A lopsided die reads as a rendering bug, which is what it would be."""
    for index, frame in enumerate(FRAMES):
        for line in frame.format(face="17").splitlines():
            if not line.strip():
                continue
            body = line.rstrip()
            left = len(body) - len(body.lstrip())
            assert left + len(body.strip()) == len(body)
            # The widest line and the top edge share a centre.
            assert abs(
                (left + len(body)) / 2 - _centre(frame)
            ) <= 1.5, f"frame {index} is lopsided: {line!r}"


def _centre(frame: str) -> float:
    widest = max(
        (line.rstrip() for line in frame.format(face="17").splitlines()),
        key=len,
    )
    left = len(widest) - len(widest.lstrip())
    return (left + len(widest)) / 2


def test_the_die_starts_still(qtbot):
    die = AsciiDie()
    qtbot.addWidget(die)
    assert not die.is_tumbling


def test_it_tumbles_until_it_is_told_what_was_rolled(qtbot):
    die = AsciiDie()
    qtbot.addWidget(die)
    die.tumble()
    assert die.is_tumbling
    die.settle(17)
    assert not die.is_tumbling
    assert "17" in die.text()


def test_the_overlay_asks_the_host_rather_than_rolling(qtbot):
    """The whole design in one test: pressing Roll sends, it does not decide."""
    dialog = RollDialog("Perception check", "1d20+5", dc=14)
    qtbot.addWidget(dialog)
    asked: list[str] = []
    dialog.roll_requested.connect(asked.append)

    dialog._roll_button.click()

    assert asked == ["1d20+5"]
    assert dialog._die.is_tumbling, "it should be waiting for the host, not done"


def test_the_hosts_answer_stops_the_die(qtbot):
    dialog = RollDialog("Perception check", "1d20+5", dc=14)
    qtbot.addWidget(dialog)
    dialog._roll_button.click()

    dialog.settle({"rolls": [17], "total": 22, "description": "1d20+5 = [17] +5 = 22"})

    assert not dialog._die.is_tumbling
    assert "17" in dialog._die.text(), "the die shows the natural roll"
    assert "beats DC 14" in dialog._detail.text()


def test_missing_the_dc_is_said_plainly(qtbot):
    dialog = RollDialog("Perception check", "1d20+5", dc=20)
    qtbot.addWidget(dialog)
    dialog._roll_button.click()
    dialog.settle({"rolls": [3], "total": 8, "description": "1d20+5 = [3] +5 = 8"})
    assert "DC 20" in dialog._detail.text()
    assert "beats" not in dialog._detail.text()


def test_no_answer_leaves_no_number(qtbot):
    """It must never settle on something this process invented."""
    dialog = RollDialog("Perception check", "1d20")
    qtbot.addWidget(dialog)
    dialog._roll_button.click()
    dialog._gave_up()
    assert "?" in dialog._die.text()
    assert "nothing was rolled" in dialog._detail.text()


# ------------------------------------------------------------- in the chat log


@pytest.fixture
def player_table(qtbot, repos):
    """A player's Table panel, with a character to roll with."""
    campaign = repos.campaigns.ensure_default("Rolling")
    shared = SharedState()
    ctx = AppContext(
        repos=repos,
        bus=Bus(),
        log=logging.getLogger("canonkeeper.test"),
        campaign_id=campaign.id,
        role="player",
        shared=shared,
    )
    widget = TableWidget(ctx)
    qtbot.addWidget(widget)
    return widget, shared


def _mine(**data) -> dict:
    return {
        "id": 1,
        "kind": KIND_PC,
        "name": "Sable",
        "summary": "",
        "data": {"sheet": _sheet(), **data},
        "own": True,
    }


def test_the_dms_words_become_links(player_table):
    widget, shared = player_table
    shared.replace_all([_mine()])
    widget._append("dm", "The DM: give me a DC 14 Perception check.")
    assert "roll:" in widget._log.toHtml()


def test_a_player_with_no_character_gets_no_links(player_table):
    """Nothing to roll with, so nothing to click."""
    widget, _shared = player_table
    widget._append("dm", "The DM: give me a DC 14 Perception check.")
    assert "roll:" not in widget._log.toHtml()


def test_the_agent_asking_counts_as_the_dm_asking(player_table):
    widget, shared = player_table
    shared.replace_all([_mine()])
    widget._append("agent", "Autopilot: roll a Dexterity saving throw.")
    assert "roll:" in widget._log.toHtml()


def test_another_player_saying_it_does_not(player_table):
    """Someone typing "stealth check" in character is not calling for one."""
    widget, shared = player_table
    shared.replace_all([_mine()])
    widget._append("player", "Marco: I'll make a Stealth check then.")
    assert "roll:" not in widget._log.toHtml()


def test_the_character_arriving_late_still_gets_the_links(player_table):
    """Chat arrives before the snapshot does. The log has to catch up."""
    widget, shared = player_table
    widget._append("dm", "The DM: make a Perception check.")
    assert "roll:" not in widget._log.toHtml()

    shared.replace_all([_mine()])

    assert "roll:" in widget._log.toHtml()


def test_hiding_a_line_takes_its_link_with_it(player_table):
    widget, shared = player_table
    shared.replace_all([_mine()])
    widget._append("dm", "The DM: Perception check.")
    before = len(widget._roll_prompts)
    widget._redraw()
    assert len(widget._roll_prompts) == before, "redrawing must not double them up"


# --------------------------------------------------- offering a line as a turn
#
# The same mechanism as a roll prompt -- a mark in the log with an anchor on it
# -- and the same danger. A mark drawn where clicking it comes back refused
# teaches people to stop looking at marks.


@pytest.fixture
def dm_table(qtbot, repos):
    """A DM's Table panel, mid-fight, with the offer turned on."""
    campaign = repos.campaigns.ensure_default("Translating")
    repos.settings.set(TRANSLATE_SETTING, "on")
    shared = SharedState()
    ctx = AppContext(
        repos=repos,
        bus=Bus(),
        log=logging.getLogger("canonkeeper.test"),
        campaign_id=campaign.id,
        role="dm",
        shared=shared,
    )
    widget = TableWidget(ctx)
    qtbot.addWidget(widget)
    shared.replace_all(
        [{"id": 7, "kind": "pc", "name": "Brok", "summary": "", "data": {}}]
    )
    widget._client._me = Member(id="dm1", name="The DM", role="dm")
    widget._client._members = [
        Member(id="dm1", name="The DM", role="dm"),
        Member(id="p1", name="Marco", role="player", character="Brok"),
    ]
    widget._on_encounter_received(
        {
            "running": True,
            "turn": 3,
            "combatants": [{"id": 3, "entity": 7}],
        }
    )
    return widget, ctx


def test_a_line_from_whoever_is_up_can_be_asked_about(dm_table):
    widget, _ctx = dm_table
    widget._append("player", "Brok: I get behind the orc and hit it", said_by="p1")
    assert "translate:" in widget._log.toHtml()


def test_a_line_from_somebody_else_is_not_marked(dm_table):
    """Their turn is not up, so asking would only come back refused."""
    widget, _ctx = dm_table
    widget._client._members.append(
        Member(id="p2", name="Ada", role="player", character="Ada")
    )
    widget._append("player", "Ada: I cover the door", said_by="p2")
    assert "translate:" not in widget._log.toHtml()


def test_nothing_is_offered_outside_a_fight(dm_table):
    """"Only in combat" is the whole of when a sentence is a turn."""
    widget, _ctx = dm_table
    widget._on_encounter_received({})
    widget._append("player", "Brok: I hit the orc", said_by="p1")
    assert "translate:" not in widget._log.toHtml()


def test_nothing_is_offered_until_the_dm_turns_it_on(dm_table):
    widget, ctx = dm_table
    ctx.repos.settings.set(TRANSLATE_SETTING, "off")
    widget._append("player", "Brok: I hit the orc", said_by="p1")
    assert "translate:" not in widget._log.toHtml()


def test_a_player_never_sees_the_offer(qtbot, repos):
    """It spends the DM's money and asks in the DM's name."""
    campaign = repos.campaigns.ensure_default("Translating")
    repos.settings.set(TRANSLATE_SETTING, "on")
    shared = SharedState()
    ctx = AppContext(
        repos=repos,
        bus=Bus(),
        log=logging.getLogger("canonkeeper.test"),
        campaign_id=campaign.id,
        role="player",
        shared=shared,
    )
    widget = TableWidget(ctx)
    qtbot.addWidget(widget)
    shared.replace_all(
        [{"id": 7, "kind": "pc", "name": "Brok", "summary": "", "data": {}}]
    )
    widget._client._me = Member(id="p1", name="Marco", role="player", character="Brok")
    widget._client._members = [widget._client._me]
    widget._on_encounter_received(
        {"running": True, "turn": 3, "combatants": [{"id": 3, "entity": 7}]}
    )
    widget._append("player", "Brok: I hit the orc", said_by="p1")
    assert "translate:" not in widget._log.toHtml()


def test_clicking_it_sends_the_words_and_not_the_name(dm_table):
    """The agent is being asked what somebody meant.

    The "Brok: " in front is the log's own doing, and sending it back would be
    handing the model a name it already has as though it were part of the
    sentence.
    """
    widget, _ctx = dm_table
    sent: list[tuple[str, str]] = []
    widget._client.send_translate = lambda member, text: (
        sent.append((member, text)) or True
    )
    widget._append("player", "Brok: I get behind the orc and hit it", said_by="p1")

    widget._on_anchor(QUrl("translate:1"))

    assert sent == [("p1", "I get behind the orc and hit it")]


def test_redrawing_does_not_double_up_the_offers(dm_table):
    widget, _ctx = dm_table
    widget._append("player", "Brok: I hit the orc", said_by="p1")
    before = len(widget._translations)
    widget._redraw()
    assert len(widget._translations) == before
