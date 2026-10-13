"""Diagnostics for the Prompt Workspace store (features A, B, D, E).

These are contract tests: they pin the invariants the store promises, not its
implementation. If any of these fail, a caller can no longer trust that a
published version is immutable, that numbering is monotonic, or that a frozen
resolution cannot move under a running pipeline.
"""

from __future__ import annotations

import pytest

from systems.contracts import ContractError
from systems.prompts import (
    InMemoryPromptStore,
    VariableDecl,
    extract_variable_names,
)


@pytest.fixture()
def store() -> InMemoryPromptStore:
    return InMemoryPromptStore()


# --------------------------------------------------------------------------- #
# A — draft handling
# --------------------------------------------------------------------------- #


def test_create_and_read_back_a_draft(store: InMemoryPromptStore) -> None:
    asset = store.create("Weekly digest")
    store.update_draft(asset.id, body="Summarise {{topic}} for {{audience}}.")
    reloaded = store.require(asset.id)
    assert reloaded.name == "Weekly digest"
    assert reloaded.body == "Summarise {{topic}} for {{audience}}."


def test_saving_a_draft_creates_no_version(store: InMemoryPromptStore) -> None:
    """A — a draft save must never mint a version."""
    asset = store.create("Draft only")
    store.update_draft(asset.id, body="A body with no publish.")
    assert store.versions(asset.id) == []
    assert store.latest(asset.id) is None
    assert store.require(asset.id).latest_version_number is None


def test_a_prompt_needs_a_name(store: InMemoryPromptStore) -> None:
    with pytest.raises(ContractError):
        store.create("   ")


# --------------------------------------------------------------------------- #
# B — immutable, monotonic versions
# --------------------------------------------------------------------------- #


def test_publish_is_monotonic_and_latest_is_a_pointer(store: InMemoryPromptStore) -> None:
    asset = store.create("Prompt", body="first")
    v1 = store.publish(asset.id)
    store.update_draft(asset.id, body="second")
    v2 = store.publish(asset.id)

    assert (v1.number, v2.number) == (1, 2)
    assert store.latest(asset.id).number == 2  # type: ignore[union-attr]
    assert [v.number for v in store.versions(asset.id)] == [1, 2]


def test_a_published_version_cannot_move(store: InMemoryPromptStore) -> None:
    """D-2 — editing the draft after a publish leaves the version untouched."""
    asset = store.create("Prompt", body="first")
    v1 = store.publish(asset.id)
    store.update_draft(asset.id, body="completely different")
    store.publish(asset.id)

    reread = store.find_version(asset.id, 1)
    assert reread is not None
    assert reread.body == "first"
    assert reread.content_hash == v1.content_hash


def test_published_versions_are_frozen(store: InMemoryPromptStore) -> None:
    asset = store.create("Prompt", body="first")
    v1 = store.publish(asset.id)
    with pytest.raises(Exception):
        v1.body = "nope"  # type: ignore[misc]


def test_publishing_an_empty_body_is_refused(store: InMemoryPromptStore) -> None:
    asset = store.create("Empty")
    with pytest.raises(ContractError):
        store.publish(asset.id)


def test_publishing_the_same_content_is_idempotent(store: InMemoryPromptStore) -> None:
    """D-3 — re-publishing the current latest body does not mint a duplicate."""
    asset = store.create("Prompt", body="same content")
    v1 = store.publish(asset.id)
    again = store.publish(asset.id)

    assert again.id == v1.id
    assert len(store.versions(asset.id)) == 1


def test_changing_a_default_makes_a_new_version(store: InMemoryPromptStore) -> None:
    """A changed default changes what a run resolves to, so it is a new version."""
    asset = store.create("Prompt")
    store.update_draft(
        asset.id, body="Hello {{name}}", variables=[VariableDecl("name", default="world")]
    )
    v1 = store.publish(asset.id)
    store.update_draft(
        asset.id, variables=[VariableDecl("name", default="friend")]
    )
    v2 = store.publish(asset.id)
    assert v2.number == v1.number + 1


# --------------------------------------------------------------------------- #
# D — variable declaration
# --------------------------------------------------------------------------- #


def test_variables_are_extracted_in_body_order() -> None:
    assert extract_variable_names("{{a}} then {{b}} then {{a}}") == ("a", "b")


def test_declared_variables_and_defaults_are_reported(store: InMemoryPromptStore) -> None:
    asset = store.create("Prompt")
    store.update_draft(
        asset.id,
        body="Summarise {{topic}} for {{audience}}.",
        variables=[VariableDecl("topic", default="everything")],
    )
    version = store.publish(asset.id)

    by_name = {v.name: v for v in version.variables}
    assert set(by_name) == {"topic", "audience"}
    assert by_name["topic"].default == "everything"
    assert by_name["audience"].default is None


# --------------------------------------------------------------------------- #
# E — resolution and freeze
# --------------------------------------------------------------------------- #


def test_resolve_latest_binds_to_the_newest(store: InMemoryPromptStore) -> None:
    asset = store.create("Prompt", body="v1 body")
    store.publish(asset.id)
    store.update_draft(asset.id, body="v2 body {{x}}", variables=[VariableDecl("x", default="!")])
    store.publish(asset.id)

    frozen = store.resolve(asset.id, version=None, variables={})
    assert frozen.version_number == 2
    assert frozen.rendered == "v2 body !"


def test_resolve_pinned_binds_to_the_exact_version(store: InMemoryPromptStore) -> None:
    asset = store.create("Prompt", body="v1 body")
    store.publish(asset.id)
    store.update_draft(asset.id, body="v2 body")
    store.publish(asset.id)

    frozen = store.resolve(asset.id, version=1, variables={})
    assert frozen.version_number == 1
    assert frozen.rendered == "v1 body"


def test_supplied_values_win_over_defaults(store: InMemoryPromptStore) -> None:
    asset = store.create("Prompt")
    store.update_draft(
        asset.id, body="Hi {{who}}", variables=[VariableDecl("who", default="world")]
    )
    store.publish(asset.id)
    frozen = store.resolve(asset.id, version=None, variables={"who": "Ada"})
    assert frozen.variables == {"who": "Ada"}
    assert frozen.rendered == "Hi Ada"


def test_an_unresolved_variable_is_refused_by_name(store: InMemoryPromptStore) -> None:
    asset = store.create("Prompt")
    store.update_draft(asset.id, body="Summarise {{topic}} for {{audience}}.")
    store.publish(asset.id)

    with pytest.raises(ContractError) as excinfo:
        store.resolve(asset.id, version=None, variables={"topic": "tax"})
    assert "audience" in str(excinfo.value)
    assert "topic" not in str(excinfo.value)


def test_a_frozen_resolution_does_not_move(store: InMemoryPromptStore) -> None:
    """D-4 — a later publish cannot change a snapshot already taken."""
    asset = store.create("Prompt", body="original")
    store.publish(asset.id)
    frozen = store.resolve(asset.id, version=None, variables={})

    store.update_draft(asset.id, body="edited afterwards")
    store.publish(asset.id)

    assert frozen.rendered == "original"
    assert frozen.version_number == 1


def test_resolving_an_unknown_prompt_is_refused(store: InMemoryPromptStore) -> None:
    with pytest.raises(ContractError):
        store.resolve("prompt_nope", version=None, variables={})


def test_resolving_a_prompt_with_no_version_is_refused(store: InMemoryPromptStore) -> None:
    asset = store.create("Prompt", body="never published")
    with pytest.raises(ContractError):
        store.resolve(asset.id, version=None, variables={})


def test_resolving_a_missing_pinned_version_is_refused(store: InMemoryPromptStore) -> None:
    asset = store.create("Prompt", body="v1")
    store.publish(asset.id)
    with pytest.raises(ContractError) as excinfo:
        store.resolve(asset.id, version=9, variables={})
    message = str(excinfo.value)
    assert "version 9" in message
    assert "v1" in message  # the message names what *is* available


# --------------------------------------------------------------------------- #
# Honesty about the store
# --------------------------------------------------------------------------- #


def test_state_declares_the_store_is_not_durable(store: InMemoryPromptStore) -> None:
    state = store.state()
    assert state["kind"] == "in-memory"
    assert state["durable"] is False
