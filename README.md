# OneShotNative

One objective, the sources it applies to, and a process you can watch.

**OneShotNative** is the native repo for **OneShot** — a system-owned responsibility workflow:

- **Frontend (`frontend/`):** React + Vite + Tailwind CSS app. It declares the request, observes the run, and presents what came back. It holds no keys, performs no pipeline work, and never invents a step or result the System did not report.
- **System (`systems/`):** Python 3.11 service (FastAPI + uvicorn, Pydantic, httpx) that owns the pipeline — Scan → Read → Prepare → Quality & Coverage Check → Generate — plus contracts, operations, and HTTP API.

## Layout

```
OneShotNative/
├── frontend/               # React client
│   ├── components/         # EditorReview, ProcessDisplay, ResultPanel, SourcePanel, SystemStatus, ui, icons
│   ├── lib/                # api.ts (typed client), config.ts, schema.ts, useRun.ts, format.ts
│   ├── App.tsx
│   ├── main.tsx
│   └── index.html
├── systems/                # Python System
│   ├── api.py
│   ├── schemas.py
│   ├── operations.py
│   └── ...
├── tests/                  # Python tests
├── tools/                  # Supporting tools
├── diagrams/               # Diagram rendering
├── scripts/                # Build/test scripts
├── docker/                 # Docker configuration
├── public/                 # Static assets
├── package.json
├── vite.config.ts
├── tsconfig.json
├── pyproject.toml
└── uv.lock
```

## Prerequisites

- Node.js 22+ and npm
- Python 3.11 via `uv` (see `.python-version`) — `uv` installs it on demand
- `uv` 0.9.x

## Run

Frontend (Vite, port 5173):

```sh
npm ci
npm run dev
```

System (separate process, reached through dev proxy / `VITE_SYSTEM_BASE_URL`):

```sh
uv run --extra test pytest
```

Configure where the client finds the System (no `.env` files, no secrets in client):

1. Runtime override in the browser (header → System URL, stored in `localStorage`), else
2. Build-time `VITE_SYSTEM_BASE_URL`, else
3. Default `http://127.0.0.1:8000`

No API key is ever held by the client.

## Build

```sh
npm run build
npm run preview
```

## License

MIT — see [LICENSE](LICENSE).