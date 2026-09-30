# Changelog

What changed, from the point of view of someone running a game. See
[RELEASING.md](RELEASING.md) for how versions are cut.

## Unreleased

### A seat can join on an invite

- A seat had two ways in: an account that already existed, and a token the DM
  minted for a stand-in. Neither is what a new player has, so an agent meant to
  be the whole client had to borrow a login made somewhere else first. Pass the
  invite once, choose your own password, and afterwards log in like anybody else.
  The whole invite works as the address your MCP client is given.

## 0.6.3

A player's seat can be played by something other than a person.

### A whole fight through the MCP

The MCP seat could read your game and say things in it. It could not take a
turn, which meant an agent holding a player's login sat through its own turn.
It can now play a fight from beginning to end — and it plays it the way a person
does, because it sends the same messages the buttons send.

- **It is told when something happens.** `wait_for_update` waits for your turn
  coming round, somebody speaking, a roll — instead of asking "anything yet?" in
  a loop. `read_pending` says what is new since the last read, and only moves its
  place when you read, so catching up late misses nothing.
- **It can answer the turn put to it.** `the_turn_on_offer` reads what somebody
  worked out for your character; `answer_a_proposal` accepts it — which is what
  makes it happen — or refuses it with a note saying what you meant instead.
- **It can say it is finished**, with `finish_my_turn`. Which is not passing the
  turn on: that is still the DM's.
- **It can make its own death save.** The host still rolls it when the clock runs
  out, so this is not the difference between dying and not — it is the difference
  between making your own and watching a number change in a list.
- **It hears what happened.** Every result the host announces — a swing and what
  it rolled, somebody going down, a save asked for — reaches the seat now. It
  used to be able to act and never learn what came of it.

None of this moves a token or decides a number, and none of it is a privileged
path. A player does not move their own token in the app either.

## 0.6.2

The map stands up, and a turn is something you line up before it happens.

**Nobody has to upgrade together, and nothing needs converting.** The wire did
not move, so a 0.6.1 player can still join a 0.6.2 table; campaign files gain
two columns when you open them.

### The map in 3D

- **The battle map stands up.** Obstacles are walls, creatures are figures in
  their side's colour, and whoever is up has the gold ring under their feet.
  Drag to walk round the room, drag with the middle button (or Shift and the
  left) to slide it, scroll to get closer. Your players see it the same way.
- **Tilted** and **From above** bring the camera back when you have lost your
  bearings, and **Low walls** knocks the walls down when one is in the way of
  what you want to see. The square numbers follow the edges of the room.
- Everything you did on the map still works on it: taking a turn with Space,
  ctrl-clicking a wall in, dropping a creature from the order, watching walks
  and damage. Space brings the choices up as a menu at the pointer.
- Walls are only for looking at: there is still no height, and nothing can be
  climbed or seen over. The first time it opens there is a moment's pause while
  your graphics card gets ready; a computer that cannot draw it at all gets the
  old flat map instead.

### Ask for a line to be worked out as a turn

- **A player says what they do; you point at it.** Turn on *Offer to turn what
  a player says into a turn* in **Agent…**, and while a fight is running a small
  mark appears beside what the player whose turn it is has said. Clicking it
  asks the agent to write that as a move and an attack and puts it to them to
  accept, exactly as it would have on its own.
- **Autopilot does not have to be on for it.** This is for the evening where you
  are running your own table and want one sentence worked out, so asking buys
  the agent one proposal for the creature whose turn it is and nothing else — it
  cannot move a token, pass the turn or set an initiative on the back of it.
  Nothing reaches your player's character until they accept, as before.
- It costs a model call each time you click it, and nothing when you do not.

### A session left on the internet is noticed

- **Going online no longer outlives the app quietly.** Publishing puts the
  address in Tailscale's own daemon rather than in Canon Keeper, so a crash, a
  kill, or closing before it finished answering could leave your machine
  published with nothing running behind it — and the next launch showed **Go
  online** as though it were off.
- It is now written down when a session is published and cleared when one is
  taken down, so a leftover is recognised. The Table panel says which port is
  still public and **Go offline** ends it. Nothing is undone behind your back: a
  public address is yours to keep or drop.

### A turn you line up before it happens

- **Picking something on the map no longer does it at once.** Choose a move,
  choose a weapon, choose who to hit — that is one turn, described in words at
  the bottom of the Combat panel and drawn on the map as a dotted line, a ghost
  and a sword. **Enter** carries it out, **Esc** forgets it. The Attack… dialog
  lines up the same way once a fight is running, so a swing chosen there can have
  a walk added to it before either happens.
- **A turn that is refused stays lined up**, with the reason beside it. "That is
  20 feet away — too far for a battleaxe" means move a square closer, not build
  the whole turn again. **Clear** discards it outright.
- **A turn you line up for somebody else's character is put to them.** Lining it
  up was your half of it, so when they accept it simply happens — you are not
  asked the same question twice. If nobody is at the table playing that character,
  it stays yours to carry out.
- **A turn can split its movement around the action** — three squares, swing,
  three more. Pick a move, a weapon, and another move: the bar reads them back in
  order, and they happen in that order. One limit: a turn with more than one move
  or attack cannot be put to a *player* yet, and says so rather than sending them
  a shortened version of it.
- **If a turn stops halfway, only the rest of it stays lined up.** Three squares
  and then a swing that cannot reach is three squares spent, so pressing **Do it**
  again tries the swing and does not walk them twice.

### Two rules the turn budget could not express

- **A fighter at level five gets two swings from one Attack.** The app knew the
  action was spent and nothing more, so a second swing looked like a second
  action and was refused. The number comes from the SRD's own level tables, so it
  is right for every class that has the feature.
- **Dash.** Spend your action to move your speed again. It is a choice within the
  turn rather than a setting, so dashing and then walking is the same turn as
  walking, dashing, and walking on — and the app can tell which one you meant.
  The rule is in; there is no button for it yet.
- **A swing that cannot land now stops the turn it was part of.** It used to
  announce itself and let the rest of the turn go ahead, which only started
  mattering once a turn could have a second half.

### Keys belong to the panel you are looking at

- **A panel's keyboard shortcuts only work while that panel has the focus.** So
  two panels can use the same key for their own version of a thing, the way two
  applications both use Ctrl+N, and nothing goes off while you are typing in the
  chat box. A few keys reach further on purpose — F9 still starts recording
  wherever you are, because the point of it is to record what you are saying
  about whatever you are looking at.
- **File ▸ Settings** is new, with a **Keyboard** page: every key, which panel
  owns it, how far it reaches, and what it does. Any key that will *not* do what
  its menu says is listed first and in bold — a key quietly taken by another
  panel is otherwise impossible to work out from the symptom.

### Fixes

- **A creature can no longer be put into a fight on a square the map does not
  have.** Placing one has always refused an impossible square; joining a fight
  already standing somewhere did not, so a goblin could be stood outside the
  room — and every legal move it then tried came back "off the map". It joins
  the initiative order without a square instead, which is a token you can drag
  onto the map.
- **Being cut down on the way somewhere is no longer reported as the square
  being taken.** Walking out of somebody's reach and falling to their swing said
  "that square is taken, or something is in the way" about an empty square,
  contradicting the line above it that said what had actually happened.
- **A character dropped crossing the room no longer lands the blow.** A turn
  that walks somewhere and then swings is carried out in two steps, and nothing
  between them checked whether whoever was holding the axe was still standing.

## 0.6.1

Fights are run on the map. Whoever is up is selected, Space opens what they can
do around their own pin, and creatures walk round each other rather than
through — players included, on their own turn.

**Everyone at a table needs this version.** The wire moved to 8 — whether a
creature has spent its reaction, and the death save it is owed, are new things
said over it — and a mixed table is refused at the door with a readable reason
rather than half-working. Campaign files are upgraded when you open them, and
nothing needs converting by hand.

**Dragging a token from square to square is gone**, for the DM as much as for
anybody. It was a teleport: no turn, no movement spent, straight through walls
and bodies. Creatures are placed onto the map and taken off it; in between they
move by taking a turn.

### Run a fight from the map

- **Whoever is up is selected on the map, and Space opens their choices around
  their pin.** One wedge for moving, one for each weapon they are carrying.
  Pick a wedge, then click a square or a creature. Running a fight used to mean
  the map for moving and a dialog for everything else, which is a decision made
  while looking away from the thing you are deciding about. Escape, or a click
  in the middle, puts the wheel away.
- Nothing can be taken back yet, which is the deal at a table once the die is
  on the felt. But a turn now travels as one plan rather than as two separate
  events, so lining a whole turn up before committing to it is a change to
  *when* the plan is handed over rather than a rewrite.
- **Picking Move and hovering a square shows the walk to it**, before you
  click. The part beyond what the turn has left turns the same warning colour a
  spent reaction is marked in, so a move that would be refused reads as
  refused before it is clicked rather than after.

### A map you can zoom

- **The wheel zooms**, on whatever the pointer is over, so closing in on a
  scrum does not slide it out from under you. The middle button drags the board
  about, `+` and `−` zoom from the keyboard, the arrows walk the view a square
  at a time, and `0` puts the whole map back. Until you zoom, the map still
  sizes itself to the panel and keeps doing so as you move the dock around.
- The coordinate rulers stay pinned while the board slides under them, the way
  a spreadsheet keeps its column letters — a square you cannot name is a square
  nobody can call out.
- **The + and − buttons on the edges of the map are gone.** They changed the
  fight when what you wanted was a closer look, and they left a big map in a
  small dock as a board of specks with no way to get nearer. How big the room
  is belongs to the fight itself, in **Fight…**.

### What a creature has left, on the map

- **Pips under whoever is up show what is left of their turn** — one for
  movement, one for the action — and they empty as the turn is spent.
- **A creature that has already swung at somebody walking past is marked.**
  The opposite way round on purpose: still having your reaction is everybody's
  default state, so a pip on every token would say nothing, while having spent
  it is exactly what somebody deciding whether to walk past needs to know.
- **Exhausted means exhausted.** A second action in one turn is now refused —
  the app had counted the action since turn budgets existed and never read the
  count, so a second swing went straight through. The DM's own turns obey the
  movement allowance too.

### Creatures walk round each other, and there is no teleport left

- **A move goes round what is in the way rather than through it.** Only the
  destination square was ever checked, so a walk across the room went straight
  through whoever stood between as long as it ended somewhere empty. Moves are
  routed now — the shortest way that touches nobody and nothing — and the
  hover preview draws that route, so you see the way round before you click.
  The fallen are stepped over rather than gone round, because a corpse that
  closed a corridor would be worse than the rule it enforced.
- **The long way costs the long way.** Movement is charged along the route, so
  going round a wall spends what going round it spends. Opportunity attacks
  follow the route too: leaving somebody's reach at a corner the straight line
  never went near still provokes one.
- **Only being walled in is refused**, and it is refused outright rather than
  put to the DM. Going too far is a rule and can be waived; a body in the way
  is a fact about the map.
- **Tokens can no longer be dragged from square to square — not even by the
  DM.** That gesture was a teleport: it belonged to nobody's turn, walked
  through walls and bodies, cost no movement and provoked nothing. It was the
  last way round the rules on a live battlefield. Creatures are still *placed*
  onto the map and *taken* off it; in between, they move by taking a turn.

### Players take their turn on the map too

- **The same wheel opens on a player's map**, for their own character and only
  while it is their turn. Space, pick a wedge, click a square or a creature.
  Describing the turn in words and accepting what autopilot works out still
  does everything it did — this is the short way round for "I step back and
  shoot".
- It is a request like any other, and the host holds it to the same rules: the
  route, the movement, the swings it provokes. It is not authority over the
  fight — no passing the turn, no initiative, nobody else's creature.

### Death saves are put to the player, and cannot be dodged

- **Falling unconscious now asks you for the save**, in the chat, as a roll you
  press. The host used to take it the instant your turn came round, so the
  loudest moment the game has was a number quietly changing in a list. The
  whole table sees it asked, which is most of what makes a death save one.
- **Asked by the rule, not by autopilot.** Nobody decides a death save
  happens; the book does. It appears whether or not anyone is running an agent.
- **You cannot dodge it.** Take your time, or ignore it — when the clock runs
  out the host rolls it for you and says so. A save nobody rolls is a character
  who neither dies nor recovers, which is worse than either. With nobody there
  to ask, it is rolled at once, as it always was.
- **The line says what you need**: ten or better on a d20, no modifier. The
  die that opens says DC 10 on it too.
- **Autopilot waits for it rather than working round it.** A death save is not
  a turn anybody chose to take, so there is nothing to formalise and nothing
  to narrate — it is told to leave the turn alone and not to say how it comes
  out.
- The rules are unchanged and were already right: ten or better on a bare d20,
  no modifier of any kind, three either way, a natural twenty puts you back up
  on one hit point and a natural one costs two.

### Fixes

- **A creature could swing more than once in a turn.** The rule was enforced on
  the DM's own map and not on the door everything else comes through, which
  spent the action without ever checking it. Movement and the action are still
  separate: having swung does not stop you walking.

### The fight moves again instead of jumping

- **Walks and swings are animated with nobody connected.** Since combat
  started working offline, the referee described every move to a wire that
  was not there — so tokens teleported and the dice landed in silence. The
  DM's own map is now told directly when nothing else is listening.
- **A walk is slower and eases in and out of a run**, rather than sliding at a
  flat speed and stopping dead. Half a second of that is the difference
  between a piece being dragged across a board and somebody walking.

### Opportunity attacks now catch a walk straight through

- **A creature that cuts through an enemy's reach on the way to somewhere
  else is swung at, even when it is never adjacent at either end of the
  move.** Opportunity attacks were checked at only the start and the end
  square of a walk, which missed exactly this: entering and leaving reach
  inside one move, with nothing at either end to show for it. Every square
  of the walk is checked now, using the same line the move is animated
  along, so what is enforced and what is shown agree. Squeezing along the
  *edge* of a reach and back out the same side is still not a leave — a rule
  that fired on every wobble would punish moving at all.

### A fight works with nobody connected

- **Combat no longer answers "go online first".** Rolling, hit points and
  passing the turn work with the laptop on the table and nobody joined. The
  same referee runs the fight either way — hosting is other people being able
  to reach it, not it existing at all — so a rule cannot come to mean one thing
  played alone and another thing played over the wire.

### Smaller

- **The app has its own icon**, and its own place in the Windows taskbar rather
  than being filed under Python.
- **Closing a panel takes its menu with it**, and opening it brings the menu
  back. A menu for a panel you cannot see acts on something you cannot watch it
  act on.

## 0.6.0

A character handed over is played by its own stand-in, which knows only what
its player knows.

**Everyone at a table needs this version.** The wire moved to 7 — whether a
stand-in is sitting in a seat, and what it is called, are new things said over
it — and a mixed table is refused at the door with a readable reason rather
than half-working. Campaign files are upgraded when you open them, and nothing
needs converting by hand.

### A character handed over is played by its own stand-in

- **Simulate turn now starts something that only knows what your player knows.**
  Until now a handed-over character was played by autopilot, which sees every
  secret in the campaign — so it knew where the ambush was and walked around
  it. That looks like good play and it is cheating. Each handed-over character
  now gets its own process, connected on a seat of its own, sent exactly what
  that player is sent. Two characters handed over are two of them, and neither
  knows what the other was told.
- **It plays the way a person does.** It says what it wants in plain words —
  *"I close on Yeemik and swing"* — autopilot turns that into rules and puts it
  back as a proposal, and it answers yes. Nothing moves until then, and you see
  the proposal exactly as you would a player's.
- **It reads the rules rather than guessing them.** How far it may walk and
  whether its attack is spent come from the host, which is the same figure the
  move will be judged against. A stand-in that guessed would spend its turns
  being refused, and an empty chair does not argue with the referee.
- It needs no API key and costs nothing to run: the decision is worked out from
  the map. Taking the character back stops it and takes its seat away in the
  same moment.

## 0.5.4

Autopilot no longer takes your turn, and the release pipeline says something
when it goes wrong.

### Fixes

- **Autopilot can no longer take your turn away.** It is meant to pass the turn
  on once it has resolved whoever was up — but that was an instruction, not a
  rule, and a model that called it one turn early skipped the person whose turn
  it actually was, with nothing on screen to say why. It happened sometimes,
  which is the worst kind. It may now end its own turns and nobody else's: a
  person's turn ends when they press **Done**, when their own clock runs out,
  or when the DM moves it on.

- **Releases publish again.** Nothing has been published since 0.3.1, and the
  reason was a dialog: when the `anthropic` package is not installed, pressing
  autopilot explains why it cannot run — and on CI, where nobody can click OK,
  that explanation waited forever. GitHub killed the run at its six-hour
  ceiling, which is reported as *cancelled* and names nothing. It only ever
  happened there, because the machine the code was written on has that package
  installed.

### Behind the scenes

- **A hung test now fails instead of disappearing.** No release has been
  published since 0.3.1: the test job was hanging and GitHub was killing the
  whole run at its six-hour ceiling, which is reported as *cancelled* and names
  nothing. Every job is now capped at thirty minutes and every test at two, so
  the next failure of this kind says which test it was.

## 0.5.3

Nobody is handed a login any more. A DM invites a player to a character, that
person makes their own password, and the DM never sees it.

**Everyone at a table needs this version.** The wire moved to 6 — making an
account is a new thing said over it — and a mixed table is refused at the door
with a readable reason rather than half-working. Campaign files are upgraded
when you open them, and nothing needs converting by hand.

**Anyone with a plugin needs to look.** The panel API is version 2: `AppContext`
gained `session_address`, `pending_join` is a record rather than a tuple, and
there is a new contract for right-click actions. A panel declaring version 1 is
skipped rather than loaded, so it degrades to absent instead of crashing.

### Fixes

- **The accept bar is back.** Autopilot would work out your turn and put it to
  you, and nothing would appear. Two rows say who plays a character — the
  login names the character, the character names the login — and when the
  second went missing the host could not tell that character from a monster:
  no accept bar, and autopilot took the turn. It now asks the login, which is
  the half a DM actually set, and puts the other half back when a session
  starts. Campaigns already in that state repair themselves on the next
  **Go online**.
- **Autopilot knows who it has been handed.** A character given to it before
  the switch was turned on was invisible to it, so it asked the table whether
  it should play them — a question somebody had already answered by pressing
  the button. The map now says *played by you*, and it is told never to ask
  that in the chat.

### A login that fails leaves you in the chooser

- **The window no longer opens on a join that did not work.** A wrong password,
  a host that is not there, or an invite already used now keeps you on **Join a
  session** with the reason and another go at it — rather than dropping you
  into an empty app with the explanation buried in a chat log you have no
  session for.
- **Nothing is remembered until it works.** The password is only saved, and
  "open this automatically next time" only applied, once the host has accepted
  the login. Setting a bad session to open automatically used to mean an app
  that failed the same way every launch.
- **A login that works is kept, without asking.** *Remember my password for
  this session* has gone from both join screens: joining your own weekly game
  is not a question with two answers. It still only ever stores a password the
  host has accepted, still in the operating system's credential store, and
  still never in a file of ours.

### Right-click anybody

- **Invite a player from wherever you are looking at them** — the Characters
  panel, the initiative order. It used to live only in the Players dialog,
  which meant knowing to go there.
- A creature now carries its menu with it. What the panel you are in can do
  comes first, because that is why you right-clicked there: **Take off the
  map** stays a Combat thing and is offered nowhere else. What is true of the
  creature anywhere follows, under a separator.
- Plugins can add to it. See *Adding to the right-click menu* in the README —
  an action declared once appears in every panel that lists characters,
  including panels written later.

### An invite is one line

- **The invite carries the address as well as the code**, so there is one thing
  to send and one thing to paste:
  `ws://192.168.1.10:8765#7K3PQ-M2XRV`. Pasting it into the invite box fills
  the address in too. Two things to copy was two things to get wrong, and the
  address is the half people mistype.
- It survives the trip: a stray space, a full stop a chat app added, a
  `canonkeeper://` somebody put in front, or the code alone all read correctly.
- On a headless server, `--invite CHARACTER --address wss://your-host` prints
  the whole line.
- **The chooser takes one too.** Paste it on **Join a session**, the first
  screen the app shows, and you are in — that is where somebody arriving for
  the first time actually is, rather than inside a panel they have not seen
  yet.

### Nobody is given a login any more

- **An invite is the only way in the first time.** There is no longer any way
  for a DM to make a player's login: not in the Players dialog, not in a
  template, and not on a headless server. A DM who set your password knew it,
  which made "do not reuse this one" advice they were not in a position to
  give.
- **After that you are trusted.** Log in with the username and password you
  chose, remembered by your machine's credential store if you asked it to.
- **Lost it? Ask for another code.** A new invite on a character you already
  play hands the seat back to whoever uses it — the same operation as somebody
  new taking the character on. The login playing them keeps working right up
  until the code is used, so a code made by accident does not throw anybody
  out mid-session; when it is used, that seat's old password stops working and
  anyone still logged in on it is told why and disconnected.
- The seat is handed over rather than replaced, so the private things that
  player had been told — shares made with them alone, ownership of their
  character — follow the character rather than being quietly dropped.
- On a headless server, `--add-player` is gone and `--invite CHARACTER` prints
  a code. `--characters` shows who is played, who is invited, and who is
  neither.

### Players make their own logins

- **Invite a player to a character.** In **Players**, pick the character and
  press **Invite a player...**: you get a code to send them. They type it once,
  choose their own username and password, and the account arrives already
  attached to that character.
- **You never see their password**, and it never crosses the network — not even
  the first time, when there is nothing on the host to check it against. The
  code is what the new password material is sealed with, and the code is not on
  the wire either.
- **Making a new code kills the old one.** Somebody who never got round to
  joining cannot come back a week later and use the first code you sent, and
  neither can anybody who read it over their shoulder.
- **Codes last 24 hours** and can be used once. Send one the way you would send
  a password: it is the whole of what stands between a stranger and that seat.
- The Players list shows which characters have a code out and nobody using it
  yet, so an unanswered invite is not invisible.

### One-shots no longer ship passwords

- **A one-shot now starts with characters and nobody in them**, and you invite
  your players as above. *The Last Coach* used to create three logins —
  `one`, `two`, `three` — and a DM login, all with a password written in the
  template file. That file is in a public repository, so those were published
  passwords: anybody who found a hosted one-shot could have read them and
  logged in as somebody's character, or as the DM.
- Templates meant for testing the app keep their fixed logins. Those are the
  ones the suite runs against, and a template that cannot be tested is worse
  than one with known passwords — but they are not adventures, and they are
  kept out of the chooser.
- **A login written into a playable template is now ignored** rather than
  trusted, so this cannot come back by somebody adding one to the next
  template.

## 0.5.2

Dying takes three rolls, walking away from somebody costs, and a fight has
sides.

**Everyone at a table needs this version.** The wire moved to 5 -- death saves,
who is lying down and which side they are on are all new things said over it --
and a mixed table is refused at the door with a readable reason rather than
half-working. Campaign files are upgraded when you open them, and nothing needs
converting by hand.

### Sides

- **A fight has teams.** Two, made with every fight and filled in without being
  asked: **The party**, and **Hostile** for everyone else. The initiative order
  is grouped by them, so "how many of them are left" is a thing you can see
  rather than count.
- **Right-click anybody to move them onto another side**, or to make a new one.
  That is the case the old guess got wrong -- the captured guard who fights
  beside the party, the rival adventurers who are not monsters -- and it now
  takes one click instead of an argument with the app.
- **Opportunity attacks follow the sides**, not what kind of creature somebody
  is. An NPC you have moved onto the party's side is an ally in every sense
  that matters.

### The dead stay where they fell

- **A body is a ghost on the map, not a gap.** Taking the token away made the
  square everybody was looking at -- the one with your friend on it -- the one
  square showing nothing. It fades, greys, and stays. Nobody has to walk around
  it: a body holds no ground.
- **Rows in the initiative order are two lines now**: the name and initiative
  on the first, and everything else -- hit points, dying and how close it is,
  off the map, autopilot, unshared -- underneath. Looking for whose turn it is
  should not mean reading past a hit point total to find it.

### The map holds you

- **Opportunity attacks.** Walk out of an enemy's reach and they swing at you
  as you go -- one reaction each per round, melee only, so a bow does not hold
  ground. Without it the grid was a diagram: you could stroll past the ogre to
  reach the wizard behind it and the ogre could only watch. Dropped on the way
  out, you fall on the square you left, which is where anyone coming to help
  will look. A DM dragging tokens about provokes nothing -- that is arranging
  the board, not somebody walking.

### Dying

- **A player character at zero is dying, not dead.** A death save at the start
  of each of their turns, on the host's dice, in front of everybody: three made
  and they are stable, three failed and they are gone. A natural twenty puts
  them back on their feet with one hit point and the turn still theirs; a
  natural one costs two. Hitting somebody who is already down costs them a save.
  The initiative order carries the count, so the table can see how close it is.
- **A monster at zero is still simply dead.** Three more d20s to confirm the orc
  is finished is nobody's idea of a good time.
- **The turn no longer stops on the dead.** They keep their place in the order
  -- a DM can still bring them round -- but the turn steps over them. A long
  fight used to get slower the closer it came to being over, offering a turn to
  every creature that had died in it.
- **Healing clears the count.** Carrying two failures through a healing word
  and into the next time you go down is a rule the game does not have.

### Also

- **Half a minute to finish your turn**, not fifteen seconds. Fifteen was a
  guess made without a table, and it hurried people who had just watched their
  attack land.

## 0.5.1

0.5.0's combat, finished: the rules it was missing, turns that end by
themselves, and enough of it happening on screen to follow without reading the
chat.

**Everyone at a table needs this version.** The wire moved to 4 — a turn ending,
a rule bending, and everything you now watch happen are all new things said over
it — and a mixed table is refused at the door with a readable reason rather than
half-working. Campaign files are upgraded when you open them, and nothing needs
converting by hand.

### Combat, continued

- **A speed limit.** A turn's movement is what the character's speed allows --
  six squares for most people, read off the sheet, with `overrides.speed` for a
  monster that differs. Checked when a turn is proposed *and* again when it is
  accepted, since the map moves while somebody is deciding.
- **What you have left.** The Combat panel shows the turn in progress: *"Your
  turn: 15 feet of 30 left, 15 used · attack still to come."*
- **Anything else, or done?** After you act, the turn is still yours -- say what
  else you do, press **Done**, or the turn passes on its own after fifteen
  seconds. The clock runs on the host, so it is a promise to the whole table
  rather than to whoever's laptop is awake. Anything you type stops it.
- **Taking a turn without autopilot.** **Attack...** in the Combat panel: who is
  up, a target, a weapon off their sheet. The host still rolls it.
- **Autopilot takes the monsters' turns.** It waits a few seconds in case
  somebody objects, then acts. Player characters are never taken this way --
  theirs are proposed and confirmed, as before.
- **The DM can overrule the rules.** Ask autopilot for something the rules
  refuse -- a creature moving out of turn, or further than its speed -- and it
  comes back to you naming the rule it breaks: *allow it, or not*. Squares off
  the map, or ones somebody is standing in, are not rules and are still refused
  outright.

- **Autopilot rolls its attacks.** It could move a goblin and talk about it
  hitting somebody, and had no way to actually swing -- so it described
  outcomes instead of asking for them. Now it swings, the host rolls, and it
  narrates what came back.
- **Simulate turn.** Hand a character to autopilot for this fight -- an empty
  chair, or a player who has stepped out. Either of you can do it, from the
  Combat panel, and either can take them back. Everyone is told, and the
  initiative order says which of them is being played by a machine.
- **A machine-played turn ends by itself.** Monsters and handed-over characters
  acted and then held the table: the only thing that ever ended a turn was a
  person pressing **Done**, and there is no person. The turn now passes a few
  seconds after autopilot stops acting -- each thing it does restarts the wait,
  and it does not run while autopilot is still thinking.
- **Things you pick up go on your sheet.** Autopilot records loot as it hands
  it over, instead of describing a sword that then exists nowhere. With
  autopilot off the DM still types it into the Inventory field.

### You can see it happen

- Tokens **walk square by square** instead of jumping.
- An attacker **leans in**, and the damage floats off whoever took it -- or the
  word *miss*, which is half of what happened.
- A creature at zero **fades off the map**, and stays in the initiative order so
  you can bring them round.
- Everyone sees the same thing at the same moment: the host says what happened
  and every screen draws that, rather than each inventing its own version. A
  creature you have not been shown does not animate on your map.

## 0.5.0

Combat: a map, an initiative order, and a way for a player to take their turn
in plain words.

**Everyone at a table needs this version.** The wire moved to 3, and a mixed
table is refused at the door with a readable reason rather than half-working.
Campaign files are upgraded when you open them, and nothing needs converting by
hand.

### Combat

- **A new Combat panel**: an initiative order and a grid, side by side. **New
  fight** makes one immediately -- no questions -- and **Add...** puts
  characters and NPCs into it. Name it or resize it later, under **Fight...**,
  if you ever want to.
- **Drag someone out of the order and onto the map** to place them, or drag a
  token from square to square to move it. **Roll initiative** rolls a d20 for
  everyone and adds their Dexterity where there is a sheet to read it from;
  double-click a row to set one by hand.
- **Start**, **Next turn** and **End fight** keep the round. Taking whoever is
  up out of the fight passes the turn on rather than dropping it, so nobody
  gets a second go.
- **Ctrl-click a square** to put something in the way -- a rock, a pillar, an
  overturned cart. Nobody can stand there and anybody can hide behind it. The
  **+ / −** buttons on the edges of the map push the walls in and out a row at
  a time.
- **Off the map is not out of the fight.** Someone who has fled, or has not
  come through the door yet, keeps their place in the order. Right-click for
  either.
- **Squares are numbered from the middle.** 0,0 is the centre, x to the right
  and y downwards, with rulers along two edges. "The one at minus three, two"
  is a square everyone can find, and it is still that square after the map
  grows.
- **Players see the fight as it happens**, read-only, and see exactly the
  creatures you have shared with them. Putting a monster on the map does *not*
  reveal it, so you can lay an ambush out in front of them. Tokens the party
  cannot see are drawn dotted on your map, and right-clicking one offers to
  share it.
- *Test Combat* opens on round one with everyone already placed, the terrain
  laid out and the order already rolled -- the same fight every time.

### Taking a turn

- **The chat says when it is your turn.**
- Say what you want in plain words -- *"I get behind the orc and hit it with my
  axe"* -- and autopilot works out what that is in rules and hands it back:
  *"Move to 0,-1 and attack Yeemik with a battleaxe."*
- The map shows it while you decide: a dotted line to where you would end up, a
  ghost of your token there, and a sword over whoever you would hit.
- The chat box waits for **Do it**, **Say more...** or **Refuse**. **Say
  more...** unlocks it for one message and what comes back is the same turn,
  changed. Nothing touches your character until you accept -- then the host
  moves you and rolls the attack, with your bonus off your sheet against the
  target's armour class.
- Weapon attacks only, and melee reaches one square. Spells, advantage and the
  rest are still the DM's to rule on.

### Autopilot

- **It can run a fight.** Start one, place everyone where the scene it just
  described put them, add the cover it mentioned, move monsters, pass the turn,
  and put a player's turn to them. All of it goes over the wire through the
  same checks your own buttons use, so it cannot build a fight the app could
  not have built itself -- and none of it works with the switch off. Start the
  agent with `--talk-only` to keep its hands off the map entirely.
- **While autopilot is on, what you type no longer reaches the party.** There
  is one voice at the table and it is the agent's. Your line goes to it instead
  -- marked *(to autopilot)* on your own screen -- and it works your direction
  into the scene in its own words, without repeating it back or letting on that
  anybody said anything. Press the switch to speak to the party yourself.
- **It answers you.** It used to drop whatever was queued the moment the DM
  spoke, which is how autopilot came to look broken: you switched it on, said
  something, and nothing ever happened.
- **It reads more of the conversation**, with the pauses marked, so an exchange
  that began several messages up is answered as a whole rather than from
  whichever line happened to arrive last.

### Rolls you can click

- When your DM writes "make a DC 14 Perception check", the words become a link.
  Clicking it opens a die that already knows what your character adds, and the
  answer comes from the table's dice, not from your own machine. Skill checks,
  saving throws, ability checks, initiative and plain dice notation all count.
- Only your DM's lines -- and autopilot's, when it is standing in for them --
  and only when you have a character to roll with.

### Sheets

- **The bundled one-shots have real characters**: species, class, level,
  abilities, skills and equipment. Every sheet now passes the same validation
  the host runs on a player's edit, which the old ones did not -- so a player's
  first change to one came back refused as an illegal sheet.
- **And real monsters.** Every NPC has a statblock: ability scores, armour
  class, hit points and what it is carrying. Which is also what makes them
  something a character can attack.

### Fixed

- **Private lines are no longer read out to the next player who logs in.** The
  chat log is handed over on every login, and everything the host had ever told
  the DM privately was in it: refusals, requests waiting for approval, and the
  text of an expired API key. Lines now record who they were for, and old ones
  stay public, which is what they were.

## 0.4.1

One-shots, and a table that says what is happening.

### One-shots

- **Start a one-shot** in the chooser builds a campaign from a template, with
  the characters, places, facts, shares and logins already in it -- the same
  every time. Three ship: *The Last Coach*, an evening for three; *Test
  Combat*, which opens on initiative and ends when the fight does; and *Test
  Table*, for exercising the app without typing a world in first.
- What you get is an ordinary campaign. It only remembers which template it
  came from, which is what lets **File > Start Again from the Beginning** put
  it back. **File > Storyline...** holds the beats and where it ends, and
  **File > Keep This One** makes it a campaign of your own.

### At the table

- **A refused change is put back.** Saying no used to leave the rejected value
  on the player's screen, which reads exactly like it was accepted. The host
  now sends the character as it actually stands and the panel reloads --
  including over a form still being edited, because the host's copy is the true
  one.
- **The log is out of the way.** The chat hid the game under the app talking
  about itself. Arrivals, departures, autopilot switching and the rest sit
  behind **Show log**. Anything addressed to you -- a refusal, a roll, an agent
  that could not answer -- is never filtered.
- **Errors are in the log, and say so.** **Show log** turns red when something
  goes wrong, and the message appears in the status bar at once. Hidden *and*
  unannounced would be the worst of both.
- **A panel with something new is highlighted** until you look at it, then the
  colour fades. An update in a panel behind another tab may as well not have
  arrived.

### Fewer buttons

- **Going online publishes it.** Hosting for the people in the room and for the
  one who could not make it was two buttons and one wish. **Share on the
  internet** is gone. A session whose publish fails stays up on your network --
  that is half a thing succeeding, not an error.
- **Join session** is gone from the DM's view, where it never meant anything.
- **Fixed: the agent was listed as a player.** It answers for the DM, and now
  says so.
- Double-clicking any list in the chooser opens what it points at.

### Underneath

- **Fixed: the canon log could reorder itself between reads.** Facts were
  ordered by timestamp alone, so several asserted in the same millisecond came
  back in whatever order SQLite chose. Found by the test that asserts two runs
  of a template are identical.

## 0.4.0

Autopilot: hand the table to an agent, and take it back.

### Autopilot

- **Press it and an agent answers for you** -- for a break, a second voice, or a
  shopkeeper haggled with while you read ahead. Press it again and the table is
  yours, mid-sentence if that is when you pressed it.
- **It cannot speak while the switch is off.** Not by good behaviour: the host
  refuses its messages. It stays connected and listening, so switching back on
  is instant, but nothing it says reaches the table.
- **It is named on the roster as an agent**, and switching autopilot on or off
  is announced in the chat and kept in the log. A table deserves to know when it
  is being answered by a machine.
- **It has no path to your campaign.** It holds a socket and a login, exactly
  like a player's app, even when the app started it for you.
- **It never rolls.** Dice are the host's; it asks for a roll like anyone else.
- **A turn is a lull, not a message.** Three players talking to each other gets
  one answer when they stop, not three interruptions. Answer them yourself and
  its queued reply is dropped. `--pause` tunes the wait.
- **Autopilot is never remembered between sessions.** Opening a campaign to find
  a machine already running your table is not a state to arrive in by accident.

### Setting it up

- **The button does the work.** It creates the agent's login, keeps its password
  in your credential store, and starts the agent against your own session. An
  agent you started yourself, on this machine or a spare box, is left alone.
- **Agent...** holds the key, the model and -- for keys that need one -- a
  workspace id. It stays reachable, so a mistyped key can be corrected.

### Knowing what it is doing

- ***Autopilot is writing...*** appears under the chat while it composes, since
  several seconds of silence looks exactly like a broken agent.
- **What it has cost** -- answers, tokens, dollars -- on the DM's screen only,
  updated after every turn rather than at the end.
- **When it cannot answer, you are told why**, privately. An expired key, a
  refused parameter, a stopped process: all of it used to be silence.

### Saying what you mean

- **`canonkeeper-mcp`** exposes one seat at the table to an MCP client, so a
  player can talk instead of typing. It holds one login and has exactly that
  login's authority: dice are rolled on the host, and a change to a character is
  a request the DM answers.

### Underneath

- **The wire contract is its own package.** `canon_keeper_protocol` depends on
  nothing but the standard library, so anything headless can speak to a session
  without installing Qt. Nothing outside the app imports the app, and that is
  checked by tests rather than remembered.
- **Fixed: two campaigns could share one saved password.** Credentials were
  keyed by `campaign.id`, which is 1 for almost every campaign there has ever
  been. Keyed by the campaign's own id now, and a saved password the host would
  refuse is replaced rather than handed over to fail at the door.

## 0.3.1

- **Speak into the chat.** A **Speak** button beside the chat box transcribes
  what you said into the box for you to correct before sending — useful for
  anyone who would rather talk than type mid-scene. Local, and primed with your
  campaign's names.

## 0.3.0

Character sheets, and a table where the DM decides.

- **The chat is kept.** Rejoin a campaign and the last hundred messages are
  already there, so a session picks up where the last one stopped. Everything is
  kept and filed by evening; only the recent tail is loaded.
- **You are the authority on your campaign.** A player's change is checked
  against the copy they were actually sent, not against anything their app
  claims. If you changed the character in the meantime their change is refused
  and their screen corrects itself, and anything they had proposed against the
  old sheet is refused automatically.
- **Players ask, you decide — for everything.** Nothing a player changes is
  written on their say-so, hit points included. Their sheet's button reads *Ask
  my DM*, a **Waiting for you** button appears with the queue, and refusing
  prompts you for a reason which is sent privately to them. Requests go to you
  alone, not the whole table.
- **Hand a character to a player** with *Played by* in the Characters panel, or
  by assigning it in **Table ▸ Players…**. They then see its whole sheet.
- **Reconnecting is cheap.** Your copy is cached, so the app shows your
  character before it connects and a reconnect fetches only what changed.
- **Equipment and spells on the sheet**: add gear (worn armour changes the
  armour class), learn and forget spells, tick which are prepared.
- **Guided character creation.** **Build...** in the Characters panel walks
  through species, class, abilities, skills and spells, with the standard array
  and point buy, and adds the class's starting equipment at the end.
- **Players get their sheet too.** Their own characters show the whole sheet and
  they keep their own hit points and conditions; level, class and ability scores
  stay the DM's to set. Other people's sheets are read-only.
- **Character sheets.** A **Sheet** tab beside Story in the Characters panel:
  species, class, level, ability scores and skills, with hit points, armour
  class, saving throws, skill bonuses and spell slots worked out as you type.
  Built on the SRD 5.1, bundled, so nothing needs the internet.
- Players can own **more than one character**, and see the whole sheet of each.
  Anyone else's shows only what a party would know: class, level, hit points,
  conditions. An NPC's statblock is never sent.
- Every character now carries a **version**, so two people editing at once no
  longer means one edit silently disappearing.

- **Panels can be renamed.** Three layers: what you call it, what the party
  calls it, and the default — the more specific one wins. The DM's names travel
  with the session and update live; yours stay on your machine.

## 0.2.0

Everything below the shell: campaigns, players, and playing together.

### Campaigns

- The app now starts from a **campaign chooser**. Campaigns you run are files on
  your machine; joining someone else's needs the login they gave you.
- **Remember my password** saves it to your operating system's credential store,
  and **Open this automatically next time** skips the chooser entirely.
- **File ▸ Open a Different Campaign…** returns to the chooser without
  restarting.

### Playing together

- **Go online** hosts your campaign so players can join over the network. They
  are listed automatically on a LAN — nobody reads out an IP address.
- **Share on the internet** publishes the session through Tailscale Funnel, with
  a real certificate. Only the host installs Tailscale.
- Shared chat and **dice rolled on the host**, so nobody can nudge them.
  `/roll 2d6+3`, `4d6kh3`, `2d20kl1`, or the quick buttons.
- Per-campaign logins with the character each player plays. Chat shows the
  character's name.
- **Players see** on any character or place: share it with the whole party or
  with particular people. Your motives, secrets and notes are never sent — an
  unshared entity does not reach a player's machine at all.
- Players own their character sheet; the host enforces that they cannot touch
  anyone else's.
- Passwords are never sent over the network, and a recorded login cannot be
  replayed.

### Transcript

- Press **F9**, narrate a beat, press it again: the clip is transcribed locally
  by Whisper. No audio leaves your machine and there is nothing to pay for.
- Known names are **highlighted** as they appear, and selecting unknown ones
  turns them into characters or places — which feeds them back into the
  transcriber, so the next time you say the name it comes out right.

### Everywhere

- **Light and dark themes**, following your desktop by default.
- Panels dock, undock into real windows, and the arrangement can be saved as
  named layouts.

## 0.1.0

The shell: dockable plugin workspace, Characters and Cities panels, saved
layouts, and a SQLite campaign file you own.
