"""Operations — admission, orchestration and the workflow state machine.

Owns, per ADR-001 D3/D4 and §12.1:

* **admission** — a run enters through :func:`admit`, which binds the objective to
  the sources the caller selected.
* **the pipeline** — Scan → Read → Prepare → Quality & Coverage Check →
  *(conditional)* Generate → validate → ``output.json``.
* **the process trace** — ordered and contiguous from 0, recording only observed
  activity (invariant #4). A stage that did not run is reported as skipped, which
  is itself an observation; nothing is narrated that did not happen.
* **the state machine** — ``Starting → Process Display → Pausing → Paused →
  Editor Review → Finalized``. The client may *request* a transition; only this
  module applies one.

Concurrency: the pipeline is deliberately blocking (network reads, parsing,
provider calls), so it runs in a worker thread via :func:`run`. Pause/resume
therefore blocks only that thread, and published events are handed to the event
loop with ``call_soon_threadsafe``.

Statelessness (ADR-001 D3): the run registry below is **in-memory transport
state for the duration of a run** — it is not a store. No run history is kept,
nothing is read back after a run ends, and ``JobCreate.verify_sources()`` is
never invoked: v1 makes no drift claim.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from systems.contracts import ContractError, JobCreate
from systems.generator import GenerationFailed, GenerationUnavailable, generate
from systems.prepare import prepare
from systems.quality import check as check_quality
from systems.read import read
from systems.scan import scan
from systems.schemas import (
    SCHEMA_VERSION,
    InspectionMode,
    Limitation,
    LimitationSeverity,
    OneShotOutput,
    ProcessTraceEvent,
    RunRequest,
    SourceInput,
    SourceProvenance,
    Stage,
    TaskKind,
    TraceStatus,
    WorkflowCommand,
    WorkflowState,
)

#: How long a finished run's channel stays available for a late subscriber.
RUN_TTL_SECONDS = 10 * 60

#: How long the events endpoint waits before emitting a keep-alive.
STREAM_HEARTBEAT_SECONDS = 15.0


# --------------------------------------------------------------------------- #
# Admission
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Admission:
    """A run that has been admitted to the System."""

    run_id: str
    request: RunRequest
    job: JobCreate | None

    @property
    def intent(self) -> str:
        return self.request.intent

    @property
    def sources(self) -> tuple[SourceInput, ...]:
        return tuple(self.request.sources)

    @property
    def task_kind(self) -> TaskKind:
        return self.request.task_kind


def admit(request: RunRequest) -> Admission:
    """Admit a request as a run.

    When the caller supplied job identity (conversation, objective message,
    idempotency key, expected versions), the domain admission contract is built
    too, so a partial identity is refused truthfully instead of half-honoured.
    **D3:** that job is carried for identity only — v1 never calls
    ``verify_sources()`` and claims no drift protection.
    """
    run_id = (request.run_id or "").strip() or f"run_{uuid4().hex[:12]}"
    supplied_identity = any(
        (
            request.conversation_id,
            request.prompt_message_id,
            request.idempotency_key,
            request.expected_versions,
        )
    )
    job = request.to_job_create() if supplied_identity else None
    return Admission(run_id=run_id, request=request, job=job)


# --------------------------------------------------------------------------- #
# Live channel (in-memory, per run)
# --------------------------------------------------------------------------- #


class RunChannel:
    """The live Process Display channel for one run.

    Holds the ordered event stream, the current workflow state, and the pause
    gate. Subscribers may attach before or after the run starts: buffered events
    are replayed from the beginning, so a late viewer still sees the whole
    observed history.

    Frame types: ``hello``, ``state``, ``trace``, ``output``, ``failed``,
    ``heartbeat``, ``end``. The failure frame is deliberately **not** named
    ``error`` — that name belongs to ``EventSource``'s own connection event, and
    overloading it would make a broken connection indistinguishable from a failed
    run.
    """

    def __init__(self, run_id: str, loop: asyncio.AbstractEventLoop) -> None:
        self.run_id = run_id
        self.state = WorkflowState.STARTING
        self.created_at = time.monotonic()
        self._loop = loop
        self._events: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._finished = False
        self._pause_requested = False
        self._resume = threading.Event()
        self._resume.set()

    # -- transport ---------------------------------------------------------- #

    def publish(self, event: dict[str, Any]) -> None:
        """Publish from the event loop."""
        self._events.put_nowait(event)

    def publish_threadsafe(self, event: dict[str, Any]) -> None:
        """Publish from the pipeline thread.

        A run's channel can outlive the event loop it was created on (for example
        a late ``/command`` request that lands on a different loop). When that loop
        is gone there is nobody streaming the channel, so this becomes a no-op: the
        observed activity is still recorded on the trace, and the workflow state is
        still applied — publishing a frame must never crash the command.
        """
        try:
            self._loop.call_soon_threadsafe(self._events.put_nowait, event)
        except RuntimeError:
            # The owning loop is closed; nothing is subscribed to receive frames.
            return

    async def stream(self):
        """Yield published events until the run closes the channel."""
        while True:
            try:
                event = await asyncio.wait_for(
                    self._events.get(), timeout=STREAM_HEARTBEAT_SECONDS
                )
            except asyncio.TimeoutError:
                yield {"type": "heartbeat", "at_ms": self.elapsed_ms()}
                continue
            if event is None:
                return
            yield event

    @property
    def finished(self) -> bool:
        return self._finished

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.created_at) * 1000)

    # -- state machine ------------------------------------------------------ #

    def set_state(self, state: WorkflowState) -> WorkflowState:
        if state is not self.state:
            self.state = state
            self.publish_threadsafe(
                {"type": "state", "state": state.value, "at_ms": self.elapsed_ms()}
            )
        return self.state

    def request_pause(self) -> WorkflowState:
        if self._finished:
            raise ContractError(
                "This run has already finished, so it cannot be paused."
            )
        self._pause_requested = True
        self._resume.clear()
        return self.state

    def request_resume(self) -> WorkflowState:
        if not self._pause_requested and self.state is not WorkflowState.PAUSED:
            raise ContractError("This run is not paused, so there is nothing to resume.")
        self._pause_requested = False
        self._resume.set()
        return self.state

    def checkpoint(self, trace: _Trace) -> None:
        """Honour a pause request at a safe point in the pipeline.

        Blocks the *worker thread* only, so the event loop (and therefore the
        events endpoint and the pause/resume commands) stays responsive.
        """
        if not self._pause_requested:
            return
        self.set_state(WorkflowState.PAUSING)
        trace(None, TraceStatus.INFO, "Pausing — finishing the current step.")
        self.set_state(WorkflowState.PAUSED)
        trace(None, TraceStatus.INFO, "Paused — waiting for you to resume.")
        self._resume.wait()
        self._pause_requested = False
        self.set_state(WorkflowState.PROCESS_DISPLAY)
        trace(None, TraceStatus.INFO, "Resumed.")

    # -- lifecycle ---------------------------------------------------------- #

    def finish(self, event: dict[str, Any]) -> None:
        self.publish_threadsafe(event)
        self._finished = True
        self.publish_threadsafe(None)


# --------------------------------------------------------------------------- #
# The trace
# --------------------------------------------------------------------------- #


class _Trace:
    """Collects observed activity, indexes it, and streams it as it happens."""

    def __init__(self, channel: RunChannel) -> None:
        self._channel = channel
        self._events: list[ProcessTraceEvent] = []
        self._start = time.monotonic()

    def __call__(
        self,
        stage: Stage | None,
        status: TraceStatus,
        message: str,
        detail: str = "",
        source_id: str | None = None,
    ) -> None:
        if self._channel.state is WorkflowState.STARTING:
            # Process Display begins the moment the System reports its first
            # observed activity.
            self._channel.set_state(WorkflowState.PROCESS_DISPLAY)
        event = ProcessTraceEvent(
            index=len(self._events),
            stage=stage,
            status=status,
            message=message,
            detail=detail,
            source_id=source_id,
            at_ms=int((time.monotonic() - self._start) * 1000),
        )
        self._events.append(event)
        self._channel.publish_threadsafe(
            {"type": "trace", "event": event.model_dump(mode="json")}
        )

    @property
    def events(self) -> list[ProcessTraceEvent]:
        return list(self._events)


# --------------------------------------------------------------------------- #
# Run registry
# --------------------------------------------------------------------------- #


class RunRegistry:
    """In-memory channels for runs that are live (or recently finished).

    Not a datastore: entries expire, nothing is persisted, and a restart starts
    with an empty registry.
    """

    def __init__(self, ttl_seconds: int = RUN_TTL_SECONDS) -> None:
        self._channels: dict[str, RunChannel] = {}
        self._ttl = ttl_seconds

    def create(self, run_id: str) -> RunChannel:
        self.sweep()
        existing = self._channels.get(run_id)
        if existing is not None and not existing.finished:
            raise ContractError(
                f"A run with id {run_id!r} is already in progress. Wait for it to "
                "finish, or use a different run id."
            )
        channel = RunChannel(run_id, asyncio.get_running_loop())
        self._channels[run_id] = channel
        return channel

    def get(self, run_id: str) -> RunChannel | None:
        self.sweep()
        return self._channels.get(run_id)

    def sweep(self) -> None:
        expired = [
            run_id
            for run_id, channel in self._channels.items()
            if channel.finished and (time.monotonic() - channel.created_at) > self._ttl
        ]
        for run_id in expired:
            del self._channels[run_id]


# --------------------------------------------------------------------------- #
# The pipeline
# --------------------------------------------------------------------------- #


def select_inspection_mode(request: RunRequest) -> InspectionMode:
    """The inspection mode the System selects (System-owned, spec §3.5).

    A single source is read as ``TEXT``; two or more are composed as ``MIXED``,
    which preserves each source's provenance rather than flattening them. No
    other mode is selected in v1, and ``MIXED`` never expands support to a
    deferred source type.
    """
    return InspectionMode.MIXED if len(request.sources) > 1 else InspectionMode.TEXT


def execute(admission: Admission, channel: RunChannel) -> OneShotOutput:
    """Run the whole pipeline and return the delivered document.

    Blocking by design — call it through :func:`run` so the event loop keeps
    serving the live channel.
    """
    request = admission.request
    trace = _Trace(channel)

    # -- Scan --------------------------------------------------------------- #
    trace(Stage.SCAN, TraceStatus.STARTED, f"Scanning {len(request.sources)} source(s).")
    mode = select_inspection_mode(request)
    trace(
        Stage.SCAN,
        TraceStatus.INFO,
        f"Selected {mode.value} Inspection for this run "
        f"({len(request.sources)} source(s) in scope).",
    )
    scan_result = scan(request, mode=mode, emit=trace)
    trace(
        Stage.SCAN,
        TraceStatus.COMPLETED,
        "Scan finished: "
        f"{scan_result.accessible_count} accessible, "
        f"{scan_result.unsupported_count} unsupported, "
        f"{scan_result.unreadable_count} unreadable, "
        f"{scan_result.excluded_count} excluded.",
    )

    # -- Read --------------------------------------------------------------- #
    trace(Stage.READ, TraceStatus.STARTED, "Reading the accessible sources.")
    read_result = read(request, scan_result, mode=mode, emit=trace)
    trace(
        Stage.READ,
        TraceStatus.COMPLETED,
        f"Read {len(read_result.read_source_ids)} of {scan_result.accessible_count} "
        f"accessible source(s): {read_result.total_chars:,} characters extracted.",
    )

    # -- Prepare ------------------------------------------------------------ #
    prepared = prepare(request, scan_result, read_result, mode=mode, emit=trace)
    trace(
        Stage.PREPARE,
        TraceStatus.COMPLETED,
        f"Prepared {prepared.context_chars:,} characters from "
        f"{len(prepared.context_blocks)} source(s); "
        f"{len(prepared.decisions)} inclusion decision(s) recorded.",
    )

    # -- Quality & Coverage Check ------------------------------------------- #
    quality = check_quality(request, scan_result, read_result, prepared, emit=trace)

    limitations: list[Limitation] = [*read_result.limitations, *quality.limitations]
    generated = None

    # -- Generate (conditional, never a tail) ------------------------------- #
    if quality.allows_generation and prepared.allows_generation:
        if request.options.pause_before_generate:
            channel.request_pause()
        channel.checkpoint(trace)
        trace(
            Stage.GENERATE,
            TraceStatus.STARTED,
            "Generating a grounded result from the prepared context.",
        )
        try:
            generated = generate(prepared)
        except GenerationUnavailable as exc:
            trace(Stage.GENERATE, TraceStatus.FAILED, str(exc))
            limitations.append(
                Limitation(
                    code="generator_not_configured",
                    message=str(exc),
                    severity=LimitationSeverity.ERROR,
                )
            )
        except GenerationFailed as exc:
            trace(Stage.GENERATE, TraceStatus.FAILED, str(exc))
            limitations.append(
                Limitation(
                    code="generation_failed",
                    message=str(exc),
                    severity=LimitationSeverity.ERROR,
                )
            )
        else:
            citations = len(generated.citations) + sum(
                len(section.citations) for section in generated.sections
            )
            trace(
                Stage.GENERATE,
                TraceStatus.COMPLETED,
                f'Generated "{generated.title}" — {len(generated.sections)} section(s), '
                f"{citations} citation(s), model {generated.model}.",
            )
    else:
        trace(
            Stage.GENERATE,
            TraceStatus.SKIPPED,
            "Generation was not run: "
            f"the quality outcome was {quality.outcome.value}"
            + (f" — {quality.reasons[0]}" if quality.reasons else ".")
            + " Nothing was generated rather than guessed.",
        )

    # -- Output ------------------------------------------------------------- #
    channel.checkpoint(trace)
    trace(
        Stage.OUTPUT,
        TraceStatus.STARTED,
        f"Validating the assembled result against schema {SCHEMA_VERSION}.",
    )

    output = OneShotOutput(
        run_id=admission.run_id,
        state=WorkflowState.EDITOR_REVIEW,
        quality=quality,
        supported_task=prepared.supported_task,
        sources=_provenance(request, scan_result, read_result, mode),
        prepared=prepared,
        context_decisions=prepared.decisions,
        generated=generated,
        trace=trace.events,
        limitations=limitations,
    )

    document = write_output(output)
    trace(
        Stage.OUTPUT,
        TraceStatus.COMPLETED,
        f"Wrote output.json — {len(document):,} characters, schema {output.schema_version}.",
    )
    return output.model_copy(update={"trace": trace.events})


def _provenance(request, scan_result, read_result, mode) -> list[SourceProvenance]:
    """The source descriptors delivered with the result (plan C2).

    Hash/version are **self-reported at read time and unverified** (ADR-001 D3).
    """
    observations = {o.source_id: o for o in scan_result.observations}
    segments = {s.source_id: s for s in read_result.segments}
    descriptors: list[SourceProvenance] = []
    for source in request.sources:
        observation = observations.get(source.id)
        segment = segments.get(source.id)
        descriptors.append(
            SourceProvenance(
                source_id=source.id,
                label=source.label,
                type=source.type,
                mode=mode,
                outcome=observation.outcome if observation else "accessible",
                reported_content_sha256=segment.reported_content_sha256 if segment else "",
                reported_source_version=segment.reported_source_version if segment else "",
            )
        )
    return descriptors


def write_output(output: OneShotOutput, *, directory: str | None = None) -> str:
    """Validate and serialize the delivered document (``output.json``).

    The Pydantic model *is* the schema gate (ADR-001 D3); re-validating here means
    a mutated document cannot be written. v1 keeps no store — when
    ``ONESHOT_OUTPUT_DIR`` is set on the host, the JSON is also written there as a
    debugging artifact: an artifact copy, never read back, never a datastore.
    """
    validated = OneShotOutput.model_validate(output.model_dump(mode="json"))
    document = validated.model_dump_json(indent=2)
    target = (directory or os.environ.get("ONESHOT_OUTPUT_DIR") or "").strip()
    if target:
        folder = Path(target)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{validated.run_id}.json").write_text(document, encoding="utf-8")
    return document


async def run(admission: Admission, channel: RunChannel) -> OneShotOutput:
    """Run the pipeline off the event loop and close the live channel."""
    try:
        output = await asyncio.to_thread(execute, admission, channel)
    except ContractError:
        channel.finish(
            {
                "type": "failed",
                "message": "OneShot could not accept this request as a run.",
                "at_ms": channel.elapsed_ms(),
            }
        )
        raise
    except Exception as exc:  # surfaced, never swallowed
        channel.finish(
            {
                "type": "failed",
                "message": f"OneShot could not complete this run: {exc}",
                "at_ms": channel.elapsed_ms(),
            }
        )
        raise

    channel.set_state(WorkflowState.EDITOR_REVIEW)
    channel.finish(
        {
            "type": "output",
            "output": output.model_dump(mode="json"),
            "at_ms": channel.elapsed_ms(),
        }
    )
    return output


def apply_command(channel: RunChannel, command: WorkflowCommand) -> WorkflowState:
    """Apply a client-requested workflow transition (the System owns the state)."""
    if command is WorkflowCommand.PAUSE:
        channel.request_pause()
    elif command is WorkflowCommand.RESUME:
        channel.request_resume()
    elif command is WorkflowCommand.REVIEW:
        if not channel.finished:
            raise ContractError(
                "This run is still going. Editor Review opens once the result has "
                "been delivered."
            )
        channel.set_state(WorkflowState.EDITOR_REVIEW)
    elif command is WorkflowCommand.FINALIZE:
        if not channel.finished:
            raise ContractError(
                "This run is still going, so there is nothing to finalize yet."
            )
        channel.set_state(WorkflowState.FINALIZED)
    else:  # pragma: no cover - WorkflowCommand is a closed enum
        raise ContractError(f"{command!r} is not a workflow command OneShot supports.")
    return channel.state