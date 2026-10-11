# tools/vision — recover diagram source from exported images

Turns the exported Mermaid PNGs in `diagrams/source/` back into `.mmd` text, using
a **self-hosted llama.cpp multimodal server** through the Strands Agents SDK.

## First, what this is *not*

- **Not part of the OneShot System.** `workspace/OneShot/systems/` never imports
  this, and this tool never imports the System. It runs *against* the repository.
- **Not a change to the pipeline.** OneShot's v1 contract explicitly excludes
  image/OCR interpretation (`InspectionMode.VISUAL` is "in the taxonomy but never
  selected in v1" — `systems/contracts.py`). Making vision a real pipeline stage
  is a specification change; that plan lives in
  [`docs/prd/vision-capability-plan.md`](../../docs/prd/vision-capability-plan.md).
- **Not byte-exact recovery.** An exported PNG is a *render* of its source, and
  rendering does not invert. The `.mmd` produced here is a faithful
  **reconstruction** of what the model can see. Review it; do not treat it as the
  original file.

## Why llama.cpp, and why local

The model runs on your machine, so there is **no API key, no Supabase secret, and
no third-party egress** — the diagram images never leave the host. That keeps this
entirely clear of the project's unresolved credential question (see the PRD's
Integrations note (a)).

## 1. Start a multimodal llama.cpp server

You need three things: a `llama-server` binary, a **vision-capable** GGUF, and its
**`mmproj`** projector file. The `--mmproj` flag is what gives the model eyes —
without it, the image is silently dropped.

Prebuilt binaries are published per release:

```
https://github.com/ggml-org/llama.cpp/releases   →  llama-<build>-bin-ubuntu-x64.tar.gz
```

Then pick a small vision model. Qwen2.5-VL works well for diagram OCR:

```sh
# model + projector (any source works — these are the ggml-org / community GGUFs)
llama-server \
  -m   models/Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf \
  --mmproj models/mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf \
  --host 127.0.0.1 --port 8080 -c 8192
```

Leave it running. `llama-server` exposes an OpenAI-compatible API on `/v1`, which
is exactly what Strands' `LlamaCppModel` talks to (it is HTTP-only — no native
`llama-cpp-python` build is required).

## 2. Install the tool's dependencies

Kept separate from the System on purpose:

```sh
pip install -r tools/vision/requirements.txt
```

## 3. Recover the diagrams

```sh
# confirm the server is up before a long run
python tools/vision/recover_diagrams.py --check

# recover every PNG in diagrams/source/
python tools/vision/recover_diagrams.py

# one file, overwriting an existing result
python tools/vision/recover_diagrams.py --only boundary.png --force
```

Each image becomes `diagrams/<name>.mmd`. A run also writes
`diagrams/recovery-report.json` recording what was recovered, skipped or failed.

Rendering those `.mmd` back to SVG/PNG is the existing pipeline:

```sh
npm run diagrams:render        # -> diagrams/dist/<name>.svg
npm run diagrams:render:png
```

## Configuration

Read from the host environment (the same convention the System uses), all
overridable by CLI flags:

| Variable | Default | Meaning |
|---|---|---|
| `ONESHOT_VISION_BASE_URL` | `http://127.0.0.1:8080` | llama.cpp server URL |
| `ONESHOT_VISION_MODEL` | `default` | model id reported by the server |
| `ONESHOT_VISION_INPUT_DIR` | `diagrams/source` | where the PNGs are |
| `ONESHOT_VISION_OUTPUT_DIR` | `diagrams` | where the `.mmd` files go |
| `ONESHOT_VISION_TIMEOUT` | `300` | per-image request timeout (seconds) |
| `ONESHOT_VISION_MAX_TOKENS` | `2048` | raise for very large diagrams |

## Failure behaviour

- **Server unreachable** → exits `2` with the exact `llama-server` command to run.
- **A model returns prose instead of Mermaid** → that file is *skipped*, not
  written. Nothing is invented to fill the gap; the run exits `1` and lists what
  failed.
- **Existing `.mmd`** → left untouched unless you pass `--force`.

## Layout

```
tools/vision/
  recover_diagrams.py     ← the tool
  requirements.txt        ← strands-agents + httpx
  README.md               ← this file
diagrams/
  source/*.png            ← exported diagram images (the input)
  *.mmd                   ← recovered Mermaid source (the output)
  dist/                   ← rendered SVG/PNG (git-ignored)
```
