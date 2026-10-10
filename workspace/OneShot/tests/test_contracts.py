"""Contract tests for the OneShot domain.

These exercise both positive and negative paths: a rejected request must fail
truthfully and a protected invariant must not be reachable.
"""

from __future__ import annotations

import pytest

from systems.contracts import (
    DELETION_OPERATION,
    SCOPE_OPERATIONS,
    ContractError,
    InspectionMode,
    JobCreate,
    QualityOutcome,
    SourceDriftError,
    SourceOperation,
    SourceRef,
    assert_scope_only,
    classify_operation,
)

SHA = "a" * 64


def make_job(**overrides: object) -> JobCreate:
    kwargs: dict[str, object] = {
        "objective": "Summarise the failing auth path.",
        "prompt_message_id": "msg_01",
        "conversation_id": "conv_01",
        "selected_source_ids": ("src_01", "src_02"),
        "expected_versions": {"src_01": "v1", "src_02": "v3"},
        "idempotency_key": "idem_01",
    }
    kwargs.update(overrides)
    return JobCreate(**kwargs)  # type: ignore[arg-type]


# --- source operations -------------------------------------------------------


def test_five_scope_operations_are_distinct_and_exclude_deletion() -> None:
    assert SCOPE_OPERATIONS == frozenset(
        {
            SourceOperation.INPUT_SOURCE,
            SourceOperation.ADD_SOURCE,
            SourceOperation.IMPORT_SOURCE,
            SourceOperation.REFERENCE_SOURCE,
            SourceOperation.REMOVE_SOURCE,
        }
    )
    assert DELETION_OPERATION is SourceOperation.DELETE_SOURCE
    assert DELETION_OPERATION not in SCOPE_OPERATIONS


@pytest.mark.parametrize("op", sorted(SCOPE_OPERATIONS, key=lambda o: o.value))
def test_scope_operations_do_not_delete_content(op: SourceOperation) -> None:
    assert op.is_scope_operation
    assert not op.deletes_content
    assert_scope_only(op)  # must not raise


def test_delete_requires_separate_authority() -> None:
    assert DELETION_OPERATION.deletes_content
    assert not DELETION_OPERATION.is_scope_operation
    with pytest.raises(ContractError, match="separate action"):
        assert_scope_only(DELETION_OPERATION)


def test_remove_source_never_reaches_deletion() -> None:
    remove = classify_operation("REMOVE_SOURCE")
    assert remove is SourceOperation.REMOVE_SOURCE
    assert not remove.deletes_content
    assert_scope_only(remove)


def test_unknown_operation_is_rejected_truthfully() -> None:
    with pytest.raises(ContractError) as excinfo:
        classify_operation("TELEPORT_SOURCE")
    message = str(excinfo.value)
    assert "TELEPORT_SOURCE" in message
    assert "REMOVE_SOURCE" in message  # tells the user what *is* supported


def test_classify_accepts_enum_identity() -> None:
    assert classify_operation(SourceOperation.ADD_SOURCE) is SourceOperation.ADD_SOURCE


# --- enumerated outcomes -----------------------------------------------------


def test_quality_outcomes_are_tri_state() -> None:
    assert {o.value for o in QualityOutcome} == {
        "Sufficient",
        "Incomplete",
        "Unusable",
    }


def test_inspection_modes_cover_supported_set() -> None:
    assert {m.value for m in InspectionMode} == {
        "Text",
        "Visual",
        "Files and Folders",
        "Mixed",
        "Research When Needed",
    }


# --- immutable source reference ---------------------------------------------


def test_source_ref_accepts_valid_payload() -> None:
    ref = SourceRef("src_01", "auth.py", "v1", SHA)
    assert ref.sha256 == SHA


@pytest.mark.parametrize("bad", ["", "abc", "A" * 64, "a" * 63])
def test_source_ref_rejects_bad_hash(bad: str) -> None:
    with pytest.raises(ContractError, match="content hash"):
        SourceRef("src_01", "auth.py", "v1", bad)


def test_source_ref_requires_filename() -> None:
    with pytest.raises(ContractError, match="filename"):
        SourceRef("src_01", "   ", "v1", SHA)


# --- job admission -----------------------------------------------------------


def test_job_binds_objective_to_versions() -> None:
    job = make_job()
    assert job.prompt_message_id == "msg_01"
    job.verify_sources({"src_01": "v1", "src_02": "v3"})  # unchanged -> ok


def test_start_blocked_without_saved_objective_message() -> None:
    with pytest.raises(ContractError, match="save the System objective"):
        make_job(prompt_message_id="  ")


def test_start_blocked_without_selected_sources() -> None:
    with pytest.raises(ContractError, match="at least one source"):
        make_job(selected_source_ids=(), expected_versions={})


def test_duplicate_source_selection_rejected() -> None:
    with pytest.raises(ContractError, match="more than once"):
        make_job(selected_source_ids=("src_01", "src_01"))


def test_missing_idempotency_key_rejected() -> None:
    with pytest.raises(ContractError, match="idempotency key"):
        make_job(idempotency_key="")


def test_version_map_must_match_selected_sources() -> None:
    with pytest.raises(ContractError, match="expected versions do not match"):
        make_job(expected_versions={"src_01": "v1"})  # src_02 has no version


def test_drift_is_detected_and_reported() -> None:
    job = make_job()
    with pytest.raises(SourceDriftError) as excinfo:
        job.verify_sources({"src_01": "v1", "src_02": "v4"})
    assert excinfo.value.source_ids == ("src_02",)
    assert "src_02" in str(excinfo.value)


def test_missing_source_counts_as_drift() -> None:
    job = make_job()
    with pytest.raises(SourceDriftError):
        job.verify_sources({"src_01": "v1"})  # src_02 vanished
