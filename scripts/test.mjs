/**
 * OneShot client — self-contained end-to-end test (`npm test`).
 *
 * Unlike `npm run test:smoke` (which expects a dev server to already be up),
 * this builds the app, serves the *production* bundle on a free port, and drives
 * it in a real browser. That makes it runnable on a clean checkout or in CI with
 * no external process to coordinate.
 *
 * The System is a separate Python service; if it is not running alongside, the
 * UI's System panel reports "unreachable", which the shared checks accept as a
 * valid state.
 */

import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { runSmoke } from "./smoke.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
// Bind explicitly to IPv4: Vite's default `localhost` is IPv6-only on some hosts,
// which Node's fetch (IPv4-first) cannot reach.
const HOST = "127.0.0.1";
const PORT = Number(process.env.TEST_PORT ?? 4173);
const BASE = `http://${HOST}:${PORT}`;

function run(command, args, options = {}) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, args, {
      cwd: ROOT,
      stdio: "inherit",
      shell: process.platform === "win32",
      ...options,
    });
    child.on("error", reject);
    child.on("exit", (code) =>
      code === 0 ? resolvePromise() : reject(new Error(`${command} ${args.join(" ")} exited ${code}`)),
    );
  });
}

async function waitForServer(url, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      // Any HTTP answer — including a 404 — means the server is listening.
      await fetch(url);
      return true;
    } catch {
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));
    }
  }
  throw new Error(`no server answered at ${url} within ${timeoutMs}ms`);
}

let server;

try {
  console.log("› building the production bundle…");
  await run("npx", ["vite", "build"]);

  console.log(`› serving it on ${BASE}…`);
  server = spawn(
    "npx",
    ["vite", "preview", "--host", HOST, "--port", String(PORT), "--strictPort"],
    {
    cwd: ROOT,
    stdio: "inherit",
  });
  await waitForServer(BASE);

  const { results, ok } = await runSmoke({ baseUrl: BASE, headless: true });
  console.log(
    `\n${results.filter((result) => result.ok).length}/${results.length} checks passed`,
  );
  process.exitCode = ok ? 0 : 1;
} catch (error) {
  console.error(`\ntest run failed: ${error}`);
  process.exitCode = 1;
} finally {
  server?.kill("SIGTERM");
}
