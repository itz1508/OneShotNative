/**
 * OneShot client — smoke test.
 *
 * Boots the published UI in a real browser and asserts the things a user can
 * actually do: the form renders, the nav/CTAs work, client-side validation
 * catches a mistake, and the page is never blank or caught by an error boundary.
 *
 * It drives the *system* Chromium (no browser download) via playwright-core, so
 * the sandbox needs no extra binaries. The dev server must already be running
 * (`npm run dev`, port 5173) — the platform preview provides it.
 *
 * Run:  npm run test:smoke        (wraps this in `xvfb-run`)
 */

import { chromium } from "playwright-core";

const BASE = process.env.SMOKE_URL ?? "http://localhost:5173";
const EXECUTABLE = process.env.CHROMIUM_PATH ?? "/usr/bin/chromium";

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
}

const browser = await chromium.launch({
  executablePath: EXECUTABLE,
  headless: false,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

// Only uncaught JS exceptions count as failures. The System is a separate service,
// so whether its /health answers depends on the environment — the sandbox preview
// starts it alongside the client, a standalone deployment may not have it running.
// Both a reachable and an unreachable panel are valid states, and neither counts
// as a page error here.
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(String(error)));

try {
  await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page
    .locator("h1", { hasText: "OneShot" })
    .first()
    .waitFor({ timeout: 15_000 });

  const rootLength = await page.evaluate(
    () => document.getElementById("root")?.innerHTML.length ?? 0,
  );
  check("app mounts (root is not blank)", rootLength > 200, `root innerHTML length=${rootLength}`);

  check("header renders the OneShot title", await page.locator("h1").first().isVisible());
  check("objective textarea (#intent) renders", await page.locator("#intent").isVisible());

  const runBtn = page.getByRole("button", { name: /Run OneShot/i });
  check("primary Run CTA renders", await runBtn.isVisible());
  check("empty state renders", await page.getByText("Nothing has run yet").isVisible());

  // Nav: the header "System" toggle must open the System panel.
  await page.getByRole("button", { name: "System" }).click();
  const panel = page.locator("#system-status");
  await panel.waitFor({ state: "visible", timeout: 5_000 });
  check("System panel toggles open", await panel.isVisible());

  // Whether the System answers depends on the environment: the sandbox preview
  // starts it alongside the client, a standalone deployment may not have it
  // running. Either verdict is valid — an indeterminate panel is not. The first
  // probe retries a few times before it gives up, so wait for the verdict, then
  // hold a reachable System to the contract it claims.
  await panel
    .getByText(/System (reachable|unreachable)/i)
    .first()
    .waitFor({ state: "visible", timeout: 20_000 })
    .catch(() => {});

  const panelText = ((await panel.innerText()) || "").replace(/\s+/g, " ").trim();
  const unreachable = /System unreachable/i.test(panelText);
  const reachable = /System reachable/i.test(panelText) && !unreachable;

  check(
    "System panel settles on a determinate verdict",
    reachable || unreachable,
    `${reachable ? "reachable" : unreachable ? "unreachable" : "indeterminate"} — ${JSON.stringify(panelText.slice(0, 160))}`,
  );

  if (reachable) {
    // A reachable System is only trustworthy if its live /schema agrees with the
    // mirror this client ships — drift is exactly what this panel exists to
    // surface. The schema probe runs *after* /health, so wait for the contract
    // verdict to land before reading it.
    await panel
      .getByText(/contract in sync|disagree on the contract/i)
      .first()
      .waitFor({ state: "visible", timeout: 10_000 })
      .catch(() => {});

    const settledText = ((await panel.innerText()) || "").replace(/\s+/g, " ").trim();
    check(
      "reachable System reports its contract in sync",
      /contract in sync/i.test(settledText),
      JSON.stringify(settledText.slice(0, 260)),
    );
  }

  // Affordance: an empty objective is caught client-side, with a human message.
  await runBtn.click();
  const alert = page.locator('[role="alert"]').first();
  await alert.waitFor({ state: "visible", timeout: 5_000 });
  const alertText = (await alert.innerText()).trim();
  check(
    "empty objective is caught by client validation",
    /tell OneShot what you want done/i.test(alertText),
    JSON.stringify(alertText),
  );

  // Affordance: adding a source adds a row.
  await page.getByRole("button", { name: /Add source/i }).click();
  const rows = await page.locator("#intent").count();
  const sourceRows = await page.locator("li").count();
  check("Add source adds a source row", sourceRows >= 2, `source rows=${sourceRows}, intent fields=${rows}`);

  check("no uncaught page errors", pageErrors.length === 0, pageErrors.join(" | ").slice(0, 400));
} catch (error) {
  check("smoke run completed without throwing", false, String(error));
} finally {
  await browser.close();
}

const failed = results.filter((result) => !result.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
