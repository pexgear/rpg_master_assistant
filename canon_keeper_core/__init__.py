"""The game, with no screen attached.

The database, the repositories over it, the 5e rules, and the SRD content. All
of it already imported neither Qt nor anything else outside the standard library
and ``platformdirs`` -- it was simply packaged inside the desktop app, which made
"run a session without a screen" mean installing 660 MB of Qt to do it.

So it lives here instead, and the arrow points one way: the app imports this, and
this has never heard of the app.

**It is not for clients.** The invariant in AGENTS.md -- "nothing outside the app
imports the app" -- existed to keep a headless client unable to open a campaign
database. Moving the database out of the app does not weaken that, but it does
move it: the rule is now that **a client imports the protocol and nothing else**,
while the *host* imports this. A client that reached in here would be reading a
campaign file directly instead of asking somebody who holds the dice, which is
the whole thing being prevented.
"""
