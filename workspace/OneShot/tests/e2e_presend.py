"""Real Chromium pre-send journey — everything that happens *before* a run.

Asserts the client is honest on the way in: an empty objective and a source with
no content are both refused **in the browser**, nothing is sent, and the System
panel truthfully reports the System it can see.

Run (the browser is headed, so a headless shell or CI needs an X server)::

    xvfb-run -a uv run --locked --extra test --python 3.11 python -m tests.e2e_presend

Exits 2 (not 1) when the browser could not be launched at all.
"""

from __future__ import annotations

import json
import sys

from tests.e2e_support import (
    BrowserUnavailable,
    Reporter,
    browser_launch_hint,
    launch_chromium,
    live_client,
    live_system,
    playwright_available,
)


def main() -> int:
    report = Reporter("e2e_presend")

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

    with live_system() as system_base, live_client() as ui_base:
        with sync_playwright() as playwright:
            try:
                browser = launch_chromium(playwright)
            except BrowserUnavailable as exc:
                print(browser_launch_hint("tests.e2e_presend", exc), flush=True)
                return 2
            context = browser.new_context(viewport={"width": 1280, "height": 900})
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
                report.check("the app starts in its empty state", page.get_by_text("Nothing has run yet").is_visible())

                # 1. An empty objective is refused client-side, in human words.
                page.get_by_role("button", name="Run OneShot").click()
                alert = page.locator("[role=alert]").first
                alert.wait_for(timeout=5_000)
                report.check(
                    "an empty objective is refused before any request is made",
                    "Tell OneShot what you want done" in alert.inner_text(),
                    alert.inner_text().strip(),
                )

                # 2. An objective with a content-less source is refused too.
                page.fill("#intent", "summarise these notes")
                page.get_by_role("button", name="Run OneShot").click()
                page.get_by_text("Fix the highlighted sources").first.wait_for(timeout=5_000)
                report.check(
                    "a source with no content is refused before any request is made",
                    page.get_by_text("Fix the highlighted sources").first.is_visible(),
                )

                # 3. Nothing was sent: still the empty state, no process display.
                report.check(
                    "no run was started by the refused attempts",
                    page.get_by_text("Nothing has run yet").is_visible(),
                )

                # 4. The System panel reports a determinate, true verdict.
                page.get_by_role("button", name="System").click()
                panel = page.locator("#system-status")
                panel.wait_for(timeout=5_000)
                # The health probe and the schema probe settle in order, so read the
                # panel again after each verdict lands. Panel labels are rendered
                # uppercase, so compare case-insensitively.
                panel.get_by_text("System reachable").first.wait_for(timeout=20_000)
                report.check(
                    "the System panel reports the System reachable",
                    "system reachable" in panel.inner_text().lower(),
                )
                panel.get_by_text("contract in sync").first.wait_for(timeout=10_000)
                report.check(
                    "the System's contract matches the client's mirror",
                    "contract in sync" in panel.inner_text().lower(),
                )

                # 5. Sources can be stacked before sending.
                page.get_by_role("button", name="Add source").click()
                report.check("another source can be added before sending", page.locator("li").count() >= 2)

                report.check("no uncaught page errors", not errors, " | ".join(errors)[:300])
            finally:
                browser.close()

    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
