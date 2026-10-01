"""The entity actions that ship with the app.

Kept apart from :mod:`canon_keeper.entity_actions`, which is the contract, so
that the contract does not import panels and panels do not import each other.
These are registered on first use; see ``_register_first_party`` there.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QMessageBox

from canon_keeper.entity_actions import Target
from canon_keeper_core.repo.entities import KIND_PC
from canon_keeper_core.repo.invites import already_played
from canon_keeper_protocol import enrol


class InviteAPlayer:
    """Make an invite for this character, wherever you are looking at them.

    Inviting somebody is a thing you decide *about a character* -- usually
    while looking at one, in whichever panel you happened to be in. It used to
    live only in the Players dialog, which meant knowing to go there.
    """

    id = "invite"
    order = 10

    def label(self, ctx, target: Target) -> str:
        if already_played(ctx.repos.accounts, ctx.campaign_id, target.entity_id):
            return f"Invite somebody else to play {target.name}..."
        return f"Invite a player to {target.name}..."

    def applies(self, ctx, target: Target) -> bool:
        # The DM's decision, about a player character, and only where there is
        # a real campaign to invite into: a player's app has no entity table
        # and nothing to make an invite from.
        return (
            target.kind == KIND_PC
            and getattr(ctx, "role", "dm") == "dm"
            and getattr(ctx, "repos", None) is not None
        )

    def run(self, ctx, target: Target, parent=None) -> None:
        repos = ctx.repos
        handover = already_played(repos.accounts, ctx.campaign_id, target.entity_id)
        if handover and QMessageBox.question(
            parent,
            "Invite a player",
            f"{target.name} already has a player.\n\nA new code hands the "
            "character over: whoever uses it chooses a new username and "
            "password, and the login playing them now stops working. That is "
            "also how somebody who has lost their password gets back in.\n\n"
            "Make the code?",
        ) != QMessageBox.StandardButton.Yes:
            return

        replacing = repos.invites.waiting_for(target.entity_id) is not None
        invite = repos.invites.create(ctx.campaign_id, target.entity_id)
        # The address comes from whoever is hosting, which this action cannot
        # see -- the Table panel owns the server. It answers on the bus, and
        # until it does the code alone is still a usable invite.
        whole = enrol.wrap(_address_of(ctx), invite.code)
        QApplication.clipboard().setText(whole)

        said = f"Invite for {target.name} copied: {whole}"
        if replacing:
            said += " -- the code you made before this one no longer works."
        ctx.bus.status_message.emit(said)


def _address_of(ctx) -> str:
    """Where this session is, if anybody is hosting it.

    Read off the context rather than asked for, because an action must not
    reach into the Table panel -- panels do not import each other, and that
    rule is what keeps a third-party action possible at all.
    """
    return str(getattr(ctx, "session_address", "") or "")


#: Registered in order by :mod:`canon_keeper.entity_actions`.
FIRST_PARTY = (InviteAPlayer,)
