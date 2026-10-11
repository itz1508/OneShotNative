# OneShot

System-owned responsibility workflow for OneShot. **Greenfield build** built
against the accepted responsibility specification - see *Provenance* below for
what that means.

## Provenance (read this first)

`Architecture.md` assumes an existing Python candidate (a ZIP, a `.diff`, GIF
diagrams, `Addon-AutoGen.md`) to *inspect and reconcile*. **None of those
artifacts are present in this workspace, and no `OneShot` code existed here
before this folder.** So this is a greenfield implementation of the accepted
decisions - not a reconciliation of a physical candidate. Claims are stated as
Observed / Accepted / Candidate / Proposed / Unknown accordingly, and nothing is
reported as "inspected from the ZIP" because that ZIP is not here.

## Runtime (verified by probe in this workspace)

| Tool | Found | Spec target | Note |
| --- | --- | --- | --- |
| `uv` | 0.9.26 | yes | present |
| `python` | 3.14.2 (system) | **3.11** | pinned to 3.11 via `.python-version`; `uv` installs it on demand |
| `node` | 25.6.0 | 22+ | ok |
| `npm` | 11.8.0 | `npm ci` | ok |
| `docker` | **absent** | `compose.yaml` | the Docker/Compose gate **cannot run in this sandbox** - report as NOT VERIFIED, not skipped silently |

The platform serves the UI through Vite (`npm run dev`, port 5173). The Python
system runs as a separate process and is reached through a dev proxy.

## Layout

```
workspace/OneShot/
├── pyproject.toml          # uv-managed, Python 3.11, stdlib-first
├── .python-version         # 3.11
├── systems/                # System-owned responsibilities
│   └── contracts.py        # domain: operations, outcomes, job admission
└── tests/                  # diagnostic suite
    └── test_contracts.py
```

Further layers (store, operations/worker, capabilities, HTTP API) are added as
they are built - not scaffolded ahead of a demonstrated need.

## Status

| Layer | State |
| --- | --- |
| Domain contracts | **Implemented** - source-operation classification, deletion-authority guard, tri-state outcomes, immutable `SourceRef`, `JobCreate` admission + drift checks |
| Persistence / store | Proposed |
| Operations / worker | Proposed |
| Capabilities (scanner, advisor, generator, researcher) | Proposed |
| HTTP API | Proposed |
| Frontend (in the repo-root Vite app) | Proposed |

## Run

```sh
cd workspace/OneShot
uv run --extra test pytest      # diagnostic suite
```