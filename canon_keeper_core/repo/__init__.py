"""Data access. Panels reach these through ``ctx.repos``."""

from __future__ import annotations

import sqlite3

from canon_keeper_core.repo.accounts import Account, AccountRepo
from canon_keeper_core.repo.campaigns import CampaignRepo
from canon_keeper_core.repo.chat import ChatMessage, ChatRepo
from canon_keeper_core.repo.encounters import Combatant, Encounter, EncounterRepo
from canon_keeper_core.repo.entities import Entity, EntityRepo, StaleWrite
from canon_keeper_core.repo.facts import Fact, FactRepo
from canon_keeper_core.repo.invites import Invite, InviteRepo
from canon_keeper_core.repo.layouts import LayoutRepo
from canon_keeper_core.repo.proposals import Proposal, ProposalRepo
from canon_keeper_core.repo.sessions import Session, SessionRepo, Utterance, UtteranceRepo
from canon_keeper_core.repo.settings import SettingsRepo
from canon_keeper_core.repo.shares import Share, ShareRepo


class Repos:
    """One container so :class:`~canon_keeper.plugin.AppContext` stays small."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.accounts = AccountRepo(conn)
        self.campaigns = CampaignRepo(conn)
        self.chat = ChatRepo(conn)
        self.encounters = EncounterRepo(conn)
        self.entities = EntityRepo(conn)
        self.facts = FactRepo(conn)
        self.invites = InviteRepo(conn)
        self.layouts = LayoutRepo(conn)
        self.proposals = ProposalRepo(conn)
        self.sessions = SessionRepo(conn)
        self.utterances = UtteranceRepo(conn)
        self.settings = SettingsRepo(conn)
        self.shares = ShareRepo(conn)


__all__ = [
    "Repos",
    "Entity",
    "EntityRepo",
    "StaleWrite",
    "Fact",
    "FactRepo",
    "Invite",
    "InviteRepo",
    "CampaignRepo",
    "LayoutRepo",
    "SettingsRepo",
    "Session",
    "SessionRepo",
    "Utterance",
    "UtteranceRepo",
    "Account",
    "AccountRepo",
    "ChatMessage",
    "ChatRepo",
    "Combatant",
    "Encounter",
    "EncounterRepo",
    "Proposal",
    "ProposalRepo",
    "Share",
    "ShareRepo",
]
