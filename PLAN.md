# Where Canon Keeper stands, and what is next

The state of the project in one file, so that picking it up on a different
machine — or after a month away — does not start with reading the git log.

[ARCHITECTURE.md](ARCHITECTURE.md) says how it is built and why.
[CHANGELOG.md](CHANGELOG.md) says what changed and when. This one says what is
true *now* and what is worth doing next, which neither of those can: a
changelog only knows about things that shipped, and an architecture document
deliberately outlives any release.

**Keep it current.** A stale plan is worse than no plan, because it is
believed. This file was the original build plan for a push-to-talk
transcription tool and sat untouched for months while something else got
built — which is exactly the failure it now exists to prevent.

---

## What it is

A dockable desktop assistant for running D&D 5e, built around one rule: **what
the DM actually says is the only source of truth.** Everything else is derived,
proposed, or projected from it.

Python and PySide6, one SQLite file per campaign, runs on Windows, macOS and
Linux. Optional agent features need an Anthropic key; the app itself runs with
no key and no internet.

---

## Where it stands

**0.6.4 is released** and published, with CI green across Windows, macOS and
Linux × Python 3.11 and 3.12.

Since 0.6.1, in the order it would matter to somebody picking this up:

- **0.6.2** stood the map up in 3D, and made a turn something you line up before
  it happens: picking a wedge stages rather than fires, the bar reads the turn
  back in words, Enter commits it. A turn for a player's character is *put to
  them* instead. Movement can split around the action, Dash and Extra Attack
  exist, and keyboard shortcuts belong to the panel that has the focus.
- **0.6.3** made a seat playable by something other than a person: an agent
  holding a player's login can run a fight end to end through the MCP, and hears
  what the host announces, which it could not before.
- **0.6.4** let that seat join on an invite rather than needing an account made
  somewhere else first, and started taking the host out of the app -- see
  *What to do next*, item 2.

**Still nobody has played any of it.** 0.6.1 was the largest change to combat the
project had and 0.6.2 is larger; all of it is verified by tests and none of it by
a person running a fight.

Working and used at a table: the plugin shell with docking and named layouts;
Characters and Cities; one-shot templates; LAN sessions with per-character
invites, shared chat and host-rolled dice; local speech-to-text; and combat —
an initiative order and a shared grid, with turns taken on the map.

That is the single most important thing to know before starting anything new.

What 0.6.1 changed, in the order it would bite:

- Dragging a token from square to square is **gone**, for the DM as much as for
  anybody. Creatures are placed onto the map and taken off it; in between they
  move by taking a turn.
- Turns are taken on the map: whoever is up is selected, Space opens a wheel of
  what they can do, and players get the same wheel for their own character on
  their own turn.
- Moves are routed round what is in the way, charged along the route, and
  swung at by anybody whose reach they leave.
- A death save is asked for in the chat and cannot be dodged.

---

## What to do next

Roughly in the order that would most improve an evening at the table.

**1. Play a fight, and fix what that finds.** Before anything below. The
combat rewrite is unexercised by a human, and the bugs it has are the kind
only playing finds — a turn that feels wrong, a wheel that opens on the wrong
creature, a walk that looks stupid.

**2. The host is half out of the app, and that is the one thing in progress.**
The goal is a standalone server with no desktop toolkit in it, a DM who can log
in over MCP, and a master who can run a fight from a terminal. Two steps of four
are done and the suite is green at each:

- `canon_keeper_core` now holds the database, the repositories, the rules and
  the SRD. None of it ever imported Qt; it was only packaged inside the app,
  which made running a session without a screen cost 660 MB of Qt anyway.
- The host no longer needs Qt to *signal* or to *wait*: hooks replaced signals,
  and an injected clock replaced its timers.

What is left is the transport, and the design is decided rather than open.
`QWebSocketServer` becomes a listener that calls the host, and `QWebSocket`
becomes a connection whose `send` is **synchronous and queued** — a writer task
drains it. That last part is the whole trick: the host sends from deep inside
synchronous rule methods, so making the send await would turn three thousand
lines inside out. Then the host moves into the core, `SessionServer` stays as a
thin Qt-flavoured wrapper so the app and the suite do not change, and the app
runs it on a thread. That thread is a real cost: `_referee()` stops being a
direct call, because sqlite and the session table would otherwise be touched
from two threads.

**3. Dodge, Disengage, Hide, Help and Ready do not exist**, and the *proposal*
path still carries one move and one attack in that order — the DM's own map
stages a full sequence, but `offer_turn` refuses one it cannot express rather
than sending a player a shortened version of it.

**Spells are deliberately out of scope**, decided rather than missing: attacks
stay a weapon, a d20 and reach, and a spell is a ruling the DM makes. Terrain is
decided the same way — line of sight only, if it is built at all, and it is the
most expensive thing on this list because what a player is *sent* is already an
allowlist, so real line of sight means the host deciding visibility per viewer.

**4. Nothing measures whether the agent plays well.** Every layer around it is
tested; whether it writes a good scene, or lays a fight out sensibly, is not
something a unit test can answer, and no other check exists.

**5. The stand-in has no judgement.** It works the turn out from the map and
costs nothing to run, which is the right default. It does not understand cover,
or that the wizard is the thing to reach.

The rest of the known limits are listed in
[ARCHITECTURE.md § Known gaps](ARCHITECTURE.md#known-gaps); that list is the
complete one and this is only the part worth acting on.

---

## Working on it from more than one machine

Everything the code needs is in git. Two things are not.

**Your campaigns are not in the repository.** They live in the per-OS data
directory, which on Windows is:

```
%APPDATA%\CanonKeeper\CanonKeeper\
  campaigns\*.sqlite3    one file per campaign
  profile.sqlite3        theme, dock layout, saved logins
  servers.json
```

They are small — a campaign in use is a couple of hundred kilobytes. Copy that
folder to the same place on the other machine and it arrives with everything,
the dock layout included. Copy the `.sqlite3` files while the app is closed, or
take them with SQLite's own backup; a plain copy of a live database can catch
it mid-write.

**The virtualenv does not travel.** It is gitignored, it is most of the
project's size on disk, and its launchers have the absolute path baked into
them. Run the installer on the new machine instead:

```
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

One trap worth knowing: if the installer picks the **Microsoft Store** build of
Python, the app runs correctly but Windows files it under Python in the taskbar
and shows Python's icon, because a packaged process cannot claim an identity of
its own. A virtualenv built from that interpreter inherits it. Use a python.org
install. The app says so in its log when it happens.

---

## The documents, and which one to change

| | |
|---|---|
| [README.md](README.md) | for someone running a game |
| [ARCHITECTURE.md](ARCHITECTURE.md) | for someone changing the code: the shape, and why |
| [AGENTS.md](AGENTS.md) | the rules an agent works under here |
| [RELEASING.md](RELEASING.md) | how a version is cut |
| [CHANGELOG.md](CHANGELOG.md) | what changed, per release |
| this file | what is true now, and what is next |

The suite checks what it can — every package, migration, panel and version
constant named in the architecture document has to exist. It cannot check
whether the prose is still true, and prose is where these drift.
