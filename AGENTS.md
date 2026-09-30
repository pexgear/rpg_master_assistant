# Working in this repository

Instructions for an AI agent changing this code. Short on purpose — the
explanation lives in [ARCHITECTURE.md](ARCHITECTURE.md), which you should read
before your first change, and what is true *right now* lives in
[PLAN.md](PLAN.md), which you should read before deciding what to do.

---

## Cutting a release

**When you change the version number, the documentation changes with it.** This
is the rule most often skipped, because a version bump feels like a one-line
edit and is not.

`RELEASING.md` has the full procedure. The part that needs saying here:

1. **`canon_keeper/__init__.py`** — the version lives in exactly one place;
   `pyproject.toml` reads it from there.
2. **`CHANGELOG.md`** — a section for the new version, written for someone
   running a game rather than someone reading the diff. Bugs in a feature that
   never shipped are not fixes; fold them into the description of the thing
   that now works.
3. **`README.md`** — if the release changed what a person *does*: a new button,
   a new command, a new install step, different behaviour. A README describing
   the previous release is worse than no README, because it is trusted.
4. **`ARCHITECTURE.md`** — if the release changed the *shape*: a package, a
   migration, a message type, a version constant, an invariant, a flow. Check
   **Known gaps** in particular: a release usually closes one, and a limitation
   still listed after it is fixed is read as true by the next person.
5. **`PLAN.md`** — where the project now stands and what is next. A release
   moves both.
6. Only then commit, tag `vX.Y.Z`, and push the tag.

The suite checks what it can — that every package, migration, panel and version
constant in `ARCHITECTURE.md` matches reality, and that the current version has
a changelog section. It cannot check whether the *reasoning* is still true, or
whether the README still describes the app someone will open. Those are yours.

Do not bump a version, commit, or push unless you were asked to.

---

## Running things

Use the project virtualenv, not whichever Python is first on `PATH`:

```bash
.venv/Scripts/python -m pytest
```

**A fresh checkout's virtualenv may hold only what the app needs to run.** If
that command says `No module named pytest`, the test dependencies are not in it
yet -- they are an extra, and it is the same one CI installs:

```bash
.venv/Scripts/python -m pip install -e ".[dev]"
```

Run the suite headlessly and **capture the exit code before piping it
anywhere**. Piping to `tail` reports the exit status of `tail`, which is always
success, and has produced confident reports of a green suite that was not:

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python -m pytest tests/ -q > out.txt; echo $?
```

The suite takes two to three minutes and is expected to be green. Panels are
tested headlessly through `pytest-qt`; the agent is `asyncio` and runs under
`asyncio_mode = "auto"`, so coroutine tests need no marker.

To exercise the agent against a real host you need `pip install -e ".[agent]"`
in that same virtualenv, and a key. `canonkeeper-agent --dry-run` prints what it
would have said without saying it.

---

## Things not to break

The full list is in [ARCHITECTURE.md](ARCHITECTURE.md) under **Invariants**.
The four that have actually been broken before:

- **The host decides.** A client asks. If you find yourself applying a change
  because a client sent it, stop.
- **Projection is an allowlist.** Add a field to an entity and it is private
  until someone deliberately shares it. Never write a denylist.
- **`campaign.id` is not unique.** Every campaign is its own SQLite file, so
  almost all of them are campaign 1. Anything that tells two campaigns apart
  from outside must use `campaigns.campaign_key()`.
- **Nothing outside the app imports the app.** That is what keeps a headless
  client unable to reach a campaign database, and it is enforced by a test.

---

## Conventions

**Comments say why, not what.** The code says what it does. A comment earns its
place by recording the reason a decision was made, or the failure that made it
necessary.

**Test names are sentences about behaviour.** `test_a_player_cannot_invent_a_figure`,
not `test_spend_validation`. A failing test should read as a description of what
broke.

**Prove a test can fail.** A test that cannot fail is decoration. When adding one
that guards a claim, break the claim once and watch it go red.

**`ARCHITECTURE.md` never names a version number.** It describes the shape,
which outlives any release. Versions belong in `CHANGELOG.md`.

---

## Traps in this environment

**Windows shell heredocs mangle escape sequences.** Writing Python through a
`bash` heredoc turns `\n` inside a string literal into a real newline and
produces a syntax error. This has happened repeatedly. Use the file-writing
tools for anything containing escapes, and never a PowerShell here-string
(`@'...'@`) inside the Bash tool — the markers land in the file.

**The editable install can go stale.** New packages and console scripts do not
appear until you re-run `pip install -e .`. The symptom is an import error or a
missing command for code that is plainly on disk.

**A subprocess that dies is silent unless you read it.** The app supervises the
agent for exactly this reason. If you spawn something and it appears to do
nothing, read its output before theorising.
