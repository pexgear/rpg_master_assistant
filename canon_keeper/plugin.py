"""The public plugin contract.

This module is the entire supported surface for third-party panels. Anything
not reachable from here is private and may change without warning.

A plugin package declares itself in its own ``pyproject.toml``::

    [project.entry-points."canonkeeper.panels"]
    weather = "my_pkg.panel:WeatherPanel"

and provides a class satisfying :class:`PanelPlugin`.

A panel's **widget** may also implement ``panel_actions()``, returning a list of
:class:`PanelAction`. The shell gives any panel that does a menu of its own, so
what a panel can do is reachable without going to look at the panel first.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Protocol, runtime_checkable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    from canon_keeper.bus import Bus
    from canon_keeper_core.repo import Repos

#: Bumped only on a breaking change to :class:`AppContext` or
#: :class:`PanelPlugin`. Panels declaring a different major version are skipped
#: rather than loaded, so an outdated plugin degrades to "absent", not "crash".
#:
#: 2 added :mod:`canon_keeper.entity_actions` -- the menu a creature carries
#: with it wherever it is shown -- and ``AppContext.session_address``.
API_VERSION = 2

#: The entry-point group scanned at startup.
ENTRY_POINT_GROUP = "canonkeeper.panels"

# ------------------------------------------------------------- how far a key goes
#
# **A key belongs to the panel you are looking at.** That is the default, and it
# is the default because the alternative does not survive a second panel: a
# window-wide key is claimed by whichever panel declared it first and then fires
# while you are typing in the chat box, reading the transcript, or naming a
# creature -- none of which asked for it.
#
# Panel-scoped keys also stop clashing with each other. Two panels may both use
# the same key for their own version of a thing, the way two applications both
# use Ctrl+N, because only one of them is ever listening.
#
# The wider reaches exist and are deliberate rather than forbidden. Push-to-talk
# is the honest example: F9 has to work while you are looking at the map, because
# the point of it is to record what you are saying *about* the map.

#: While the focus is inside this panel. The default, and where a key should be
#: unless there is a reason.
REACH_PANEL = "panel"
#: Anywhere in the Canon Keeper window, whichever panel has the focus. For
#: something that is about the whole app rather than about one panel.
REACH_WINDOW = "window"
#: Even when another application is in front. Costs the key for everything else
#: running on the machine, so it wants a strong reason and an unusual key.
REACH_EVERYWHERE = "everywhere"


@dataclass(frozen=True)
class PanelAction:
    """One item in a panel's own menu.

    A panel's buttons live inside it, which is fine while you are looking at
    that panel and useless when you are not: starting a fight from the
    Characters panel meant finding Combat first. A widget may list the things
    it can do, and the shell gives it a menu of its own next to File and View.

    ``run`` is called with no arguments and is a bound method of the widget, so
    it has whatever state it needs. Keep ``label`` short -- it is a menu item,
    not a sentence.
    """

    label: str
    run: "Callable[[], None]"
    #: A key sequence, e.g. "Ctrl+Shift+N". Empty for none. It reaches as far as
    #: :attr:`reach` says and no further, so two panels may claim the same key
    #: without either having to know about the other.
    shortcut: str = ""
    #: How far that key reaches: ``REACH_PANEL`` (the default), ``REACH_WINDOW``
    #: or ``REACH_EVERYWHERE``. See the note above them. A menu item is always
    #: clickable whatever this says -- reach is about the *key*, not about the
    #: action, and a menu that greyed itself out depending on where the focus was
    #: would be a menu you could not use.
    reach: str = REACH_PANEL
    #: Shown greyed when False. Evaluated when the menu is built, so a panel
    #: that wants this to change should say so on the bus and let the shell
    #: rebuild rather than holding a reference to the action.
    enabled: bool = True


@dataclass(frozen=True)
class ReservedKey:
    """A key a panel handles itself, rather than through a menu item.

    Declared so it can be *reported*. The map reads Space in its own key handler
    and nothing outside it knows: a panel taking Space window-wide would simply
    win, and the map would go quiet -- no error, no warning from Qt, just a grid
    ignoring the key its own tooltip documents.

    A panel lists these from ``reserved_keys()``, the way it lists its menu items
    from ``panel_actions()``. Next to the handler rather than in the shell,
    because a list of somebody else's keys kept somewhere else goes stale the
    first time they add one.

    It buys a mention in the report and nothing else. The key still works because
    the panel's own handler runs, not because anything here arranged it.
    """

    key: str
    #: What pressing it does, for a person reading the report.
    what: str = ""
    #: Almost always the default. A panel whose own handler is reached only when
    #: it has the focus is a panel-reach key, whatever else it might wish.
    reach: str = REACH_PANEL


@dataclass(frozen=True)
class PendingJoin:
    """A session the app was launched to join, carried to the Table panel.

    A record rather than a tuple because it grew a fourth thing -- an invite --
    and a four-tuple unpacked in one place and indexed in another is how the
    fourth thing quietly becomes the third.
    """

    url: str
    username: str
    password: str
    #: Set when the person is arriving on an invite rather than a login they
    #: already have. The Table panel enrols instead of logging in.
    invite: str = ""


class AppContext:
    """Everything a panel is given. Passed to :meth:`PanelPlugin.create_widget`."""

    def __init__(
        self,
        repos: "Repos",
        bus: "Bus",
        log: logging.Logger,
        campaign_id: int,
        api_version: int = API_VERSION,
        role: str = "dm",
        shared=None,
        names=None,
    ) -> None:
        self.repos = repos
        self.bus = bus
        self.log = log
        self.api_version = api_version
        #: "dm" or "player". Players run the same app with a reduced panel set;
        #: see PanelPlugin.roles.
        self.role = role
        #: In player mode, the host's filtered view of the campaign. Panels read
        #: this instead of `repos`, because a player's app is only ever shown
        #: what the host decided to send. None for the DM, who reads the real
        #: database.
        self.shared = shared
        #: Set when the app was launched by joining a session: the Table panel
        #: connects with it instead of making the player log in twice.
        self.pending_join: PendingJoin | None = None
        #: Resolves what each panel is called. A panel's `title` is only
        #: its default: the user or the DM may have renamed it.
        self.names = names
        #: Mutated by the shell when the DM opens a different campaign; the
        #: change is announced on ``bus.campaign_changed``.
        self.campaign_id = campaign_id
        #: Where this session is reachable, while it is being hosted. Set by
        #: the Table panel, which owns the server, and read by anything that
        #: needs to hand somebody an address -- an invite, most of all. Empty
        #: when nobody is hosting, which is a state callers must expect rather
        #: than a reason to fail.
        self.session_address = ""


@runtime_checkable
class PanelPlugin(Protocol):
    """What an entry point must resolve to.

    The class is instantiated with no arguments, so keep ``__init__`` trivial and
    do real work in :meth:`create_widget`, where failures are contained.
    """

    #: Stable, unique, and used verbatim as the QDockWidget objectName. Changing
    #: it orphans every saved layout that mentions the panel, so treat it as
    #: permanent once released.
    id: str

    #: Shown in the dock title bar and the Panels menu.
    title: str

    #: The API_VERSION this panel was written against.
    api_version: int

    #: Optional. Which roles the panel appears for -- ("dm",), ("player",) or
    #: both. A panel that does not declare it is shown in every role, so
    #: existing plugins are unaffected.
    roles: tuple[str, ...]

    def create_widget(self, ctx: AppContext) -> QWidget:
        """Build the panel's contents. Raising here disables only this panel."""
        ...

    def default_area(self) -> Qt.DockWidgetArea:
        """Where the dock lands the first time, before any saved layout exists."""
        ...


class BasePanel:
    """Optional convenience base. Implementing the Protocol directly is fine."""

    id: str = "unnamed"
    title: str = "Unnamed"
    api_version: int = API_VERSION
    roles: tuple[str, ...] = ("dm", "player")

    def create_widget(self, ctx: AppContext) -> QWidget:  # pragma: no cover
        raise NotImplementedError

    def default_area(self) -> Qt.DockWidgetArea:
        return Qt.DockWidgetArea.LeftDockWidgetArea
