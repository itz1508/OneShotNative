# OneShot diagrams — source of truth

This folder is where the OneShot architecture diagrams live **as text** (`.mmd`),
and where they are rendered from. Mermaid source is diffable, reviewable and
version-controlled — unlike the exported images.

## Layout

```
diagrams/
  source/*.png      ← exported diagram images (inputs — the nine from the docs volume)
  *.mmd             ← Mermaid source (the thing you edit and review)
  puppeteer.json    ← headless Chromium config used by the renderer
  dist/             ← rendered output (git-ignored)
```

## Recovering source from the exported images

The nine PNGs in `source/` were exported from Mermaid, and their original source
is not in this repository. They can, however, be **reconstructed** into `.mmd` by a
local vision model — see [`tools/vision/`](../tools/vision/README.md):

```sh
python tools/vision/recover_diagrams.py
```

A reconstruction is not the original file: rendering is one-directional, so the
model produces a faithful *reading* of the image rather than the exact source.
Review what it writes before relying on it, and check the run's
`recovery-report.json`.

Recovering these diagrams matters because the architecture diagrams are meant to
be reconciled against the implementation, and until they exist as text that
reconciliation cannot be done.

## Rendering

```sh
npm run diagrams:render        # every *.mmd → diagrams/dist/<name>.svg
npm run diagrams:render:png    # also emit .png
```

A `.mmd` file just contains Mermaid, e.g.:

```
flowchart LR
  A[Stage one] --> B[Stage two]
```

`npm run diagrams:render` with no `.mmd` files present is not an error — it prints a
short message and exits 0.

## Environment

`puppeteer.json` points the renderer at the system Chromium
(`/usr/bin/chromium`) with `--no-sandbox`, so no extra browser download is needed.
If the browser lives somewhere else, edit `executablePath` there.

- Node: verified on **v25.6.0** (Mermaid requires ≥ 22.12.0).
- Renderer: `@mermaid-js/mermaid-cli` (dev dependency) → `mmdc`, rendering headless
  via Puppeteer. Verified end-to-end here (SVG and PNG both produced).
