"""The dockable workspace.

Every panel lives in a QDockWidget, so undocking it produces a real OS window
you can drop on a second monitor, and the whole arrangement round-trips through
``saveState``/``restoreState``.

One rule matters more than the rest: **every dock must have an objectName** --
we use the panel id -- because Qt silently drops nameless docks when restoring,
and the symptom looks like "my layout keeps resetting itself".
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QByteArray, QEvent, Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QWidget,
)

from canon_keeper import __version__, campaigns, config
from canon_keeper_core.content import ATTRIBUTION as SRD_ATTRIBUTION
from canon_keeper.plugin import (
    API_VERSION,
    REACH_EVERYWHERE,
    REACH_PANEL,
    REACH_WINDOW,
    AppContext,
)
from canon_keeper.shell import keys
from canon_keeper_core.repo.layouts import AUTOSAVE_NAME
from canon_keeper.shell.attention import Attention
from canon_keeper.templates import build
from canon_keeper.shell.loader import LoadedPanel, LoadError
from canon_keeper.shell.rename_panels import RenamePanelsDialog
from canon_keeper.shell.theme import Theme, ThemeController

#: A dock coming or going. Four events rather than two because a dock closed
#: before the window is on screen never gets a Show -- only a ShowToParent --
#: and the menus are set up before the window is shown.
_APPEARING = (
    QEvent.Type.Show,
    QEvent.Type.Hide,
    QEvent.Type.ShowToParent,
    QEvent.Type.HideToParent,
)

#: Bumped if the set of docks changes in a way that makes old saved states
#: meaningless. Qt refuses to restore a state saved under a different version,
#: which is the desired behaviour -- a stale layout is dropped, not misapplied.
LAYOUT_VERSION = 1


class MainWindow(QMainWindow):
    def __init__(
        self,
        ctx: AppContext,
        panels: list[LoadedPanel],
        errors: list[LoadError],
        log: logging.Logger,
        theme: "ThemeController | None" = None,
    ) -> None:
        super().__init__()
        self._ctx = ctx
        self._log = log
        self._theme = theme
        #: Set when the user asks for the chooser again; app.main loops on it.
        self.switch_requested = False
        self._errors = list(errors)
        self._docks: dict[str, QDockWidget] = {}
        # Tints a panel that has something waiting, and fades it once the
        # panel has actually been looked at.
        self._attention = Attention(self)
        self._panels: dict[str, LoadedPanel] = {}

        self.setObjectName("CanonKeeperMainWindow")
        self.resize(1280, 820)
        self.setDockNestingEnabled(True)
        self.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks
            | QMainWindow.DockOption.AllowTabbedDocks
            | QMainWindow.DockOption.AnimatedDocks
            | QMainWindow.DockOption.GroupedDragging
        )

        # A zero-size central widget lets the docks occupy the entire window.
        # QMainWindow reserves the centre for a central widget whether or not one
        # is useful here, and this is the standard way to give that space back.
        filler = QWidget(self)
        filler.setMaximumSize(0, 0)
        self.setCentralWidget(filler)

        self._build_panels(panels)
        self._build_menus()
        self._recolour_attention()

        self._ctx.bus.status_message.connect(self._on_status_message)
        self._ctx.bus.panel_names_changed.connect(self._retitle_docks)
        self._ctx.bus.campaign_changed.connect(lambda _id: self._update_title())
        self._ctx.bus.panel_attention.connect(self._attention.flag)
        # The tint sits on the title bar, so it has to follow the theme or it
        # is a bright blue stripe on a dark window.
        self._ctx.bus.theme_changed.connect(lambda _dark: self._recolour_attention())

        self._update_title()
        self._retitle_docks()
        self._restore_initial_layout()

        if self._errors:
            self.statusBar().showMessage(
                f"{len(self._errors)} panel(s) failed to load - see Help > Installed Panels",
                8000,
            )

    # ------------------------------------------------------------------ panels

    def _build_panels(self, panels: list[LoadedPanel]) -> None:
        for entry in panels:
            plugin = entry.plugin
            try:
                widget = plugin.create_widget(self._ctx)
                area = plugin.default_area()
            except Exception as exc:  # noqa: BLE001 - contain the blast radius
                self._log.exception("panel %r failed to build", plugin.id)
                self._errors.append(
                    LoadError(entry.entry_point, f"create_widget() raised: {exc}")
                )
                continue

            if self._ctx.names is not None:
                self._ctx.names.register(plugin.id, plugin.title)
            dock = QDockWidget(self._panel_title(plugin.id, plugin.title), self)
            # Non-negotiable: without this, restoreState() drops the dock.
            dock.setObjectName(plugin.id)
            dock.setWidget(widget)
            dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
            dock.setFeatures(
                QDockWidget.DockWidgetFeature.DockWidgetMovable
                | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                | QDockWidget.DockWidgetFeature.DockWidgetClosable
            )
            self.addDockWidget(area, dock)
            self._attention.watch(plugin.id, dock)
            self._docks[plugin.id] = dock
            self._panels[plugin.id] = entry

    # -------------------------------------------------------------- one-shots

    def _sync_one_shot_actions(self) -> None:
        """These only mean anything for a campaign built from a template."""
        from canon_keeper.shell.storyline import has_storyline

        from_template = bool(build.source(self._ctx.repos))
        self._act_storyline.setVisible(has_storyline(self._ctx))
        self._act_start_again.setVisible(from_template)
        self._act_keep.setVisible(from_template)

    def _show_storyline(self) -> None:
        from canon_keeper.shell.storyline import StorylineDialog

        StorylineDialog(self._ctx, self).exec()

    def _start_again(self) -> None:
        """Put it back to the template's starting point.

        Asked plainly rather than softened. Everything from the last run goes,
        which is the point, and a confirmation that undersold that would be the
        wrong kind of kind.
        """
        answer = QMessageBox.question(
            self,
            "Start again?",
            "Everything from this run goes: the characters as they now stand, "
            "what was said, what was written down.\n\n"
            "It goes back to exactly how the one-shot begins.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            build.restart(self._ctx.repos, self._ctx.campaign_id)
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            QMessageBox.warning(self, "Could not start again", str(exc))
            return
        # Every panel is now showing rows that no longer exist.
        self._ctx.bus.campaign_changed.emit(self._ctx.campaign_id)
        self.statusBar().showMessage("Back to the beginning.", 5000)

    def _keep_one_shot(self) -> None:
        build.release(self._ctx.repos)
        self.statusBar().showMessage(
            "This is your campaign now. It will not offer to start again.", 8000
        )

    def _recolour_attention(self) -> None:
        self._attention.set_colour(self.palette().highlight().color())

    # ------------------------------------------------------------------- menus

    def _build_menus(self) -> None:
        bar = self.menuBar()

        # --- File -----------------------------------------------------------
        file_menu = bar.addMenu("&File")
        self._campaign_menu = file_menu.addMenu("&Campaign")
        self._campaign_menu.aboutToShow.connect(self._populate_campaign_menu)

        act_new = QAction("&New Campaign...", self)
        act_new.triggered.connect(self._new_campaign)
        file_menu.addAction(act_new)

        act_rename = QAction("&Rename Campaign...", self)
        act_rename.triggered.connect(self._rename_campaign)
        file_menu.addAction(act_rename)

        act_switch = QAction("&Open a Different Campaign...", self)
        act_switch.triggered.connect(self._switch_campaign_file)
        file_menu.addAction(act_switch)

        self._act_no_auto = QAction("Stop Opening This Automatically", self)
        self._act_no_auto.triggered.connect(self._clear_autostart)
        file_menu.addAction(self._act_no_auto)

        # --- one-shots, only when this campaign is one ----------------------
        file_menu.addSeparator()
        self._act_storyline = QAction("&Storyline...", self)
        self._act_storyline.triggered.connect(self._show_storyline)
        file_menu.addAction(self._act_storyline)

        self._act_start_again = QAction("Start &Again from the Beginning...", self)
        self._act_start_again.triggered.connect(self._start_again)
        file_menu.addAction(self._act_start_again)

        self._act_keep = QAction("&Keep This One", self)
        self._act_keep.setToolTip(
            "Stop treating this as a one-shot and keep it as a campaign of "
            "your own."
        )
        self._act_keep.triggered.connect(self._keep_one_shot)
        file_menu.addAction(self._act_keep)
        file_menu.aboutToShow.connect(self._sync_one_shot_actions)

        file_menu.addSeparator()
        act_folder = QAction("Open &Data Folder", self)
        act_folder.triggered.connect(self._open_data_folder)
        file_menu.addAction(act_folder)

        act_settings = QAction("&Settings...", self)
        act_settings.triggered.connect(self._show_settings)
        file_menu.addAction(act_settings)

        file_menu.addSeparator()
        act_quit = QAction("&Quit", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        # --- Panels ---------------------------------------------------------
        panels_menu = bar.addMenu("&Panels")
        for panel_id, dock in self._docks.items():
            action = dock.toggleViewAction()
            action.setObjectName(f"toggle_{panel_id}")
            panels_menu.addAction(action)
        panels_menu.addSeparator()
        act_rename = QAction("&Rename Panels...", self)
        act_rename.triggered.connect(self._rename_panels)
        panels_menu.addAction(act_rename)

        act_show_all = QAction("Show &All Panels", self)
        act_show_all.triggered.connect(self._show_all_panels)
        panels_menu.addAction(act_show_all)

        # --- One menu per panel that has anything to offer -------------------
        #
        # After Panels, before View, so the things you *do* sit together and
        # the things that shape the window sit together. A panel with nothing
        # to declare gets no menu rather than an empty one.
        self._panel_menus: dict[str, object] = {}
        #: Every key claim the window knows about, and the clashes among them.
        #: Gathered as panels are built rather than asked for later, because a
        #: panel that failed to load has no claims to gather.
        self._key_claims: list[keys.Claim] = list(self._own_key_claims())
        for panel_id in self._docks:
            self._build_panel_menu(bar, panel_id)
        self._follow_panel_visibility()
        self._note_key_clashes()

        # --- View -----------------------------------------------------------
        view_menu = bar.addMenu("&View")
        theme_menu = view_menu.addMenu("&Theme")
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        for choice in Theme:
            action = QAction(choice.label, self)
            action.setCheckable(True)
            action.setData(choice.value)
            action.setChecked(self._theme is not None and self._theme.theme is choice)
            action.setEnabled(self._theme is not None)
            action.triggered.connect(
                lambda _checked=False, value=choice: self._set_theme(value)
            )
            self._theme_group.addAction(action)
            theme_menu.addAction(action)

        # --- Layouts --------------------------------------------------------
        self._layouts_menu = bar.addMenu("&Layouts")
        self._layouts_menu.aboutToShow.connect(self._populate_layouts_menu)
        self._populate_layouts_menu()

        # --- Help -----------------------------------------------------------
        help_menu = bar.addMenu("&Help")
        act_plugins = QAction("Installed &Panels...", self)
        act_plugins.triggered.connect(self._show_plugins_dialog)
        help_menu.addAction(act_plugins)
        act_about = QAction("&About", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    def _set_theme(self, theme: Theme) -> None:
        if self._theme is None:
            return
        self._theme.set_theme(theme)
        self.statusBar().showMessage(f"Theme: {theme.label}", 4000)

    def _panel_title(self, panel_id: str, default: str) -> str:
        if self._ctx.names is None:
            return default
        return self._ctx.names.resolve(panel_id)

    def _build_panel_menu(self, bar, panel_id: str) -> None:
        """A menu for one panel, if its widget has anything to put in it.

        Built from the widget rather than the plugin because the actions are
        bound methods: "New fight" is a thing *this* Combat panel does, holding
        the campaign it is looking at.

        A widget that raises while listing its actions gets no menu and does
        not stop the window opening -- the same rule the panel loader follows,
        for the same reason.
        """
        entry = self._panels.get(panel_id)
        dock = self._docks.get(panel_id)
        widget = dock.widget() if dock is not None else None
        default = entry.plugin.title if entry is not None else panel_id
        # Gathered before the menu is, and whether or not there is a menu: a
        # panel with no menu items may still handle keys of its own, and a key
        # taken out from under it is exactly what the report is for.
        self._key_claims.extend(
            self._reserved_key_claims(
                panel_id, widget, self._panel_title(panel_id, default)
            )
        )

        lister = getattr(widget, "panel_actions", None)
        if lister is None:
            return
        try:
            wanted = list(lister() or ())
        except Exception:  # noqa: BLE001 - a bad panel is not fatal
            self._log.exception("%s could not list its menu actions", panel_id)
            return
        if not wanted:
            return

        menu = bar.addMenu(self._panel_title(panel_id, default))
        menu.setObjectName(f"menu_{panel_id}")
        for item in wanted:
            # Parented to the *panel*, not to the window. That is what scopes the
            # key: a Qt shortcut reaches as far as its parent widget, so an action
            # owned by the main window fires from anywhere in the main window --
            # including out of the chat box you were typing in.
            owner = widget if self._reach_of(item) == REACH_PANEL else self
            action = QAction(item.label, owner)
            if item.shortcut:
                action.setShortcut(item.shortcut)
                action.setShortcutContext(self._context_for(item, panel_id))
                # Added to the widget as well as the menu, because a shortcut
                # scoped to a widget is only listened for by a widget that holds
                # the action. In a menu alone it would be drawn and never fire.
                if owner is widget and widget is not None:
                    widget.addAction(action)
                self._key_claims.append(
                    keys.Claim(
                        key=item.shortcut,
                        owner=self._panel_title(panel_id, default),
                        reach=self._reach_of(item),
                        what=item.label,
                    )
                )
            action.setEnabled(bool(item.enabled))
            action.triggered.connect(
                lambda _checked=False, run=item.run, which=panel_id: self._run_panel_action(
                    which, run
                )
            )
            menu.addAction(action)
        self._panel_menus[panel_id] = menu

    #: Qt's name for each reach. `WidgetWithChildren` rather than `Widget`
    #: because a panel's focus is almost never on the panel itself -- it is in a
    #: list, a line edit or the map inside it, and a key pressed there is still a
    #: key pressed in that panel.
    _REACHES = {
        REACH_PANEL: Qt.ShortcutContext.WidgetWithChildrenShortcut,
        REACH_WINDOW: Qt.ShortcutContext.WindowShortcut,
        REACH_EVERYWHERE: Qt.ShortcutContext.ApplicationShortcut,
    }

    def _reach_of(self, item) -> str:
        """What the panel asked for, or the default if it asked for nonsense.

        A panel is other people's code. One that names a reach this shell has
        never heard of gets the narrow one, because the failure that matters is a
        key quietly reaching further than anybody intended.
        """
        wanted = getattr(item, "reach", REACH_PANEL)
        return wanted if wanted in self._REACHES else REACH_PANEL

    def _own_key_claims(self) -> list:
        """The keys the shell itself owns. Panels declare their own.

        Only the ones that belong to the window rather than to anything in it.
        Everything a panel handles -- the map's Space, the transcript's
        push-to-talk -- comes from that panel's ``reserved_keys()``, next to the
        handler that reads it, because a list of somebody else's keys kept here
        would go stale the first time they added one.
        """
        return [keys.Claim("Ctrl+Q", "Canon Keeper", REACH_WINDOW, "quit")]

    def _reserved_key_claims(self, panel_id: str, widget, title: str) -> list:
        """What this panel says it handles itself, if it says anything.

        The same forgiveness the menu listing gets: a panel that raises while
        answering costs its own entry in a report and not the window.
        """
        lister = getattr(widget, "reserved_keys", None)
        if lister is None:
            return []
        try:
            wanted = list(lister() or ())
        except Exception:  # noqa: BLE001 - a bad panel is not fatal
            self._log.exception("%s could not list its reserved keys", panel_id)
            return []
        return [
            keys.Claim(
                key=item.key,
                owner=title,
                reach=self._reach_of(item),
                what=getattr(item, "what", ""),
            )
            for item in wanted
            if getattr(item, "key", "")
        ]

    def key_clashes(self) -> list:
        """Every clash between the keys this window's panels have claimed.

        Public because it is the thing a settings page would show. Recomputed on
        each call rather than cached: panels come and go, and a stale list of
        clashes is worse than none.
        """
        return keys.clashes(self._key_claims)

    def _show_settings(self) -> None:
        """The window's settings. Built fresh each time it is opened.

        Not held onto, because what it shows changes: panels open and close, and
        a dialog remembering the keys of a panel that is no longer there would be
        confidently wrong about the one thing it exists to be right about.
        """
        from canon_keeper.shell.settings_dialog import SettingsDialog

        dialog = SettingsDialog(list(self._key_claims), self.key_clashes(), self)
        dialog.exec()

    def _note_key_clashes(self) -> None:
        found = self.key_clashes()
        if not found:
            return
        # In the log now, and in the log whether or not anybody ever builds the
        # page that shows them: the point is that a key that will not work is
        # discoverable before somebody presses it forty times.
        self._log.warning("%d keyboard clash(es) among the panels:", len(found))
        for clash in found:
            self._log.warning("  %s", clash)

    def _context_for(self, item, panel_id: str):
        reach = self._reach_of(item)
        if reach != REACH_PANEL:
            # Worth a line in the log. A key that reaches past its own panel is a
            # key every other panel has lost, and the question "why does F9 not
            # work in here" has to be answerable.
            self._log.info(
                "%s claims %r %s", panel_id, item.shortcut, {
                    REACH_WINDOW: "across the window",
                    REACH_EVERYWHERE: "across the whole machine",
                }[reach]
            )
        return self._REACHES[reach]

    def _follow_panel_visibility(self) -> None:
        """A closed panel takes its menu with it.

        A menu for something that is not on screen is a menu whose items act on
        a panel you cannot see them act on -- "New fight" quietly filling a list
        nobody is looking at. Closing a panel is how you say you are not doing
        that right now, so the menu goes too, and comes back when it does.
        """
        for panel_id, dock in self._docks.items():
            if panel_id not in self._panel_menus:
                continue
            # Watched rather than wired to ``toggleViewAction``: that action is
            # only in step once the window is on screen, and the docks are
            # arranged before it ever is.
            dock.installEventFilter(self)
            self._sync_panel_menu(panel_id)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt's name
        if event.type() in _APPEARING:
            for panel_id, dock in self._docks.items():
                if dock is watched:
                    self._sync_panel_menu(panel_id)
                    break
        return super().eventFilter(watched, event)

    def _sync_panel_menu(self, panel_id: str) -> None:
        menu = self._panel_menus.get(panel_id)
        dock = self._docks.get(panel_id)
        if menu is not None and dock is not None:
            menu.menuAction().setVisible(not dock.isHidden())

    def _run_panel_action(self, panel_id: str, run) -> None:
        """One menu item, with its failure kept to itself."""
        try:
            run()
        except Exception:  # noqa: BLE001 - a bad panel is not fatal
            self._log.exception("%s failed to carry out a menu action", panel_id)

    def _retitle_docks(self) -> None:
        """Re-label every dock after a rename, keeping objectName untouched.

        The name on the title bar is cosmetic; the objectName is what saved
        layouts are keyed on, so renaming must never touch it.
        """
        for panel_id, dock in self._docks.items():
            entry = self._panels.get(panel_id)
            default = entry.plugin.title if entry is not None else panel_id
            dock.setWindowTitle(self._panel_title(panel_id, default))
            if self._ctx.names is not None:
                dock.toggleViewAction().setText(dock.windowTitle())
                dock.setToolTip(self._ctx.names.describe(panel_id))
            # The panel's own menu carries the same name, so renaming a panel
            # renames it in both places rather than leaving them disagreeing.
            menu = getattr(self, "_panel_menus", {}).get(panel_id)
            if menu is not None:
                menu.setTitle(dock.windowTitle())

    def _rename_panels(self) -> None:
        if self._ctx.names is None:
            return
        dialog = RenamePanelsDialog(self._ctx, self)
        if dialog.exec():
            dialog.apply()
            self._retitle_docks()

    def _switch_campaign_file(self) -> None:
        """Close this workspace and go back to the chooser."""
        self.switch_requested = True
        self.close()

    def _clear_autostart(self) -> None:
        campaigns.clear_autostart()
        self.statusBar().showMessage(
            "The chooser will appear next time you start.", 5000
        )

    def _open_data_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.data_dir())))

    def _populate_campaign_menu(self) -> None:
        self._campaign_menu.clear()
        for campaign in self._ctx.repos.campaigns.list():
            action = QAction(campaign.name, self)
            action.setCheckable(True)
            action.setChecked(campaign.id == self._ctx.campaign_id)
            action.triggered.connect(
                lambda _checked=False, cid=campaign.id: self._switch_campaign(cid)
            )
            self._campaign_menu.addAction(action)

    def _populate_layouts_menu(self) -> None:
        self._layouts_menu.clear()

        act_save = QAction("&Save Current Layout...", self)
        act_save.triggered.connect(self._save_layout_as)
        self._layouts_menu.addAction(act_save)

        act_reset = QAction("&Reset to Default Arrangement", self)
        act_reset.triggered.connect(self._apply_default_arrangement)
        self._layouts_menu.addAction(act_reset)

        layouts = self._ctx.repos.layouts.list()
        if layouts:
            self._layouts_menu.addSeparator()
            for layout in layouts:
                label = layout.name + (" *" if layout.is_default else "")
                sub = self._layouts_menu.addMenu(label)

                act_apply = QAction("Apply", self)
                act_apply.triggered.connect(
                    lambda _c=False, n=layout.name: self._apply_layout(n)
                )
                sub.addAction(act_apply)

                act_overwrite = QAction("Overwrite with current", self)
                act_overwrite.triggered.connect(
                    lambda _c=False, n=layout.name: self._save_layout(n)
                )
                sub.addAction(act_overwrite)

                act_default = QAction("Open on startup", self)
                act_default.setCheckable(True)
                act_default.setChecked(layout.is_default)
                act_default.triggered.connect(
                    lambda _c=False, n=layout.name: self._ctx.repos.layouts.set_default(n)
                )
                sub.addAction(act_default)

                sub.addSeparator()
                act_delete = QAction("Delete", self)
                act_delete.triggered.connect(
                    lambda _c=False, n=layout.name: self._delete_layout(n)
                )
                sub.addAction(act_delete)

    # ----------------------------------------------------------------- layouts

    def _save_layout(self, name: str, *, is_default: bool = False) -> None:
        self._ctx.repos.layouts.save(
            name,
            bytes(self.saveGeometry()),
            bytes(self.saveState(LAYOUT_VERSION)),
            is_default=is_default,
        )
        self._log.info("saved layout %r", name)

    def _save_layout_as(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Save Layout", "Layout name:", text="At the table"
        )
        name = name.strip()
        if not ok or not name:
            return
        if name == AUTOSAVE_NAME:
            QMessageBox.warning(self, "Save Layout", f"{AUTOSAVE_NAME} is a reserved name.")
            return
        self._save_layout(name)
        self.statusBar().showMessage(f"Layout {name} saved", 4000)

    def _apply_layout(self, name: str) -> bool:
        layout = self._ctx.repos.layouts.get(name)
        if layout is None:
            return False
        self.restoreGeometry(QByteArray(layout.geometry))
        # Docks named in the state whose panel is not installed are skipped by
        # Qt; the rest of the arrangement still restores.
        ok = self.restoreState(QByteArray(layout.state), LAYOUT_VERSION)
        if not ok:
            self._log.warning("layout %r could not be restored (version mismatch?)", name)
        return ok

    def _delete_layout(self, name: str) -> None:
        confirm = QMessageBox.question(self, "Delete Layout", f"Delete the layout {name}?")
        if confirm == QMessageBox.StandardButton.Yes:
            self._ctx.repos.layouts.delete(name)
            self.statusBar().showMessage(f"Layout {name} deleted", 4000)

    def _restore_initial_layout(self) -> None:
        default = self._ctx.repos.layouts.default()
        if default and self._apply_layout(default.name):
            self._log.info("restored default layout %r", default.name)
            return
        if self._apply_layout(AUTOSAVE_NAME):
            self._log.info("restored previous session layout")
            return
        self._log.info("no saved layout; using default arrangement")

    def _apply_default_arrangement(self) -> None:
        """Put every dock back where its plugin asked to be, and show it."""
        for panel_id, dock in self._docks.items():
            entry = self._panels[panel_id]
            dock.setFloating(False)
            self.removeDockWidget(dock)
            try:
                area = entry.plugin.default_area()
            except Exception:  # noqa: BLE001
                area = Qt.DockWidgetArea.LeftDockWidgetArea
            self.addDockWidget(area, dock)
            dock.show()
        self.statusBar().showMessage("Panels reset to their default arrangement", 4000)

    def _show_all_panels(self) -> None:
        for dock in self._docks.values():
            dock.show()

    # --------------------------------------------------------------- campaigns

    def _switch_campaign(self, campaign_id: int) -> None:
        if campaign_id == self._ctx.campaign_id:
            return
        self._ctx.campaign_id = campaign_id
        self._ctx.bus.campaign_changed.emit(campaign_id)
        self._update_title()

    def _new_campaign(self) -> None:
        name, ok = QInputDialog.getText(self, "New Campaign", "Campaign name:")
        name = name.strip()
        if not ok or not name:
            return
        campaign = self._ctx.repos.campaigns.create(name)
        self._switch_campaign(campaign.id)

    def _rename_campaign(self) -> None:
        current = self._ctx.repos.campaigns.get(self._ctx.campaign_id)
        if current is None:
            return
        name, ok = QInputDialog.getText(
            self, "Rename Campaign", "Campaign name:", text=current.name
        )
        name = name.strip()
        if ok and name:
            self._ctx.repos.campaigns.rename(current.id, name)
            self._update_title()

    def _update_title(self) -> None:
        campaign = self._ctx.repos.campaigns.get(self._ctx.campaign_id)
        label = campaign.name if campaign else "no campaign"
        self.setWindowTitle(f"Canon Keeper - {label}")

    # -------------------------------------------------------------------- misc

    def _on_status_message(self, text: str) -> None:
        self.statusBar().showMessage(text, 5000)

    def _show_plugins_dialog(self) -> None:
        lines = [f"Panel API version {API_VERSION}", ""]
        if self._panels:
            lines.append("Loaded:")
            lines += [
                f"  - {e.plugin.title}  [{e.plugin.id}]  from {e.module}"
                for e in self._panels.values()
            ]
        else:
            lines.append("No panels loaded.")
        if self._errors:
            lines += ["", "Failed:"]
            lines += [f"  - {err.entry_point}: {err.reason}" for err in self._errors]
        QMessageBox.information(self, "Installed Panels", "\n".join(lines))

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Canon Keeper",
            f"<b>Canon Keeper</b> {__version__}<br><br>"
            "A dockable desktop assistant for running D&amp;D 5e.<br>"
            "What the DM actually says is the only source of truth."
            "<br><br><small>" + SRD_ATTRIBUTION + "</small>",
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        try:
            self._save_layout(AUTOSAVE_NAME)
        except Exception:  # noqa: BLE001 - never block exit on a bad write
            self._log.exception("failed to autosave layout")
        super().closeEvent(event)
