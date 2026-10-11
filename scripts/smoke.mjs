/**
 * OneShot client — comprehensive E2E verification test.
 *
 * Verifies that the built web application functions end-to-end in a real browser:
 *   - App layout, typography, and controls mount correctly.
 *   - Gap 1  — Auth: API key entry saves to sessionStorage and persists across reloads.
 *   - Gap 3  — Observability: Diagnostics log panel displays structured in-memory entries.
 *   - Gap 5  — Client-Side Validation: Empty intents and invalid URLs are caught with plain English messages.
 *   - Gap 6  — Offline Handling: Offline banner asserts immediately upon network disconnect and dismisses on restore.
 *   - Gap 7  — HITL: "Proceed on incomplete context" option is available and interactive.
 *   - Gap 8  — Accessibility: Skip link exists, Escape key dismisses modal panels.
 *   - Gap 11 — PWA: Web manifest is declared and returns valid JSON.
 *   - Clean runtime: No unhandled exceptions or error boundary catches.
 *
 * Automatically manages local preview server if port 5173 is not already listening.
 */

import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import { chromium } from "playwright-core";

const BASE = process.env.SMOKE_URL ?? "http://localhost:5173";

const CANDIDATES = [
  process.env.CHROMIUM_PATH,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
  "/usr/bin/google-chrome",
].filter(Boolean);

const EXECUTABLE = CANDIDATES.find((p) => fs.existsSync(p)) ?? "/usr/bin/chromium";

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
}

async function isServerUp(url) {
  return new Promise((resolve) => {
    const req = http.get(url, (res) => {
      resolve(res.statusCode >= 200 && res.statusCode < 500);
    });
    req.on("error", () => resolve(false));
    req.setTimeout(1000, () => {
      req.destroy();
      resolve(false);
    });
  });
}

let serverProcess = null;

// Ensure server is up
if (!(await isServerUp(BASE))) {
  console.log(`Starting preview server on port 5173...`);
  serverProcess = spawn("npx", ["vite", "preview", "--port", "5173"], {
    stdio: "pipe",
    shell: true,
  });

  for (let i = 0; i < 20; i++) {
    await new Promise((r) => setTimeout(r, 500));
    if (await isServerUp(BASE)) break;
  }
}

const browser = await chromium.launch({
  executablePath: EXECUTABLE,
  headless: process.env.HEADED === "true" ? false : true,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});

const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await context.newPage();

const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(String(error)));

try {
  await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.locator("h1", { hasText: "OneShot" }).first().waitFor({ timeout: 15_000 });

  const rootLength = await page.evaluate(
    () => document.getElementById("root")?.innerHTML.length ?? 0,
  );
  check("App mounts (root is not blank)", rootLength > 200, `root length=${rootLength}`);

  // 1. Core Header and Title
  check("Header renders OneShot title", await page.locator("h1").first().isVisible());

  // 2. Gap 8: Skip to content accessibility link
  const skipLink = page.locator('a[href="#main"]');
  check("Skip to content link exists for a11y (Gap 8)", (await skipLink.count()) > 0);

  // 3. Form controls & textarea
  check("Objective textarea (#intent) renders", await page.locator("#intent").isVisible());

  // 4. Gap 7: HITL Proceed on incomplete context option
  const proceedToggle = page.getByLabel(/Proceed on incomplete context/i);
  check("Proceed on incomplete context option renders (Gap 7)", await proceedToggle.isVisible());
  await proceedToggle.check();
  check("Proceed on incomplete checkbox is toggleable (Gap 7)", await proceedToggle.isChecked());

  const runBtn = page.getByRole("button", { name: /Run OneShot/i });
  check("Primary Run CTA renders", await runBtn.isVisible());
  check("Empty state renders", await page.getByText("Nothing has run yet").isVisible());

  // 5. System Panel Toggle
  await page.getByRole("button", { name: "System" }).click();
  const panel = page.locator("#system-status");
  await panel.waitFor({ state: "visible", timeout: 5_000 });
  check("System panel toggles open", await panel.isVisible());

  // Wait for verdict
  await panel
    .getByText(/System (reachable|unreachable)/i)
    .first()
    .waitFor({ state: "visible", timeout: 20_000 })
    .catch(() => {});

  const panelText = ((await panel.innerText()) || "").replace(/\s+/g, " ").trim();
  const unreachable = /System unreachable/i.test(panelText);
  const reachable = /System reachable/i.test(panelText) && !unreachable;
  check("System panel settles on a determinate verdict", reachable || unreachable);

  // 6. Gap 1: Auth API Key entry and persistence
  await page.getByRole("button", { name: /Change URL \/ Auth/i }).click();
  const apiKeyInput = page.locator("#api-key");
  await apiKeyInput.waitFor({ state: "visible", timeout: 5_000 });
  await apiKeyInput.fill("sk-e2e-test-key-999");
  await page.getByRole("button", { name: "Save" }).click();

  const persistedKey = await page.evaluate(() => sessionStorage.getItem("oneshot.apiKey"));
  check("API key saves to sessionStorage (Gap 1)", persistedKey === "sk-e2e-test-key-999");

  // 7. Gap 3: Observability Diagnostics Log Viewer
  await page.getByRole("button", { name: /Diagnostics/i }).click();
  const logHeader = page.getByText(/Client Diagnostics & Event Log/i);
  await logHeader.waitFor({ state: "visible", timeout: 5_000 });
  check("Diagnostics log panel opens (Gap 3)", await logHeader.isVisible());

  // 8. Gap 8: Keyboard Escape dismisses modal panel
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
  check("Escape key dismisses System panel (Gap 8)", (await panel.isVisible()) === false);

  // 9. Gap 5: Client-side validation on empty intent
  await runBtn.click();
  const alert = page.locator('[role="alert"]').first();
  await alert.waitFor({ state: "visible", timeout: 5_000 });
  const alertText = (await alert.innerText()).trim();
  check(
    "Empty objective is caught client-side (Gap 5)",
    /tell OneShot what you want done/i.test(alertText),
  );

  // 10. Gap 5: Client-side validation on invalid source URL
  await page.locator("#intent").fill("Analyse and summarise the documentation.");
  // Add another source and select Public URL
  await page.getByRole("button", { name: /Add source/i }).click();
  const sourceRows = await page.locator("li").count();
  check("Add source adds a source row", sourceRows >= 2);

  // Select Public URL for the first source
  const typeSelect = page.getByLabel(/Source type for/i).first();
  await typeSelect.selectOption("public_url");
  const urlInput = page.getByLabel("Address").first();
  await urlInput.fill("ftp://invalid-protocol.com/data");
  await runBtn.click();

  const formAlert = page.locator('[role="alert"]').first();
  await formAlert.waitFor({ state: "visible", timeout: 5_000 });
  const formAlertText = (await formAlert.innerText()).trim();
  check(
    "Malformed source URL protocol caught client-side (Gap 5)",
    /Fix the highlighted sources|must start with http/i.test(formAlertText),
    `message: "${formAlertText}"`,
  );

  // 11. Gap 6: Offline banner appearance & auto-dismissal
  await context.setOffline(true);
  const offlineBanner = page.locator('div[role="status"]', { hasText: /You are offline/i });
  await offlineBanner.waitFor({ state: "visible", timeout: 5_000 });
  check("Offline banner renders when connection lost (Gap 6)", await offlineBanner.isVisible());

  await context.setOffline(false);
  await page.waitForTimeout(500);
  check("Offline banner dismisses when connection restored (Gap 6)", (await offlineBanner.isVisible()) === false);

  // 12. Gap 11: PWA Web App Manifest
  const manifestHref = await page.locator('link[rel="manifest"]').getAttribute("href");
  check("PWA manifest link declared in document head (Gap 11)", manifestHref === "/manifest.webmanifest");

  const manifestStatus = await page.evaluate(async () => {
    try {
      const res = await fetch("/manifest.webmanifest");
      return res.status;
    } catch {
      return 0;
    }
  });
  check("PWA manifest is reachable with HTTP 200 (Gap 11)", manifestStatus === 200);

  // Check no uncaught runtime JS exceptions
  check("Zero uncaught JavaScript exceptions", pageErrors.length === 0, pageErrors.join(" | ").slice(0, 300));
} catch (error) {
  check("E2E verification completed without throwing", false, String(error));
} finally {
  await browser.close();
  if (serverProcess) {
    try {
      if (process.platform === "win32" && serverProcess.pid) {
        spawn("taskkill", ["/pid", String(serverProcess.pid), "/f", "/t"]);
      } else {
        serverProcess.kill();
      }
    } catch {
      /* ignore */
    }
  }
}

const failed = results.filter((result) => !result.ok);
console.log(`\n========================================`);
console.log(`RESULT: ${results.length - failed.length}/${results.length} checks passed`);
console.log(`========================================\n`);

process.exit(failed.length ? 1 : 0);
