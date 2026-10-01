"""A player's view of the fight: the same map, and nothing to press.

Built from ``ctx.shared`` -- what the host chose to send -- rather than from a
database this app does not have. So the reason a player cannot see the goblin
in the corner is not that this widget declines to draw it: the goblin is not in
the bytes.

Read-only is about the *fight*: a player builds no terrain, moves nobody about
and cannot touch the order. Their own character on their own turn is not that
-- Space opens the same wheel the DM gets, for that one creature and only while
it is theirs to act with. Everything it asks for goes to the host as a request
like any other, and the host applies the same rules to it as to anybody.

While a turn is waiting on you, this panel and the chat box are held -- and
deliberately nothing else. Your character sheet, the places you know and the
rest of the app stay open, because being asked what your character does is not
a reason to stop being able to look anything up.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from canon_keeper.panels.encounter.grid import Choice, GridMap, Preview, Token
from canon_keeper.panels.encounter.view3d import MapView
from canon_keeper.plugin import AppContext
from canon_keeper_core.repo.entities import KIND_PC
from canon_keeper_core.rules import death


class PlayerEncounterWidget(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._ctx = ctx

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        self._heading = QLabel("")
        self._heading.setWordWrap(True)
        outer.addWidget(self._heading)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._order = QListWidget()
        self._order.setMaximumWidth(230)
        self._order.setToolTip("The initiative order, as far as you can see it")
        splitter.addWidget(self._order)

        self._map = GridMap()
        self._map.read_only = True
        # Read-only is about the *fight*: a player builds no terrain and moves
        # nobody about. Their own character on their own turn is not that, and
        # taking your turn on the map is the whole point of there being one.
        self._map.radial_wanted.connect(self._offer_wheel)
        self._map.planned.connect(self._carry_out)
        # Seen in 3D. The panel still talks only to `_map`, which holds the
        # fight; the view is how it is drawn.
        self._view = MapView(self._map)
        splitter.addWidget(self._view)
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter, 1)

        # What is on offer, in words, above the map that is showing it in dots.
        # No buttons: answering happens where the player already is, in the
        # chat box they typed the turn into. Two places to press yes is one
        # place too many.
        # What is left of the turn in progress. Shown to everyone rather than
        # only to whoever is up: at a table this is said out loud, and it is
        # the answer to "can I get there" that a player would otherwise be
        # counting squares on a screen to work out.
        self._budget = QLabel("")
        self._budget.setWordWrap(True)
        self._budget.setVisible(False)
        outer.addWidget(self._budget)

        self._offer_text = QLabel("")
        self._offer_text.setWordWrap(True)
        self._offer_text.setVisible(False)
        outer.addWidget(self._offer_text)

        row = QHBoxLayout()
        self._note = QLabel("")
        self._note.setWordWrap(True)
        row.addWidget(self._note, 1)

        # Stepping out, or just wanting the fight to move. Yours to give and
        # yours to take back -- you should not have to find the DM to do
        # either.
        self._hand_over = QPushButton("Simulate turn")
        self._hand_over.setToolTip(
            "Let autopilot play your character for this fight. Press again to "
            "take them back. Everyone is told either way."
        )
        self._hand_over.clicked.connect(self._toggle_simulated)
        self._hand_over.setVisible(False)
        row.addWidget(self._hand_over)
        outer.addLayout(row)

        #: The turn we are being asked about, if any.
        self._pending: dict | None = None
        #: SRD rules, for reading the weapons off your own sheet. Built on
        #: first use: most of a session never opens the wheel.
        self._srd = None
        ctx.bus.action_proposed.connect(self._on_offered)
        ctx.bus.action_withdrawn.connect(self._on_withdrawn)

        if ctx.shared is not None:
            ctx.shared.encounter_changed.connect(self._refresh)
            # Names and hit points come off the entities, so those moving
            # changes what this shows too.
            ctx.shared.changed.connect(self._refresh)
        ctx.bus.theme_changed.connect(lambda _dark: self._map.update())
        # Walks, swings and creatures going down, as the host described them.
        ctx.bus.play.connect(self._map.play)

        self._refresh()

    def _refresh(self) -> None:
        encounter = self._ctx.shared.encounter if self._ctx.shared is not None else None
        if not encounter:
            self._heading.setText("No fight right now.")
            self._note.setText("")
            self._order.clear()
            self._budget.setVisible(False)
            self._hand_over.setVisible(False)
            self._map.set_grid(1, 1)
            self._map.set_obstacles(())
            self._map.set_tokens([])
            return

        combatants = [c for c in encounter.get("combatants", []) if isinstance(c, dict)]
        teams = [t for t in encounter.get("teams") or () if isinstance(t, dict)]
        turn = encounter.get("turn")
        # What is left of the turn, and which round it is -- the second is what
        # turns "the round they last reacted in" into "they have reacted".
        budget = encounter.get("budget") or {}
        this_round = int(encounter.get("round") or 0)

        self._map.set_grid(
            int(encounter.get("width") or 1), int(encounter.get("height") or 1)
        )
        # Terrain reaches everyone. The reason to draw a pillar on a player's
        # map is so they can decide to stand behind it.
        self._map.set_obstacles(
            square
            for square in encounter.get("obstacles") or ()
            if isinstance(square, (list, tuple)) and len(square) == 2
        )
        self._map.set_tokens(
            [
                Token(
                    id=int(c.get("id") or 0),
                    label=self._name_of(c),
                    x=int(c["x"]),
                    y=int(c["y"]),
                    ours=self._on_the_party_side(c, teams),
                    is_turn=c.get("id") == turn,
                    down=bool(c.get("down")),
                    squares_left=(
                        int(budget.get("left") or 0) if c.get("id") == turn else 0
                    ),
                    acted=bool(budget.get("acted")),
                    reacted=(
                        this_round > 0
                        and int(c.get("reacted_round") or 0) >= this_round
                    ),
                )
                for c in combatants
                if isinstance(c.get("x"), int) and isinstance(c.get("y"), int)
            ]
        )
        self._mind_the_wheel(combatants, turn)

        self._order.clear()
        for team in teams:
            members = [c for c in combatants if c.get("team") == team.get("id")]
            if not members:
                continue
            self._order.addItem(_header(str(team.get("name") or ""), self.palette()))
            for combatant in members:
                self._order.addItem(self._row(combatant, turn))

        loose = [c for c in combatants if not _on_a_side(c, teams)]
        for combatant in loose:
            self._order.addItem(self._row(combatant, turn))

        self._show_budget(encounter, combatants)
        self._show_hand_over(combatants)

        name = encounter.get("name") or "The fight"
        round_number = int(encounter.get("round") or 0)
        if round_number:
            up = self._up(combatants, turn)
            self._heading.setText(f"<b>{name}</b> -- round {round_number}, {up}.")
        else:
            self._heading.setText(f"<b>{name}</b> -- not started yet.")

        # Said plainly rather than left to be inferred from a short list. A
        # player counting four goblins on the DM's side of the screen and three
        # on theirs should know which of the two is the truth.
        self._note.setText(
            "You see what your DM has shared with you. There may be more in the "
            "room than is on this map."
        )

    def _row(self, combatant: dict, turn) -> QListWidgetItem:
        """One two-line row: who they are, then what is true of them.

        The name line stays the name, so a player scanning for whose turn it is
        does not read past a hit point total to find it. Everything else -- how
        hurt they are, whether they are dying and how close that is -- goes
        underneath.
        """
        initiative = combatant.get("initiative")
        head = (
            "  -- " if initiative is None else f"{int(initiative):>4}"
        ) + f"  {self._name_of(combatant)}"

        state = [part for part in (self._hp_of(combatant),) if part]
        successes = int(combatant.get("death_successes") or 0)
        failures = int(combatant.get("death_failures") or 0)
        if failures >= death.SAVES_NEEDED:
            state.append("dead")
        elif successes >= death.SAVES_NEEDED:
            state.append("stable, unconscious")
        elif combatant.get("down"):
            # Only a player character ever rolls these, so no count at all on
            # somebody who is down means a creature that simply died.
            state.append(
                f"dying — {successes} made, {failures} failed"
                if successes or failures
                else "down"
            )
        if not _placed(combatant):
            state.append("off the map")
        if combatant.get("simulated"):
            state.append(
                f"played by {combatant.get('stand_in_name') or 'a machine'}"
            )

        item = QListWidgetItem(head + (f"\n{' · '.join(state)}" if state else ""))
        if combatant.get("id") == turn:
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        if combatant.get("down") or not _placed(combatant):
            item.setForeground(self.palette().placeholderText())
        return item

    def _show_budget(self, encounter: dict, combatants: list[dict]) -> None:
        """Movement left and whether the action is gone.

        In feet as well as squares, because a rule is written in feet and a map
        is drawn in squares, and the translation between them is exactly the
        arithmetic worth doing for somebody.
        """
        budget = encounter.get("budget") or {}
        if not budget:
            self._budget.setVisible(False)
            return

        acting = next(
            (c for c in combatants if c.get("id") == budget.get("combatant")), None
        )
        who = self._name_of(acting) if acting else "Whoever is up"
        mine = acting is not None and self._is_mine(acting)

        left = int(budget.get("left") or 0)
        speed = int(budget.get("speed") or 0)
        moved = int(budget.get("moved") or 0)
        walked = f", {moved * 5} used" if moved else ""
        action = "attack used" if budget.get("acted") else "attack still to come"

        label = (
            f"{'Your turn' if mine else who}: "
            f"<b>{left * 5} feet</b> of {speed * 5} left{walked} &middot; {action}."
        )
        self._budget.setText(label)
        self._budget.setVisible(True)

    # ------------------------------------------------------------- the wheel

    def _mind_the_wheel(self, combatants: list[dict], turn) -> None:
        """Whether the map may take a turn right now, and for whom.

        One creature, on one turn: your own character while it is theirs. Any
        other moment and the map is what it always was, something to look at.
        Selected as well as permitted, so that Space works without anybody
        aiming first -- the same way the DM's map follows whoever is up.
        """
        mine = self._mine_in_the_fight(combatants)
        acting = (
            mine is not None
            and mine.get("id") == turn
            and not mine.get("simulated")
            and not mine.get("down")
        )
        self._map.acts_for = int(mine["id"]) if acting else None
        if acting:
            self._map.select(int(mine["id"]))
        else:
            self._map.close_radial()

    def _offer_wheel(self, combatant_id: int) -> None:
        """Space, on your own character. What they could do, round their pin."""
        if self._map.acts_for != combatant_id:
            return
        choices: list[Choice] = [Choice(kind="move", label="Move")]
        for weapon in self._my_weapons():
            choices.append(Choice(kind="attack", label=weapon, weapon=weapon))
        self._map.offer(combatant_id, choices)

    def _my_weapons(self) -> list[str]:
        """What your character is carrying, off the sheet you were sent.

        Your own sheet arrives whole -- it is yours -- so this needs nothing
        the host has not already told this login.
        """
        own = self._ctx.shared.own_character() if self._ctx.shared is not None else None
        sheet = ((own or {}).get("data") or {}).get("sheet") or {}
        from canon_keeper_core.rules import attack

        try:
            return [w.name for w in attack.weapons_of(sheet, self._content())]
        except Exception:  # noqa: BLE001 - a broken sheet is not worth a crash
            return []

    def _content(self):
        if self._srd is None:
            from canon_keeper_core.content import Content

            self._srd = Content(self._ctx.repos.settings)
        return self._srd

    def _carry_out(self, plan) -> None:
        """A turn lined up on the map, on its way to the host.

        Through the same bus signal the DM's map uses. Where it goes from
        there -- to a referee in this app or over the wire to somebody else's
        -- is the Table panel's to know, and the host's rules are the same
        either way.
        """
        if plan is None or plan.is_empty:
            return
        turn: dict = {"combatant": plan.combatant}
        if plan.move is not None:
            turn["move"] = list(plan.move)
        if plan.target is not None:
            turn["target"] = plan.target
            turn["weapon"] = plan.weapon
        self._ctx.bus.turn_taken.emit(turn)

    def _mine_in_the_fight(self, combatants: list[dict]) -> dict | None:
        for combatant in combatants:
            if self._is_mine(combatant):
                return combatant
        return None

    def _show_hand_over(self, combatants: list[dict]) -> None:
        mine = self._mine_in_the_fight(combatants)
        self._hand_over.setVisible(mine is not None)
        if mine is None:
            return
        self._hand_over.setText(
            "Take them back" if mine.get("simulated") else "Simulate turn"
        )

    def _toggle_simulated(self) -> None:
        fight = self._ctx.shared.encounter if self._ctx.shared is not None else None
        mine = self._mine_in_the_fight((fight or {}).get("combatants") or [])
        if mine is None:
            return
        self._ctx.bus.simulate_requested.emit(
            int(mine["id"]), not bool(mine.get("simulated"))
        )

    def _is_mine(self, combatant: dict) -> bool:
        own = self._ctx.shared.own_character() if self._ctx.shared is not None else None
        return own is not None and combatant.get("entity") == own.get("id")

    # ------------------------------------------------------------- your turn

    def _on_offered(self, action: dict) -> None:
        """A turn worked out for you, waiting on your word.

        The bar says it in words and the map says it in dots and a ghost. Both,
        because the words are what you check and the map is what you recognise.
        """
        self._pending = action
        watching = bool(action.get("watching"))
        self._offer_text.setText(
            f"<b>{action.get('text', 'A turn.')}</b> "
            + (
                "-- waiting on them."
                if watching
                else "-- confirm it in the chat, or say what you meant instead."
            )
        )
        self._offer_text.setVisible(True)
        # Held, not greyed: the map is showing the very thing being decided,
        # so it has to stay readable. It just stops taking clicks.
        self._map.frozen = not watching
        self._order.setEnabled(watching)
        self._show_preview()

    def _on_withdrawn(self, action_id: str) -> None:
        if self._pending and self._pending.get("id") == action_id:
            self._clear_offer()

    def _clear_offer(self) -> None:
        self._pending = None
        self._offer_text.setVisible(False)
        self._map.frozen = False
        self._order.setEnabled(True)
        self._map.set_preview(None)

    def _show_preview(self) -> None:
        action = self._pending
        if not action:
            self._map.set_preview(None)
            return
        move = action.get("move")
        self._map.set_preview(
            Preview(
                token=int(action.get("combatant") or 0),
                to=(int(move[0]), int(move[1])) if move else None,
                target=self._square_of(action.get("target")),
            )
        )

    def _square_of(self, combatant_id) -> tuple[int, int] | None:
        fight = self._ctx.shared.encounter if self._ctx.shared is not None else None
        for combatant in (fight or {}).get("combatants") or []:
            if combatant.get("id") == combatant_id:
                x, y = combatant.get("x"), combatant.get("y")
                if isinstance(x, int) and isinstance(y, int):
                    return x, y
        return None

    # ---------------------------------------------------------------- helpers

    def _entity(self, combatant: dict) -> dict | None:
        entity_id = combatant.get("entity")
        if not isinstance(entity_id, int) or self._ctx.shared is None:
            return None
        return self._ctx.shared.get(entity_id)

    def _name_of(self, combatant: dict) -> str:
        entity = self._entity(combatant)
        return (entity or {}).get("name") or "someone"

    def _is_pc(self, combatant: dict) -> bool:
        entity = self._entity(combatant)
        return bool(entity) and entity.get("kind") == KIND_PC

    def _on_the_party_side(self, combatant: dict, teams: list) -> bool:
        """Which colour to draw them. The side they are on, not what they are."""
        for team in teams:
            if team.get("id") == combatant.get("team"):
                return bool(team.get("party"))
        return self._is_pc(combatant)

    def _hp_of(self, combatant: dict) -> str:
        entity = self._entity(combatant)
        data = (entity or {}).get("data") or {}
        hp, max_hp = data.get("hp"), data.get("max_hp")
        if isinstance(hp, int) and isinstance(max_hp, int):
            return f"({hp}/{max_hp})"
        return ""

    def _up(self, combatants: list[dict], turn) -> str:
        for combatant in combatants:
            if combatant.get("id") == turn:
                return f"{self._name_of(combatant)} is up"
        # The turn belongs to something we were not sent. Saying so is more
        # honest than a blank line, and it is what the DM would say out loud.
        return "something you cannot see is up"


def _placed(combatant: dict) -> bool:
    return isinstance(combatant.get("x"), int) and isinstance(combatant.get("y"), int)


def _on_a_side(combatant: dict, teams: list) -> bool:
    return any(team.get("id") == combatant.get("team") for team in teams)


def _header(name: str, palette) -> QListWidgetItem:
    """A side's name in the order. Not selectable -- it is not a combatant."""
    item = QListWidgetItem(name.upper())
    item.setFlags(Qt.ItemFlag.NoItemFlags)
    font = item.font()
    font.setBold(True)
    font.setPointSizeF(max(6.0, font.pointSizeF() - 1.0))
    item.setFont(font)
    item.setForeground(palette.placeholderText())
    return item
