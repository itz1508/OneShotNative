"""Real Chromium -> the published UI -> live System -> persisted result.

The clicking end of the release gate: a real browser drives the real UI, which
calls the real System over HTTP, which returns a document that is validated and
persisted. The UI is pointed at the System under test by setting the client's
runtime System URL (the same override the header exposes) before the app loads.

Run (the browser is headed, so a headless shell or CI needs an X server)::

    xvfb-run -a uv run --locked --extra test --python 3.11 python -m tests.e2e_browser

Exits 2 (not 1) when the browser could not be launched at all, so an unrunnable
gate is never mistaken for a passing one.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from systems.schemas import OneShotOutput
from tests.e2e_support import (
    BrowserUnavailable,
    LONG_TEXT,
    Reporter,
    browser_launch_hint,
    launch_chromium,
    live_client,
    live_system,
    playwright_available,
)


def main() -> int:
    report = Reporter("e2e_browser")

    if not playwright_available():
        print(
            "BLOCKED — Playwright (Python) is not installed, so the real-browser "
            "gate cannot run.\n"
            "  uv sync --locked --extra test\n"
            "  uv run --locked --python 3.11 python -m playwright install chromium",
            flush=True,
        )
        return 2

    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory() as artifacts:
        os.environ["ONESHOT_OUTPUT_DIR"] = artifacts
        os.environ.pop("ONESHOT_MODEL_PROVIDER", None)

        with live_system() as system_base, live_client() as ui_base:
            with sync_playwright() as playwright:
                try:
                    browser = launch_chromium(playwright)
                except BrowserUnavailable as exc:
                    print(browser_launch_hint("tests.e2e_browser", exc), flush=True)
                    return 2
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                # Point the UI at the System under test before any app code runs.
                context.add_init_script(
                    "window.localStorage.setItem('oneshot.systemBaseUrl', "
                    f"{json.dumps(system_base)});"
                )
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda exc: errors.append(str(exc)))

                try:
                    page.goto(ui_base, wait_until="domcontentloaded", timeout=30_000)
                    page.locator("h1", has_text="OneShot").first.wait_for(timeout=15_000)
                    report.check("the UI mounts in real Chromium", page.locator("#intent").is_visible())

                    # Declare the objective and hand over one source.
                    page.fill("#intent", "summarise the session expiry rules")
                    page.uncheck("input[type=checkbox]")  # run straight through
                    page.fill("textarea[id^='text-']", LONG_TEXT)
                    page.get_by_role("button", name="Run OneShot").click()

                    # The System's own delivery signal.
                    page.get_by_text("Run delivered").first.wait_for(timeout=45_000)
                    report.check(
                        "the run is delivered back to the browser",
                        page.get_by_text("Run delivered").first.is_visible(),
                    )
                    report.check(
                        "the workflow reaches Editor Review",
                        page.locator("header").get_by_text("Editor Review").first.is_visible(),
                    )

                    # The persisted result the System produced for this run.
                    artifact = next(iter(Path(artifacts).glob("*.json")), None)
                    report.check(
                        "the System persisted output.json for the browser run",
                        artifact is not None,
                        str(artifact),
                    )
                    if artifact is not None:
                        document = OneShotOutput.model_validate_json(artifact.read_text())
                        report.check(
                            "the persisted result is Sufficient",
                            document.quality.outcome.value == "Sufficient",
                        )

                    report.check(
                        "no uncaught page errors",
                        not errors,
                        " | ".join(errors)[:300],
                    )
                finally:
                    browser.close()

    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
