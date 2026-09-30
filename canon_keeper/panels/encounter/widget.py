"""The DM's combat panel: an initiative order and a map to move people on.

Everything here writes through ``ctx.repos`` and then says so on
``bus.encounter_changed``. The Table panel is what turns that into frames for
the players -- this panel has no socket and no idea whether anyone is connected,
which is the same arrangement Characters and Cities have and for the same
reason: a panel that knew about the network would be a panel that could leak.

The one thing worth knowing before reading it: **a token being on the map does
not mean the party can see it.** Visibility is a share, exactly as it is
everywhere else in the app. So an unshared token is drawn dotted and the panel
offers to share it, rather than quietly showing the DM a fight their players
are watching a third of.
"""

from __future__ import annotations

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from canon_keeper.panels.encounter.dialogs import (
    AddCombatantsDialog,
    AttackDialog,
    FightDialog,
)
from canon_keeper.panels.encounter.grid import (
    COMBATANT_MIME,
    Choice,
    GridMap,
    Preview,
    Token,
    TurnPlan,
)
from canon_keeper.panels.encounter.view3d import MapView
from canon_keeper import entity_actions
from canon_keeper.plugin import AppContext, PanelAction, ReservedKey
from canon_keeper.repo.encounters import Encounter
from canon_keeper.repo.entities import KIND_NPC, KIND_PC
from canon_keeper import campaigns
from canon_keeper.rules import death
from canon_keeper_protocol import robots, turns


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

#: Kinds that can hold a sword. A city does not roll initiative.
FIGHTING_KINDS = (KIND_PC, KIND_NPC)


class OrderList(QListWidget):
    """The initiative order, and the thing you drag people onto the map from.

    Qt's own drag would hand the map an item-model blob to unpick. One combatant
    id in one mime type is the whole payload, so it says so directly.
    """

    def mimeTypes(self) -> list[str]:  # noqa: N802 - Qt's name
        return [COMBATANT_MIME]

    def mimeData(self, items) -> QMimeData:  # noqa: N802 - Qt's name
        data = QMimeData()
        for item in items:
            combatant_id = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(combatant_id, int):
                data.setData(COMBATANT_MIME, str(combatant_id).encode("ascii"))
                break
        return data


class EncounterWidget(QWidget):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._ctx = ctx
        self._encounter: Encounter | None = None
        self._combatants: list = []
        #: The turn being lined up, as an ordered list of steps, and who it is
        #: for. Runtime only, and never for more than one creature at a time --
        #: see `_stage`.
        self._staged_steps: list[dict] = []
        self._staged_for: int | None = None
        self._teams: list = []
        self._entities: dict[int, object] = {}
        self._shared: set[int] = set()
        self._content = None
        #: Whoever was up the last time we filled. Kept so that selecting the
        #: creature whose turn it is happens when the turn moves, and not on
        #: every redraw -- which would fight the DM for the selection.
        self._following: int | None = None
        #: Set while we are the ones writing, so our own bus signal does not
        #: rebuild the list under the mouse mid-drag.
        self._loading = False

        self._build_ui()

        ctx.bus.encounter_changed.connect(self._refresh)
        ctx.bus.turn_settled.connect(self._on_turn_settled)
        # Hit points and names live on entities, and both are on this screen.
        ctx.bus.entity_changed.connect(lambda _id: self._refresh())
        ctx.bus.entity_deleted.connect(lambda _id: self._refresh())
        # Whether a token is dotted is a share, so a share changing redraws it.
        ctx.bus.share_changed.connect(lambda _id: self._refresh())
        ctx.bus.campaign_changed.connect(lambda _id: self._refresh())
        ctx.bus.theme_changed.connect(lambda _dark: self._map.update())
        # The DM's own map watches the same events the players' do, so what
        # they are all looking at is the same fight at the same moment.
        ctx.bus.play.connect(self._map.play)

        self._refresh()

    # --------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        bar = QHBoxLayout()
        self._fights = QComboBox()
        self._fights.setMinimumWidth(140)
        self._fights.setToolTip("Which fight this is. Only the running one reaches players.")
        self._fights.activated.connect(self._switch_fight)
        bar.addWidget(self._fights, 1)

        # Starting a fight and renaming one are in the Combat menu rather than
        # on this bar. They are things you do once, and a row of buttons is
        # worth spending on the things you press every turn -- whose turn it
        # is, who swings, when it ends. The menu is also reachable from the
        # panel you happen to be looking at, which the bar is not.

        self._add_button = QPushButton("Add...")
        self._add_button.setToolTip("Put characters and NPCs into this fight")
        self._add_button.clicked.connect(self._add)
        bar.addWidget(self._add_button)

        self._roll_button = QPushButton("Roll initiative")
        self._roll_button.setToolTip(
            "A d20 for everyone, plus their Dexterity where there is a sheet to "
            "read it from"
        )
        self._roll_button.clicked.connect(self._roll_initiative)
        bar.addWidget(self._roll_button)

        # One button for the whole life of the fight: it starts one, and once
        # one is running it ends it. Two buttons meant reading both to find out
        # which state you were in; one button *is* the state.
        self._fight_button = QPushButton("Start fight")
        self._fight_button.setToolTip(
            "A fight is laid out first and started when everyone is ready. "
            "Nothing takes a turn -- and no machine acts -- until it is."
        )
        self._fight_button.clicked.connect(self._start_or_end)
        bar.addWidget(self._fight_button)

        self._turn_button = QPushButton("Next turn")
        self._turn_button.clicked.connect(self._start_or_next)
        bar.addWidget(self._turn_button)

        # Handing a character over is not on this bar. It is a thing you do to
        # *one* character, and the place you are already pointing at that
        # character is its right-click menu -- where it says who it hands them
        # to. A button that acted on whatever happened to be selected was one
        # more thing to aim before pressing.

        self._attack_button = QPushButton("Attack...")
        self._attack_button.setToolTip(
            "Whoever is up swings at somebody. The host rolls it."
        )
        self._attack_button.clicked.connect(self._attack)
        bar.addWidget(self._attack_button)

        outer.addLayout(bar)

        self._heading = QLabel("")
        self._heading.setWordWrap(True)
        outer.addWidget(self._heading)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self._order = OrderList()
        self._order.setToolTip(
            "The initiative order. Drag someone onto the map to place them, and "
            "double-click to set an initiative by hand."
        )
        self._order.setDragEnabled(True)
        self._order.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self._order.setDefaultDropAction(Qt.DropAction.CopyAction)
        self._order.currentItemChanged.connect(self._on_row_selected)
        self._order.itemDoubleClicked.connect(self._edit_initiative)
        self._order.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._order.customContextMenuRequested.connect(self._on_list_menu)
        left_layout.addWidget(self._order, 1)

        self._only_unseen = QCheckBox("Mark what players cannot see")
        self._only_unseen.setChecked(True)
        self._only_unseen.setToolTip(
            "A token is only visible to a player if the creature has been "
            "shared with them. Putting it on the map is not sharing it."
        )
        self._only_unseen.toggled.connect(lambda _on: self._refresh())
        left_layout.addWidget(self._only_unseen)
        splitter.addWidget(left)

        self._map = GridMap()
        self._map.picked.connect(self._on_picked)
        self._map.square_clicked.connect(self._on_square)
        self._map.dropped.connect(self._on_dropped)
        self._map.obstacle_toggled.connect(self._on_obstacle)
        self._map.menu_requested.connect(self._on_map_menu)
        self._map.radial_wanted.connect(self._offer_wheel)
        self._map.planned.connect(self._stage)
        self._map.commit_wanted.connect(self._commit_staged)
        self._map.staging_cancelled.connect(self._clear_staged)
        # Seen in 3D. The panel still talks only to `_map`, which holds the
        # fight; the view is how it is drawn.
        self._view = MapView(self._map)
        splitter.addWidget(self._view)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 520])
        outer.addWidget(splitter, 1)

        # The staged turn, in words, with the two ways out of it. Hidden until
        # there is something staged: a bar that is always there, usually empty,
        # is furniture rather than information.
        self._staged_bar = QWidget()
        staged_row = QHBoxLayout(self._staged_bar)
        staged_row.setContentsMargins(0, 0, 0, 0)
        self._staged_text = QLabel("")
        self._staged_text.setWordWrap(True)
        staged_row.addWidget(self._staged_text, 1)
        self._do_it = QPushButton("Do it")
        self._do_it.setToolTip("Carry out the staged turn (Enter)")
        self._do_it.clicked.connect(self._commit_staged)
        staged_row.addWidget(self._do_it)
        clear = QPushButton("Clear")
        clear.setToolTip("Forget it and stage nothing (Esc)")
        clear.clicked.connect(self._clear_staged)
        staged_row.addWidget(clear)
        self._staged_bar.setVisible(False)
        outer.addWidget(self._staged_bar)

        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        outer.addWidget(self._hint)

    # ---------------------------------------------------------------- reading

    def reserved_keys(self) -> list[ReservedKey]:
        """The keys the map reads itself, declared so they can be reported.

        None of these is a shortcut: :meth:`GridMap.keyPressEvent` reads them
        directly, which means nothing outside this panel knows they are taken. A
        panel claiming Space window-wide would win it, and the map would go
        quiet with no error anywhere -- so the shell is told, and says so.

        Listed here rather than in the shell because they belong to the handler
        a few hundred lines below, and a copy kept somewhere else goes stale the
        first time a key is added.
        """
        return [
            ReservedKey("Space", "open the wheel on whoever is selected"),
            ReservedKey("Escape", "put the wheel away, or clear the staged turn"),
            ReservedKey("Return", "carry out the staged turn"),
            ReservedKey("+", "zoom in"),
            ReservedKey("-", "zoom out"),
            ReservedKey("0", "fit the map to the panel"),
            ReservedKey("Arrow keys", "pan the map"),
        ]

    def panel_actions(self) -> list[PanelAction]:
        """What this panel can do, for a menu of its own.

        The same things its buttons do. A DM who is looking at the Characters
        panel when a fight starts should not have to find Combat first in order
        to begin one -- the button is where the panel is, and the menu is where
        they are.
        """
        running = self._encounter is not None
        return [
            PanelAction("&New fight", self._new_fight, "Ctrl+Shift+F"),
            PanelAction("&Add to the fight...", self._add, enabled=running),
            PanelAction("&Roll initiative", self._roll_initiative, enabled=running),
            PanelAction("&Attack...", self._attack, enabled=running),
            PanelAction(
                "&Start the fight",
                self._start_or_end,
                enabled=running and not self._encounter.has_begun,
            ),
            PanelAction(
                "&End the fight",
                self._end,
                enabled=running and self._encounter.has_begun,
            ),
            PanelAction("F&ight...", self._edit_fight, enabled=running),
            # The map's own view. In the menu as well as on the wheel because a
            # shortcut nobody can find is a shortcut nobody has.
            PanelAction("Zoom &in", lambda: self._view.zoom_by(1), "Ctrl+="),
            PanelAction("Zoom &out", lambda: self._view.zoom_by(-1), "Ctrl+-"),
            PanelAction("&Whole map", self._view.fit, "Ctrl+0"),
            PanelAction("From &above", self._view.from_above, "Ctrl+9"),
        ]

    def _refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        try:
            self._read()
            self._fill()
        finally:
            self._loading = False

    def _read(self) -> None:
        repos = self._ctx.repos
        campaign = self._ctx.campaign_id
        self._encounter = repos.encounters.current(campaign)
        if self._encounter is not None:
            # A fight made before teams existed has none, and one made a moment
            # ago has nobody on them yet. Both are sorted out on the way in, so
            # nothing downstream has to cope with a combatant on no side.
            repos.encounters.sort_into_teams(self._encounter.id)
        self._teams = (
            repos.encounters.teams(self._encounter.id)
            if self._encounter is not None
            else []
        )
        self._combatants = (
            repos.encounters.combatants(self._encounter.id)
            if self._encounter is not None
            else []
        )
        self._entities = {
            entity.id: entity for entity in repos.entities.list(campaign)
        }
        self._shared = {
            entity_id
            for entity_id in self._entities
            if any(repos.shares.audiences(entity_id))
        }

    def _name_of(self, combatant) -> str:
        entity = self._entities.get(combatant.entity_id)
        return entity.name if entity is not None else (combatant.name or "someone")

    def _is_pc(self, combatant) -> bool:
        entity = self._entities.get(combatant.entity_id)
        return entity is not None and entity.kind == KIND_PC

    def _on_the_party_side(self, combatant) -> bool:
        """Drawn in the party's colour. The side they are on, not what they are.

        A captured guard moved onto the party's side should read as one of
        theirs on the map, which is the whole reason sides are a thing a DM can
        change rather than a thing the app decides from the creature.
        """
        for team in self._teams:
            if team.id == combatant.team_id:
                return team.is_party
        return self._is_pc(combatant)

    def _unseen(self, combatant) -> bool:
        """On the map, but no player has been told this creature exists."""
        if not self._only_unseen.isChecked():
            return False
        return combatant.entity_id is None or combatant.entity_id not in self._shared

    def _hp_of(self, combatant) -> str:
        entity = self._entities.get(combatant.entity_id)
        if entity is None:
            return ""
        data = entity.data or {}
        hp, max_hp = data.get("hp"), data.get("max_hp")
        if isinstance(hp, int) and isinstance(max_hp, int):
            return f"{hp}/{max_hp}"
        return ""

    def _stand_in_name(self, combatant) -> str:
        """What the thing playing this character is called.

        Named rather than labelled "autopilot", because there is one of these
        per character and a table with three of them cannot tell them apart by
        a word they all share.
        """
        if combatant.entity_id is None:
            return "a machine"
        return robots.name_for_character(
            campaigns.campaign_key(self._ctx.repos), combatant.entity_id
        )

    def _condition_of(self, combatant) -> str:
        entity = self._entities.get(combatant.entity_id)
        if entity is None:
            return death.UP
        hp = (entity.data or {}).get("hp")
        if not isinstance(hp, int):
            return death.UP
        return death.condition(
            hp, entity.kind, combatant.death_successes, combatant.death_failures
        )

    # ---------------------------------------------------------------- filling

    def _fill(self) -> None:
        self._follow_the_turn()
        self._fill_fights()
        self._fill_order()
        self._fill_map()
        self._fill_heading()
        self._update_buttons()

    def _follow_the_turn(self) -> None:
        """Select whoever just came up, on the map and in the order.

        Only when the turn *moves*, so a DM who clicked someone else to check
        their hit points is not dragged back a second later. The point is that
        Space is always about the right creature without anybody aiming first.
        """
        up = self._encounter.turn_combatant_id if self._encounter is not None else None
        if up == self._following:
            return
        self._following = up
        # A wheel left open for the last creature is about a turn that is over.
        self._map.close_radial()
        if up is not None:
            # The order list follows the map: filling it reads the selection
            # back, so there is one answer to "who is picked" rather than two.
            self._map.select(up)

    def _fill_fights(self) -> None:
        self._fights.blockSignals(True)
        self._fights.clear()
        fights = self._ctx.repos.encounters.list(self._ctx.campaign_id)
        for fight in fights:
            label = fight.name or "The fight"
            if fight.running:
                label += "  (running)"
            self._fights.addItem(label, fight.id)
        if self._encounter is not None:
            index = self._fights.findData(self._encounter.id)
            if index >= 0:
                self._fights.setCurrentIndex(index)
        self._fights.setEnabled(len(fights) > 1)
        self._fights.blockSignals(False)

    def _state_line(self, combatant) -> str:
        """The second line of a row: what is true of them beyond their name.

        Everything that is *not* their name goes here, so the first line stays
        scannable. A DM looking for who is up should not have to read past
        "(12/12) - unshared" to find it.
        """
        parts = []
        hp = self._hp_of(combatant)
        if hp:
            parts.append(hp)

        state = self._condition_of(combatant)
        if state == death.DYING:
            # The count, not just the word: "one more failure" and "one more
            # save" are the whole tension of the thing.
            parts.append(
                f"dying — {combatant.death_successes} made, "
                f"{combatant.death_failures} failed"
            )
        elif state == death.STABLE:
            parts.append("stable, unconscious")
        elif state == death.DEAD:
            parts.append("dead")

        if not combatant.on_map:
            parts.append("off the map")
        if combatant.simulated:
            parts.append(f"played by {self._stand_in_name(combatant)}")
        if self._unseen(combatant):
            parts.append("unshared")
        return " · ".join(parts)

    def _fill_order(self) -> None:
        """The order, grouped by side, two lines to a row.

        Grouped because "who is left on their side" is the question a fight is
        actually about, and an interleaved list makes counting it a chore. The
        order *within* a group is still initiative, so the list still answers
        "who is next" -- it just answers the other question as well.
        """
        chosen = self._map.selected
        self._order.blockSignals(True)
        self._order.clear()

        unsorted = [c for c in self._combatants if c.team_id is None]
        for team in self._teams:
            members = [c for c in self._combatants if c.team_id == team.id]
            if not members:
                continue
            self._order.addItem(_header(team.name, self.palette()))
            for combatant in members:
                self._add_row(combatant, chosen)

        if unsorted:
            self._order.addItem(_header("No side", self.palette()))
            for combatant in unsorted:
                self._add_row(combatant, chosen)
        self._order.blockSignals(False)

    def _add_row(self, combatant, chosen) -> None:
        initiative = (
            "  -- " if combatant.initiative is None else f"{combatant.initiative:>4}"
        )
        state = self._state_line(combatant)
        item = QListWidgetItem(
            f"{initiative}  {self._name_of(combatant)}" + (f"\n{state}" if state else "")
        )
        item.setData(Qt.ItemDataRole.UserRole, combatant.id)
        if self._is_turn(combatant):
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        if not combatant.on_map or self._condition_of(combatant) != death.UP:
            item.setForeground(self.palette().placeholderText())
        self._order.addItem(item)
        if combatant.id == chosen:
            self._order.setCurrentItem(item)

    def _fill_map(self) -> None:
        if self._encounter is None:
            self._map.set_grid(1, 1)
            self._map.set_obstacles(())
            self._map.set_tokens([])
            return
        self._map.set_grid(self._encounter.width, self._encounter.height)
        self._map.set_obstacles(
            self._ctx.repos.encounters.obstacles(self._encounter.id)
        )
        self._map.set_tokens(
            [
                Token(
                    id=combatant.id,
                    label=self._name_of(combatant),
                    x=combatant.x,
                    y=combatant.y,
                    ours=self._on_the_party_side(combatant),
                    is_turn=self._is_turn(combatant),
                    unseen=self._unseen(combatant),
                    down=combatant.down,
                    squares_left=self._squares_left(combatant),
                    acted=bool(self._encounter.action_used),
                    reacted=self._has_reacted(combatant),
                )
                for combatant in self._combatants
                if combatant.on_map
            ]
        )

    def _squares_left(self, combatant) -> int:
        """How far this creature could still walk, if it is their turn.

        Zero for everybody else, which is what the map wants: the pips are only
        drawn for whoever is up, and a number worked out for the other fifteen
        would be fifteen sheet reads a frame for something nobody draws.
        """
        if self._encounter is None or not self._is_turn(combatant):
            return 0
        entity = self._entities.get(combatant.entity_id)
        sheet = (getattr(entity, "data", None) or {}).get("sheet") or {}
        from canon_keeper.rules import derive

        try:
            speed = derive.speed_in_squares(sheet, self._content_for_rules())
        except Exception:  # noqa: BLE001 - a broken sheet is not worth a crash
            return 0
        return max(0, speed - int(self._encounter.moved_squares or 0))

    def _has_reacted(self, combatant) -> bool:
        """Already swung at somebody walking past, this round.

        Compared against the round rather than stored as a flag: what is
        recorded is the round they reacted in, so nothing has to be cleared at
        the top of a round and no stale flag can survive one.
        """
        if self._encounter is None or not self._encounter.has_begun:
            return False
        return int(getattr(combatant, "reaction_round", 0) or 0) >= self._encounter.round

    def _fill_heading(self) -> None:
        if self._encounter is None:
            self._heading.setText(
                "No fight yet. <b>New fight</b> makes one, <b>Add...</b> puts "
                "people in it, and dragging them onto the grid places them."
            )
            self._hint.setText("")
            return

        name = self._encounter.name or "The fight"
        if self._encounter.has_begun:
            up = self._current_combatant()
            whose = self._name_of(up) if up is not None else "nobody"
            self._heading.setText(
                f"<b>{name}</b> -- round {self._encounter.round}, {whose} is up."
            )
        else:
            waiting = sum(1 for c in self._combatants if c.initiative is None)
            tail = f" {waiting} still to roll." if waiting else ""
            self._heading.setText(
                f"<b>{name}</b> -- not started. {len(self._combatants)} in the "
                f"fight.{tail}"
            )

        off = [c for c in self._combatants if not c.on_map]
        unseen = [c for c in self._combatants if c.on_map and self._unseen(c)]
        hints = []
        if off:
            hints.append(
                f"{len(off)} not on the map -- drag them onto it from the list."
            )
        if unseen:
            hints.append(
                f"{len(unseen)} on the map that no player can see (dotted). "
                "Right-click to share."
            )
        hints.append("Ctrl-click a square for something to hide behind.")
        self._hint.setText("  ".join(hints))

    def _update_buttons(self) -> None:
        has_fight = self._encounter is not None
        anyone = bool(self._combatants)
        self._add_button.setEnabled(has_fight)
        self._roll_button.setEnabled(has_fight and anyone)
        begun = has_fight and self._encounter.has_begun
        # Passing the turn only means anything once there are turns to pass.
        self._turn_button.setEnabled(begun and anyone)
        self._attack_button.setEnabled(begun and len(self._combatants) > 1)
        self._fight_button.setEnabled(has_fight and (anyone or begun))
        self._fight_button.setText("End fight" if begun else "Start fight")

    def _is_turn(self, combatant) -> bool:
        return (
            self._encounter is not None
            and self._encounter.turn_combatant_id == combatant.id
        )

    def _current_combatant(self):
        for combatant in self._combatants:
            if self._is_turn(combatant):
                return combatant
        return None

    # ---------------------------------------------------------------- actions

    def _changed(self) -> None:
        """Say the fight moved. The Table panel turns it into frames.

        No refresh of our own: we are subscribed to the same signal as everyone
        else, so announcing it is what redraws us. One path, and no way for the
        map to be right here and stale for the table.
        """
        self._ctx.bus.encounter_changed.emit()

    def _new_fight(self) -> None:
        """A grid and an empty order, immediately. Nothing to answer first."""
        self._encounter = self._ctx.repos.encounters.create(self._ctx.campaign_id)
        self._changed()

    def _edit_fight(self) -> None:
        """Name it, or change the grid, once there is something to look at."""
        if self._encounter is None:
            return
        dialog = FightDialog(
            self, self._encounter.name, self._encounter.width, self._encounter.height
        )
        if not dialog.exec():
            return
        name, width, height = dialog.values()
        self._ctx.repos.encounters.rename(self._encounter.id, name)
        if (width, height) != (self._encounter.width, self._encounter.height):
            self._ctx.repos.encounters.resize(self._encounter.id, width, height)
        self._changed()

    def _switch_fight(self, index: int) -> None:
        encounter_id = self._fights.itemData(index)
        if not isinstance(encounter_id, int):
            return
        # Selecting it makes it the one being run, because the alternative is
        # a panel showing one fight while the table is sent another.
        self._ctx.repos.encounters.set_running(encounter_id, True)
        self._changed()

    def _add(self) -> None:
        if self._encounter is None:
            return
        already = {c.entity_id for c in self._combatants}
        candidates = [
            entity
            for entity in self._entities.values()
            if entity.kind in FIGHTING_KINDS and entity.id not in already
        ]
        dialog = AddCombatantsDialog(candidates, self)
        if not dialog.exec():
            return
        for entity_id in dialog.chosen():
            self._ctx.repos.encounters.add(
                self._encounter.id,
                entity_id=entity_id,
                tiebreak=self._dex_modifier(entity_id),
            )
        self._changed()

    def _roll_initiative(self) -> None:
        """A d20 each, and the Dexterity bonus where there is a sheet."""
        from canon_keeper_protocol.dice import roll

        if self._encounter is None:
            return
        for combatant in self._combatants:
            bonus = self._dex_modifier(combatant.entity_id)
            result = roll(f"1d20{bonus:+d}")
            self._ctx.repos.encounters.set_initiative(combatant.id, result.total)
        self._changed()

    def _edit_initiative(self, item: QListWidgetItem) -> None:
        combatant = self._combatant_of(item)
        if combatant is None:
            return
        value, ok = QInputDialog.getInt(
            self,
            "Initiative",
            f"What did {self._name_of(combatant)} roll?",
            combatant.initiative if combatant.initiative is not None else 10,
            -20,
            60,
        )
        if ok:
            self._ctx.repos.encounters.set_initiative(combatant.id, value)
            self._changed()

    def _start_or_end(self) -> None:
        """Start the fight, or end the one that is running.

        A fight is made unstarted on purpose: laying it out is a job, and
        nothing should be taking turns while tokens are still being dragged
        about. Starting it is the moment the rules -- and the machines -- come
        on.
        """
        if self._encounter is None:
            return
        if self._encounter.has_begun:
            self._end()
            return
        self._ctx.repos.encounters.begin(self._encounter.id)
        self._changed()

    def _start_or_next(self) -> None:
        if self._encounter is None:
            return
        if not self._encounter.has_begun:
            self._ctx.repos.encounters.begin(self._encounter.id)
            self._changed()
            return
        # Ask the host, which knows the hit points and holds the dice: passing
        # the turn is also when a dying character rolls their death save.
        self._ctx.bus.turn_requested.emit("next")
        self._changed()

    def _end(self) -> None:
        if self._encounter is None:
            return
        self._ctx.repos.encounters.end(self._encounter.id)
        self._changed()

    def _attack(self) -> None:
        """Whoever is up swings at somebody the DM picks.

        The move half of a turn is dragging, which the map already does. This
        is the other half, and it goes to the host because the dice and the hit
        points coming off the other creature are the host's.
        """
        acting = self._current_combatant()
        if acting is None:
            self._ctx.bus.status_message.emit("Nobody is up. Press Start.")
            return

        others = [c for c in self._combatants if c.id != acting.id]
        if not others:
            self._ctx.bus.status_message.emit("There is nobody else in the fight.")
            return

        weapons = self._weapons_of(acting)
        target, weapon = AttackDialog(
            self._name_of(acting),
            [(c.id, self._name_of(c)) for c in others],
            weapons,
            self,
        ).ask()
        if target is None:
            return

        # Staged, not sent, once a fight is running -- so a swing chosen here can
        # have a walk added to it on the map before either happens. The dialog
        # and the wheel are the same turn, and having one of them commit while
        # the other waits would make which door you came through matter.
        if self._encounter is not None and self._encounter.has_begun:
            self._stage(
                TurnPlan(combatant=acting.id, target=target, weapon=weapon)
            )
            return

        self._ctx.bus.turn_taken.emit(
            {"combatant": acting.id, "target": target, "weapon": weapon}
        )

    # ------------------------------------------------------------- the wheel
    #
    # Space on the map opens a ring of choices around whoever is up. It is the
    # same turn the Attack dialog sends -- one path to the host, so a turn
    # taken from the map and a turn taken from a dialog cannot mean two
    # different things -- but taken without looking away from the fight.

    def _choices_for(self, combatant) -> list[Choice]:
        """What this creature could do now: go somewhere, or hit somebody.

        One wedge per weapon rather than one "attack" wedge that then asks
        which: the question "what can they do" is answered by the wheel itself,
        and a dagger and a longbow are not the same answer.
        """
        if self._condition_of(combatant) != death.UP:
            return []
        choices: list[Choice] = []
        if combatant.on_map and self._squares_left(combatant) > 0:
            choices.append(Choice(kind="move", label="Move"))
        # A spent action takes every weapon with it: one action is one swing,
        # so offering a wedge the host would refuse would be a wheel that lies.
        if not (self._encounter is not None and self._encounter.action_used):
            for weapon in self._weapons_of(combatant):
                choices.append(Choice(kind="attack", label=weapon, weapon=weapon))
        return choices

    def _offer_wheel(self, combatant_id: int) -> None:
        combatant = self._by_id(combatant_id)
        if combatant is None:
            return
        if self._encounter is None or not self._encounter.running:
            self._ctx.bus.status_message.emit("The fight has not started.")
            return
        if not self._is_turn(combatant):
            self._ctx.bus.status_message.emit(
                f"It is not {self._name_of(combatant)}'s turn."
            )
            return
        choices = self._choices_for(combatant)
        if not choices:
            self._ctx.bus.status_message.emit(
                f"{self._name_of(combatant)} has nothing left this turn."
            )
            return
        self._map.offer(combatant_id, choices)
        self._hint.setText(
            "Pick a wedge, then click a square to move or a creature to hit. "
            "Escape puts the wheel away."
        )

    # ------------------------------------------------------- staging a turn
    #
    # A turn is a move and an action, and until now the DM got neither as a
    # thing: each wedge fired the moment it was picked, so "walk over there and
    # swing" was two commitments with no way back from the first. A player has
    # had the better deal all along -- they are shown the whole turn and accept
    # it -- and the map was built for this: see `TurnPlan`, which says its own
    # future is "a matter of holding it a little longer".
    #
    # So the DM stages, reads it, and commits. It is also what parity with the
    # agent requires: autopilot could compose a move-and-attack through
    # `take_turn` and the DM's own panel could not.

    def _stage(self, plan) -> None:
        """Append what was just picked to the turn being lined up.

        Appended, not merged. Picking two moves is a turn that walks, does
        something, and walks on -- which is the whole point of a turn being a
        sequence, and was impossible while a plan carried one move and one
        attack in that order.

        Staging for a different creature starts again: two half-turns for two
        creatures is not a thing anybody meant to ask for.
        """
        if plan is None or plan.is_empty:
            return
        if self._staged_for != plan.combatant:
            self._staged_for = plan.combatant
            self._staged_steps = []
        if plan.move is not None:
            self._staged_steps.append(turns.a_move(*plan.move))
        if plan.target is not None:
            self._staged_steps.append(turns.a_swing(plan.target, plan.weapon))
        self._show_staged()

    def _clear_staged(self) -> None:
        if not self._staged_steps:
            return
        self._staged_steps = []
        self._staged_for = None
        self._show_staged()

    def _commit_staged(self) -> None:
        """Send the staged turn to the host, which is the only thing that acts.

        Held, not cleared. The host may refuse it -- too far, already acted, out
        of reach -- and a refusal is nearly always something to *adjust*. Losing
        the turn on refusal would mean rebuilding the whole thing to move one
        square less. :meth:`_on_turn_settled` decides what is left of it.
        """
        if not self._staged_steps or self._staged_for is None:
            return
        self._ctx.bus.turn_taken.emit(
            {"combatant": self._staged_for, "steps": list(self._staged_steps)}
        )

    def _on_turn_settled(self, went_through: bool, why_not: str, done: int) -> None:
        """Clear what happened; keep what did not, and say why.

        ``done`` is the part that has already been paid for. A turn that walked
        three squares and was then refused its swing keeps the swing and drops
        the walk -- pressing **Do it** again must not walk it a second time.
        """
        if done:
            self._staged_steps = self._staged_steps[done:]
        if went_through or not self._staged_steps:
            self._staged_steps = []
            self._staged_for = None
            self._show_staged()
            return

        self._show_staged()
        if why_not:
            # In the bar rather than only the status line, because the bar is
            # what they are about to press again.
            self._staged_text.setText(f"{self._staged_text.text()}  — {why_not}")

    def _show_staged(self) -> None:
        """The bar says it in words; the map shows it in squares.

        Both, because they answer different questions. "Move to 3,-1" is precise
        and unreadable at a glance; a dotted line to a square is instant and says
        nothing about which weapon.
        """
        if not self._staged_steps or self._staged_for is None:
            self._staged_bar.setVisible(False)
            self._map.set_preview(None)
            return

        actor = self._combatant(self._staged_for)
        self._staged_text.setText(
            turns.describe_steps(
                self._staged_steps,
                lambda combatant_id: self._name_of(self._combatant(combatant_id))
                if self._combatant(combatant_id) is not None
                else "",
                who=self._name_of(actor) if actor is not None else "They",
            )
        )
        self._staged_bar.setVisible(True)

        # Where they end up, and the first thing they swing at. The map draws one
        # destination and one sword, so a turn with several of each shows the
        # last square it reaches -- the rest of the order is what the bar is for.
        walks = [s for s in self._staged_steps if s["kind"] == turns.MOVE]
        swings = [s for s in self._staged_steps if s["kind"] == turns.ATTACK]
        hit = self._combatant(swings[0]["target"]) if swings else None
        self._map.set_preview(
            Preview(
                token=self._staged_for,
                to=(walks[-1]["x"], walks[-1]["y"]) if walks else None,
                target=(hit.x, hit.y) if hit is not None and hit.on_map else None,
            )
        )

    def _combatant(self, combatant_id):
        return next(
            (c for c in self._combatants if c.id == combatant_id), None
        )

    def _weapons_of(self, combatant) -> list[str]:
        entity = self._entities.get(combatant.entity_id)
        sheet = (getattr(entity, "data", None) or {}).get("sheet") or {}
        from canon_keeper.rules import attack

        try:
            return [w.name for w in attack.weapons_of(sheet, self._content_for_rules())]
        except Exception:  # noqa: BLE001 - a broken sheet is not worth a crash
            return []

    def _dex_modifier(self, entity_id: int | None) -> int:
        """Their Dexterity bonus, or nothing if there is no sheet to read."""
        entity = self._entities.get(entity_id)
        if entity is None:
            return 0
        sheet = (entity.data or {}).get("sheet")
        if not isinstance(sheet, dict):
            return 0
        from canon_keeper.rules import derive

        try:
            return derive.initiative(sheet, self._content_for_rules())
        except Exception:  # noqa: BLE001 - a broken sheet is not worth a crash
            return 0

    def _content_for_rules(self):
        if self._content is None:
            from canon_keeper.content import Content

            self._content = Content(self._ctx.repos.settings)
        return self._content

    # ------------------------------------------------------------- selection

    def _combatant_of(self, item: QListWidgetItem | None):
        if item is None:
            return None
        combatant_id = item.data(Qt.ItemDataRole.UserRole)
        for combatant in self._combatants:
            if combatant.id == combatant_id:
                return combatant
        return None

    def _by_id(self, combatant_id: int):
        for combatant in self._combatants:
            if combatant.id == combatant_id:
                return combatant
        return None

    def _on_row_selected(self, item: QListWidgetItem | None, _previous=None) -> None:
        combatant = self._combatant_of(item)
        self._map.select(combatant.id if combatant is not None else None)
        if combatant is not None and not combatant.on_map:
            self._hint.setText(
                f"{self._name_of(combatant)} is not on the map. Drag them onto "
                "it, or click a square."
            )

    def _on_picked(self, combatant_id: int) -> None:
        if combatant_id < 0:
            return
        for row in range(self._order.count()):
            item = self._order.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == combatant_id:
                self._order.setCurrentItem(item)
                return

    # ----------------------------------------------------------------- moving

    def _on_dropped(self, combatant_id: int, x: int, y: int) -> None:
        """Dragged off the initiative order and onto a square.

        Putting somebody *on* the map, which is the one thing dragging still
        does. There is no gesture that slides a token from one square to
        another any more: that was a teleport, it belonged to nobody's turn,
        it walked through walls and bodies and cost no movement, and it was the
        last way round the rules left on the board. Creatures move by taking a
        turn -- Space on the map -- and come off it by the menu.
        """
        combatant = self._by_id(combatant_id)
        if combatant is None:
            return
        if combatant.on_map:
            self._ctx.bus.status_message.emit(
                f"{self._name_of(combatant)} is already on the map. "
                "Press Space on them to take their turn."
            )
            return
        if not self._ctx.repos.encounters.place(combatant_id, x, y):
            self._ctx.bus.status_message.emit("Someone is already standing there.")
            self._refresh()
            return
        self._changed()

    def _on_obstacle(self, x: int, y: int) -> None:
        """Ctrl-click: a rock goes in, or comes out.

        The refusal case is worth a word rather than a shrug: ctrl-clicking
        under someone looks exactly like ctrl-clicking anywhere else, and
        silence would read as the feature not working.
        """
        if self._encounter is None:
            return
        occupied = self._ctx.repos.encounters.at(self._encounter.id, x, y) is not None
        self._ctx.repos.encounters.toggle_obstacle(self._encounter.id, x, y)
        if occupied:
            self._ctx.bus.status_message.emit(
                "Somebody is standing there. Move them first."
            )
            return
        self._changed()

    def _on_square(self, x: int, y: int) -> None:
        """An empty square. Puts the selected token there, if it is off the map."""
        chosen = self._map.selected
        if chosen is None:
            return
        combatant = self._by_id(chosen)
        if combatant is None or combatant.on_map:
            return
        if self._ctx.repos.encounters.place(chosen, x, y):
            self._changed()

    # ------------------------------------------------------------------ menus

    def _on_list_menu(self, position) -> None:
        item = self._order.itemAt(position)
        combatant = self._combatant_of(item)
        if combatant is not None:
            self._show_menu(combatant, self._order.mapToGlobal(position))

    def _on_map_menu(self, combatant_id: int, position) -> None:
        combatant = self._by_id(combatant_id) if combatant_id >= 0 else None
        if combatant is not None:
            self._show_menu(combatant, position)

    def _show_menu(self, combatant, position) -> None:
        menu = QMenu(self)
        name = self._name_of(combatant)

        if combatant.on_map:
            off = menu.addAction("Take off the map")
            off.triggered.connect(
                lambda: self._take_off(combatant.id)
            )
        if combatant.entity_id is not None and combatant.entity_id not in self._shared:
            share = menu.addAction("Share with the party")
            share.triggered.connect(lambda: self._share(combatant.entity_id))
        if self._is_pc(combatant):
            simulate = menu.addAction(
                "Play them myself again" if combatant.simulated
                else "Let autopilot play them"
            )
            simulate.triggered.connect(
                lambda: self._simulate(combatant.id, not combatant.simulated)
            )
        sides = menu.addMenu("Side")
        for team in self._teams:
            action = sides.addAction(team.name)
            action.setCheckable(True)
            action.setChecked(team.id == combatant.team_id)
            action.triggered.connect(
                lambda _checked=False, t=team.id: self._set_team(combatant.id, t)
            )
        sides.addSeparator()
        made = sides.addAction("New side...")
        made.triggered.connect(lambda: self._new_team(combatant.id))

        menu.addSeparator()
        remove = menu.addAction(f"Take {name} out of the fight")
        remove.triggered.connect(lambda: self._remove(combatant.id, name))

        # Everything above belongs to this panel and is why you right-clicked
        # here, so it stays at the top. What a creature carries with it
        # everywhere goes underneath.
        entity = self._entities.get(combatant.entity_id)
        if entity is not None:
            entity_actions.fill(
                menu,
                self._ctx,
                entity_actions.Target(
                    entity_id=entity.id,
                    kind=entity.kind,
                    name=entity.name,
                    panel="encounter",
                    extra={"combatant": combatant.id},
                ),
            )
        menu.exec(position)

    def _set_team(self, combatant_id: int, team_id: int | None) -> None:
        self._ctx.repos.encounters.set_team(combatant_id, team_id)
        self._changed()

    def _new_team(self, combatant_id: int) -> None:
        """Another side, with whoever was right-clicked already on it.

        Made from the menu of the creature that needs it rather than from a
        Teams dialog somewhere, because a third side is something a DM realises
        they want *about a particular creature* -- the guard who turns, the
        summoned thing that answers to nobody.
        """
        if self._encounter is None:
            return
        name, ok = QInputDialog.getText(self, "New side", "What are they called?")
        if not ok or not name.strip():
            return
        team = self._ctx.repos.encounters.add_team(self._encounter.id, name.strip())
        self._ctx.repos.encounters.set_team(combatant_id, team.id)
        self._changed()

    def _simulate_selected(self) -> None:
        combatant = self._by_id(self._map.selected) if self._map.selected else None
        if combatant is None:
            self._ctx.bus.status_message.emit(
                "Pick a character first -- this hands one of them to autopilot."
            )
            return
        if not self._is_pc(combatant):
            self._ctx.bus.status_message.emit(
                f"{self._name_of(combatant)} has no player, so autopilot already "
                "runs them."
            )
            return
        self._simulate(combatant.id, not combatant.simulated)

    def _simulate(self, combatant_id: int, on: bool) -> None:
        """Hand a character to autopilot for this fight, or take it back.

        Written straight to the database and announced: the DM's app is the
        host, and this is the DM's decision about their own table.
        """
        self._ctx.repos.encounters.set_simulated(combatant_id, on)
        self._changed()

    def _take_off(self, combatant_id: int) -> None:
        self._ctx.repos.encounters.place(combatant_id, None, None)
        self._changed()

    def _share(self, entity_id: int) -> None:
        self._ctx.repos.shares.share(self._ctx.campaign_id, entity_id)
        # The Table panel republishes the entity, which is what actually puts it
        # on a player's screen; the map only stops drawing it dotted.
        self._ctx.bus.share_changed.emit(entity_id)
        self._changed()

    def _remove(self, combatant_id: int, name: str) -> None:
        confirm = QMessageBox.question(
            self,
            "Out of the fight",
            f"Take {name} out of this fight?\n\nThey stay in the campaign; only "
            "the initiative order and the map forget them.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._ctx.repos.encounters.remove(combatant_id)
        self._changed()
