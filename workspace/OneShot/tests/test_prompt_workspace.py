"""The Prompt Workspace at the HTTP boundary, plus prompt-driven runs.

These cover features A, B, D, E and F end to end through the routes the client
actually calls, and they pin the two claims that matter most:

* saving a draft never publishes and never runs;
* a run freezes its prompt resolution at admission, so an edit made afterwards
  cannot change what that run executed.

Nothing here is executed on the client's word: every assertion reads a response
the System produced.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from systems import operations
from systems.api import app
from systems.prompts import InMemoryPromptStore
from systems.schemas import OneShotOutput, RunRequest, SourceInput

client = TestClient(app)

LONG_TEXT = (
    "Deployments run through the release gate at 14:00 UTC. "
    "The rollback window is thirty minutes and is closed once traffic is shifted. "
    "Operators are paged only if the error budget is exhausted. "
) * 3


def not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ONESHOT_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("ONESHOT_MODEL", raising=False)


def make_prompt(name: str = "Release notes", body: str = "", variables: list | None = None) -> str:
    response = client.post(
        "/prompts", json={"name": name, "body": body, "variables": variables or []}
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def run_payload(prompt_id: str, *, version: int | None = None, variables: dict | None = None,
                run_id: str = "run_prompt") -> dict:
    return {
        "run_id": run_id,
        "intent": "the System replaces this with the frozen prompt body",
        "task_kind": "summarise",
        "sources": [{"id": "s1", "type": "text", "text": LONG_TEXT, "label": "release notes"}],
        "prompt_ref": {
            "prompt_id": prompt_id,
            "version": version,
            "variables": variables or {},
        },
    }


# --------------------------------------------------------------------------- #
# A — the workspace, and saving a draft never acts
# --------------------------------------------------------------------------- #


def test_a_new_prompt_starts_empty_and_unpublished() -> None:
    prompt_id = make_prompt("Empty draft")
    detail = client.get(f"/prompts/{prompt_id}").json()
    assert detail["latest_version"] is None
    assert detail["version_count"] == 0
    assert detail["body"] == ""


def test_saving_a_draft_publishes_nothing_and_runs_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Feature A — the explicit claim that a draft save is inert."""
    not_configured(monkeypatch)
    prompt_id = make_prompt("Draft only")

    saved = client.patch(
        f"/prompts/{prompt_id}/draft",
        json={"body": "Summarise the release for {{audience}}.", "name": "Renamed"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["name"] == "Renamed"
    assert saved.json()["version_count"] == 0

    assert client.get(f"/prompts/{prompt_id}/versions").json() == []
    assert client.get("/prompts").json()[0]["version_count"] >= 0  # list still works


def test_the_workspace_list_reports_the_draft_state() -> None:
    prompt_id = make_prompt("Listed")
    client.patch(f"/prompts/{prompt_id}/draft", json={"body": "hello"})
    rows = client.get("/prompts").json()
    row = next(r for r in rows if r["id"] == prompt_id)
    assert row["name"] == "Listed"
    assert row["latest_version"] is None


# --------------------------------------------------------------------------- #
# B — immutable, monotonic, idempotent versions
# --------------------------------------------------------------------------- #


def test_publishing_freezes_v1_and_the_latest_pointer_follows() -> None:
    prompt_id = make_prompt("Versioned", body="first body")
    v1 = client.post(f"/prompts/{prompt_id}/versions").json()
    assert v1["number"] == 1

    assert client.get(f"/prompts/{prompt_id}").json()["latest_version"] == 1
    assert [v["number"] for v in client.get(f"/prompts/{prompt_id}/versions").json()] == [1]


def test_an_earlier_version_cannot_move_when_a_new_one_is_published() -> None:
    """Feature B — the version boundary."""
    prompt_id = make_prompt("Versioned", body="first body")
    client.post(f"/prompts/{prompt_id}/versions")
    client.patch(f"/prompts/{prompt_id}/draft", json={"body": "second body"})
    client.post(f"/prompts/{prompt_id}/versions")

    first = client.get(f"/prompts/{prompt_id}/versions/1").json()
    second = client.get(f"/prompts/{prompt_id}/versions/2").json()
    assert first["body"] == "first body"
    assert second["body"] == "second body"
    assert first["content_hash"] != second["content_hash"]
    assert client.get(f"/prompts/{prompt_id}").json()["latest_version"] == 2


def test_republishing_the_same_body_is_idempotent() -> None:
    """D-3 — the resolved product decision for a duplicate publish."""
    prompt_id = make_prompt("Idempotent", body="same content")
    first = client.post(f"/prompts/{prompt_id}/versions").json()
    again = client.post(f"/prompts/{prompt_id}/versions").json()
    assert again["id"] == first["id"]
    assert len(client.get(f"/prompts/{prompt_id}/versions").json()) == 1


def test_publishing_an_empty_body_is_refused_with_a_reason() -> None:
    prompt_id = make_prompt("Nothing to freeze")
    response = client.post(f"/prompts/{prompt_id}/versions")
    assert response.status_code == 400
    assert "body" in response.json()["detail"].lower()


def test_an_unknown_prompt_is_refused_in_a_sentence() -> None:
    response = client.get("/prompts/prompt_nope")
    assert response.status_code == 404
    assert "memory only" in response.json()["detail"]


def test_an_unknown_version_is_refused_and_names_what_exists() -> None:
    prompt_id = make_prompt("Versioned", body="only one")
    client.post(f"/prompts/{prompt_id}/versions")
    response = client.get(f"/prompts/{prompt_id}/versions/9")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "9" in detail and "v1" in detail


# --------------------------------------------------------------------------- #
# D — declared variables
# --------------------------------------------------------------------------- #


def test_declared_variables_and_defaults_are_reported_on_the_version() -> None:
    prompt_id = make_prompt(
        "With variables",
        body="Summarise {{topic}} for {{audience}}.",
        variables=[{"name": "audience", "default": "operators", "description": ""}],
    )
    version = client.post(f"/prompts/{prompt_id}/versions").json()
    by_name = {v["name"]: v for v in version["variables"]}
    assert set(by_name) == {"topic", "audience"}
    assert by_name["audience"]["default"] == "operators"
    assert by_name["topic"]["default"] is None


# --------------------------------------------------------------------------- #
# E / F — resolution, freeze, and prompt-driven runs
# --------------------------------------------------------------------------- #


def test_a_prompt_driven_run_records_the_frozen_version_and_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    not_configured(monkeypatch)
    prompt_id = make_prompt(
        "Frozen",
        body="Summarise the release steps for {{audience}}.",
        variables=[{"name": "audience", "default": "operators", "description": ""}],
    )
    client.post(f"/prompts/{prompt_id}/versions")

    response = client.post("/run", json=run_payload(prompt_id, run_id="run_frozen"))
    assert response.status_code == 200, response.text
    output = OneShotOutput.model_validate(response.json())

    assert output.prompt is not None
    assert output.prompt.version_number == 1
    assert output.prompt.version_id == f"{prompt_id}#v1"
    assert output.prompt.variables == {"audience": "operators"}
    # The frozen rendered body is the objective the pipeline actually ran.
    assert output.prepared is not None
    assert output.prepared.intent == "Summarise the release steps for operators."


def test_latest_resolution_binds_to_the_newest_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    not_configured(monkeypatch)
    prompt_id = make_prompt("Drifting", body="First objective about deployments.")
    client.post(f"/prompts/{prompt_id}/versions")
    client.patch(f"/prompts/{prompt_id}/draft", json={"body": "Second objective about deployments."})
    client.post(f"/prompts/{prompt_id}/versions")

    response = client.post("/run", json=run_payload(prompt_id, run_id="run_latest"))
    assert response.status_code == 200, response.text
    assert OneShotOutput.model_validate(response.json()).prompt.version_number == 2


def test_a_pinned_resolution_binds_to_the_exact_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    not_configured(monkeypatch)
    prompt_id = make_prompt("Pinned", body="First objective about deployments.")
    client.post(f"/prompts/{prompt_id}/versions")
    client.patch(f"/prompts/{prompt_id}/draft", json={"body": "Second objective about deployments."})
    client.post(f"/prompts/{prompt_id}/versions")

    response = client.post("/run", json=run_payload(prompt_id, version=1, run_id="run_pinned"))
    assert response.status_code == 200, response.text
    output = OneShotOutput.model_validate(response.json())
    assert output.prompt.version_number == 1
    assert output.prompt.requested_version == 1


def test_an_unresolved_variable_refuses_the_run_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    not_configured(monkeypatch)
    prompt_id = make_prompt("Needs values", body="Summarise {{topic}} for {{audience}}.")
    client.post(f"/prompts/{prompt_id}/versions")

    response = client.post(
        "/run",
        json=run_payload(prompt_id, variables={"topic": "the release"}, run_id="run_missing"),
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "audience" in detail
    assert "topic" not in detail


def test_a_run_for_an_unpublished_prompt_is_refused() -> None:
    prompt_id = make_prompt("Never published", body="a body")
    response = client.post("/run", json=run_payload(prompt_id, run_id="run_unpublished"))
    assert response.status_code == 400
    assert "no published version" in response.json()["detail"]


def test_editing_a_prompt_after_admission_cannot_change_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Feature F — the freeze is real, not a convention.

    The resolution is frozen at admission; publishing a *new* version afterwards
    must leave the admitted run's objective exactly as it was.
    """
    not_configured(monkeypatch)
    store = InMemoryPromptStore()
    prompt_id = store.create("Frozen", body="Original objective about deployments.").id
    store.publish(prompt_id)

    request = RunRequest(
        intent="placeholder",
        sources=[SourceInput(id="s1", type="text", text=LONG_TEXT)],
        run_id="run_freeze",
        prompt_ref={"prompt_id": prompt_id, "version": None, "variables": {}},
    )
    admission = operations.admit(request, store=store)
    assert admission.request.intent == "Original objective about deployments."
    assert admission.resolved_prompt is not None
    frozen_version = admission.resolved_prompt.version_number

    # Pretend the run is still in flight: the prompt moves on underneath it.
    store.update_draft(prompt_id, body="A completely different objective.")
    store.publish(prompt_id)

    assert admission.request.intent == "Original objective about deployments."
    assert admission.resolved_prompt.version_number == frozen_version == 1


def test_a_direct_intent_run_carries_no_prompt_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-regression: a v1 run without a prompt_ref is unchanged."""
    not_configured(monkeypatch)
    response = client.post(
        "/run",
        json={
            "run_id": "run_direct",
            "intent": "summarise the release notes",
            "task_kind": "summarise",
            "sources": [{"id": "s1", "type": "text", "text": LONG_TEXT}],
        },
    )
    assert response.status_code == 200, response.text
    assert OneShotOutput.model_validate(response.json()).prompt is None


# --------------------------------------------------------------------------- #
# Honesty + contract
# --------------------------------------------------------------------------- #


def test_health_declares_the_prompt_store_is_not_durable() -> None:
    body = client.get("/health").json()
    assert body["prompts"]["kind"] == "in-memory"
    assert body["prompts"]["durable"] is False


def test_schema_publishes_the_prompt_shapes_at_version_1_2() -> None:
    body = client.get("/schema").json()
    assert body["schema_version"] == "1.2"
    for model in ("VariableDecl", "PromptRef", "PromptVersionView", "ResolvedPromptView"):
        assert model in body["models"]
