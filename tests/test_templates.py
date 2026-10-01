"""Campaigns that start the same way every time.

The claim worth testing is the whole feature: run a template twice and get the
same evening twice. Everything else -- the storyline, the shares, the logins --
is only useful because of that.
"""

from __future__ import annotations

import json

import pytest

from canon_keeper_core.db import connect, migrate
from canon_keeper_core.repo import Repos
from canon_keeper_core.repo.entities import KIND_PC
from canon_keeper.templates import (
    PROGRESS_SETTING,
    SOURCE_SETTING,
    STORYLINE_SETTING,
    Template,
    TemplateError,
    available,
    get,
    load,
)
from canon_keeper.templates import build
from canon_keeper_protocol import auth


@pytest.fixture
def blank(tmp_path):
    """An empty campaign to fill."""
    conn = connect(tmp_path / "one.sqlite3")
    migrate(conn)
    repos = Repos(conn)
    campaign = repos.campaigns.ensure_default("Blank")
    yield repos, campaign.id
    conn.close()


def _second(tmp_path, name="two.sqlite3"):
    conn = connect(tmp_path / name)
    migrate(conn)
    repos = Repos(conn)
    campaign = repos.campaigns.ensure_default("Blank")
    return conn, repos, campaign.id


def _snapshot(repos, campaign_id: int) -> dict:
    """Everything that should be identical between two runs."""
    return {
        "entities": [
            (e.kind, e.name, e.summary, json.dumps(e.data, sort_keys=True))
            for e in repos.entities.list(campaign_id)
        ],
        "facts": [
            (f.predicate, f.object) for f in repos.facts.current(campaign_id)
        ],
        "accounts": [
            (a.username, a.role, a.display_name)
            for a in repos.accounts.list(campaign_id)
        ],
        "storyline": repos.settings.get(STORYLINE_SETTING),
        # The fight, if it opens on one. Initiative and squares are stated by
        # the template rather than rolled, so two runs lay out identically --
        # and if that ever stops being true, this is what says so.
        "fight": _fight(repos, campaign_id),
    }


def _fight(repos, campaign_id: int):
    encounter = repos.encounters.current(campaign_id)
    if encounter is None:
        return None
    return (
        encounter.name,
        encounter.width,
        encounter.height,
        encounter.round,
        [
            (c.initiative, c.x, c.y, repos.entities.get(c.entity_id).name)
            for c in repos.encounters.combatants(encounter.id)
        ],
        sorted(repos.encounters.obstacles(encounter.id)),
    )


# ------------------------------------------------------------------- the files


def test_the_bundled_templates_load():
    assert available(), "no templates ship, which is a packaging failure"


def test_each_one_says_what_it_is():
    for template in available():
        assert template.name
        assert template.summary, f"{template.id} has no line for the chooser"


def test_each_one_knows_where_it_ends():
    """A one-shot that never says where it stops is a campaign."""
    for template in available():
        assert template.ending is not None, f"{template.id} has no ending beat"


def test_only_one_beat_ends_it():
    for template in available():
        endings = [beat for beat in template.storyline if beat.ends_it]
        assert len(endings) == 1, f"{template.id} has {len(endings)} endings"


def test_beat_ids_are_unique():
    for template in available():
        ids = [beat.id for beat in template.storyline]
        assert len(ids) == len(set(ids)), f"{template.id} repeats a beat id"


def test_every_played_character_exists():
    """A login pointing at nothing is a player with no character."""
    for template in available():
        keys = {str(e.get("key") or e.get("name")) for e in template.entities}
        for account in template.accounts:
            plays = account.get("plays")
            if plays:
                assert plays in keys, f"{template.id}: {plays} is not an entity"


def test_every_share_and_parent_points_somewhere():
    for template in available():
        keys = {str(e.get("key") or e.get("name")) for e in template.entities}
        for share in template.shares:
            assert share.get("entity") in keys, f"{template.id}: bad share"
        for entity in template.entities:
            parent = entity.get("parent")
            if parent:
                assert parent in keys, f"{template.id}: bad parent {parent}"


def test_every_combatant_points_somewhere():
    """A fight naming an entity the template does not contain is empty air."""
    for template in available():
        keys = {str(e.get("key") or e.get("name")) for e in template.entities}
        for combatant in template.encounter.get("combatants") or []:
            assert combatant.get("entity") in keys, (
                f"{template.id}: a combatant names {combatant.get('entity')!r}, "
                "which is not in the template"
            )


def test_a_fight_states_its_initiative_rather_than_rolling_it():
    """The determinism rule, at the one place it is easiest to break."""
    for template in available():
        for combatant in template.encounter.get("combatants") or []:
            assert isinstance(combatant.get("initiative"), int), (
                f"{template.id}: a combatant has no initiative, so the order "
                "would depend on when it was started"
            )


def test_nobody_shares_a_square():
    for template in available():
        squares = [
            (c.get("x"), c.get("y"))
            for c in template.encounter.get("combatants") or []
            if c.get("x") is not None
        ]
        assert len(squares) == len(set(squares)), f"{template.id}: two on one square"


def test_nobody_starts_standing_in_a_rock():
    for template in available():
        blocked = {
            tuple(square) for square in template.encounter.get("obstacles") or []
        }
        for combatant in template.encounter.get("combatants") or []:
            square = (combatant.get("x"), combatant.get("y"))
            assert square not in blocked, (
                f"{template.id}: {combatant.get('entity')} starts inside an obstacle"
            )


def test_everyone_in_a_fight_is_on_its_grid():
    """0,0 is the middle, so a sixteen-wide map runs from -8 to 7."""
    from canon_keeper_protocol import grid

    for template in available():
        fight = template.encounter
        if not fight:
            continue
        width, height = int(fight.get("width") or 20), int(fight.get("height") or 15)
        squares = [
            (c.get("x"), c.get("y"), c.get("entity"))
            for c in fight.get("combatants") or []
        ]
        squares += [(x, y, "an obstacle") for x, y in fight.get("obstacles") or []]
        for x, y, what in squares:
            if x is None or y is None:
                continue
            assert grid.holds(width, height, x, y), (
                f"{template.id}: {what} is at {x},{y}, outside a "
                f"{width}x{height} grid ({grid.bounds(width, height)})"
            )


# ------------------------------------------------------------------ the sheets
#
# A template's characters are the first sheets anybody sees, and for a test
# fixture they are the *only* ones. A sheet that does not validate is worse than
# no sheet: the host refuses the player's first edit as illegal, and the reason
# is a template nobody has looked at since it was written.


def _sheets(kind: str):
    """Every sheet of one kind across every bundled template."""
    for template in available():
        for entity in template.entities:
            if entity.get("kind") != kind:
                continue
            sheet = (entity.get("data") or {}).get("sheet")
            yield template.id, entity.get("key"), sheet


def test_every_character_has_a_sheet():
    for template_id, key, sheet in _sheets("pc"):
        assert sheet, f"{template_id}: {key} has no sheet"


def test_every_monster_has_a_statblock():
    """An NPC's sheet is its statblock, and is never sent to a player."""
    for template_id, key, sheet in _sheets("npc"):
        assert sheet, f"{template_id}: {key} has no sheet"


def test_every_sheet_is_one(repos):
    from canon_keeper_core.rules.sheet import is_sheet

    for kind in ("pc", "npc"):
        for template_id, key, sheet in _sheets(kind):
            assert is_sheet(sheet), f"{template_id}: {key} is not a sheet"


def test_every_sheet_is_legal(repos):
    """The same check the host runs on a player's first edit."""
    from canon_keeper_core.content import Content
    from canon_keeper_core.rules.validation import validate

    content = Content(repos.settings)
    for kind in ("pc", "npc"):
        for template_id, key, sheet in _sheets(kind):
            report = validate(sheet, content)
            assert report.ok, f"{template_id}: {key} -- {report.summary()}"


def test_characters_have_a_species_and_a_class():
    """What the sheet is *for*. A level-one nobody is not a character."""
    for template_id, key, sheet in _sheets("pc"):
        assert sheet.get("species"), f"{template_id}: {key} has no species"
        assert sheet.get("class_index"), f"{template_id}: {key} has no class"
        assert sheet.get("abilities"), f"{template_id}: {key} has no abilities"


def test_a_monsters_numbers_are_stated_rather_than_derived():
    """A goblin has no class, so nothing can work out its hit points or armour."""
    for template_id, key, sheet in _sheets("npc"):
        overrides = sheet.get("overrides") or {}
        assert "ac" in overrides, f"{template_id}: {key} has no armour class"
        assert "hp_max" in overrides, f"{template_id}: {key} has no hit points"


def test_the_two_hit_point_numbers_agree(repos):
    """``data.hp`` is what players are shown; the sheet is what the DM edits.

    They are two copies of one fact, so a template that disagrees with itself
    would show a player one number and the DM another.
    """
    from canon_keeper_core.content import Content
    from canon_keeper_core.rules import derive

    content = Content(repos.settings)
    for template in available():
        for entity in template.entities:
            data = entity.get("data") or {}
            sheet = data.get("sheet")
            if not sheet or "max_hp" not in data:
                continue
            _current, maximum = derive.hit_points(sheet, content)
            assert data["max_hp"] == maximum, (
                f"{template.id}: {entity.get('key')} says max_hp {data['max_hp']}, "
                f"but the sheet works out to {maximum}"
            )
            assert data.get("hp") == sheet.get("hp_current"), (
                f"{template.id}: {entity.get('key')} disagrees with its own sheet "
                "about current hit points"
            )


def test_a_broken_file_is_skipped_not_fatal(tmp_path):
    """One bad file must not stop the others being offered."""
    (tmp_path / "broken.json").write_text("{ not json", encoding="utf-8")
    (tmp_path / "fine.json").write_text(
        json.dumps({"id": "fine", "name": "Fine", "entities": [{"name": "A"}]}),
        encoding="utf-8",
    )

    found = available(tmp_path)

    assert [t.id for t in found] == ["fine"]


def test_an_empty_template_is_refused(tmp_path):
    path = tmp_path / "hollow.json"
    path.write_text(json.dumps({"id": "hollow", "name": "Hollow"}), encoding="utf-8")

    with pytest.raises(TemplateError):
        load(path)


# --------------------------------------------------------------- the same twice


def test_two_runs_produce_the_same_campaign(blank, tmp_path):
    """The whole feature."""
    repos_one, campaign_one = blank
    template = get("the-last-coach")
    build.populate(repos_one, campaign_one, template)

    conn, repos_two, campaign_two = _second(tmp_path)
    try:
        build.populate(repos_two, campaign_two, template)
        assert _snapshot(repos_one, campaign_one) == _snapshot(repos_two, campaign_two)
    finally:
        conn.close()


def test_no_template_brings_a_login(blank):
    """Not even the ones used for testing, and not even the DM's.

    A template file is public, so a password written in one is a published
    password: anybody who found a hosted one-shot could read the credentials
    out of the repository and log in as somebody's character, or as the DM.
    Everybody arrives by invitation now.
    """
    repos, campaign_id = blank
    for template in available():
        build.populate(repos, campaign_id, template)
        assert repos.accounts.list(campaign_id) == [], (
            f"{template.id} made a login"
        )


# ------------------------------------------------- no published passwords


def test_a_one_shot_people_play_ships_no_logins(blank):
    """A template file is public, so a password in one is a published password.

    Anybody who found a hosted one-shot could otherwise read the credentials
    out of the repository and log in as somebody's character -- or, worse, as
    the DM.
    """
    repos, campaign_id = blank
    template = get("the-last-coach")
    assert template.for_testing is False

    build.populate(repos, campaign_id, template)

    assert repos.accounts.list(campaign_id) == []


def test_a_one_shot_still_brings_its_characters(blank):
    """Empty seats, not an empty campaign. The characters are the point."""
    repos, campaign_id = blank
    build.populate(repos, campaign_id, get("the-last-coach"))

    played = repos.entities.list(campaign_id, kinds=(KIND_PC,))
    assert len(played) == 3


def test_the_chooser_counts_seats_not_logins():
    """It used to count accounts, which now says nought players for three."""
    assert get("the-last-coach").player_count == 3


def test_no_template_anybody_plays_states_a_password():
    """The guard in the builder, checked against the files as shipped.

    A future template that put a login back would be refused at build time --
    this is here so it is caught by whoever writes it rather than by whoever
    hosts it.
    """
    for template in available():
        if template.for_testing:
            continue
        assert template.accounts == [], (
            f"{template.id} ships logins. A template people play brings "
            "characters and invitations, not passwords."
        )


def test_even_a_login_written_into_one_is_ignored(blank):
    """The rule is enforced, not merely observed by the files that exist."""
    import dataclasses

    repos, campaign_id = blank
    template = dataclasses.replace(
        get("the-last-coach"),
        accounts=[
            {"username": "gm", "password": "published", "role": "dm"},
            {"username": "one", "password": "published", "plays": "wren"},
        ],
    )

    build.populate(repos, campaign_id, template)

    assert repos.accounts.list(campaign_id) == []


def test_nobody_owns_a_character_until_somebody_is_invited(blank):
    """Ownership follows the account, and there are no accounts yet.

    A character with an owner nobody can log in as would be a seat that looks
    taken and is not.
    """
    repos, campaign_id = blank
    build.populate(repos, campaign_id, get("test-table"))

    played = repos.entities.list(campaign_id, kinds=(KIND_PC,))
    assert played, "the template brought no characters"
    assert all(e.owner_account_id is None for e in played)


def test_places_are_nested(blank):
    """The cellar is in the inn, which is in the town."""
    repos, campaign_id = blank
    build.populate(repos, campaign_id, get("test-table"))

    by_name = {e.name: e for e in repos.entities.list(campaign_id)}
    cellar = by_name["The Cellar"]
    inn = by_name["The Stonehill Inn"]

    assert cellar.parent_id == inn.id
    assert inn.parent_id == by_name["Phandalin"].id


def test_a_parent_may_appear_later_in_the_file(tmp_path, blank):
    """Requiring authors to order the file correctly is a trap."""
    repos, campaign_id = blank
    template = Template(
        id="ordering",
        name="Ordering",
        entities=[
            {"key": "room", "kind": "location", "name": "Room", "parent": "house"},
            {"key": "house", "kind": "location", "name": "House"},
        ],
    )
    build.populate(repos, campaign_id, template)

    by_name = {e.name: e for e in repos.entities.list(campaign_id)}
    assert by_name["Room"].parent_id == by_name["House"].id


def test_the_party_starts_knowing_something(blank):
    """A one-shot that shares nothing opens with blank screens."""
    repos, campaign_id = blank
    build.populate(repos, campaign_id, get("the-last-coach"))

    assert repos.shares.shared_count(campaign_id) > 0


def test_a_one_shot_can_open_on_a_fight(blank):
    """Test Combat says it starts on initiative. It has to actually do that."""
    repos, campaign_id = blank
    build.populate(repos, campaign_id, get("test-combat"))

    encounter = repos.encounters.running(campaign_id)
    assert encounter is not None, "Test Combat opens with no fight running"
    assert encounter.round == 1, "the fight has not begun"

    order = repos.encounters.combatants(encounter.id)
    assert len(order) == 7
    assert encounter.turn_combatant_id == order[0].id
    # Highest initiative first, and it is the rogue who was already behind them.
    assert repos.entities.get(order[0].entity_id).name == "Sable"
    assert all(c.on_map for c in order), "someone is in the fight but off the map"


def test_the_storyline_is_stored_with_nothing_done(blank):
    repos, campaign_id = blank
    build.populate(repos, campaign_id, get("the-last-coach"))

    assert len(repos.settings.get(STORYLINE_SETTING)) == 5
    assert repos.settings.get(PROGRESS_SETTING) == []


def test_it_remembers_which_template_it_came_from(blank):
    repos, campaign_id = blank
    build.populate(repos, campaign_id, get("test-table"))

    assert repos.settings.get(SOURCE_SETTING) == "test-table"


# ------------------------------------------------------------- starting again


def test_starting_again_puts_it_back(tmp_path):
    """A one-shot run twice should be the same evening twice."""
    path = tmp_path / "oneshot.sqlite3"
    conn = connect(path)
    migrate(conn)
    repos = Repos(conn)
    campaign_id = repos.campaigns.ensure_default("One Shot").id
    build.populate(repos, campaign_id, get("test-table"))
    before = _snapshot(repos, campaign_id)

    # An evening happens.
    from canon_keeper_core.repo.entities import KIND_NPC, Entity

    repos.entities.create(
        Entity(id=None, campaign_id=campaign_id, kind=KIND_NPC, name="An invention")
    )
    hurt = repos.entities.list(campaign_id)[0]
    hurt.data["hp"] = 1
    repos.entities.update(hurt)
    repos.facts.assert_fact(campaign_id, None, "the party", "burnt the inn down")
    conn.close()

    build.start_again(path)

    conn = connect(path)
    try:
        after = _snapshot(Repos(conn), campaign_id)
        assert after == before
        names = [e[1] for e in after["entities"]]
        assert "An invention" not in names, "last table's leavings should be gone"
    finally:
        conn.close()


def test_starting_again_needs_a_template(tmp_path):
    path = tmp_path / "ordinary.sqlite3"
    conn = connect(path)
    migrate(conn)
    Repos(conn).campaigns.ensure_default("Ordinary")
    conn.close()

    with pytest.raises(TemplateError, match="did not come from a template"):
        build.start_again(path)


def test_keeping_it_makes_it_an_ordinary_campaign(tmp_path):
    """Nothing about the content changes -- there was never anything special."""
    path = tmp_path / "kept.sqlite3"
    conn = connect(path)
    migrate(conn)
    repos = Repos(conn)
    campaign_id = repos.campaigns.ensure_default("Kept").id
    build.populate(repos, campaign_id, get("test-table"))
    before = _snapshot(repos, campaign_id)
    conn.close()

    build.keep(path)

    assert build.source_of(path) == ""
    conn = connect(path)
    try:
        assert _snapshot(Repos(conn), campaign_id) == before
    finally:
        conn.close()

    with pytest.raises(TemplateError):
        build.start_again(path)


def test_an_ordinary_campaign_has_no_source(tmp_path):
    path = tmp_path / "plain.sqlite3"
    conn = connect(path)
    migrate(conn)
    Repos(conn).campaigns.ensure_default("Plain")
    conn.close()

    assert build.source_of(path) == ""


# ------------------------------------------------------------------ the chooser


@pytest.fixture
def chooser(qtbot):
    from canon_keeper.shell.startup import CampaignDialog

    dialog = CampaignDialog()
    qtbot.addWidget(dialog)
    return dialog


def test_the_chooser_offers_the_one_shots(chooser):
    from canon_keeper.shell.startup import TEMPLATE_TAB

    chooser._tabs.setCurrentIndex(TEMPLATE_TAB)
    offered = {
        chooser._template_list.item(row).data(256)
        for row in range(chooser._template_list.count())
    }

    assert {"the-last-coach", "test-combat", "test-table"} <= offered


def test_the_button_says_start_on_that_tab(chooser):
    from PySide6.QtWidgets import QDialogButtonBox

    from canon_keeper.shell.startup import LOCAL_TAB, ONLINE_TAB, TEMPLATE_TAB

    button = chooser._buttons.button(QDialogButtonBox.StandardButton.Open)

    chooser._tabs.setCurrentIndex(LOCAL_TAB)
    assert button.text() == "Open"
    chooser._tabs.setCurrentIndex(ONLINE_TAB)
    assert button.text() == "Join"
    chooser._tabs.setCurrentIndex(TEMPLATE_TAB)
    assert button.text() == "Start"


def test_the_button_starts_a_one_shot_rather_than_opening_a_campaign(
    chooser, monkeypatch
):
    """The tab was added after _accept was written, and fell through to the
    local branch -- so the Open button tried to open whatever was selected on
    another tab."""
    from canon_keeper.shell.startup import TEMPLATE_TAB

    started: list = []
    monkeypatch.setattr(chooser, "_start_template", lambda: started.append(True))
    chooser._tabs.setCurrentIndex(TEMPLATE_TAB)

    chooser._accept()

    assert started == [True]


@pytest.mark.parametrize(
    "which,tab",
    [("_local_list", 0), ("_remote_list", 1), ("_template_list", 2)],
)
def test_every_list_opens_on_a_double_click(chooser, monkeypatch, which, tab):
    """Picking and pressing is one gesture; double-clicking is the other."""
    accepted: list = []
    monkeypatch.setattr(chooser, "_accept", lambda: accepted.append(True))
    chooser._tabs.setCurrentIndex(tab)
    widget = getattr(chooser, which)

    from PySide6.QtWidgets import QListWidgetItem

    item = QListWidgetItem("something")
    widget.addItem(item)
    widget.itemDoubleClicked.emit(item)

    assert accepted == [True], f"{which} ignores a double click"


def test_a_started_one_shot_hands_back_a_path(chooser, monkeypatch, tmp_path):
    """app.main compares these against saved paths; a string is not a Path."""
    from pathlib import Path

    from canon_keeper import campaigns as campaigns_module
    from canon_keeper.shell.startup import TEMPLATE_TAB
    from PySide6.QtWidgets import QInputDialog

    made = campaigns_module.LocalCampaign(tmp_path / "run.sqlite3", "A Run", 0.0)
    monkeypatch.setattr(build, "start", lambda _t, _n: made)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("A Run", True)))
    chooser._tabs.setCurrentIndex(TEMPLATE_TAB)

    chooser._start_template()

    assert isinstance(chooser.launch().path, Path)
    assert chooser.launch().name == "A Run"
