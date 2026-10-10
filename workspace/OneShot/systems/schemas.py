"""OneShot wire + stage contracts (Pydantic v2).

This module is the **single source of truth** for the HTTP boundary
(``RunRequest`` / ``OneShotOutput``) and for every stage's input/output, per
ADR-001 (D3/D4) and the PRD ("Pydantic models define ``RunRequest``,
``OneShotOutput`` and every stage's I/O; the client mirrors the schema for
typing only").

Why this is a separate module from :mod:`systems.contracts`
----------------------------------------------------------
``systems.contracts`` states an explicit design rule: it imports *no* framework,
provider or storage dependency so the domain can be reasoned about in isolation.
Pydantic is a third-party dependency, so the wire contract lives here and
**reuses** the domain enums (``SourceOperation``, ``QualityOutcome``,
``InspectionMode``) rather than re-declaring them — one source of truth, no
duplicate vocabularies that can drift apart.

Invariants encoded *in the types* (so they cannot be violated by a caller)
------------------------------------------------------------------------
* A generated result may only accompany a ``Sufficient`` outcome (conditional
  generation, ADR-001 D3) — see :class:`OneShotOutput`.
* A non-``Sufficient`` outcome must carry reasons ("never overclaim").
* A ``Sufficient`` outcome must carry **positive evidence** — at least one
  coverage dimension (with at least one covered) or an explicit sufficiency
  reason. Absence of recorded problems is not sufficiency (plan C3/D4).
* A generated result must be **grounded**: every section cites at least one
  source, and every citation resolves to a declared source (plan C4).
* A wire source operation can never be content deletion
  (``REMOVE_SOURCE`` != delete) — see :class:`SourceInput`.
* Source content is structurally separated from privileged instructions and can
  only ever be marked untrusted (plan C9) — see :class:`ContextBlock`.
* The process trace is ordered and contiguous from 0 (truthful Process
  Display).

Honesty about provenance (ADR-001 D3)
-------------------------------------
v1 is stateless: nothing here means a hash or version was *recorded*,
*verified*, or *protected against drift*. Fields are named
``reported_content_sha256`` / ``reported_source_version`` and every source
descriptor carries a literal ``provenance_status: "reported_unverified"`` so the
shape cannot be misread as verified provenance.
"""

from __future__ import annotations

import base64
import binascii
import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from systems.contracts import (
    ContractError,
    InspectionMode,
    JobCreate,
    QualityOutcome,
    SourceOperation,
    assert_scope_only,
)

__all__ = [
    "SCHEMA_VERSION",
    "MAX_UPLOAD_BYTES",
    "MAX_INLINE_TEXT_CHARS",
    "SourceType",
    "Stage",
    "TraceStatus",
    "WorkflowState",
    "WorkflowCommand",
    "TaskKind",
    "LimitationSeverity",
    "Limitation",
    "ScanOutcome",
    "ScanReason",
    "ContextDecisionReason",
    "SourceInput",
    "RunOptions",
    "RunRequest",
    "SourceProvenance",
    "ScanObservation",
    "ScanResult",
    "ReadSegment",
    "ReadResult",
    "SupportedTask",
    "ContextBlock",
    "ContextDecision",
    "PreparedInput",
    "CoverageDimension",
    "QualityCheck",
    "Citation",
    "GeneratedSection",
    "GeneratedResult",
    "ProcessTraceEvent",
    "OneShotOutput",
    "decode_upload",
    "schema_snapshot",
]

# Bumped whenever a wire-visible shape changes; the client mirrors it, so a
# mismatch is detectable rather than silent (PRD acceptance criteria).
# 1.0 -> 1.1 applies the authorized corrections C1-C10 (workspace-context plan
# §6/§12.1): provenance is named as unverified, Sufficient needs positive
# evidence, generated results must be grounded, task kind is enumerated, source
# identity is required, scan outcomes are classified, instructions and content
# are separated, and context inclusion/exclusion is recorded.
SCHEMA_VERSION = "1.1"

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_INLINE_TEXT_CHARS = 2_000_000

# A verbatim span this long, shared between a privileged instruction and source
# content, means the instruction was influenced by the content (plan C9).
_INJECTION_WINDOW = 40

_DATA_URL = re.compile(r"\Adata:[^;,]*;base64,", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")
_SAFE_ID = re.compile(r"\A[A-Za-z0-9_.:-]{1,64}\Z")


# --------------------------------------------------------------------------- #
# Vocabularies
# --------------------------------------------------------------------------- #


class SourceType(str, Enum):
    """The five v1 source types (PRD Goals)."""

    PUBLIC_URL = "public_url"
    TEXT = "text"
    RAW_HTML = "raw_html"
    FILE_PDF = "pdf"
    FILE_DOCX = "docx"
    AUDIO = "audio"


class Stage(str, Enum):
    """The stages a trace entry can be attributed to. System-owned (spec §3)."""

    SCAN = "Scan"
    READ = "Read"
    PREPARE = "Prepare"
    QUALITY = "Quality & Coverage Check"
    GENERATE = "Generate"
    OUTPUT = "Output"


class TraceStatus(str, Enum):
    """How an observed activity ended. Only observed activity is recorded."""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    INFO = "info"


class WorkflowState(str, Enum):
    """The system-owned state machine (workflow specification §3.11).

    Values are the exact labels from the governing specification.
    """

    STARTING = "Starting"
    PROCESS_DISPLAY = "Process Display"
    PAUSING = "Pausing"
    PAUSED = "Paused"
    EDITOR_REVIEW = "Editor Review"
    FINALIZED = "Finalized"


class WorkflowCommand(str, Enum):
    """Workflow transitions are system-owned, never a client-side trick.

    The client only *requests* a transition over the live Process Display
    channel; :mod:`systems.operations` owns whether it is applied.
    """

    PAUSE = "pause"
    RESUME = "resume"
    REVIEW = "review"
    FINALIZE = "finalize"


class TaskKind(str, Enum):
    """What the objective asks for (plan C5; spec §3.2 *Identify Requested
    Action* and §3.3 *Determine Applicable Responsibility*).

    :attr:`UNCLASSIFIED` is the safe default: a request that does not name a
    supported task is never silently treated as one.
    """

    SUMMARISE = "summarise"
    EXPLAIN_SOURCE = "explain_source"
    EVALUATE_REQUIREMENTS = "evaluate_requirements"
    GENERATE = "generate"
    UNCLASSIFIED = "unclassified"


class LimitationSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ScanOutcome(str, Enum):
    """How the Scan stage classified one source (plan C7).

    Four distinct states, because an unrepresentable state is an unreportable
    one: material that was **excluded** is not the same as material that was
    **unreadable**, and neither is the same as a type the System does not
    **support**.
    """

    ACCESSIBLE = "accessible"
    UNSUPPORTED = "unsupported"
    UNREADABLE = "unreadable"
    EXCLUDED = "excluded"


class ScanReason(str, Enum):
    """Why a source landed in its scan outcome. Enumerated, not prose (C7)."""

    SUPPORTED = "supported"
    UNSUPPORTED_SOURCE_TYPE = "unsupported_source_type"
    UNSUPPORTED_INSPECTION_MODE = "unsupported_inspection_mode"
    EXCLUDED_FROM_SCOPE = "excluded_from_scope"
    CONTENT_NOT_IMPORTED = "content_not_imported"
    EMPTY_CONTENT = "empty_content"
    MALFORMED_UPLOAD = "malformed_upload"
    OVERSIZED_UPLOAD = "oversized_upload"



class ContextDecisionReason(str, Enum):
    """Why a source's content was included in, or dropped from, the context (C10).

    Inclusion and exclusion are recorded decisions, never a silent side effect of
    a character budget. Which *limit* caused a truncation — the per-source share
    or the global budget — is named in the decision's ``note``.
    """

    INCLUDED = "included"
    INCLUDED_TRUNCATED_FOR_BUDGET = "included_truncated_for_budget"
    DROPPED_OVER_GLOBAL_BUDGET = "dropped_over_global_budget"
    NOT_READABLE = "not_readable"
    EXCLUDED_FROM_SCOPE = "excluded_from_scope"



class Limitation(BaseModel):
    """A recorded, truthful limitation or non-fatal failure.

    ``message`` is written to be shown to the user verbatim: it says what
    happened and what it means, never a raw code or stack trace.
    """

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: LimitationSeverity = LimitationSeverity.WARNING
    source_id: str | None = None


# --------------------------------------------------------------------------- #
# Request side
# --------------------------------------------------------------------------- #


def decode_upload(raw: str) -> bytes:
    """Decode an uploaded payload, tolerating a ``data:`` URL prefix."""
    compact = _WHITESPACE.sub("", _DATA_URL.sub("", raw))
    if not compact:
        raise ValueError("The uploaded file is empty.")
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(
            "The uploaded file is not valid base64 — re-upload it and try again."
        ) from exc


class SourceInput(BaseModel):
    """One source supplied to a run.

    Only the fields relevant to ``type`` are required; the rest are ignored and
    rejected outright if unexpected (``extra="forbid"``) so client/server drift
    surfaces immediately rather than silently.

    ``id`` is **required** (plan C6): the run's scope, the trace and every
    citation are keyed by it, so a source without stable identity could not be
    cited, dropped, or reported truthfully.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=64, pattern=r"\A[A-Za-z0-9_.:-]+\Z")
    type: SourceType
    label: str = ""
    url: str | None = None
    text: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    content_base64: str | None = None
    operation: SourceOperation = SourceOperation.ADD_SOURCE

    @field_validator("operation")
    @classmethod
    def _scope_operations_only(cls, value: SourceOperation) -> SourceOperation:
        try:
            assert_scope_only(value)
        except ContractError as exc:  # deliver the domain message unchanged
            raise ValueError(str(exc)) from exc
        return value

    @model_validator(mode="after")
    def _require_type_specific_fields(self) -> SourceInput:
        stype = self.type

        if stype is SourceType.PUBLIC_URL:
            if not self.url:
                raise ValueError("A public URL source requires a url.")
            if not self.url.lower().startswith(("http://", "https://")):
                raise ValueError(
                    "The public URL must start with http:// or https:// — "
                    "paste the full address of the page."
                )
        elif stype in (SourceType.TEXT, SourceType.RAW_HTML):
            if not (self.text or "").strip():
                raise ValueError(f"A {stype.value} source requires non-empty text.")
            if len(self.text or "") > MAX_INLINE_TEXT_CHARS:
                raise ValueError(
                    f"Inline text is limited to {MAX_INLINE_TEXT_CHARS:,} "
                    "characters — split it or upload it as a file."
                )
        else:
            if not self.content_base64:
                raise ValueError(
                    "This source type requires an uploaded file (content_base64)."
                )
            size = len(decode_upload(self.content_base64))
            if size > MAX_UPLOAD_BYTES:
                limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
                raise ValueError(
                    f"The uploaded file is larger than {limit_mb} MB. "
                    "Upload a smaller file and try again."
                )

        if not self.label:
            self.label = self.filename or self.url or stype.value

        return self


class RunOptions(BaseModel):
    """Knobs the caller may set. Everything else is the System's decision."""

    model_config = ConfigDict(extra="forbid")

    # Lets the UI demonstrate the Pausing -> Paused transition by holding the
    # conditional Generate stage until the user resumes.
    pause_before_generate: bool = False
    max_context_chars: int = Field(default=200_000, ge=1_000)
    # When unset, each source receives an equal share of ``max_context_chars`` so
    # one large source cannot silently evict the content the objective depends on
    # (plan C10 / Q4b).
    max_chars_per_source: int | None = Field(default=None, ge=200)


class RunRequest(BaseModel):
    """``POST /run`` body: one or more sources plus the stated objective.

    Identity fields (``conversation_id``, ``prompt_message_id``,
    ``idempotency_key``, ``expected_versions``) reconcile this wire shape with
    the domain admission contract :class:`~systems.contracts.JobCreate` (plan
    C6). They are optional because v1 runs statelessly; see
    :meth:`to_job_create`.
    """

    model_config = ConfigDict(extra="forbid")

    intent: str = Field(min_length=1)
    sources: list[SourceInput] = Field(min_length=1)
    # ``UNCLASSIFIED`` is the safe default: the System identifies the requested
    # action (spec §3.2) and reports what it could support (§3.6) rather than
    # assuming.
    task_kind: TaskKind = TaskKind.UNCLASSIFIED
    options: RunOptions = Field(default_factory=RunOptions)

    # Client-supplied correlation id for the live Process Display channel, so the
    # browser can subscribe before the run starts. The System mints one when
    # omitted.
    run_id: str | None = Field(default=None, pattern=r"\A[A-Za-z0-9_.:-]{1,64}\Z")

    conversation_id: str = ""
    prompt_message_id: str = ""
    idempotency_key: str = ""
    expected_versions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _no_duplicate_ids(self) -> RunRequest:
        given = [s.id for s in self.sources]
        if len(set(given)) != len(given):
            raise ValueError("A source id was supplied more than once.")
        return self

    @model_validator(mode="after")
    def _expected_versions_match_scope(self) -> RunRequest:
        if not self.expected_versions:
            return self
        selected = {s.id for s in self.sources}
        unknown = sorted(set(self.expected_versions) - selected)
        if unknown:
            raise ValueError(
                "The expected versions name sources that are not in this "
                f"request: {', '.join(unknown)}."
            )
        return self

    def to_job_create(self) -> JobCreate:
        """Build the domain admission contract from this request (plan C6).

        Contract-level only — **no v1 runtime caller** (ADR-001 D3). It exists so
        the two halves of the contract are one shape instead of two disjoint
        ones: a request that carries full identity can produce a conforming job.

        Raises :class:`~systems.contracts.ContractError` naming exactly what is
        missing, rather than inventing identity the caller never supplied.
        """
        missing = [
            name
            for name, value in (
                ("conversation_id", self.conversation_id),
                ("prompt_message_id", self.prompt_message_id),
                ("idempotency_key", self.idempotency_key),
            )
            if not value.strip()
        ]
        if not self.expected_versions:
            missing.append("expected_versions")
        if missing:
            raise ContractError(
                "This request cannot be admitted as a job — "
                + ", ".join(missing)
                + " must be supplied. v1 runs statelessly without them; the "
                "identity fields exist for a persistence-enabled scope."
            )
        return JobCreate(
            objective=self.intent,
            prompt_message_id=self.prompt_message_id,
            conversation_id=self.conversation_id,
            selected_source_ids=tuple(s.id for s in self.sources),
            expected_versions=dict(self.expected_versions),
            idempotency_key=self.idempotency_key,
        )


# --------------------------------------------------------------------------- #
# Stage I/O
# --------------------------------------------------------------------------- #


class SourceProvenance(BaseModel):
    """A source descriptor delivered with the result (plan C2).

    ``provenance_status`` is a literal: v1 reports what it *observed*, and claims
    no recording, verification or drift protection (ADR-001 D3).
    """

    source_id: str
    label: str
    type: SourceType
    mode: InspectionMode
    outcome: ScanOutcome = ScanOutcome.ACCESSIBLE
    reported_content_sha256: str = ""
    reported_source_version: str = ""
    provenance_status: Literal["reported_unverified"] = "reported_unverified"


class ScanObservation(BaseModel):
    """What the Scan stage observed about one source. Facts only."""

    source_id: str
    type: SourceType
    label: str
    outcome: ScanOutcome
    reason: ScanReason = ScanReason.SUPPORTED
    detail: str = ""
    size_bytes: int | None = Field(default=None, ge=0)
    limitation: Limitation | None = None

    @model_validator(mode="after")
    def _reason_matches_outcome(self) -> ScanObservation:
        if self.outcome is ScanOutcome.ACCESSIBLE:
            if self.reason is not ScanReason.SUPPORTED:
                raise ValueError(
                    "An accessible source must carry the 'supported' scan reason."
                )
        elif self.reason is ScanReason.SUPPORTED:
            raise ValueError(
                "A source that is not accessible must record why it could not be "
                "scanned (unsupported, unreadable or excluded)."
            )
        if self.outcome is ScanOutcome.EXCLUDED and self.reason not in (
            ScanReason.EXCLUDED_FROM_SCOPE,
            ScanReason.CONTENT_NOT_IMPORTED,
        ):
            raise ValueError(
                "An excluded source must record why it was not read "
                "('excluded_from_scope' or 'content_not_imported')."
            )
        return self

    @property
    def accessible(self) -> bool:
        return self.outcome is ScanOutcome.ACCESSIBLE


class ScanResult(BaseModel):
    observations: list[ScanObservation] = Field(default_factory=list)

    def _count(self, outcome: ScanOutcome) -> int:
        return sum(1 for o in self.observations if o.outcome is outcome)

    @property
    def accessible_count(self) -> int:
        return self._count(ScanOutcome.ACCESSIBLE)

    @property
    def inaccessible_count(self) -> int:
        return len(self.observations) - self.accessible_count

    @property
    def unsupported_count(self) -> int:
        return self._count(ScanOutcome.UNSUPPORTED)

    @property
    def unreadable_count(self) -> int:
        return self._count(ScanOutcome.UNREADABLE)

    @property
    def excluded_count(self) -> int:
        return self._count(ScanOutcome.EXCLUDED)


class ReadSegment(BaseModel):
    """Extracted text for one source, in the inspection mode that produced it.

    ``locator`` pins the text to where it came from (character range, page, or
    timestamp) and ``dropped_chars`` records how much was left out — "truncated"
    alone never said what was lost (plan C10).
    """

    source_id: str
    mode: InspectionMode
    text: str
    char_count: int = Field(ge=0)
    truncated: bool = False
    dropped_chars: int = Field(default=0, ge=0)
    locator: str = ""
    reported_content_sha256: str = ""
    reported_source_version: str = ""

    @model_validator(mode="after")
    def _char_count_matches(self) -> ReadSegment:
        if self.char_count != len(self.text):
            raise ValueError("char_count must equal the length of text.")
        return self

    @model_validator(mode="after")
    def _truncation_is_consistent(self) -> ReadSegment:
        if self.dropped_chars and not self.truncated:
            raise ValueError(
                "A segment that dropped characters must be marked truncated."
            )
        return self


class ReadResult(BaseModel):
    segments: list[ReadSegment] = Field(default_factory=list)
    limitations: list[Limitation] = Field(default_factory=list)

    @property
    def total_chars(self) -> int:
        return sum(s.char_count for s in self.segments)

    @property
    def read_source_ids(self) -> tuple[str, ...]:
        """Sources that yielded readable text (the rest failed to read)."""
        seen: list[str] = []
        for segment in self.segments:
            if segment.text.strip() and segment.source_id not in seen:
                seen.append(segment.source_id)
        return tuple(seen)


class SupportedTask(BaseModel):
    """What the inspected sources can actually support (spec §3.6)."""

    supported: bool
    statement: str = ""
    task_type: TaskKind = TaskKind.UNCLASSIFIED


class ContextBlock(BaseModel):
    """Untrusted source content, attached to a prepared context (plan C9).

    The instruction/content boundary is *structural*: this object can only ever
    be marked ``untrusted_source_content``, and :class:`PreparedInput` keeps
    privileged instructions in a separate field. Source text cannot reach the
    instructions by construction, and :class:`PreparedInput` refuses to build if
    an instruction was visibly copied from content.
    """

    source_id: str
    source_label: str = ""
    mode: InspectionMode
    text: str
    char_count: int = Field(ge=0)
    locator: str = ""
    trust: Literal["untrusted_source_content"] = "untrusted_source_content"

    @model_validator(mode="after")
    def _char_count_matches(self) -> ContextBlock:
        if self.char_count != len(self.text):
            raise ValueError("char_count must equal the length of text.")
        return self


class ContextDecision(BaseModel):
    """What happened to one source's content, and why (plan C10)."""

    source_id: str
    mode: InspectionMode
    included: bool
    reason: ContextDecisionReason
    chars_included: int = Field(default=0, ge=0)
    chars_dropped: int = Field(default=0, ge=0)
    locator: str = ""
    note: str = ""

    @model_validator(mode="after")
    def _decision_is_consistent(self) -> ContextDecision:
        inclusive = (
            ContextDecisionReason.INCLUDED,
            ContextDecisionReason.INCLUDED_TRUNCATED_FOR_BUDGET,
        )
        if self.included:
            if self.reason not in inclusive:
                raise ValueError(
                    "An included context block must record an inclusion reason."
                )
            if not self.chars_included:
                raise ValueError(
                    "An included context block must record how much was included."
                )
            if (
                self.reason is ContextDecisionReason.INCLUDED
                and self.chars_dropped
            ):
                raise ValueError(
                    "A fully included block cannot also report dropped characters."
                )
        else:
            if self.reason in inclusive:
                raise ValueError(
                    "A dropped context block must record a drop reason, not an "
                    "inclusion reason."
                )
            if self.chars_included:
                raise ValueError(
                    "A dropped context block cannot report included characters."
                )
        return self


class PreparedInput(BaseModel):
    """Prepared context handed to the (conditional) Generate stage.

    ``instructions`` are system-authored and privileged; ``context_blocks`` are
    untrusted source content (plan C9). ``decisions`` records inclusion and
    exclusion per source (C10) so a drop is a reported decision, not a silent
    truncation.
    """

    intent: str
    task_kind: TaskKind = TaskKind.UNCLASSIFIED
    supported_task: SupportedTask
    instructions: list[str] = Field(default_factory=list)
    context_blocks: list[ContextBlock] = Field(default_factory=list)
    decisions: list[ContextDecision] = Field(default_factory=list)
    scope: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    context_chars: int = Field(default=0, ge=0)
    context_available: bool = False

    @model_validator(mode="after")
    def _instructions_are_not_source_derived(self) -> PreparedInput:
        """Refuse a prepared context whose instructions were influenced by it."""
        for instruction in self.instructions:
            for block in self.context_blocks:
                if _shares_span(instruction, block.text):
                    raise ValueError(
                        "A privileged instruction may not be derived from source "
                        "content (the instruction/content boundary was crossed)."
                    )
        return self

    @model_validator(mode="after")
    def _context_chars_match_blocks(self) -> PreparedInput:
        actual = sum(b.char_count for b in self.context_blocks)
        if self.context_chars != actual:
            raise ValueError(
                "context_chars must equal the total length of the context blocks."
            )
        if self.context_available and actual == 0:
            raise ValueError(
                "context_available cannot be true when no context was assembled."
            )
        return self

    @property
    def allows_generation(self) -> bool:
        return self.supported_task.supported and self.context_available


def _shares_span(instruction: str, content: str) -> bool:
    """True when ``instruction`` and ``content`` share a verbatim span.

    Used to prove the instruction/content boundary was not crossed (plan C9):
    either the whole instruction appears in the content, or any
    ``_INJECTION_WINDOW``-character window of it does.
    """
    instruction = instruction.strip()
    content = content.strip()
    if not instruction or not content:
        return False
    if instruction in content:
        return True
    if len(instruction) < _INJECTION_WINDOW:
        return False
    for start in range(0, len(instruction) - _INJECTION_WINDOW + 1):
        if instruction[start : start + _INJECTION_WINDOW] in content:
            return True
    return False


class CoverageDimension(BaseModel):
    name: str
    covered: bool
    detail: str = ""


class QualityCheck(BaseModel):
    """The tri-state coverage outcome, with reasons and limitations recorded.

    Two independent rules, both enforced here:

    * A non-``Sufficient`` outcome **must** carry at least one reason (never
      overclaim).
    * A ``Sufficient`` outcome **must** carry positive evidence — at least one
      coverage dimension (with at least one dimension covered) or an explicit
      sufficiency reason. The absence of recorded problems is not evidence of
      sufficiency (plan C3/D4).
    """

    outcome: QualityOutcome
    reasons: list[str] = Field(default_factory=list)
    limitations: list[Limitation] = Field(default_factory=list)
    coverage: list[CoverageDimension] = Field(default_factory=list)

    @model_validator(mode="after")
    def _never_overclaim(self) -> QualityCheck:
        if self.outcome is QualityOutcome.SUFFICIENT:
            if not self.coverage and not self.reasons:
                raise ValueError(
                    "A Sufficient outcome requires positive evidence: record at "
                    "least one coverage dimension or an explicit sufficiency "
                    "reason."
                )
            if self.coverage and not any(d.covered for d in self.coverage):
                raise ValueError(
                    "A Sufficient outcome cannot be recorded when every coverage "
                    "dimension is uncovered."
                )
        elif not self.reasons:
            raise ValueError(
                "An Incomplete or Unusable outcome must record at least one reason."
            )
        return self

    @property
    def allows_generation(self) -> bool:
        return self.outcome is QualityOutcome.SUFFICIENT


# --------------------------------------------------------------------------- #
# Generation side
# --------------------------------------------------------------------------- #


class Citation(BaseModel):
    """A pointer from generated content back to a declared source.

    ``reported_*`` fields are **self-reported and unverified** (ADR-001 D3): they
    record what the System observed at read time. They are deliberately not named
    ``sha256``/``version``, which would imply recorded, verified provenance.
    """

    source_id: str
    source_label: str = ""
    locator: str = ""
    reported_content_sha256: str = ""
    reported_source_version: str = ""


class GeneratedSection(BaseModel):
    """One section of the result. Grounding is mandatory, not optional (C4)."""

    heading: str = Field(min_length=1)
    body: str = Field(min_length=1)
    citations: list[Citation] = Field(min_length=1)


class GeneratedResult(BaseModel):
    """The schema-conforming generated result (``Systems.generate`` output).

    Deliberately concrete: the Generate stage must produce *this* shape, which
    is then validated against the Pydantic model before ``output.json`` is
    assembled (ADR-001 D3). An ungrounded or citation-free result is not a valid
    result (plan C4).
    """

    title: str = Field(min_length=1)
    summary: str = ""
    sections: list[GeneratedSection] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    model: str | None = None

    @model_validator(mode="after")
    def _is_grounded(self) -> GeneratedResult:
        if not self.citations and not any(s.citations for s in self.sections):
            raise ValueError(
                "A generated result must cite at least one source — an "
                "ungrounded result is not a result."
            )
        return self


# --------------------------------------------------------------------------- #
# Trace + output
# --------------------------------------------------------------------------- #


class ProcessTraceEvent(BaseModel):
    """One observed activity. Nothing is recorded that did not happen."""

    index: int = Field(ge=0)
    stage: Stage | None = None
    status: TraceStatus
    message: str = Field(min_length=1)
    detail: str = ""
    source_id: str | None = None
    at_ms: int = Field(default=0, ge=0)


class OneShotOutput(BaseModel):
    """``output.json`` — the complete, stateless result of one run.

    Delivered in :attr:`WorkflowState.EDITOR_REVIEW`: the user reviews and edits
    before finalizing. v1 keeps no store, so finalizing is acknowledged over the
    live channel and does not rewrite this document.
    """

    schema_version: str = SCHEMA_VERSION
    run_id: str = Field(min_length=1)
    state: WorkflowState = WorkflowState.EDITOR_REVIEW
    quality: QualityCheck
    supported_task: SupportedTask
    sources: list[SourceProvenance] = Field(default_factory=list)
    prepared: PreparedInput | None = None
    context_decisions: list[ContextDecision] = Field(default_factory=list)
    generated: GeneratedResult | None = None
    trace: list[ProcessTraceEvent] = Field(default_factory=list)
    limitations: list[Limitation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _trace_is_ordered_and_contiguous(self) -> OneShotOutput:
        for expected, event in enumerate(self.trace):
            if event.index != expected:
                raise ValueError(
                    "The process trace must be ordered and contiguous from 0."
                )
        return self

    @model_validator(mode="after")
    def _generation_is_conditional(self) -> OneShotOutput:
        if self.generated is not None and not self.quality.allows_generation:
            raise ValueError(
                "A generated result may only be returned when the quality "
                "outcome is Sufficient (generation is conditional)."
            )
        return self

    @model_validator(mode="after")
    def _generated_citations_resolve(self) -> OneShotOutput:
        if self.generated is None:
            return self
        declared = {s.source_id for s in self.sources}
        cited = {c.source_id for c in self.generated.citations}
        for section in self.generated.sections:
            cited |= {c.source_id for c in section.citations}
        unknown = sorted(cited - declared)
        if unknown:
            raise ValueError(
                "Every generated citation must resolve to a declared source; "
                f"unknown: {', '.join(unknown)}."
            )
        if self.quality.allows_generation and not self.sources:
            raise ValueError(
                "A Sufficient run must declare the sources its result was built "
                "from."
            )
        return self

    @property
    def is_generated(self) -> bool:
        return self.generated is not None

    @property
    def source_needed(self) -> bool:
        """True when the run stopped short of generation for lack of sources."""
        return self.generated is None and not self.quality.allows_generation


def schema_snapshot() -> dict[str, Any]:
    """JSON Schema for the wire + stage models, for client drift detection.

    The client mirrors these shapes as TypeScript types; comparing against this
    snapshot makes any divergence detectable instead of silent.
    """
    models: list[type[BaseModel]] = [
        SourceInput,
        RunOptions,
        RunRequest,
        SourceProvenance,
        ScanObservation,
        ScanResult,
        ReadSegment,
        ReadResult,
        SupportedTask,
        ContextBlock,
        ContextDecision,
        PreparedInput,
        CoverageDimension,
        QualityCheck,
        Citation,
        GeneratedSection,
        GeneratedResult,
        ProcessTraceEvent,
        OneShotOutput,
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "models": {m.__name__: m.model_json_schema() for m in models},
    }
