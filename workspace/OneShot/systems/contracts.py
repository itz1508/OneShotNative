"""OneShot - system-owned responsibility contracts.

Pure standard library: no framework, provider, or storage dependency is imported
here, so the domain can be reasoned about and tested in isolation.

Derived from the accepted responsibility specification:

* the five scope source operations are distinct from deletion authority;
* quality outcomes are tri-state and must never overclaim;
* job admission binds an immutable, saved System objective to explicit source
  versions and an idempotency key.

Scope note for v1 (ADR-001 D3 / workspace-context plan D3)
---------------------------------------------------------
v1 is **stateless**: there is no store, so nothing here may be read as evidence
that a source version or content hash was *recorded*, *independently verified*,
or *protected against drift*. ``JobCreate.verify_sources()`` and
:class:`SourceDriftError` remain the contract for a later, persistence-enabled
scope and are deliberately **not wired into the v1 runtime path** — the v1
pipeline never calls drift verification and never implies that it ran. Hash and
version values that do appear in a v1 result are self-reported informational
metadata (see ``reported_content_sha256`` / ``reported_source_version`` in
:mod:`systems.schemas`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

__all__ = [
    "ContractError",
    "SourceDriftError",
    "SourceOperation",
    "SCOPE_OPERATIONS",
    "DELETION_OPERATION",
    "classify_operation",
    "assert_scope_only",
    "InspectionMode",
    "QualityOutcome",
    "SourceRef",
    "JobCreate",
]


class ContractError(ValueError):
    """An inbound request is invalid.

    The message is written to be shown to the user verbatim: it says what is
    wrong and what to do next, never a raw code or stack trace.
    """


class SourceOperation(str, Enum):
    """A requested source action.

    ``DELETE_SOURCE`` is modelled because it exists as a distinct decision, but
    it is **not** one of the five scope operations: detaching a source from the
    current scope must never delete underlying content.
    """

    INPUT_SOURCE = "INPUT_SOURCE"
    ADD_SOURCE = "ADD_SOURCE"
    IMPORT_SOURCE = "IMPORT_SOURCE"
    REFERENCE_SOURCE = "REFERENCE_SOURCE"
    REMOVE_SOURCE = "REMOVE_SOURCE"
    DELETE_SOURCE = "DELETE_SOURCE"

    @property
    def is_scope_operation(self) -> bool:
        """True for the five operations that act on the current working scope."""
        return self is not SourceOperation.DELETE_SOURCE

    @property
    def deletes_content(self) -> bool:
        """Only an explicit deletion carries deletion authority."""
        return self is SourceOperation.DELETE_SOURCE


SCOPE_OPERATIONS: frozenset[SourceOperation] = frozenset(
    op for op in SourceOperation if op.is_scope_operation
)
DELETION_OPERATION = SourceOperation.DELETE_SOURCE


def classify_operation(raw: str | SourceOperation) -> SourceOperation:
    """Resolve a requested source operation.

    Raises ``ContractError`` for an unknown operation so the caller can emit an
    explicit unresolved-operation result instead of guessing.
    """
    if isinstance(raw, SourceOperation):
        return raw
    try:
        return SourceOperation(raw)
    except ValueError as exc:
        supported = ", ".join(op.value for op in SourceOperation)
        raise ContractError(
            f"Unsupported source operation {raw!r}. Supported operations are: "
            f"{supported}."
        ) from exc


def assert_scope_only(operation: SourceOperation) -> None:
    """Guard: refuse to route deletion through a scope-removal request."""
    if operation.deletes_content:
        raise ContractError(
            "Removing a source from the working scope never deletes the "
            "underlying content. Deletion is a separate action that requires its "
            "own explicit authorization."
        )


class InspectionMode(str, Enum):
    """The inspection-mode taxonomy of the accepted workflow specification (§3.5).

    All five modes are System-owned responsibilities. **Which of them v1
    selects is a separate, frozen decision:**

    * **Selected in v1:** :attr:`TEXT` and :attr:`MIXED`. ``TEXT`` reads textual
      content supplied through an accepted v1 input path; ``MIXED`` composes two
      or more v1 sources into one inspection and preserves each source's
      provenance instead of flattening them.
    * **In the taxonomy but never selected in v1:** :attr:`VISUAL`,
      :attr:`FILES_AND_FOLDERS` and :attr:`RESEARCH_WHEN_NEEDED`. They are kept
      because the governing specification defines them — removing them would
      contradict §3.5 — but v1 must not implement or traverse them: no folder or
      recursive tree walking, no ignore/exclusion rules, no symlink handling, no
      image or OCR interpretation, and no Research call (§5 — crawling,
      extraction and collection are Researcher-owned and its documentation is
      still pending).

    ``MIXED`` is a *composition* mode: it combines source types that are already
    supported, and never expands support to a deferred mode or source type.
    """

    TEXT = "Text"
    VISUAL = "Visual"
    FILES_AND_FOLDERS = "Files and Folders"
    MIXED = "Mixed"
    RESEARCH_WHEN_NEEDED = "Research When Needed"


class QualityOutcome(str, Enum):
    """The accepted coverage outcomes. Never upgraded by structural checks alone."""

    SUFFICIENT = "Sufficient"
    INCOMPLETE = "Incomplete"
    UNUSABLE = "Unusable"


_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class SourceRef:
    """An immutable pointer to an original source at a specific version."""

    source_id: str
    filename: str
    version: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ContractError("A source id is required.")
        if not self.filename.strip():
            raise ContractError("An original source filename is required.")
        if not self.version.strip():
            raise ContractError("A source version is required.")
        if not _SHA256.match(self.sha256):
            raise ContractError(
                "A source content hash (64 lowercase hex characters) is required."
            )


class SourceDriftError(ContractError):
    """A selected source no longer matches the version the job was admitted with."""

    def __init__(self, source_ids: Sequence[str]) -> None:
        self.source_ids: tuple[str, ...] = tuple(source_ids)
        names = ", ".join(self.source_ids)
        super().__init__(
            "The selected source(s) changed since the objective was saved: "
            f"{names}. Re-select the current versions and start again."
        )


@dataclass(frozen=True, slots=True)
class JobCreate:
    """An admitted job bound to an immutable, saved System objective.

    **Not part of the v1 runtime path (ADR-001 D3).** v1 is stateless, so a run
    never constructs a job, never calls :meth:`verify_sources`, and never claims
    drift protection. This class is kept verbatim as the contract for a
    persistence-enabled scope; in v1 the only bridge that can build one is
    ``systems.schemas.RunRequest.to_job_create()``, which has **no runtime
    caller** in the v1 pipeline.
    """


    objective: str
    prompt_message_id: str
    conversation_id: str
    selected_source_ids: tuple[str, ...]
    expected_versions: Mapping[str, str]
    idempotency_key: str

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ContractError("An objective is required before starting.")
        if not self.prompt_message_id.strip():
            raise ContractError(
                "Start blocked: save the System objective message before starting, "
                "then start the job again."
            )
        if not self.conversation_id.strip():
            raise ContractError("A conversation is required before starting.")
        if not self.selected_source_ids:
            raise ContractError("Select at least one source before starting.")
        if len(set(self.selected_source_ids)) != len(self.selected_source_ids):
            raise ContractError("A source was selected more than once.")
        if not self.idempotency_key.strip():
            raise ContractError("An idempotency key is required to start a job.")

        missing = [s for s in self.selected_source_ids if s not in self.expected_versions]
        extra = [s for s in self.expected_versions if s not in self.selected_source_ids]
        if missing or extra:
            raise ContractError(
                "The expected versions do not match the selected sources."
            )

    def verify_sources(self, current_versions: Mapping[str, str]) -> None:
        """Confirm every selected source still matches its expected version.

        Raises ``SourceDriftError`` when a source has moved or disappeared, so the
        operator is told the truth rather than run against stale content.
        """
        drifted = [
            source_id
            for source_id in self.selected_source_ids
            if current_versions.get(source_id) != self.expected_versions[source_id]
        ]
        if drifted:
            raise SourceDriftError(drifted)
