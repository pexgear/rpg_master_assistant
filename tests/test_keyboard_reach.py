"""Which keys belong to which panel, and which clashes are real.

Two claims here, and the second is the one that is easy to get backwards.

**A key belongs to the panel you are looking at.** Qt's default is the opposite --
an action owned by the window fires from anywhere in the window -- so a panel's
shortcut would go off while you were typing in the chat box.

**Two panels wanting the same key is not a clash.** Only the focused one is
listening, the way two applications both using Ctrl+N is not a clash. Reporting
those would bury the clashes that do matter, which are a wider claim silently
beating a narrower one.
"""

from __future__ import annotations

from canon_keeper.plugin import REACH_EVERYWHERE, REACH_PANEL, REACH_WINDOW
from canon_keeper.shell import keys


def _claim(key, owner, reach=REACH_PANEL, what="do a thing"):
    return keys.Claim(key=key, owner=owner, reach=reach, what=what)


# ------------------------------------------------------------ one spelling


def test_the_same_key_written_differently_is_the_same_key():
    assert keys.normalise("Ctrl+N") == keys.normalise("ctrl+n")
    assert keys.normalise("Ctrl+Shift+N") == keys.normalise("Shift+Ctrl+N")
    assert keys.normalise("Control+N") == keys.normalise("Ctrl+N")
    assert keys.normalise("Ctrl-N") == keys.normalise("Ctrl+N")


def test_an_empty_key_is_no_key():
    assert keys.normalise("") == ""
    assert keys.normalise("  ") == ""


# -------------------------------------------------- what is not a clash


def test_two_panels_may_share_a_key():
    """The case that looks wrong and is right.

    Combat and Characters both wanting Ctrl+N for their own "new thing" is how
    two applications both use Ctrl+N. Only the one with the focus is listening.
    """
    found = keys.clashes(
        [_claim("Ctrl+N", "Combat"), _claim("Ctrl+N", "Characters")]
    )
    assert found == []


def test_different_keys_do_not_clash():
    found = keys.clashes(
        [_claim("Ctrl+N", "Combat"), _claim("Ctrl+M", "Combat")]
    )
    assert found == []


def test_a_claim_on_nothing_is_ignored():
    """A menu item with no shortcut cannot clash with one that has none either."""
    found = keys.clashes([_claim("", "Combat"), _claim("", "Characters")])
    assert found == []


# ------------------------------------------------------ what is a clash


def test_a_window_key_beats_a_panel_key_and_that_is_reported():
    """The one that actually happens, and the one nobody diagnoses.

    The panel's menu goes on showing a shortcut that will never fire, including
    inside that panel, so the symptom is "the menu is lying" rather than
    anything that points at the panel which took it.
    """
    found = keys.clashes(
        [
            _claim("Ctrl+N", "Combat"),
            _claim("Ctrl+N", "Transcript", REACH_WINDOW, "new beat"),
        ]
    )

    assert len(found) == 1
    assert found[0].wins.owner == "Transcript"
    assert found[0].loses.owner == "Combat"
    assert "never fires" in found[0].why


def test_two_window_keys_clash():
    found = keys.clashes(
        [
            _claim("Ctrl+N", "Combat", REACH_WINDOW),
            _claim("Ctrl+N", "Characters", REACH_WINDOW),
        ]
    )
    assert len(found) == 1
    assert "not" in found[0].why and "defined" in found[0].why


def test_one_panel_claiming_a_key_twice_is_a_clash():
    """Its own two menu items, one of which cannot be reached."""
    found = keys.clashes(
        [
            _claim("Ctrl+N", "Combat", REACH_PANEL, "New fight"),
            _claim("Ctrl+N", "Combat", REACH_PANEL, "New team"),
        ]
    )
    assert len(found) == 1
    assert "twice" in found[0].why


def test_an_application_key_beats_everything():
    found = keys.clashes(
        [
            _claim("F9", "Transcript", REACH_EVERYWHERE, "push-to-talk"),
            _claim("F9", "Combat", REACH_WINDOW, "next turn"),
        ]
    )
    assert len(found) == 1
    assert found[0].wins.reach == REACH_EVERYWHERE


def test_the_worst_clash_is_reported_first():
    """A report is read from the top, so it is ordered by what to care about."""
    found = keys.clashes(
        [
            _claim("Ctrl+M", "Combat", REACH_WINDOW),
            _claim("Ctrl+M", "Characters", REACH_WINDOW),
            _claim("F9", "Transcript", REACH_EVERYWHERE),
            _claim("F9", "Cities"),
        ]
    )
    assert [c.wins.reach for c in found] == [REACH_EVERYWHERE, REACH_WINDOW]


def test_a_clash_reads_as_a_sentence():
    """It goes in front of a person, so it has to say what is wrong."""
    found = keys.clashes(
        [
            _claim("Space", "Combat", REACH_PANEL, "open the wheel"),
            _claim("Space", "Transcript", REACH_WINDOW, "start recording"),
        ]
    )
    said = str(found[0])
    assert "Combat" in said and "Transcript" in said
    assert "open the wheel" in said


# --------------------------------------------------------- the page that says so


def test_the_page_lists_every_key(qtbot):
    from canon_keeper.shell.settings_dialog import KeyboardPage

    page = KeyboardPage(
        [
            _claim("Ctrl+N", "Combat", REACH_PANEL, "New fight"),
            _claim("F9", "Transcript", REACH_EVERYWHERE, "push-to-talk"),
        ],
        [],
    )
    qtbot.addWidget(page)

    assert page._table.rowCount() == 2
    said = {
        page._table.item(row, 0).text() for row in range(page._table.rowCount())
    }
    assert said == {"Ctrl+N", "F9"}


def test_the_page_says_so_when_nothing_clashes(qtbot):
    from canon_keeper.shell.settings_dialog import KeyboardPage

    page = KeyboardPage([_claim("Ctrl+N", "Combat")], [])
    qtbot.addWidget(page)

    assert "No clashes" in page._trouble.text()


def test_the_page_puts_the_clashing_keys_first(qtbot):
    """It is the reason anybody opened it; a list sorted by key would bury it."""
    from canon_keeper.shell.settings_dialog import KeyboardPage

    claims = [
        _claim("Ctrl+A", "Aardvark", REACH_PANEL, "fine"),
        _claim("Ctrl+Z", "Combat", REACH_PANEL, "loses it"),
        _claim("Ctrl+Z", "Transcript", REACH_WINDOW, "takes it"),
    ]
    page = KeyboardPage(claims, keys.clashes(claims))
    qtbot.addWidget(page)

    first_column = [
        page._table.item(row, 0).text() for row in range(page._table.rowCount())
    ]
    assert first_column[0] == "Ctrl+Z", first_column
    assert "will not do what the menu says" in page._trouble.text()


def test_the_page_names_the_panel_and_what_the_key_does(qtbot):
    """A report nobody can act on is a report nobody reads."""
    from canon_keeper.shell.settings_dialog import KeyboardPage

    page = KeyboardPage(
        [_claim("Ctrl+N", "Combat", REACH_PANEL, "New fight")], []
    )
    qtbot.addWidget(page)

    row = [page._table.item(0, col).text() for col in range(4)]
    assert row[1] == "Combat"
    assert row[2] == "this panel only", "the reach was shown as a code word"
    assert row[3] == "New fight"
