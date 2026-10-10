"""The HTTP boundary (ADR-001 D2/D5, plan §12.1).

===========================================  ==========================================
``POST /run``                                one complete ``output.json`` per run
``GET  /runs/{run_id}/events``               the live Process Display (Server-Sent Events)
``POST /runs/{run_id}/command``              pause / resume / review / finalize
``GET  /health``                             service + configuration truth
``GET  /schema``                             Pydantic schema snapshot (client drift detection)
===========================================  ==========================================

The published UI is a client of this service (D5): it can be served from this
platform, the System cannot. Configuration comes from the **host environment
only** — there are no ``.env`` files on this platform, and nothing read here is
reachable from the client bundle.

CORS allows the published ``*.nativelyai.app`` origin, preview URLs and
localhost, plus any extra origins named in ``ONESHOT_CORS_ORIGINS``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from systems.contracts import ContractError
from systems.generator import provider_status
from systems.operations import RunRegistry, admit, apply_command, run
from systems.read import transcriber_status
from systems.scan import V1_INSPECTION_MODES
from systems.schemas import (
    SCHEMA_VERSION,
    OneShotOutput,
    RunRequest,
    WorkflowCommand,
    WorkflowState,
    schema_snapshot,
)

logger = logging.getLogger("oneshot.api")

registry = RunRegistry()

app = FastAPI(
    title="OneShot System",
    version=SCHEMA_VERSION,
    summary=(
        "System-owned content pipeline: Scan → Read → Prepare → Quality & "
        "Coverage Check → (conditional) Generate → output.json."
    ),
    description=(
        "No stage is owned by an agent: the System executes the workflow end to "
        "end. Generation runs only when a supported task exists and the prepared "
        "context is sufficient. The process trace records observed activity only."
    ),
)

#: The published origin, preview URLs and localhost. ``*.nativelyai.app`` is what
#: the platform publishes; localhost covers local and preview development.
_ORIGIN_REGEX = r"^https?://([a-z0-9-]+\.)*(nativelyai\.app|localhost|127\.0\.0\.1)(:\d+)?$"


def _extra_origins() -> list[str]:
    raw = os.environ.get("ONESHOT_CORS_ORIGINS") or ""
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_ORIGIN_REGEX,
    allow_origins=_extra_origins(),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
    max_age=600,
)


@app.exception_handler(ContractError)
async def _contract_error(_request: Any, exc: ContractError) -> JSONResponse:
    """A refused request is explained in the words the domain chose."""
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# --------------------------------------------------------------------------- #
# Service truth
# --------------------------------------------------------------------------- #


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "service": "oneshot-system",
        "schema_version": SCHEMA_VERSION,
        "endpoints": ["/health", "/schema", "/run", "/runs/{run_id}/events", "/runs/{run_id}/command"],
        "note": (
            "The System runs here; the published UI is a client of it. Every run "
            "returns one output.json and keeps no history."
        ),
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    """What this deployment can truthfully say about itself."""
    return {
        "status": "ok",
        "service": "oneshot-system",
        "schema_version": SCHEMA_VERSION,
        "stateless": True,
        "generate": provider_status(),
        "transcribe": transcriber_status(),
        "inspection_modes_v1": sorted(mode.value for mode in V1_INSPECTION_MODES),
        "workflow_states": [state.value for state in WorkflowState],
    }


@app.get("/schema")
async def schema() -> dict[str, Any]:
    """The contract the client mirrors, so drift is detectable rather than silent."""
    return schema_snapshot()


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #


@app.post("/run", response_model=OneShotOutput)
async def start_run(request: RunRequest) -> OneShotOutput:
    """Execute one run and return its complete document.

    The live channel is registered before the pipeline starts, so the client can
    subscribe to ``/runs/{run_id}/events`` first (supply ``run_id`` in the
    request) and watch the run happen.
    """
    admission = admit(request)
    channel = registry.create(admission.run_id)
    try:
        return await run(admission, channel)
    except ContractError:
        raise
    except Exception as exc:
        logger.exception("Run %s failed", admission.run_id)
        raise HTTPException(
            status_code=500,
            detail=(
                "OneShot could not complete this run. The System recorded what "
                "happened on the live channel."
            ),
        ) from exc


@app.get("/runs/{run_id}/events")
async def run_events(run_id: str) -> StreamingResponse:
    """The Process Display channel — Server-Sent Events, one stream per run.

    Events already published are replayed, so a subscriber that attaches late
    still sees the run's whole observed history.
    """
    channel = registry.get(run_id)
    if channel is None:
        # A client subscribes the moment it POSTs /run, and the two requests can
        # arrive in either order. Waiting briefly turns that race into a
        # deterministic subscribe-before-start without weakening the 404 below.
        for _ in range(25):
            await asyncio.sleep(0.2)
            channel = registry.get(run_id)
            if channel is not None:
                break
    if channel is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No run with id {run_id!r} is known to this System. Runs are held "
                "in memory only — v1 keeps no history — so it may have expired or "
                "never started."
            ),
        )

    async def publisher():
        yield _frame(
            {
                "type": "hello",
                "run_id": run_id,
                "schema_version": SCHEMA_VERSION,
                "state": channel.state.value,
                "at_ms": channel.elapsed_ms(),
            }
        )
        async for event in channel.stream():
            yield _frame(event)
        yield _frame(
            {
                "type": "end",
                "run_id": run_id,
                "state": channel.state.value,
                "at_ms": channel.elapsed_ms(),
            }
        )

    return StreamingResponse(
        publisher(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class CommandRequest(BaseModel):
    """A client-requested workflow transition."""

    model_config = ConfigDict(extra="forbid")

    command: WorkflowCommand


@app.post("/runs/{run_id}/command")
async def run_command(run_id: str, body: CommandRequest) -> dict[str, Any]:
    """Request a transition. The System owns whether it is applied."""
    channel = registry.get(run_id)
    if channel is None:
        raise HTTPException(
            status_code=404,
            detail=f"No run with id {run_id!r} is known to this System.",
        )
    state = apply_command(channel, body.command)
    return {"run_id": run_id, "command": body.command.value, "state": state.value}


def _frame(event: dict[str, Any]) -> str:
    """One Server-Sent Event frame.

    The event type travels both as the SSE event name and inside the payload, so
    a client using plain ``fetch`` streaming sees exactly the same thing as one
    using ``EventSource``.
    """
    import json

    name = str(event.get("type") or "message")
    return f"event: {name}\ndata: {json.dumps(event)}\n\n"


def main() -> None:
    """Run the System locally (``python -m systems.api``), ADR-001 D2 defaults."""
    import uvicorn

    uvicorn.run(
        "systems.api:app",
        host=(os.environ.get("ONESHOT_HOST") or "127.0.0.1").strip(),
        port=int(os.environ.get("ONESHOT_PORT") or "8000"),
        log_level=(os.environ.get("ONESHOT_LOG_LEVEL") or "info").strip(),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
