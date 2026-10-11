"""Scan — the entry to Source Inspection (workflow specification §3.4/§3.5).

The System enumerates **exactly** the sources the request named and classifies
each one. There is no tree walking, no recursive directory traversal, no
globbing, no symlink handling and no archive ingestion — a request cannot make
this module discover content it was not given (§12.1, and the frozen v1
inspection-mode decision).

Classification is a four-way split, because an unrepresentable state is an
unreportable one (plan C7):

* ``ACCESSIBLE`` — the System can try to read it.
* ``UNSUPPORTED`` — the System was asked to inspect in a mode v1 does not select.
* ``UNREADABLE`` — the payload is empty, oversized or malformed.
* ``EXCLUDED`` — deliberately not read: removed from scope, or registered by
  reference without importing content.

Nothing here reads content, so nothing here reports on content. Whether a source
is *reachable* is observed by :mod:`systems.read`, not guessed here.
"""

from __future__ import annotations

from collections.abc import Callable

from systems.contracts import SourceOperation
from systems.schemas import (
    InspectionMode,
    Limitation,
    LimitationSeverity,
    RunRequest,
    ScanObservation,
    ScanOutcome,
    ScanReason,
    ScanResult,
    SourceInput,
    SourceType,
    Stage,
    TraceStatus,
    decode_upload,
)

#: The inspection modes v1 selects. ``TEXT`` reads supplied textual content;
#: ``MIXED`` composes two or more v1 sources. Everything else in
#: :class:`~systems.contracts.InspectionMode` is reported as unsupported rather
#: than silently approximated (frozen v1 decision).
V1_INSPECTION_MODES: frozenset[InspectionMode] = frozenset(
    {InspectionMode.TEXT, InspectionMode.MIXED}
)

#: Operations that deliberately do not read the source's content.
_NOT_READ: dict[SourceOperation, tuple[ScanOutcome, ScanReason, str]] = {
    SourceOperation.REMOVE_SOURCE: (
        ScanOutcome.EXCLUDED,
        ScanReason.EXCLUDED_FROM_SCOPE,
        "Removed from the working scope by the request, so it was not read and "
        "nothing from it can be cited. Removing a source from scope never "
        "deletes its content.",
    ),
    SourceOperation.REFERENCE_SOURCE: (
        ScanOutcome.EXCLUDED,
        ScanReason.CONTENT_NOT_IMPORTED,
        "Registered by reference only: its content was deliberately not "
        "imported, so it was not read and nothing from it can be cited.",
    ),
}


def scan(
    request: RunRequest,
    *,
    mode: InspectionMode = InspectionMode.TEXT,
    emit: Callable[..., None] | None = None,
) -> ScanResult:
    """Classify every source in ``request``.

    ``mode`` is the inspection mode the System selected (System-owned, spec
    §3.5). A mode v1 does not select yields a truthful ``UNSUPPORTED`` result for
    every source — and **no traversal of any kind occurs**.
    """
    if mode not in V1_INSPECTION_MODES:
        return _unsupported_mode(request, mode, emit=emit)
    observations = [_observe(source, emit=emit) for source in request.sources]
    return ScanResult(observations=observations)


def scan_one_source(
    source: SourceInput, *, emit: Callable[..., None] | None = None
) -> ScanObservation:
    """Classify a single source (used by the pipeline and by tests)."""
    return _observe(source, emit=emit)


def _unsupported_mode(
    request: RunRequest,
    mode: InspectionMode,
    *,
    emit: Callable[..., None] | None = None,
) -> ScanResult:
    detail = (
        f"{mode.value} Inspection is not supported in v1. v1 selects Text "
        "Understanding and Mixed-Source Inspection only. No source was read and "
        "no part of any source was inspected."
    )
    limitation = Limitation(
        code="unsupported_inspection_mode",
        message=detail,
        severity=LimitationSeverity.WARNING,
    )
    observations = [
        ScanObservation(
            source_id=source.id,
            type=source.type,
            label=source.label,
            outcome=ScanOutcome.UNSUPPORTED,
            reason=ScanReason.UNSUPPORTED_INSPECTION_MODE,
            detail=detail,
            limitation=limitation,
        )
        for source in request.sources
    ]
    if emit is not None:
        emit(Stage.SCAN, TraceStatus.FAILED, detail)
    return ScanResult(observations=observations)


def _observe(
    source: SourceInput, *, emit: Callable[..., None] | None = None
) -> ScanObservation:
    """Classify one source from what the System can observe without reading it."""
    if source.operation in _NOT_READ:
        outcome, reason, detail = _NOT_READ[source.operation]
        observation = ScanObservation(
            source_id=source.id,
            type=source.type,
            label=source.label,
            outcome=outcome,
            reason=reason,
            detail=detail,
            size_bytes=_declared_size(source),
            limitation=Limitation(
                code=reason.value,
                message=f"{source.label}: {detail}",
                severity=LimitationSeverity.INFO,
                source_id=source.id,
            ),
        )
        _announce(observation, emit)
        return observation

    empty = _empty_payload(source)
    if empty is not None:
        observation = ScanObservation(
            source_id=source.id,
            type=source.type,
            label=source.label,
            outcome=ScanOutcome.UNREADABLE,
            reason=empty[0],
            detail=empty[1],
            size_bytes=_declared_size(source),
            limitation=Limitation(
                code=empty[0].value,
                message=f"{source.label}: {empty[1]}",
                severity=LimitationSeverity.ERROR,
                source_id=source.id,
            ),
        )
        _announce(observation, emit)
        return observation

    size = _declared_size(source)
    observation = ScanObservation(
        source_id=source.id,
        type=source.type,
        label=source.label,
        outcome=ScanOutcome.ACCESSIBLE,
        reason=ScanReason.SUPPORTED,
        detail=_accessible_detail(source, size),
        size_bytes=size,
    )
    _announce(observation, emit)
    return observation


def _empty_payload(source: SourceInput) -> tuple[ScanReason, str] | None:
    """Return a truthful reason when the payload cannot be read at all.

    Uploads were already validated at the wire boundary, so anything caught here
    is reported as observed — never silently repaired.
    """
    if source.type in (SourceType.TEXT, SourceType.RAW_HTML):
        if not (source.text or "").strip():
            return ScanReason.EMPTY_CONTENT, "The supplied text is empty."
        return None

    if source.type is SourceType.PUBLIC_URL:
        return None  # well-formedness is enforced on the wire; reachability is Read's job

    if not (source.content_base64 or "").strip():
        # A present-but-empty upload: reported as empty rather than decoded into a
        # malformed error, which is what the wire lets through for Scan to classify.
        return ScanReason.EMPTY_CONTENT, "The uploaded file is empty."

    try:
        payload = decode_upload(source.content_base64 or "")
    except ValueError as exc:
        return ScanReason.MALFORMED_UPLOAD, str(exc)
    if not payload:
        return ScanReason.EMPTY_CONTENT, "The uploaded file is empty."
    return None


def _declared_size(source: SourceInput) -> int | None:
    """The size the System can actually observe for this source."""
    if source.type in (SourceType.TEXT, SourceType.RAW_HTML):
        return len((source.text or "").encode("utf-8"))
    if source.type is SourceType.PUBLIC_URL:
        return None  # nothing is fetched during Scan, so no size is known yet
    try:
        return len(decode_upload(source.content_base64 or ""))
    except ValueError:
        return None


def _accessible_detail(source: SourceInput, size: int | None) -> str:
    if source.type in (SourceType.TEXT, SourceType.RAW_HTML):
        return f"Supplied text, {size:,} bytes as given."
    if source.type is SourceType.PUBLIC_URL:
        return f"Public URL {source.url} — not fetched yet; reachability is observed when it is read."
    if source.type is SourceType.AUDIO:
        return (
            f"Uploaded audio, {size:,} bytes. It will be read only if a "
            "transcription provider is configured on this host."
        )
    return f"Uploaded {source.type.value}, {size:,} bytes."


def _announce(
    observation: ScanObservation, emit: Callable[..., None] | None
) -> None:
    if emit is None:
        return
    status = (
        TraceStatus.INFO
        if observation.outcome is ScanOutcome.ACCESSIBLE
        else TraceStatus.SKIPPED
    )
    emit(
        Stage.SCAN,
        status,
        f"Scanned {observation.label}: {observation.outcome.value} "
        f"({observation.reason.value}).",
        observation.detail,
        source_id=observation.source_id,
    )