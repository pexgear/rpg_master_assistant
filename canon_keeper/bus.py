"""The signal bus that keeps panels from importing one another.

Panels never talk directly. Characters emits ``active_entity_changed``; the
Conversation panel hears it and retargets. That indirection is what makes the
plugin system real rather than decorative — a third-party panel gets exactly the
same wiring the first-party ones use.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class Bus(QObject):
    """Application-wide events. Every payload is a row id, or -1 for 'none'."""

    # An entity was created, edited or deleted.
    entity_changed = Signal(int)
    entity_deleted = Signal(int)

    # Selection: what the DM is looking at right now.
    active_entity_changed = Signal(int)
    active_location_changed = Signal(int)

    # Who may see an entity changed. The host republishes it, so a revoked
    # share actually takes the thing off the player's screen.
    share_changed = Signal(int)

    # A player asking the host to change their own character. The host decides;
    # the change only becomes real when it is echoed back.
    player_edit_requested = Signal(int, dict)

    # A panel was renamed, by you or by the DM. The shell re-titles the docks.
    panel_names_changed = Signal()

    # The fight changed: someone moved, the turn passed, the order grew. No id
    # travels with it -- a client is sent the whole encounter at once, because
    # a map whose tokens and turn marker can disagree about how current they
    # are is worse than one that cannot.
    encounter_changed = Signal()

    # A turn worked out for you and waiting on your word: the payload the host
    # sent, then its id when it is no longer waiting, then your answer going
    # back. Three signals rather than one, because "offered", "withdrawn" and
    # "answered" happen in different places and at different times.
    action_proposed = Signal(dict)
    action_withdrawn = Signal(str)
    action_answered = Signal(str, bool, str)  # (id, accept, what you meant)

    # The DM taking a turn by hand, with no agent involved: {combatant, move,
    # target, weapon}. It goes to the host like everything else, because the
    # dice and the hit points are the host's even when the DM is the one asking.
    turn_taken = Signal(dict)

    # What became of it: (went through, why not). A turn is staged before it is
    # sent, and a refusal has to reach whoever is holding it -- "too far for a
    # battleaxe" is something to adjust, and throwing the staged turn away would
    # mean rebuilding it from nothing to move one square less.
    # (went through, why not, how many steps happened). The count is the part
    # that matters for a turn made of steps: three squares and then a refused
    # swing is three squares *spent*, and whoever is holding the turn has to stop
    # holding the part that already happened or committing again walks it twice.
    turn_settled = Signal(bool, str, int)

    # A player handing their own character to autopilot for this fight, or
    # taking it back: (combatant id, on). Yours to give without finding the DM.
    simulate_requested = Signal(int, bool)

    # The DM passing the turn: "next". It goes to the host, because passing the
    # turn can roll a death save, and dice are the host's. The panel falls back
    # to advancing the order itself when there is no host to ask -- a DM laying
    # a fight out on their own is not a table, and nothing is being decided.
    turn_requested = Signal(str)

    # Something to show on the map: a walk, a swing, a creature going down.
    # Described by the host so that every screen at the table shows the same
    # thing, rather than each working its own version out from two states.
    play = Signal(dict)

    # Canon.
    fact_committed = Signal(int)
    utterance_added = Signal(int)

    # The open campaign was switched.
    campaign_changed = Signal(int)

    # Light/dark switched. True when the new appearance is dark. Panels that
    # paint their own colours -- syntax highlighting, charts -- should listen.
    theme_changed = Signal(bool)

    # How the join the app was *launched* with turned out: (ok, reason).
    # Emitted once, by the Table panel, for the connection it makes on startup
    # and for nothing afterwards. The shell waits for it before showing the
    # window, so a wrong password leaves you in the chooser being asked again
    # rather than inside an empty app with an error in the chat.
    session_ready = Signal(bool, str)

    # Free-text status for the main window's status bar.
    status_message = Signal(str)

    # A change of ours was refused by the host: (entity id, reason). Panels
    # showing that entity should throw away what is on screen and re-read --
    # including mid-edit, because the host's copy is the true one.
    edit_refused = Signal(int, str)

    # Something arrived that this panel is showing, and the person may not be
    # looking at it. The shell highlights the panel until they are.
    panel_attention = Signal(str)
