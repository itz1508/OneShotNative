#!/usr/bin/env node
/**
 * Render every Mermaid source file in diagrams/ to SVG (and optionally PNG).
 *
 *   npm run diagrams:render          -> SVG
 *   npm run diagrams:render:png      -> SVG + PNG
 *
 * Output lands in diagrams/dist/ (git-ignored). An empty source folder is a
 * normal welcome state, not a failure.
 */
import { readdirSync, mkdirSync, existsSync } from "node:fs";
import { join, basename, resolve, dirname } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SRC_DIR = join(repoRoot, "diagrams");
const OUT_DIR = join(repoRoot, "diagrams", "dist");
const CONFIG = join(repoRoot, "diagrams", "puppeteer.json");
const MMDC = join(repoRoot, "node_modules", ".bin", "mmdc");

const wantPng = process.argv.includes("--png");

if (!existsSync(MMDC)) {
  console.error("mmdc not found — run `npm install` first.");
  process.exit(1);
}

const sources = existsSync(SRC_DIR)
  ? readdirSync(SRC_DIR).filter((f) => f.endsWith(".mmd"))
  : [];

if (sources.length === 0) {
  console.log("");
  console.log("  Nothing to render — diagrams/ has no .mmd source yet.");
  console.log("");
  console.log("  The exported images live in diagrams/source/. Reconstruct them into");
  console.log("  Mermaid with the local vision tool:");
  console.log("");
  console.log("    python tools/vision/recover_diagrams.py");
  console.log("");
  console.log("  …or add a file such as diagrams/architecture.mmd by hand (e.g. starting");
  console.log("  with `flowchart LR`), then run `npm run diagrams:render` again.");
  console.log("");
  process.exit(0);
}

mkdirSync(OUT_DIR, { recursive: true });

let rendered = 0;
let failed = 0;

for (const file of sources) {
  const name = basename(file, ".mmd");
  const input = join(SRC_DIR, file);
  const formats = wantPng ? ["svg", "png"] : ["svg"];

  for (const format of formats) {
    const output = join(OUT_DIR, `${name}.${format}`);
    const args = ["-i", input, "-o", output];
    if (existsSync(CONFIG)) args.push("-p", CONFIG);

    const result = spawnSync(MMDC, args, { cwd: repoRoot, encoding: "utf8" });

    if (result.status === 0 && existsSync(output)) {
      rendered += 1;
      console.log(`  ✓ ${file} → diagrams/dist/${name}.${format}`);
    } else {
      failed += 1;
      console.error(`  ✗ ${file} (${format})`);
      const detail = (result.stderr || result.stdout || "").trim();
      if (detail) console.error("    " + detail.split("\n").slice(-3).join("\n    "));
    }
  }
}

console.log("");
console.log(`  ${rendered} rendered, ${failed} failed.`);
console.log("");
process.exit(failed ? 1 : 0);
