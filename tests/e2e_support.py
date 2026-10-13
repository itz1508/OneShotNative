"""Shared harness for the OneShot end-to-end gates.

These modules are run as scripts (``python -m tests.e2e_http``), so they are **not**
collected by pytest and may start real servers, spawn the dev server, and drive a
real browser. Everything they start is torn down again on the way out.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Iterator

import httpx
import uvicorn

#: The Python project root (where ``uv.lock`` and ``systems/`` live).
PROJECT_DIR = Path(__file__).resolve().parents[1]
#: The Vite app root (the repo root above ``workspace/OneShot``).
REPO_ROOT = Path(__file__).resolve().parents[3]

#: System Chromium — avoids a Playwright browser download, matching the node smoke.
CHROMIUM_PATH = os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium")

#: Enough text to clear the quality gate's minimum-context threshold.
LONG_TEXT = (
    "The session token expires thirty minutes after it is issued. "
    "Refresh tokens last fourteen days and are rotated on every use. "
    "Revocation is immediate once an operator disables the account. "
    "Audit entries are retained for ninety days in the evidence log. "
) * 4

#: A grounded result shaped exactly like the schema the System validates against.
GROUNDED_RESULT = {
    "title": "Session expiry",
    "summary": "Session tokens are short-lived; refresh tokens are long-lived and rotated.",
    "sections": [
        {
            "heading": "Expiry",
            "body": "The session token expires thirty minutes after issue.",
            "citations": [{"source_id": "s1", "locator": "chars 0-120"}],
        }
    ],
}


def free_port() -> int:
    """An unused loopback port, so gates never collide with a running preview."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Reporter:
    """Collects PASS/FAIL lines and yields a process exit code."""

    def __init__(self, title: str) -> None:
        self.title = title
        self.total = 0
        self.failures: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.total += 1
        if not ok:
            self.failures.append(name)
        suffix = f" — {detail}" if detail else ""
        print(f"{'PASS' if ok else 'FAIL'}  {name}{suffix}", flush=True)
        return ok

    def finish(self) -> int:
        passed = self.total - len(self.failures)
        print(f"\n{self.title}: {passed}/{self.total} checks passed", flush=True)
        if self.failures:
            print("NOT VERIFIED — failed: " + "; ".join(self.failures), flush=True)
            return 1
        return 0


def playwright_available() -> bool:
    """True when the optional Playwright Python package is importable."""
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


class BrowserUnavailable(RuntimeError):
    """The real-browser gate could not launch Chromium at all."""


def launch_chromium(playwright):
    """Launch the system Chromium *headed*, or raise :class:`BrowserUnavailable`.

    Raising (rather than returning ``None``) lets a launch failure become the
    documented BLOCKED / exit-2 outcome instead of an uncaught traceback and exit
    1 -- which would be indistinguishable from a genuine failing check.
    """
    try:
        return playwright.chromium.launch(
            executable_path=CHROMIUM_PATH,
            headless=False,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
    except Exception as exc:  # noqa: BLE001 - the cause varies by environment
        raise BrowserUnavailable(str(exc)) from exc


def browser_launch_hint(gate: str, error: BaseException) -> str:
    """A human explanation of a launch failure, with the two usual remedies."""
    # Playwright appends a multi-line ASCII banner to the message; keep only the
    # headline so the actionable instructions are not buried underneath it.
    headline = str(error).splitlines()[0] or type(error).__name__
    return (
        "BLOCKED — could not launch Chromium, so the real-browser gate did not run.\n"
        "  This gate drives a headed browser (as the node smoke does), which needs an\n"
        "  X server. On a headless shell or in CI, run it under xvfb:\n"
        f"      xvfb-run -a uv run --locked --extra test --python 3.11 python -m {gate}\n"
        "  If the browser binary is missing entirely:\n"
        "      uv run --locked --python 3.11 python -m playwright install chromium\n"
        f"  Underlying error: {type(error).__name__}: {headline}"
    )


@contextlib.contextmanager
def live_system(port: int | None = None) -> Iterator[str]:
    """A real uvicorn server for the OneShot System, torn down on exit."""
    from systems.api import app

    chosen = port or free_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=chosen, log_level="warning", access_log=False
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="oneshot-e2e-system", daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{chosen}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if server.started:
            try:
                httpx.get(f"{base}/health", timeout=1.0).raise_for_status()
                break
            except Exception:  # noqa: BLE001 - not up yet
                pass
        time.sleep(0.05)
    else:
        server.should_exit = True
        raise RuntimeError("the OneShot System did not become reachable")

    try:
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=15)


@contextlib.contextmanager
def live_client(port: int | None = None, *, timeout: float = 120.0) -> Iterator[str]:
    """The published UI served by the Vite dev server, torn down on exit."""
    chosen = port or free_port()
    log = tempfile.NamedTemporaryFile("w+", suffix=".vite.log", delete=False)
    env = {**os.environ, "BROWSER": "none"}
    process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [
            "npm",
            "run",
            "dev",
            "--",
            "--host",
            "127.0.0.1",
            "--port",
            str(chosen),
            "--strictPort",
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    # Bind IPv4 explicitly: Vite's default `localhost` is IPv6-only on some hosts,
    # which a client resolving IPv4 first cannot reach.
    base = f"http://127.0.0.1:{chosen}"
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                log.flush()
                raise RuntimeError(
                    "the Vite dev server exited early:\n" + Path(log.name).read_text()
                )
            try:
                httpx.get(base, timeout=1.0)
                break
            except Exception:  # noqa: BLE001 - not up yet
                time.sleep(0.25)
        else:
            raise RuntimeError(f"the Vite dev server did not become ready at {base}")
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()
        with contextlib.suppress(OSError):
            Path(log.name).unlink()
