"""Quality & Coverage Check — the decision rule (spec §3.7, plan C8).

The rule, stated once and implemented once:

``Sufficient``
    Requires **positive evidence**, not merely the absence of recorded problems:
    every coverage dimension is covered, at least :data:`MIN_CONTEXT_CHARS` of
    readable content exists, each in-scope source was actually read, a supported
    task was identified, and a context was prepared from real text.

``Incomplete``
    Some evidence exists but coverage is not complete — a source in scope could
    not be read, the context is too thin, or no supported task could be
    identified. The run stops short of generation and says what to fix.

``Unusable``
    Nothing usable came back: no accessible source, or no readable text at all.

The outcome is never upgraded by structural checks alone: a run whose sources
are merely *present* is not sufficient. Every dimension is recorded on the
:class:`~systems.schemas.QualityCheck`, so "Sufficient" is always backed by
checkable evidence.
"""

from __future__ import annotations

from collections.abc import Callable

from systems.contracts import QualityOutcome
from systems.schemas import (
    CoverageDimension,
    Limitation,
    LimitationSeverity,
    PreparedInput,
    QualityCheck,
    ReadResult,
    RunRequest,
    ScanOutcome,
    ScanResult,
    Stage,
    TraceStatus,
)

#: Readable text below this is too thin to build a result on.
MIN_CONTEXT_CHARS = 200


def check(
    request: RunRequest,
    scan_result: ScanResult,
    read_result: ReadResult,
    prepared: PreparedInput,
    *,
    emit: Callable[..., None] | None = None,
) -> QualityCheck:
    """Run the coverage check and return the recorded outcome."""
    labels = {source.id: source.label for source in request.sources}

    accessible = [o.source_id for o in scan_result.observations if o.outcome is ScanOutcome.ACCESSIBLE]
    read_ids = set(read_result.read_source_ids)
    unread = [source_id for source_id in accessible if source_id not in read_ids]
    readable_chars = sum(
        segment.char_count
        for segment in read_result.segments
        if segment.source_id in read_ids
    )

    dimensions = [
        CoverageDimension(
            name="sources accessible",
            covered=bool(accessible),
            detail=(
                f"{len(accessible)} of {len(scan_result.observations)} sources were "
                f"accessible ({scan_result.unsupported_count} unsupported, "
                f"{scan_result.unreadable_count} unreadable, "
                f"{scan_result.excluded_count} excluded)."
            ),
        ),
        CoverageDimension(
            name="content readable",
            covered=readable_chars >= MIN_CONTEXT_CHARS,
            detail=(
                f"{readable_chars:,} characters were read "
                f"(the minimum for a sufficient context is {MIN_CONTEXT_CHARS:,})."
            ),
        ),
        CoverageDimension(
            name="task supportable",
            covered=prepared.supported_task.supported,
            detail=prepared.supported_task.statement,
        ),
        CoverageDimension(
            name="context prepared",
            covered=prepared.context_available and prepared.context_chars > 0,
            detail=(
                f"{prepared.context_chars:,} characters were prepared from "
                f"{len(prepared.context_blocks)} of {len(request.sources)} sources."
            ),
        ),
        CoverageDimension(
            name="every source in scope was read",
            covered=bool(accessible) and not unread,
            detail=(
                "Every accessible source was read."
                if not unread
                else "Not read: " + ", ".join(labels.get(s, s) for s in unread)
            ),
        ),
    ]

    uncovered = [d for d in dimensions if not d.covered]
    reasons: list[str] = []
    limitations: list[Limitation] = []

    if not accessible:
        reasons.append(
            "None of the supplied sources could be read, so there was nothing to "
            "work from."
        )
    elif not read_ids or readable_chars == 0:
        reasons.append(
            "Every accessible source was tried but no readable text came back, so "
            "there was nothing to work from."
        )
    else:
        if unread:
            names = ", ".join(labels.get(s, s) for s in unread)
            reasons.append(
                f"These sources are in scope but could not be read: {names}. Remove "
                "them from the scope or supply them again, then run once more."
            )
        if not prepared.supported_task.supported:
            reasons.append(
                "No supported task could be identified from the objective, so the "
                "sources cannot be said to support one."
            )
        if not dimensions[3].covered:
            reasons.append(
                "No usable context was prepared from the sources — check the context "
                "budget and the source sizes."
            )
        if not dimensions[1].covered and readable_chars > 0:
            limitations.append(
                Limitation(
                    code="context_too_thin",
                    message=(
                        f"Only {readable_chars:,} characters of readable text were "
                        f"found; at least {MIN_CONTEXT_CHARS:,} are needed before a "
                        "result can be built on them."
                    ),
                    severity=LimitationSeverity.WARNING,
                )
            )
            reasons.append(
                f"Only {readable_chars:,} characters of readable text were found — "
                "too thin to build a result on. Add or enlarge a source."
            )

    if not accessible or not read_ids or readable_chars == 0:
        outcome = QualityOutcome.UNUSABLE
    elif uncovered:
        outcome = QualityOutcome.INCOMPLETE
    else:
        outcome = QualityOutcome.SUFFICIENT
        reasons.append(
            "Every source in scope was read and a context was prepared from it, "
            "with a supported task identified."
        )

    result = QualityCheck(
        outcome=outcome,
        reasons=reasons,
        limitations=limitations,
        coverage=dimensions,
    )

    if emit is not None:
        for dimension in dimensions:
            emit(
                Stage.QUALITY,
                TraceStatus.INFO,
                f"{dimension.name}: {'covered' if dimension.covered else 'not covered'}.",
                dimension.detail,
            )
        emit(
            Stage.QUALITY,
            TraceStatus.COMPLETED if outcome is QualityOutcome.SUFFICIENT else TraceStatus.FAILED,
            f"Quality & Coverage Check: {outcome.value}.",
            " ".join(reasons),
        )
    return result
