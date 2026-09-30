"""Publishing a session through Tailscale Funnel.

Funnel asks Tailscale to expose one local port to the public internet at
``https://your-machine.tailXXXX.ts.net``, with a real certificate. For us it
solves three problems in one command:

* **NAT** -- the tunnel dials outward, so routers and carrier-grade NAT stop
  mattering and nobody forwards a port.
* **TLS** -- the certificate is provisioned to your machine by Tailscale, so we
  ship no SSL code and renew nothing. Players connect over ``wss://``.
* **The address** -- a stable hostname, rather than a home IP that changes.

And the players install nothing: they need Tailscale no more than they need it
to visit a website.

We drive the ``tailscale`` command rather than reimplementing any of it, which
makes two behaviours important to know about:

1. **It blocks when Funnel is not enabled on the tailnet.** It prints the URL to
   enable it and then sits there polling until you do. So we check the node's
   capabilities first and never run the blocking command in that state.
2. **It can wait on stdin.** Every invocation gets ``stdin`` closed, so a prompt
   we did not anticipate fails fast instead of hanging forever.

Beyond that the installed version decides the exact flags, so parsing is
defensive: take the URL if it is there, fall back to ``status --json``, and when
something goes wrong hand Tailscale's own wording to the user -- it names the
setting to change and links to it, which ours would not.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("canonkeeper.net.funnel")

#: Where the CLI lives when it is not on PATH, which is usual on Windows and
#: macOS because the installer does not always add it.
_KNOWN_PATHS = (
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/usr/local/bin/tailscale",
    "/opt/homebrew/bin/tailscale",
    "/usr/bin/tailscale",
)

_URL = re.compile(r"https://[A-Za-z0-9._-]+\.ts\.net\b")

#: Node capability granted when a tailnet has Funnel switched on.
FUNNEL_CAPABILITY = "cap/funnel"

ENABLE_URL = "https://login.tailscale.com/f/funnel?node={node_id}"

#: Short: status queries answer immediately or something is wrong.
QUERY_TIMEOUT = 10
#: Long: the first Funnel on a tailnet provisions a TLS certificate, which is
#: slow enough that a short timeout would report failure on a working setup.
START_TIMEOUT = 90


@dataclass(slots=True)
class Result:
    ok: bool
    url: str = ""
    message: str = ""
    #: Set when the tailnet needs Funnel switched on; the UI offers the link.
    enable_url: str = ""
    #: Whether the command that publishes was actually run. A failure before it
    #: ran -- no Tailscale, or Funnel not switched on for the tailnet -- means
    #: nothing is on the internet. A failure after it ran means something may
    #: well be, and the caller must keep treating the session as published.
    attempted: bool = False

    @property
    def websocket_url(self) -> str:
        """The address a player types: Funnel serves HTTPS, we speak WebSocket."""
        return to_websocket_url(self.url)


@dataclass(slots=True)
class NodeInfo:
    dns_name: str = ""
    node_id: str = ""
    funnel_enabled: bool = False
    #: False when the CLI told us nothing useful, in which case we must not
    #: conclude Funnel is unavailable -- older versions report no capabilities.
    capabilities_known: bool = False


def to_websocket_url(url: str) -> str:
    if not url:
        return ""
    return url.replace("https://", "wss://", 1).replace("http://", "ws://", 1).rstrip("/")


def executable() -> str | None:
    """The tailscale CLI, or None if this machine has not got it."""
    found = shutil.which("tailscale")
    if found:
        return found
    for candidate in _KNOWN_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


def is_installed() -> bool:
    return executable() is not None


def _flags() -> int:
    # Stops a console window flashing up on Windows.
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _run(args: list[str], timeout: int = QUERY_TIMEOUT) -> tuple[int, str]:
    """Run a short command and return ``(returncode, combined output)``."""
    binary = executable()
    if binary is None:
        return 127, "The tailscale command was not found."

    try:
        completed = subprocess.run(  # noqa: S603 - a fixed binary and our own args
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
            creationflags=_flags(),
        )
    except subprocess.TimeoutExpired:
        return 124, f"tailscale {' '.join(args)} did not finish in {timeout}s."
    except OSError as exc:
        return 126, f"Could not run tailscale: {exc}"

    return completed.returncode, f"{completed.stdout}\n{completed.stderr}".strip()


def _run_capturing(args: list[str], timeout: int) -> tuple[int | None, str]:
    """Run a command that may never exit, keeping whatever it printed.

    Returns ``(returncode, output)`` with a returncode of None if we had to kill
    it. The partial output is the point: ``tailscale funnel`` says what is wrong
    within a second and only *then* blocks.
    """
    binary = executable()
    if binary is None:
        return 127, "The tailscale command was not found."

    try:
        process = subprocess.Popen(  # noqa: S603 - a fixed binary and our own args
            [binary, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            stdin=subprocess.DEVNULL,
            creationflags=_flags(),
        )
    except OSError as exc:
        return 126, f"Could not run tailscale: {exc}"

    try:
        output, _ = process.communicate(timeout=timeout)
        return process.returncode, (output or "").strip()
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate()
        return None, (output or "").strip()


def node_info() -> NodeInfo:
    """What Tailscale knows about this machine."""
    code, output = _run(["status", "--json"])
    if code != 0:
        return NodeInfo()
    try:
        this = json.loads(output).get("Self") or {}
    except (json.JSONDecodeError, AttributeError):
        return NodeInfo()

    capabilities = this.get("CapMap") or {}
    return NodeInfo(
        dns_name=str(this.get("DNSName", "")).strip().rstrip("."),
        node_id=str(this.get("ID", "")),
        funnel_enabled=any(FUNNEL_CAPABILITY in key for key in capabilities),
        capabilities_known=bool(capabilities),
    )


def machine_url() -> str:
    """This machine's public Funnel hostname."""
    name = node_info().dns_name
    return f"https://{name}" if name else ""


def _not_enabled_result(info: NodeInfo) -> Result:
    enable_url = ENABLE_URL.format(node_id=info.node_id) if info.node_id else ""
    message = (
        "Funnel is not switched on for your tailnet yet.\n\n"
        "Open the link below, turn Funnel on for this machine, then try again. "
        "It is a one-off; your players still need nothing."
    )
    if enable_url:
        message += f"\n\n{enable_url}"
    return Result(False, message=message, enable_url=enable_url)


def start(port: int) -> Result:
    """Publish ``port`` to the internet. Returns the public address."""
    if not is_installed():
        return Result(
            False,
            message=(
                "Tailscale is not installed on this machine.\n\n"
                "Install it from https://tailscale.com/download, sign in, and try "
                "again. Your players do not need it -- only you."
            ),
        )

    info = node_info()
    # The blocking case, headed off before we run anything: without the
    # capability the CLI prints instructions and then waits, forever, for the
    # tailnet setting to change.
    if info.capabilities_known and not info.funnel_enabled:
        log.info("funnel is not enabled for node %s", info.node_id)
        return _not_enabled_result(info)

    code, output = _run_capturing(["funnel", "--bg", str(port)], START_TIMEOUT)

    match = _URL.search(output)
    if match:
        url = match.group(0)
        log.info("funnel serving port %s at %s", port, url)
        return Result(True, url=url, message=output, attempted=True)

    # No address: work out whether it is the not-enabled case after all (an
    # older CLI that reports no capabilities would land here) before giving up.
    if "not enabled" in output.lower():
        return _not_enabled_result(info)

    if code is None:
        return Result(
            False,
            message=(
                f"Tailscale did not answer within {START_TIMEOUT}s.\n\n"
                "It usually does this when it is waiting for something. What it "
                "printed:\n\n" + (output or "(nothing)")
            ),
            # It ran. Whether it got as far as publishing, nobody here knows --
            # which is exactly the case to assume it did.
            attempted=True,
        )

    if code != 0:
        log.warning("tailscale funnel failed (%s): %s", code, output)
        return Result(
            False,
            message=output or "Tailscale refused to start Funnel.",
            attempted=True,
        )

    fallback = machine_url()
    if fallback:
        return Result(True, url=fallback, message=output, attempted=True)
    return Result(
        False,
        message="Funnel started, but Tailscale did not report a public address.\n\n"
        + output,
        attempted=True,
    )


def stop(port: int | None = None) -> Result:
    """Take the session off the public internet again."""
    if not is_installed():
        return Result(True)  # nothing of ours is running

    code, output = _run(["funnel", "--https=443", "off"])
    if code != 0:
        # Older releases spell it differently; reset clears whatever is set.
        code, output = _run(["funnel", "reset"])

    if code != 0:
        log.warning("could not stop funnel: %s", output)
        return Result(False, message=output)
    log.info("funnel stopped")
    return Result(True, message=output)


def is_running() -> bool:
    code, output = _run(["funnel", "status"])
    if code != 0:
        return False
    lowered = output.lower()
    return "https://" in lowered and "no serve config" not in lowered


# --------------------------------------------------------- what we left behind
#
# `funnel --bg` is configuration in the tailscaled daemon, not a child process.
# It outlives the app, a crash, and a reboot, and the app cannot tell on its
# next run that it is there -- so it is written down.
#
# Beside the campaigns rather than inside one, because a funnel belongs to the
# machine. A session published from one campaign and a different campaign opened
# the next evening is the case a per-campaign note would miss entirely.


def _marker() -> Path:
    from canon_keeper import config

    return config.data_dir() / "published.json"


def remember(port: int) -> None:
    """Note that we asked for a funnel, before the command that does it.

    Before rather than after: the command takes effect in the daemon whether or
    not we are still here to read its answer.
    """
    try:
        _marker().write_text(
            json.dumps({"port": int(port)}), encoding="utf-8"
        )
    except OSError:
        # A note we could not write is not a reason to refuse to publish. The
        # cost is a leftover funnel we cannot attribute, which is what the
        # situation was before this existed.
        log.warning("could not write the published marker", exc_info=True)


def forget() -> None:
    """Note that there is nothing of ours published any more."""
    try:
        _marker().unlink(missing_ok=True)
    except OSError:
        log.warning("could not clear the published marker", exc_info=True)


def left_over() -> int | None:
    """The port a previous run published and never took down, if there is one.

    The marker outliving the funnel is the harmless direction: taking down a
    funnel that has already gone is a command that succeeds and changes
    nothing. The other direction is the one this exists to prevent.
    """
    try:
        raw = _marker().read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        port = json.loads(raw).get("port")
    except (ValueError, AttributeError):
        return None
    return int(port) if isinstance(port, int) else None
