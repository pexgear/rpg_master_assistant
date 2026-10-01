"""The menu a creature carries with it, wherever it is shown.

Two halves, and the order is the claim: **what this panel can do** first,
because that is why somebody right-clicked here, then **what is true of the
creature anywhere** under a separator.

The rest of this file is the plugin contract, which is the same contract panels
have and is tested for the same reason: a third-party action that misbehaves
must cost its own menu item and nothing else.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMenu

from canon_keeper import entity_actions
from canon_keeper.entity_actions import Target
from canon_keeper_core.repo.entities import KIND_NPC, KIND_PC, Entity


@pytest.fixture(autouse=True)
def clean_registry():
    """Actions are global, so a test that registers one must not leak it."""
    entity_actions.clear()
    yield
    entity_actions.clear()


@pytest.fixture
def marla(ctx):
    return ctx.repos.entities.create(
        Entity(id=None, campaign_id=ctx.campaign_id, kind=KIND_PC, name="Marla")
    )


def _target(entity, panel="characters", **extra) -> Target:
    return Target(
        entity_id=entity.id,
        kind=entity.kind,
        name=entity.name,
        panel=panel,
        extra=extra,
    )


class _Spy:
    """A minimal action, for testing the machinery rather than the invite."""

    id = "spy"
    order = 50

    def __init__(self, applies=True, explodes_on=None) -> None:
        self._applies = applies
        self._explodes_on = explodes_on
        self.ran: list[Target] = []

    def label(self, ctx, target):
        return f"Spy on {target.name}"

    def applies(self, ctx, target):
        if self._explodes_on == "applies":
            raise RuntimeError("a bad plugin")
        return self._applies

    def run(self, ctx, target, parent=None):
        if self._explodes_on == "run":
            raise RuntimeError("a bad plugin")
        self.ran.append(target)


# ------------------------------------------------------------------ the shape


def test_an_action_appears_for_a_creature(ctx, marla):
    entity_actions.register(_Spy())

    offered = entity_actions.available(ctx, _target(marla))

    assert [a.id for a in offered] == ["spy"]


def test_a_panel_can_refuse_one(ctx, marla):
    """The first of the two ways to say no: it makes no sense *here*."""
    entity_actions.register(_Spy())

    assert entity_actions.available(ctx, _target(marla), skip={"spy"}) == []


def test_an_action_can_refuse_itself(ctx, marla):
    """The second: it makes no sense for this creature, or this person, now."""
    entity_actions.register(_Spy(applies=False))

    assert entity_actions.available(ctx, _target(marla)) == []


def test_the_panel_is_told_which_panel_asked(ctx, marla, qtbot):
    spy = _Spy()
    entity_actions.register(spy)
    menu = QMenu()
    qtbot.addWidget(menu)

    entity_actions.fill(menu, ctx, _target(marla, panel="encounter", combatant=7))
    menu.actions()[0].trigger()

    assert spy.ran[0].panel == "encounter"
    assert spy.ran[0].extra == {"combatant": 7}


def test_actions_come_out_in_order(ctx, marla):
    first, second = _Spy(), _Spy()
    first.id, first.order = "second", 20
    second.id, second.order = "first", 10
    entity_actions.register(first)
    entity_actions.register(second)

    assert [a.id for a in entity_actions.available(ctx, _target(marla))] == [
        "first",
        "second",
    ]


def test_registering_the_same_id_twice_replaces_it(ctx, marla):
    """Otherwise a double import shows the same item twice."""
    entity_actions.register(_Spy())
    entity_actions.register(_Spy())

    assert len(entity_actions.available(ctx, _target(marla))) == 1


# ------------------------------------------------- what the panel keeps on top


def test_the_panels_own_actions_stay_at_the_top(ctx, marla, qtbot):
    """The reason you right-clicked *here* should not be below a separator."""
    entity_actions.register(_Spy())
    menu = QMenu()
    qtbot.addWidget(menu)
    menu.addAction("Take off the map")

    entity_actions.fill(menu, ctx, _target(marla))

    labels = [a.text() for a in menu.actions()]
    assert labels[0] == "Take off the map"
    assert menu.actions()[1].isSeparator()
    assert labels[-1] == "Spy on Marla"


def test_nothing_to_add_adds_nothing(ctx, marla, qtbot):
    """Not even the separator. A menu ending in a line looks broken."""
    entity_actions.register(_Spy(applies=False))
    menu = QMenu()
    qtbot.addWidget(menu)
    menu.addAction("Take off the map")

    entity_actions.fill(menu, ctx, _target(marla))

    assert len(menu.actions()) == 1


# --------------------------------------------------------- one bad plugin


def test_an_action_that_explodes_while_deciding_is_dropped(ctx, marla):
    """Being wrong about whether to appear is not a reason for nothing to."""
    entity_actions.register(_Spy(explodes_on="applies"))
    good = _Spy()
    good.id = "good"
    entity_actions.register(good)

    assert [a.id for a in entity_actions.available(ctx, _target(marla))] == ["good"]


def test_an_action_that_explodes_when_run_does_not_take_the_app_down(
    ctx, marla, qtbot
):
    entity_actions.register(_Spy(explodes_on="run"))
    menu = QMenu()
    qtbot.addWidget(menu)
    entity_actions.fill(menu, ctx, _target(marla))

    menu.actions()[0].trigger()  # must not raise


# ------------------------------------------------------------------ inviting


def test_inviting_is_offered_on_a_player_character(ctx, marla):
    from canon_keeper.panels.actions import InviteAPlayer

    entity_actions.register(InviteAPlayer())

    assert [a.id for a in entity_actions.available(ctx, _target(marla))] == ["invite"]


def test_inviting_is_not_offered_on_a_goblin(ctx):
    from canon_keeper.panels.actions import InviteAPlayer

    entity_actions.register(InviteAPlayer())
    goblin = ctx.repos.entities.create(
        Entity(id=None, campaign_id=ctx.campaign_id, kind=KIND_NPC, name="Yeemik")
    )

    assert entity_actions.available(ctx, _target(goblin)) == []


def test_a_player_is_never_offered_it(ctx, marla):
    """A player right-clicking their own character is not inviting anybody."""
    from canon_keeper.panels.actions import InviteAPlayer

    entity_actions.register(InviteAPlayer())
    ctx.role = "player"

    assert entity_actions.available(ctx, _target(marla)) == []


def test_inviting_from_a_menu_makes_a_code(ctx, marla, qtbot):
    from canon_keeper.panels.actions import InviteAPlayer

    entity_actions.register(InviteAPlayer())
    menu = QMenu()
    qtbot.addWidget(menu)
    entity_actions.fill(menu, ctx, _target(marla))

    menu.actions()[0].trigger()

    waiting = ctx.repos.invites.waiting_for(marla.id)
    assert waiting is not None
    assert waiting.entity_id == marla.id


def test_the_code_it_copies_carries_the_address(ctx, marla, qtbot):
    """The Table panel publishes where the session is; the action reads it."""
    from PySide6.QtWidgets import QApplication

    from canon_keeper.panels.actions import InviteAPlayer

    entity_actions.register(InviteAPlayer())
    ctx.session_address = "ws://10.0.0.4:8765"
    menu = QMenu()
    qtbot.addWidget(menu)
    entity_actions.fill(menu, ctx, _target(marla))

    menu.actions()[0].trigger()

    copied = QApplication.clipboard().text()
    waiting = ctx.repos.invites.waiting_for(marla.id)
    assert copied == f"ws://10.0.0.4:8765#{waiting.code}"


def test_it_says_so_when_a_character_already_has_a_player(ctx, marla):
    """The wording changes, because so does what pressing it means."""
    from canon_keeper.panels.actions import InviteAPlayer

    action = InviteAPlayer()
    assert "Invite a player" in action.label(ctx, _target(marla))

    ctx.repos.accounts.create(
        ctx.campaign_id, "marco", "goblin-teeth", character_entity_id=marla.id
    )
    assert "somebody else" in action.label(ctx, _target(marla))


# ------------------------------------------------------------------ discovery


def test_the_actions_that_ship_are_registered_on_first_use(ctx):
    """Nothing has to remember to wire them up -- that is what discovery is."""
    entity_actions.clear(sealed=False)

    assert "invite" in [a.id for a in entity_actions.discover()]
