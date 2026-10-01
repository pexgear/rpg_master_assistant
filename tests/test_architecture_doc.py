"""ARCHITECTURE.md, checked against the code it describes.

An architecture document rots. It rots quietly, and the first person to notice
is someone who trusted it and lost an afternoon.

So the claims in it that *can* be checked are checked here: which packages
exist, which way they depend, what the version constants are, what migrations
there are, which panels ship. Change the shape without changing the file and
this fails.

What is deliberately not checked is the prose about *why* -- the reasoning,
the costs, the known gaps. No test can tell whether that is still true, which
is exactly why it is the part worth reading and the part worth maintaining by
hand.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

import canon_keeper
import canon_keeper_protocol
from canon_keeper.plugin import API_VERSION

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "ARCHITECTURE.md"


@pytest.fixture(scope="module")
def text() -> str:
    assert DOC.exists(), "ARCHITECTURE.md has been deleted, not merely gone stale"
    return DOC.read_text(encoding="utf-8")


def _packages() -> set[str]:
    return {
        path.name
        for path in ROOT.iterdir()
        if path.is_dir() and path.name.startswith("canon_keeper")
    }


# ----------------------------------------------------------------- the packages


def test_every_package_is_described(text):
    missing = {name for name in _packages() if name not in text}
    assert not missing, (
        f"{missing} exist but ARCHITECTURE.md does not mention them. A new "
        "package is a change of shape, which is what this file is for."
    )


def test_it_describes_no_package_that_does_not_exist(text):
    named = set(re.findall(r"`(canon_keeper\w*)`", text))
    # Module paths like `canon_keeper/net` are not package claims.
    named = {n for n in named if "/" not in n}
    ghosts = named - _packages()
    assert not ghosts, f"{ghosts} are described but do not exist"


def test_the_dependency_arrows_still_point_one_way():
    """The claim the whole split rests on."""
    for package in _packages() - {"canon_keeper"}:
        for source in (ROOT / package).rglob("*.py"):
            body = source.read_text(encoding="utf-8")
            reaching = re.findall(r"^\s*(?:from|import)\s+(canon_keeper)\b", body, re.M)
            assert not reaching, (
                f"{package}/{source.name} imports the app. ARCHITECTURE.md says "
                "nothing outside the app does, and the safety argument rests on it."
            )


def test_the_protocol_still_needs_nothing(text):
    """It is described as stdlib-only, which is why a headless install is cheap."""
    assert "stdlib only" in text or "standard library" in text
    root = Path(canon_keeper_protocol.__file__).parent
    allowed = set(sys.stdlib_module_names) | {"canon_keeper_protocol"}
    for source in root.rglob("*.py"):
        body = source.read_text(encoding="utf-8")
        for name in re.findall(r"^\s*(?:from|import)\s+(\w+)", body, re.M):
            assert name in allowed, f"{source.name} imports {name}"


# ------------------------------------------------------------------ the numbers


def test_the_protocol_version_is_right(text):
    stated = re.search(r"`PROTOCOL_VERSION = (\d+)`", text)
    assert stated, "ARCHITECTURE.md no longer states the protocol version"
    assert int(stated.group(1)) == canon_keeper_protocol.PROTOCOL_VERSION


def test_the_panel_api_version_is_right(text):
    stated = re.search(r"`API_VERSION = (\d+)`", text)
    assert stated, "ARCHITECTURE.md no longer states the panel API version"
    assert int(stated.group(1)) == API_VERSION


def test_the_frame_caps_are_right(text):
    """Both numbers, because the whole point is that they differ."""
    from canon_keeper_protocol import MAX_FRAME_BYTES, MAX_HOST_FRAME_BYTES

    assert f"({MAX_FRAME_BYTES // 1024} KB)" in text
    assert f"({MAX_HOST_FRAME_BYTES // 1024 // 1024} MB)" in text


# --------------------------------------------------------------- the migrations


def test_every_migration_is_listed(text):
    on_disk = sorted(p.name for p in (ROOT / "canon_keeper_core/db/migrations").glob("*.sql"))
    missing = [name for name in on_disk if name not in text]
    assert not missing, (
        f"{missing} are not in ARCHITECTURE.md. A migration is a change to the "
        "shape of the data, which is the thing this file describes."
    )


def test_it_lists_no_migration_that_does_not_exist(text):
    on_disk = {p.name for p in (ROOT / "canon_keeper_core/db/migrations").glob("*.sql")}
    listed = set(re.findall(r"`(\d{3}_\w+\.sql)`", text))
    assert not listed - on_disk, f"{listed - on_disk} are listed but missing"


# ------------------------------------------------------------------- the panels


def test_every_shipped_panel_is_mentioned(text):
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    panels = config["project"]["entry-points"]["canonkeeper.panels"]
    missing = [name for name in panels if name not in text]
    assert not missing, f"{missing} ship as panels but are not described"


def test_the_console_scripts_still_exist(text):
    """The doc names what each package is for; a removed entry point is a lie."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = config["project"]["scripts"]
    for module in ("canon_keeper_dm_agent", "canon_keeper_mcp"):
        assert any(module in target for target in scripts.values()), (
            f"{module} is described as having a command; it no longer does"
        )


# --------------------------------------------------------------- the file map


def test_the_file_map_is_not_fiction(text):
    """Every path in the layout block exists.

    The map covers more than one package now, so an indented path belongs to
    whichever package heading it sits under rather than to a fixed root. Getting
    that wrong is how a map that reads correctly fails anyway.
    """
    block = text.split("## Where to look", 1)[1].split("```", 2)[1]
    missing = []
    package = "canon_keeper"
    for line in block.splitlines():
        if not line.strip():
            continue
        if not line.startswith(" "):
            # A package heading: "canon_keeper_core/  the game, no screen".
            package = line.split()[0].rstrip("/")
            continue
        found = re.match(r"^\s{2}(\S+)", line)
        if found and not (ROOT / package / found.group(1).rstrip("/")).exists():
            missing.append(f"{package}/{found.group(1)}")
    assert not missing, f"{missing} are in the file map but not on disk"


def test_the_version_is_not_hard_coded_anywhere_in_it(text):
    """A doc that names the current version is out of date one release later."""
    assert canon_keeper.__version__ not in text, (
        "ARCHITECTURE.md names a version number. It describes the shape, which "
        "outlives any release; CHANGELOG.md is where versions belong."
    )


# -------------------------------------------------------------------- the flows
#
# The diagrams name real message types and real methods. Naming one that does
# not exist is the specific way a flow diagram goes wrong: it stays plausible.


def test_every_message_type_named_in_a_diagram_exists(text):
    from canon_keeper_protocol import MessageType

    known = {member.name for member in MessageType}
    diagrams = re.findall(r"```mermaid(.*?)```", text, re.S)
    assert diagrams, "the flow diagrams have gone"

    named = set()
    for block in diagrams:
        # Only the arrow lines, and only outside the payload. A `Note over` is
        # prose, and `{proof = HMAC(...)}` describes a payload -- neither is
        # claiming HMAC is a message type.
        for line in block.splitlines():
            if "->>" not in line:
                continue
            spoken = re.sub(r"\{.*?\}|\(.*?\)", "", line)
            named |= set(re.findall(r"\b([A-Z][A-Z_]{3,})\b", spoken))

    ghosts = named - known
    assert not ghosts, (
        f"{ghosts} appear in a flow diagram but are not message types. A "
        "diagram naming a message that does not exist stays plausible, which "
        "is what makes it dangerous."
    )


@pytest.mark.parametrize(
    "described",
    [
        ("canon_keeper.net.server", "SessionServer", "publish_entity"),
        ("canon_keeper.net.server", "SessionServer", "refuse_conflicting"),
        ("canon_keeper.net.server", "SessionServer", "decide"),
        ("canon_keeper.net.projection", None, "project_entity"),
        ("canon_keeper.net.projection", None, "snapshot_since"),
        ("canon_keeper.campaigns", None, "campaign_key"),
    ],
)
def test_the_functions_the_flows_name_still_exist(text, described):
    """Renaming one of these is exactly when the flow section needs editing."""
    import importlib

    module_name, class_name, attribute = described
    if attribute not in text:
        pytest.fail(f"{attribute} is no longer described in ARCHITECTURE.md")

    module = importlib.import_module(module_name)
    owner = getattr(module, class_name) if class_name else module
    assert hasattr(owner, attribute), (
        f"ARCHITECTURE.md describes {attribute}, which no longer exists"
    )


def test_the_flow_section_covers_the_paths_that_matter(text):
    """A missing flow is harder to notice than a wrong one."""
    section = text.split("## The main flows", 1)[1].split("## Decisions", 1)[0]
    for topic in ("Joining", "changes their character", "Autopilot", "Reconnecting"):
        assert topic in section, f"the {topic!r} flow is no longer described"


# ------------------------------------------------------------ the release rule
#
# AGENTS.md says the documentation moves with the version number. Most of that
# is judgement, but one part is mechanical: a version that exists has notes.


def test_the_current_version_has_changelog_notes():
    notes = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = f"## {canon_keeper.__version__}"
    assert heading in notes, (
        f"the version is {canon_keeper.__version__} but CHANGELOG.md has no "
        f"'{heading}' section. Bumping the number is not the release; saying "
        "what changed is."
    )


def test_the_changelog_keeps_a_place_for_the_next_one():
    notes = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## Unreleased" in notes, (
        "the Unreleased heading has gone, so the next change has nowhere to be "
        "written down as it lands"
    )


def test_agents_md_still_names_the_documents_it_governs():
    """It is a rule about four files. Losing one silently is the failure mode."""
    rules = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for document in ("CHANGELOG.md", "README.md", "ARCHITECTURE.md", "RELEASING.md"):
        assert document in rules, f"AGENTS.md no longer mentions {document}"


def test_the_release_procedure_is_still_written_down():
    steps = (ROOT / "RELEASING.md").read_text(encoding="utf-8")
    assert "ARCHITECTURE.md" in steps, (
        "RELEASING.md no longer asks anyone to check the architecture doc, "
        "which is the step this project keeps needing to be reminded of"
    )
