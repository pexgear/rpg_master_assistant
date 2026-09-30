# Canon Keeper

A dockable desktop assistant for running D&D 5e, built around one rule:
**what the DM actually says is the only source of truth.**

Every panel docks, undocks into a real window you can throw onto a second
monitor, and the whole arrangement can be saved as a named layout — one for prep,
one for the table, one for combat.

Runs on Windows, macOS and Linux. Your data is a single SQLite file you own.

## Status

Early, but usable at a table. The plugin shell with Characters, Cities, Combat
and Transcript panels; local speech-to-text; one-shots that start the same way
every time; and LAN sessions with logins, shared chat and dice, and
role-filtered sharing.

**Combat** is an initiative order and a shared grid your players watch: turns
are taken on the map, creatures walk round each other rather than through, and
players see exactly the ones you have shared.

**Autopilot** hands the table to an agent when you want it, and takes it back
when you press the button again — and while it has the table it can run a fight
on that grid, and turn "I get behind the orc and hit it with my axe" into a
turn the player confirms before anything moves. An MCP server lets a player say
what they mean instead of typing it. All of it is optional and needs a key you
supply — the app itself still runs with no key and no internet.

## Install

Requires Python 3.11 or newer.

### From a source checkout

Run the installer for your system. It finds a suitable Python, builds a
virtualenv, installs everything, and writes a launcher:

```bash
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

```bash
./install.sh
```

Then start it from that folder:

```bash
.\canonkeeper
```

```bash
./canonkeeper
```

Add `-Shortcut` (Windows) to also put Canon Keeper on your desktop.

> **`canonkeeper` on its own will not be found**, and that is expected: the
> command lives inside the project's virtualenv, which is only on your `PATH`
> while the virtualenv is activated. The `.\canonkeeper` launcher exists so you
> do not have to think about that. If you would rather activate it,
> `.\.venv\Scripts\Activate.ps1` (or `source .venv/bin/activate`) makes the bare
> name work for the rest of the session.

### With pip

```bash
pip install canon-keeper
canonkeeper
```

Installing into your own environment does put `canonkeeper` on `PATH` — provided
that environment's scripts directory is on it, which is not always true of the
Microsoft Store build of Python.

## Starting a campaign

The campaign comes first: the app opens a chooser before anything else, because
a Characters panel with no campaign behind it is a list of nobody.

- **On this computer** lists the campaigns you run. Each is a single `.sqlite3`
  file you own, so there is no login -- holding the file makes you its DM. Copy
  one to another machine and it appears there.
- **Join a session** lists sessions running on your network, plus ones you have
  joined before. The first time, paste the invite your DM sent into the
  **Invite code** box — it fills the address in for you, and the username and
  password you type there become your login. After that, just the username and
  password you chose.

### Skipping the chooser

A login that works is remembered, without being asked. Next time, picking that
session fills the password in for you. Tick **Open this automatically next
time** and the app goes straight in without asking at all -- on either tab, so
it works for the campaign you run as well as one you join.

Passwords go to the operating system's credential store: Windows Credential
Manager, the macOS Keychain, or the Secret Service on Linux. Never to a file of
ours. Only a password the host has actually accepted is kept, so a typo is never
saved. On a machine with no credential store nothing is stored and you type it
each time.

To get the chooser back: **File > Open a Different Campaign...**, or **File >
Stop Opening This Automatically**, or start with `canonkeeper --choose`. If the
campaign file has been deleted or the saved password no longer works, the
chooser comes back on its own rather than failing a login you never saw.

**A join that does not work keeps you in the chooser.** A wrong password, a
host that is not answering, or an invite already used leaves you on the same
tab with the reason and another go at it — the app does not open until the host
has actually let you in, and nothing is remembered until then either.

## Playing together (LAN)

Open your campaign and press **Go online** in the Table panel. That hosts the
session on your network *and* publishes it, so players can join whether they are
in the room or not. Players open Canon Keeper, pick your session from the list,
or paste the address you send them.

If publishing is not set up, you are told and the session stays up on your
network regardless -- failing to reach the wider internet is not failing to
host.

Before anyone can join you need to invite them -- **Table ▸ Players...**: pick
the character, press **Invite a player...**, and send them the line it copies.
They make their own login from it; see [Inviting players](#inviting-players).
Chat then shows the character's name, because at the table people are their
characters.

**Speak** beside the chat box records you and puts the words in the box — it does
not send them. Correct whatever came out wrong, then press Enter. It runs
locally like the Transcript panel, and is primed with the names in your campaign
(or, for a player, the names shared with them), which is what makes it get
*Cragmaw Castle* right. The button is disabled with an explanation if
`faster-whisper` is not installed.

Dice are rolled **on the host**, never on the client that asked. `/roll 2d6+3`,
`/roll 4d6kh3` (keep highest three), `/roll 2d20kl1` (disadvantage), or the quick
buttons. `/r` works too.

**Rolls you can click.** When the DM writes *"make a DC 14 Perception check"*,
those words become a link in the chat. Clicking one opens a die that already
knows what your character adds — proficiency included — and tells you whether
you beat the DC. Skill checks, saving throws, ability checks, initiative and
plain dice notation all count. It appears for players who have a character, on
lines from the DM or from autopilot standing in for them.

The die tumbles while the request is with the host and stops on what the host
actually rolled. If nothing comes back it says so, rather than settling on a
number your own machine invented.

### What players see

Every panel works for both roles, but a player's copy is built from what the
host sent, and the host sends only what you shared.

- **Characters** and **Cities** have a **Players see** button. Share an entity
  with the whole party, or with particular people -- so only the rogue knows
  about the contact in the Dock Ward.
- Players get the name, the one-liner, and the text you wrote for them: *what
  the party knows* on a character, *players read* on a place. Your motives,
  secrets, notes and rumours are never sent. Not hidden on their screen -- never
  sent.
- An entity you have not shared does not appear at all. Its existence is itself
  a secret.
- Players own their character sheet: hit points, conditions, inventory, their
  own notes. They cannot touch anyone else's, and the host enforces that rather
  than trusting the client.
- The **Combat** map follows the same rule. A player is sent a token only for a
  creature you have shared, so the ambush on the map is not on theirs.
- The Transcript stays yours alone.

Take a share back and it disappears from their screen, rather than going stale.

Players do not move anything. You move it, and their map follows.

### Inviting players

A campaign starts with characters and nobody playing them. In **Players**, pick
the character and press **Invite a player…** — you get one line to send whoever
is playing them, holding the address *and* the code:

```
ws://192.168.1.10:8765#7K3PQ-M2XRV
```

They paste the whole thing into the invite box when they join; it fills the
address in for them, and they choose their own username and password. Their
account arrives already attached to that character. One thing to send is one
thing to get wrong, and the address is the half people mistype.

**You never see their password.** It does not cross the network either, not even
the first time — their app derives the password material locally and seals it
with the invite code, which is not on the wire at all. That is the same promise
login makes, extended to the one moment there was nothing on the host to check
against.

Send a code the way you would send a password: it is the whole of what stands
between a stranger and that seat. Codes are good for **24 hours**, can be used
once, and **making a new one kills the old** — so somebody who never got round
to joining cannot use last week's code, and neither can anyone who read it over
their shoulder.

### Passwords

Your password is never sent over the network. The host sends a challenge, your
app proves it knows the password without transmitting it, and a recorded login
cannot be replayed. That matters because a LAN has no TLS, and people reuse
passwords.

Stored passwords are scrypt verifiers, never the password itself. Anyone holding
the campaign file could still log in as a player -- but they would already have
your secrets, which is the thing actually worth protecting. The same is true of
any invite still outstanding: the host has to hold the code to open what was
sealed with it.

### Hosting somewhere else

The host can be a separate machine -- a spare box on the LAN, or a server anyone
can reach. It hosts one campaign, and inviting people is part of the same
command because a dedicated server has no other way to be told who may join:

```bash
canonkeeper-server --db our-campaign.sqlite3 --characters
```

```bash
canonkeeper-server --db our-campaign.sqlite3 --invite Marla --address wss://your-host
```

That prints the whole invite to send. Then start it:

```bash
canonkeeper-server --db our-campaign.sqlite3
```

The DM joins it like everyone else, with a `--add-dm` login. There is no
`--add-player`: nobody's password is set by anybody but them.

**Windows will ask** to allow the app on private networks the first time you
host. Say yes, or nobody can connect.

### Playing over the internet

**Go online** publishes the session through
[Tailscale Funnel](https://tailscale.com/kb/1223/funnel) and gives you an
address like `wss://your-machine.tailXXXX.ts.net` to send your players. It is
not a separate button: hosting for the people in the room and for the one who
could not make it is the same wish.

It solves three things at once:

- **NAT** — the tunnel dials outward, so no port forwarding, and it works behind
  carrier-grade NAT where forwarding is not even possible.
- **Encryption** — Tailscale provisions a real certificate to your machine, so
  the connection is `wss://` rather than plain text. Nothing to configure or
  renew.
- **The address** — a stable hostname, instead of a home IP that changes.

**Only you install Tailscale.** Your players need nothing: they paste the
address into the chooser and log in as usual. Sign in once at
[tailscale.com/download](https://tailscale.com/download).

Funnel has to be switched on for your tailnet the first time, and it is off by
default. The app checks before doing anything and, if it is off, offers an
**Open Tailscale settings** button that takes you straight to the right page —
because the `tailscale` command itself does something unhelpful here: it prints
the instructions and then sits there waiting for you to act, looking like a
hang.

Sharing stops when you leave the session or close the app normally — and that
last word is doing real work. The address lives in
Tailscale's own background service rather than inside Canon Keeper, so a crash —
or quitting before it has finished publishing — can leave your machine published
with nothing behind it. Canon Keeper writes down that it published, notices a
leftover on the next launch, and says which port is still public; **Go offline**
takes it down. It does not do that for you unasked, because a public address is
yours to keep or drop.

If you want to check by hand, `tailscale funnel status` says what your machine is
serving.

**Copy invite** gives you the right address to send either way: the public one
when you are sharing, your LAN address otherwise.

If you would rather not use a tunnel, the alternatives are unchanged: forward a
port on your router (but then the traffic is unencrypted), or run
`canonkeeper-server` on a machine that already has a public address.

## One-shots

**Start a one-shot** in the chooser builds a campaign from a template: the
characters, places and facts are already in it, and it is the same every time.
Good for an evening that begins somewhere specific, and for trying the app
without typing a world in first.

It ships **characters and nobody in them**. Invite your players to them from
**Players…**, the same way as any campaign — a template that carried logins
would be carrying passwords, and a template file is public.

| | |
|---|---|
| **The Last Coach** | An evening for three. The last coach before the pass closes, and someone on it is being hunted. |
| **Test Combat** | Initiative on the first line. Three characters, four goblins, one room. |
| **Test Table** | A world with something in it, for exercising sharing, sheets and autopilot. |

What you get is an ordinary campaign — the only difference is that it remembers
which template it came from, which is what lets **File ▸ Start Again from the
Beginning** put it back. **File ▸ Storyline…** holds the beats and where it
ends. **File ▸ Keep This One** drops the connection and it becomes a campaign of
your own.

## Naming the panels

**Panels ▸ Rename Panels…** lets you call things whatever you call them at your
table. There are three layers, and the more specific one wins:

| | Who sets it | Who sees it |
|---|---|---|
| **Your name** | you | you |
| **The party calls it** | whoever runs the campaign | everyone in the session |
| **Default** | the panel | the fallback |

So the DM can rename *Cities & Places* to **The Sword Coast** for the whole
table, and a player who prefers *Places* can still call it that on their own
machine. Clearing your own name falls back to the party's; clearing that falls
back to the default. Hover a panel's title bar to see which is which.

Party names travel with the session and update live, so renaming something
mid-game reaches everyone immediately. What you call a panel yourself is never
sent anywhere.

## Light and dark

**View ▸ Theme** offers *Follow System*, *Light* and *Dark*. The default follows
your desktop and switches live when your OS does — handy if your machine already
goes dark in the evening, which is when you are usually running a game.

The choice is remembered. Transcript highlight colours are picked separately for
each appearance, so names stay legible either way.

## Where your data lives

One SQLite file per campaign, in `campaigns/` under the standard per-OS location
— `%APPDATA%` on Windows, `~/Library/Application Support` on macOS,
`~/.local/share` on Linux. Find it from the app with **File ▸ Open Data Folder**.
Back a campaign up by copying its file; move it to another machine and it shows
up in the chooser there.

Your own settings, theme and dock layout live separately in `profile.sqlite3`,
so they follow you rather than the campaign — and so a player, whose campaign
lives on someone else's machine, still has somewhere to keep them.

Set `CANONKEEPER_DATA_DIR` to put it somewhere else, for example on a synced
drive.

## The keyboard

**A key belongs to the panel you are looking at.** Press Space on the map and
the map answers; press it while you are typing in the chat box and you get a
space. Two panels can use the same key for their own version of a thing, because
only the one with the focus is listening.

A few keys reach further, on purpose. **F9** starts and stops recording wherever
you are — including while another application is in front — because the point of
it is to record what you are saying *about* whatever you are looking at.

**File ▸ Settings ▸ Keyboard** lists the lot: every key, which panel owns it, how
far it reaches and what it does. If a key will not do what its menu says —
because something with a longer reach took it — that one is at the top, in bold,
with a sentence saying who took it. That is worth a page of its own because it is
the one thing about this app you cannot find out by looking at it: "why does
Ctrl+N do nothing in here" is answered by knowing what *else* claimed Ctrl+N.

## Panels

**Characters** — NPCs and PCs, narrative-first: who they are, what they want,
what they are hiding, and what the party has actually worked out. Stat blocks
stay in your books.

**Cities & Places** — region, city, district, building, room. Characters are
parented to a place, so every location knows who is standing in it.

**Combat** — an initiative order and a grid, side by side. **New fight** makes
one straight away; **Add…** puts characters and NPCs into it; drag a name out of
the order and onto the map to place them. **Roll initiative** rolls for everyone
and adds their Dexterity where there is a sheet to read it from. **Start**,
**Next turn** and **End fight** keep the round.

**Whoever is up is selected, and Space opens what they can do around their own
pin** — one wedge for moving, one for each weapon they carry. Pick a wedge,
then click a square to walk to or a creature to hit; hovering shows the walk
first, round anything in the way, in the colour that says whether the turn has
the movement for it. Escape puts the wheel away. Players get the same wheel on
their own map, for their own character, on their own turn.

There is no dragging a token from one square to another: that was a teleport,
free of everybody's turn and of every rule. Creatures go **onto** the map by
being dropped there and come **off** it by the right-click menu; in between,
they move by taking a turn.

**Ctrl-click a square** to put something in the way — a rock, a pillar, an
overturned cart. Nobody can stand there, and it is what a creature gets behind
for cover.

**The wheel zooms**, on whatever the pointer is over, and the middle button
drags the board about. `+` and `−` zoom from the keyboard, the arrows walk the
view a square at a time, and `0` puts the whole map back on screen. Until you
zoom, the map sizes itself to the panel and keeps doing so as you move the dock
around. How big the room is belongs to the fight itself — **Fight…** in the
Combat menu.

**The map is 3D**: obstacles stand up as walls and creatures as figures, for
you and for your players. Drag with the left button to turn the room round, and
with the middle button — or Shift and the left, on a touchpad — to slide it. The
buttons in its top-left corner get you back to a view you know — **Tilted**
(also `0`, or **Whole map** in the Combat menu) and **From above** (**Ctrl+9**),
which is straight down like a sheet of squared paper — and **Low walls** knocks
the walls down to a kerb when one is hiding what is behind it. The square
numbers run along the near and left edges, whichever way the room is turned.
Walls are only walls: there is no height, and nobody can climb or see over
anything.

Squares are numbered from the middle: **0,0 is the centre**, x to the right and
y downwards, so "the one at minus three, two" is a square everybody can find —
and it is still that square after the map grows.

Someone off the map is still in the fight — the one who fled down the corridor,
or has not come through the door yet — so *off the map* and *out of the fight*
are two different right-click actions.

**Things happen where people can see them.** A token walks square by square
rather than jumping; an attacker leans in and the damage floats off whoever
took it, or the word *miss* does; a creature dropping to zero fades off the map
rather than vanishing between two frames. Everyone at the table sees the same
thing at the same moment, because the host says what happened and every screen
draws that — rather than each working its own version out.

**A creature at zero stays where it fell**, drawn as a grey ghost on its square.
It is the square everybody is looking at — the one with your friend on it — and
a gap there told you nothing. Nobody has to walk around a body, and the turn
steps over the dead rather than stopping to offer each of them one.

**A fight has sides.** Two are made with it and filled in without asking: *The
party*, and *Hostile* for everyone else. The initiative order is grouped by
them, so how many are left on each side is something you can see. Right-click
anybody to move them across, or to start a third — for the captured guard who
fights beside you, or the rival adventurers who are not monsters. Opportunity
attacks follow the sides rather than what kind of creature somebody is.

Each row in the order is two lines: the name and the initiative on top, and
what is true of them underneath — hit points, dying and how close it is, off the
map, played by autopilot, not yet shared.

**A monster at zero is dead; a player character at zero is dying.** At the
start of each of their turns the table is told a death save is owed, and their
player rolls it — on the host's dice and in front of everybody. It is not
optional: take your time or ignore it, and when the clock runs out the host
rolls it for you and says so. Three made and they are stable, three failed and
they are gone.
A natural twenty puts them back up on one hit point with the turn still theirs,
a natural one costs two, and hitting somebody who is already down costs them a
save. The initiative order carries the running count, so the table can see how
close it is. Healing above zero clears it: two failures do not follow you into
the next time you go down.

Your players see the fight as it happens and cannot touch it. They see exactly
the creatures you have **shared** with them: putting a monster on the map does
not reveal it, so you can lay an ambush out in front of them. Tokens the party
cannot see are drawn dotted on your map, and right-clicking one offers to share
it.

**Transcript** — press **F9** (or the Record button), narrate a beat, press it
again. The clip is transcribed locally and appears on screen. You can also just
type a line instead.

Names you already know are **highlighted as they appear** — places in green,
characters in blue, factions and items in their own colours. Double-click one to
jump to it in the panel that owns it.

Names you *don't* know yet are one gesture away from existing: select the words
(or just right-click a single word) and choose **Add "…" as ▸ Character /
Place / Faction / Item**. The name lights up immediately, the owning panel
selects it so you can fill in details, and — the part that compounds — it joins
the Whisper glossary, so the next time you say it out loud it is transcribed
correctly.

Right-click a line for **Edit this line… / Transcribe again / Delete**. Editing
matters: an utterance is the only thing allowed to be the source of a fact, so
the text here is what everything downstream will treat as what you actually
said.

### Transcription

Transcription runs entirely on your machine — no audio leaves it, and there is
nothing to pay for. It needs one extra package:

```bash
pip install faster-whisper
```

Without it the panel still opens and tells you the command; recording is
disabled until it is installed.

The first recording downloads the chosen model (a few hundred MB for `small`,
the default). Larger models in the dropdown are more accurate and slower. If you
have an NVIDIA GPU *and* the CUDA runtime installed it will use it, and quietly
falls back to the CPU if the CUDA libraries are missing.

**The glossary is the reason this works on fantasy names.** Before every
transcription the app builds a prompt from the names in your Characters and
Cities panels and primes Whisper with it. Same audio, same `tiny` model:

| | Result |
|---|---|
| Without glossary | "Crag**more** Castle … **Silder Hall Winter**" |
| With glossary | "**Cragmaw** Castle … **Sildar Hallwinter**" |

So the more you write down, the better the transcription gets. If a name still
comes out wrong, fix the row — and add the name to an entity so it is right next
time.

## Autopilot

You are the DM. Canon Keeper is for you, and it always hosts. **Autopilot** is a
switch you hold: while it is on, an agent answers in your place — for a break, a
second voice, or a shopkeeper haggled with while you read ahead. Press it again
and you have the table back, mid-sentence if that is when you pressed it.

Install the agent once, **into the same environment Canon Keeper runs from**:

```bash
.\.venv\Scripts\python -m pip install -e ".[agent]"
```

From a source checkout that is the project's own virtualenv, not whichever
Python is first on your `PATH` -- installing it elsewhere is the one way to get
an Autopilot button that appears to do nothing.

Then press **Autopilot** in the Table panel while hosting. The first press opens
**Agent…**, where you paste a key and pick a model; after that it does the rest
itself — creates the agent's login, keeps its password in your credential store,
and starts the agent against your own session. There is nothing to configure and
no command to run.

**Agent…** stays available from the Table panel, so a key can be corrected,
replaced or forgotten later. It also has a **Workspace id** field, which most
keys do not need: only a key that can reach several workspaces has to name one,
and the API says so plainly if yours does. The simplest answer is usually a new
key scoped to a single workspace, which needs no id at all. The key goes to your credential store, never to the
campaign file, so copying a campaign to another machine does not carry it along.
An `ANTHROPIC_API_KEY` in your environment wins over the stored one.

If you would rather run the agent yourself — on a spare box, or to watch what it
does — start it before pressing the button and the app will leave it alone:

```bash
canonkeeper-server --db our-campaign.sqlite3 --add-agent autopilot
canonkeeper-agent --url ws://192.168.1.20:8766 --user autopilot
```

`--dry-run` prints what it would have said instead of saying it, which is the
honest way to find out whether you trust it.

The design is worth stating plainly, because it is what makes handing over safe:

- **It cannot speak while the switch is off.** Not by good behaviour — the host
  refuses its messages. It stays connected and keeps listening, so switching back
  on is instant, but nothing it says reaches the table.
- **It is on the roster as an agent.** A table deserves to know when it is being
  answered by a machine, so turning autopilot on and off is announced in the
  chat and kept in the log.
- **It has no path to your campaign.** It holds a socket and a login, exactly
  like a player's app, even when the app started it for you. Convenience changed
  who types the command, not what the agent can reach.
- **It never rolls.** Dice are the host's, so it asks for a roll like anyone else.

### What it can do

Autopilot is not only a voice. While the switch is on it can put a fight on the
shared map — start it, place everyone where the scene it just described put
them, add the rocks and pillars it mentioned, move monsters, and pass the turn.
Everything goes over the wire and through the same checks your own buttons go
through, so it cannot build a fight the app could not have built itself, and
the moment you press the switch it can do none of it.

It rolls its own attacks rather than describing them — the host rolls, and it
narrates what came back — and it records what characters pick up onto their own
sheets as it hands it over.

**Simulate turn** hands a character over for the fight: an empty chair, or a
player who has stepped out. Either of you can do it from the Combat panel, and
either can take them back. Everyone is told, and the initiative order says who
is playing them.

**Each one gets its own stand-in, and its own name.** Not autopilot — a separate
thing per character, called BRASS or SPINDLE or VERGE, the same name for that
character every time. It matters more than it sounds: a stand-in is sent exactly
what that player is sent, so it does not know where your ambush is. Played by
autopilot, a handed-over character knew every secret in the campaign and walked
around them, which looks like good play and is cheating.

It plays the way a person does. It says what it wants in plain words — *"I close
on Yeemik and swing"* — autopilot turns that into rules and puts it back as a
proposal, and it answers. You see the proposal exactly as you would a player's,
and nothing moves until it is accepted. It needs no API key and costs nothing to
run.

Start it with `--talk-only` if you would rather it kept its hands off the map.

**While autopilot is on, what you type does not reach the party.** There is one
voice at the table and it is the agent's; two DMs saying different things is
worse than either. Your line goes to the agent instead — marked *(to autopilot)*
on your own screen — and it works your direction into the scene in its own
words, without repeating it back or letting on that anybody said anything. To
speak to the party yourself, press the switch.

### Players taking their turn

When it is your character's turn the chat says so. Say what you want in plain
words — *"I get behind the orc and hit it with my axe"* — and autopilot works
out what that is in rules and hands it back to you:

> **Move to 0,-1 and attack Yeemik with a battleaxe.**  [Do it] [Say more…] [Refuse]

The map shows it while you decide: a dotted line to where you would end up, a
ghost of your token there, and a sword over whoever you would be hitting. The
chat box waits — **Do it**, **Say more…**, or **Refuse** — because a question
about what your character is about to do, left open while everyone chats, is a
question nobody gets back to. **Say more…** unlocks the box for one message, and
what comes back is the same turn, changed.

Nothing happens until you accept. Then the host moves you and rolls the attack:
the d20, your bonus off your sheet, the target's armour class, and the damage.
Melee reaches one square, diagonals included.

**How far you can go is your speed** — six squares for most people, read off
your sheet. The panel keeps the count while the turn is yours: *"Your turn: 15
feet of 30 left, 15 used · attack still to come."*

**Acting does not end your turn.** Say what else you do, press **Done**, or let
it pass on its own after half a minute — anything you type stops that clock. The
clock runs on the host, so it is a promise to the whole table rather than to
whoever's laptop happens to be awake.

**Walking away from somebody costs.** Leave an enemy's reach and they get a
swing at you as you go — one reaction each per round, and melee only, so an
archer cannot hold you there. It is the rule that makes standing somewhere mean
something; without it you could stroll past the ogre to reach the wizard behind
it. Dropped on the way out, you fall on the square you left.

It is deliberately the simple case — a weapon on your sheet, one attack, no
spells. Everything else is still a DM's ruling, which is where it belongs.

**You can also ask for it, one line at a time.** Turn on *Offer to turn what a
player says into a turn* in **Agent…** and, while a fight is running, a small
mark appears beside what the player whose turn it is has said. Click it and the
agent writes that line as a move and an attack and puts it to them, exactly as
above — the difference is only who decided to ask.

**Autopilot does not have to be on for that.** It is meant for the evening where
you are running the table yourself and want one sentence worked out rather than
argued about. Asking buys the agent one proposal, for the creature whose turn it
is, and nothing else: it cannot move a token, pass the turn or set an initiative
on the strength of it, and your player still accepts or refuses. It costs a model
call each time you click the mark, and nothing when you do not.

### Turns you take yourself

You do not need autopilot to run a fight. **Attack…** in the Combat panel asks
who is up, who they are hitting and which of their weapons — and the host rolls
it, the same roll it would have rolled for anybody else. **Simulate turn** goes
the other way: it hands a character to autopilot for this fight, for an empty
chair or a player who has stepped out, and either of you can take them back.

**The rules bend when you say so.** Ask autopilot for something the rules refuse
— a creature moving out of turn, or further than its speed — and it comes back
to you naming the rule it would break, to allow or not. Squares off the map, and
ones somebody is already standing in, are not rules; those are still refused
outright.
- **Autopilot is never remembered.** Opening a campaign to find a machine already
  running your table is not a state to arrive in by accident.
- **It answers players.** While you are at the table, your own lines are yours
  -- if you answer a player, its queued reply is dropped. With nobody else
  connected it answers you, so trying it out on your own actually works.
- **It waits for a pause.** A turn is a lull, not a message -- three players
  talking to each other gets one answer when they stop, not three
  interruptions. If you answer first, its queued reply is dropped. `--pause`
  tunes the wait.
- **You can see it thinking.** *Autopilot is writing...* shows under the chat
  while it composes, and what it has cost so far -- answers, tokens, dollars --
  shows on your screen only.

It does get what a DM gets — your motives, your secrets, the canon log — because
it is standing in for you and cannot answer as an NPC without them. That is also
the reason the agent login is worth guarding as carefully as your own.

An API key is yours to supply, and everything above is optional. The app still
works with no key and no internet.

## Saying what you mean

An MCP server exposing **one seat** at the table, so "I drink the potion and
check the door for traps" becomes real messages instead of typing:

```bash
pip install "canon-keeper[mcp]"
canonkeeper-mcp --url wss://your-host.tailXXXX.ts.net --user marco
```

**The first time, pass the invite your DM sent** and pick your own password.
The whole invite works as the url, because that is the shape a DM sends it in —
the code lives in the fragment, which is the part of a URL never sent to a
server:

```bash
canonkeeper-mcp --url "wss://your-host.tailXXXX.ts.net#ABCDE-FGHIJ" --user marco
```

After that leave the invite off: the account exists, and logging in is the
ordinary way in. The code is used once and thrown away — enrolment makes the
account and stops, and the client turns round and logs in with what it chose, so
there is one door into a session rather than two.

It holds one login and has exactly that login's authority. `roll` is rolled on
the host. `update_my_character` is a **request** — it returns "sent to your DM",
because that is what happened. There is no privileged path, which is why a bug
in it cannot corrupt a campaign.

**A seat can play a whole fight, not only watch one.** That is the difference
between a client that reads your game and one that sits at your table:

- `wait_for_update` waits until something happens — your turn coming round,
  somebody speaking, a roll — instead of asking "anything yet?" in a loop.
  `read_pending` says what is new since you last read, and only moves its place
  when you call it, so reading late misses nothing.
- `the_turn_on_offer` is the turn somebody worked out for your character and put
  to you, in words. `answer_a_proposal` accepts it — which is what makes it
  happen — or refuses it with a note saying what you meant instead.
- `finish_my_turn` says you are done, which is not the same as passing the turn:
  that is still the DM's.
- `roll_my_death_save` is yours to make when you are at nought. The host rolls it
  for you when the clock runs out either way, so this is not the difference
  between dying and not — it is the difference between making your own death
  save and watching a number change in a list.

Nothing there moves a token or decides a number. A player does not move their own
token in the app either: they say what they mean, somebody works it out in rules,
and they accept it. The seat plays the same game the same way.

The caveat worth reading first: whatever model your MCP client runs will see
what that login sees. For a player, that is what you shared with them. Point it
at a DM login and you have sent your campaign's secrets to whoever runs that
model.

## Still to come

**Simulated players, off the battlefield.** A stand-in fills an empty chair for
a *fight*. What is missing is the rest of the evening: a character that talks to
the party between fights, asks you things, and is approved as anyone else would
be. The seat and the stand-in both exist now; what it lacks is a model to think
with, and a reason to speak when nobody is rolling initiative.

**A simulated master** that invents rather than stands in: prep by playing your
own scenario against something that argues back. Further off, and the harder of
the two — a simulated player proposes into a structure that already exists,
while this has to build one.

## How it is put together

Six packages, and the arrows only point one way:

| Package | Is | Needs |
|---|---|---|
| `canon_keeper_protocol` | the wire contract | the standard library, and nothing else |
| `canon_keeper_client` | a headless connection | `websockets` |
| `canon_keeper` | the app, and the host | PySide6 |
| `canon_keeper_dm_agent` / `canon_keeper_mcp` | things that connect to speak | `anthropic` / `mcp` |
| `canon_keeper_player_agent` | one character, played while its player is away | the standard library |

Nothing outside the app imports the app. That is what lets an agent run on a
spare box without installing 660 MB of Qt, and it is what makes "a client cannot
reach your campaign database" true of the import graph rather than true by
habit. Both are enforced in `tests/test_protocol_package.py` rather than
remembered.

## Writing a plugin

Every panel in the app, first-party ones included, loads through the same entry
point group. A plugin is an ordinary Python package that declares:

```toml
[project.entry-points."canonkeeper.panels"]
weather = "my_pkg.panel:WeatherPanel"
```

and provides a class satisfying `canon_keeper.plugin.PanelPlugin`:

```python
from PySide6.QtCore import Qt
from canon_keeper.plugin import API_VERSION, AppContext

class WeatherPanel:
    id = "weather"          # permanent: it is the dock's objectName in saved layouts
    title = "Weather"
    api_version = API_VERSION

    def create_widget(self, ctx: AppContext):
        ...                  # ctx.repos, ctx.bus, ctx.campaign_id, ctx.log

    def default_area(self):
        return Qt.DockWidgetArea.BottomDockWidgetArea
```

Install it into the same environment and it appears on next launch. Panels never
import each other — they coordinate through `ctx.bus` signals, so a panel that
is not installed simply does not exist rather than breaking its neighbours.

A panel that fails to import, declares the wrong API version, or raises while
building its widget is disabled and reported under **Help ▸ Installed Panels**;
it never stops the app from opening. Set `CANONKEEPER_DISABLE_PLUGINS=id1,id2`
to turn one off from outside the app.

### Adding to the right-click menu

A creature carries a menu with it wherever it is shown. Declare an action and it
appears in every panel that lists characters — the Characters panel, the
initiative order, and any panel written later:

```toml
[project.entry-points."canonkeeper.entity_actions"]
send_to_vtt = "my_pkg.actions:SendToVirtualTabletop"
```

```python
class SendToVirtualTabletop:
    id = "send_to_vtt"       # stable: it is how a panel opts out
    order = 20

    def label(self, ctx, target):
        return f"Send {target.name} to the tabletop"

    def applies(self, ctx, target):
        return target.kind == "pc" and ctx.role == "dm"

    def run(self, ctx, target, parent=None):
        ...                   # target.entity_id, target.panel, target.extra
```

A panel's own items stay above yours, under a separator, because they are the
reason somebody right-clicked *there*. An action that raises is disabled rather
than fatal, exactly like a panel.

## Contributing and releases

Tests run on Windows, macOS and Linux on every push. To run them yourself:

```bash
pytest
```

[ARCHITECTURE.md](ARCHITECTURE.md) is the map: what the packages are, which way
they depend, the invariants worth preserving, and the main flows.
[AGENTS.md](AGENTS.md) is the short version for anyone — human or otherwise —
changing the code. Its structural claims are
checked by the suite, so it fails rather than quietly going stale.

Releases are cut by pushing a `v*` tag; see [RELEASING.md](RELEASING.md).
Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Licence

MIT — see [LICENSE](LICENSE).

Canon Keeper uses PySide6, which is licensed under the LGPLv3. Installing it
from PyPI links it dynamically, which is what the LGPL asks for. If you
redistribute a bundled build, read the Qt licensing terms first.

This app includes material from the **System Reference Document 5.1**
("SRD 5.1") by Wizards of the Coast LLC, available at
<https://dnd.wizards.com/resources/systems-reference-document>, licensed under
the [Creative Commons Attribution 4.0 International
License](https://creativecommons.org/licenses/by/4.0/legalcode). The JSON
transcription comes from [5e-bits/5e-database](https://github.com/5e-bits/5e-database).

No adventure text ships with this repository. Loading your own copy of a
published module into a tool you run privately is ordinary personal use; putting
that text in a public repository is not.
