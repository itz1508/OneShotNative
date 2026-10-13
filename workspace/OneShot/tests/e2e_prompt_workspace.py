"""The Prompt Workspace end-to-end gate (features A, B, D, E, F).

Run as a script, not under pytest::

    uv run --extra test --python 3.11 python -m tests.e2e_prompt_workspace

It starts a real uvicorn System and drives the Milestone 1 scenarios from
``prd/oneshot-prompt-workspace.features.json`` over real HTTP — the same routes
the client calls. Every ``PASS`` here was executed against a live server; nothing
is asserted on the client's word.

Two scenarios are reported ``REQUIRES_PERSISTENCE`` rather than ``PASS``: the M1
store is in memory (decision D-0), so a prompt genuinely does **not** survive a
restart and immutability across a restart cannot be observed. Those lines are
printed as neither pass nor fail — a capability that was not provided must not be
reported as one.
"""

from __future__ import annotations

import threading
import time

import httpx

from tests.e2e_support import LONG_TEXT, Reporter, live_system


# --------------------------------------------------------------------------- #
# HTTP helpers — the same calls the client makes
# --------------------------------------------------------------------------- #


def create_prompt(base: str, name: str, body: str = "", variables: list | None = None) -> str:
    response = httpx.post(
        f"{base}/prompts", json={"name": name, "body": body, "variables": variables or []}
    )
    response.raise_for_status()
    return response.json()["id"]


def save_draft(base: str, prompt_id: str, body: str | None = None, name: str | None = None) -> dict:
    payload: dict = {}
    if body is not None:
        payload["body"] = body
    if name is not None:
        payload["name"] = name
    response = httpx.patch(f"{base}/prompts/{prompt_id}/draft", json=payload)
    response.raise_for_status()
    return response.json()


def publish(base: str, prompt_id: str) -> dict:
    response = httpx.post(f"{base}/prompts/{prompt_id}/versions")
    response.raise_for_status()
    return response.json()


def versions(base: str, prompt_id: str) -> list[dict]:
    return httpx.get(f"{base}/prompts/{prompt_id}/versions").json()


def get_version(base: str, prompt_id: str, number: int) -> dict:
    return httpx.get(f"{base}/prompts/{prompt_id}/versions/{number}").json()


def get_prompt(base: str, prompt_id: str) -> dict:
    return httpx.get(f"{base}/prompts/{prompt_id}").json()


def run_payload(prompt_id: str, *, version: int | None = None, variables: dict | None = None,
                run_id: str = "run_e2e", pause: bool = False) -> dict:
    return {
        "run_id": run_id,
        "intent": "placeholder — the System replaces this with the frozen body",
        "task_kind": "summarise",
        "sources": [{"id": "s1", "type": "text", "text": LONG_TEXT}],
        "options": {"pause_before_generate": pause},
        "prompt_ref": {"prompt_id": prompt_id, "version": version, "variables": variables or {}},
    }


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #


def check_a_and_b(base: str, report: Reporter) -> str:
    """A (draft) and B (versions) — and the idempotent-publish product decision."""
    prompt_id = create_prompt(base, "E2E digest")

    saved = save_draft(base, prompt_id, body="Summarise {{topic}} for {{audience}}.")
    report.check(
        "E2E_CREATE_DRAFT saving a draft creates no version",
        saved["version_count"] == 0 and versions(base, prompt_id) == [],
        f"version_count={saved['version_count']}",
    )

    v1 = publish(base, prompt_id)
    report.check("E2E_CREATE_DRAFT publish mints v1", v1["number"] == 1)

    save_draft(base, prompt_id, body="A second, different body.")
    v2 = publish(base, prompt_id)
    report.check("E2E_PUBLISH_V1_V2 v2 is minted", v2["number"] == 2)

    reread = get_version(base, prompt_id, 1)
    report.check(
        "E2E_PUBLISH_V1_V2 v1 is unchanged after v2",
        reread["body"] == "Summarise {{topic}} for {{audience}}."
        and reread["content_hash"] == v1["content_hash"],
    )
    report.check(
        "E2E_LIST_READ_VERSIONS list and read-back agree",
        [v["number"] for v in versions(base, prompt_id)] == [1, 2]
        and get_version(base, prompt_id, 2)["body"] == "A second, different body.",
    )
    report.check(
        "E2E_PUBLISH_V1_V2 latest pointer follows to v2",
        get_prompt(base, prompt_id)["latest_version"] == 2,
    )

    again = publish(base, prompt_id)
    report.check(
        "E2E_DUPLICATE_PUBLISH re-publishing the same content is idempotent",
        again["id"] == v2["id"] and len(versions(base, prompt_id)) == 2,
        f"returned {again['id']}",
    )
    return prompt_id


def check_d(base: str, report: Reporter) -> str:
    prompt_id = create_prompt(
        base,
        "E2E variables",
        body="Summarise {{topic}} for {{audience}}.",
        variables=[{"name": "audience", "default": "operators", "description": ""}],
    )
    version = publish(base, prompt_id)
    by_name = {v["name"]: v for v in version["variables"]}
    report.check(
        "E2E_DECLARE_VARIABLES declared variables and defaults are reported",
        set(by_name) == {"topic", "audience"} and by_name["audience"]["default"] == "operators",
        f"variables={sorted(by_name)}",
    )
    return prompt_id


def check_e_and_f(base: str, report: Reporter) -> None:
    """E (resolution) and F (freeze / prompt-driven runs)."""
    prompt_id = create_prompt(base, "E2E objective", body="First objective about deployments.")
    publish(base, prompt_id)
    save_draft(base, prompt_id, body="Second objective about deployments.")
    publish(base, prompt_id)

    latest = httpx.post(f"{base}/run", json=run_payload(prompt_id, run_id="run_e2e_latest"))
    latest.raise_for_status()
    latest_out = latest.json()
    report.check(
        "E2E_RESOLVE_LATEST latest binds to the newest version",
        latest_out["prompt"]["version_number"] == 2,
        f"used v{latest_out['prompt']['version_number']}",
    )

    pinned = httpx.post(
        f"{base}/run", json=run_payload(prompt_id, version=1, run_id="run_e2e_pinned")
    )
    pinned.raise_for_status()
    pinned_out = pinned.json()
    report.check(
        "E2E_RESOLVE_PINNED pinned vN binds to exactly vN",
        pinned_out["prompt"]["version_number"] == 1
        and pinned_out["prompt"]["requested_version"] == 1,
    )

    report.check(
        "E2E_FREEZE_RECORDED the run records the frozen version id and values",
        pinned_out["prompt"]["version_id"] == f"{prompt_id}#v1"
        and pinned_out["prompt"]["variables"] == {},
        f"version_id={pinned_out['prompt']['version_id']}",
    )

    # An unresolved variable is refused at admission, naming the variable.
    need = create_prompt(base, "E2E needs values", body="Summarise {{topic}} for {{audience}}.")
    publish(base, need)
    refused = httpx.post(
        f"{base}/run",
        json=run_payload(need, variables={"topic": "the release"}, run_id="run_e2e_unresolved"),
    )
    detail = refused.json().get("detail", "")
    report.check(
        "E2E_UNRESOLVED_VARIABLE refused at admission, naming the variable",
        refused.status_code == 400 and "audience" in detail and "topic" not in detail,
        f"{refused.status_code}: {detail}",
    )


def check_edit_after_admission(base: str, report: Reporter) -> None:
    """F — a publish made *while the run is held* cannot change that run.

    The run is started with ``pause_before_generate`` so it stops at the pause
    gate; a new version is published during that window; then the run is resumed.
    If the freeze were not real, the resumed run would pick up v2.
    """
    prompt_id = create_prompt(base, "E2E in flight", body="Original objective about deployments.")
    publish(base, prompt_id)

    run_id = "run_e2e_inflight"
    captured: dict = {}

    def worker() -> None:
        captured["response"] = httpx.post(
            f"{base}/run",
            json=run_payload(prompt_id, version=1, run_id=run_id, pause=True),
            timeout=60.0,
        )

    thread = threading.Thread(target=worker, name="e2e-inflight-run", daemon=True)
    thread.start()

    # Let the run reach admission (freeze) and the pause gate, then move the prompt on.
    time.sleep(2.0)
    save_draft(base, prompt_id, body="A completely different objective.")
    publish(base, prompt_id)
    httpx.post(f"{base}/runs/{run_id}/command", json={"command": "resume"}, timeout=10.0)
    thread.join(timeout=45)

    response = captured.get("response")
    if response is None:
        report.check("E2E_EDIT_AFTER_START the run returned", False, "no response")
        return
    if response.status_code != 200:
        report.check("E2E_EDIT_AFTER_START the run returned", False, response.text)
        return

    output = response.json()
    was_paused = any("Paused" in event["message"] for event in output["trace"])
    report.check(
        "E2E_EDIT_AFTER_START publishing mid-flight does not change the in-flight run",
        was_paused
        and output["prompt"]["version_number"] == 1
        and output["prepared"]["intent"] == "Original objective about deployments.",
        f"paused={was_paused}, used v{output['prompt']['version_number']}, "
        f"latest is now v{get_prompt(base, prompt_id)['latest_version']}",
    )


def check_contract(base: str, report: Reporter) -> None:
    schema = httpx.get(f"{base}/schema").json()
    health = httpx.get(f"{base}/health").json()
    report.check(
        "E2E_SCHEMA_LOCKSTEP /schema reports 1.2 with the prompt shapes",
        schema["schema_version"] == "1.2"
        and {"VariableDecl", "PromptRef", "ResolvedPromptView"} <= set(schema["models"]),
        f"schema_version={schema['schema_version']}",
    )
    report.check(
        "health declares the prompt store is not durable",
        health["prompts"]["kind"] == "in-memory" and health["prompts"]["durable"] is False,
    )


def report_persistence_boundary(base: str) -> None:
    """State the boundary plainly: two scenarios are NOT satisfiable in M1.

    This does not silently skip them — it prints the verdict the brief's own
    vocabulary provides, and it is neither a pass nor a failure.
    """
    # Demonstrate the underlying fact: a fresh store (a restart) carries nothing.
    from systems.prompts import InMemoryPromptStore

    created = httpx.post(f"{base}/prompts", json={"name": "before restart"})
    created.raise_for_status()
    restarted = InMemoryPromptStore()
    print(
        f"INFO  a fresh in-memory store holds {len(restarted.list())} prompt(s) "
        f"(the live System held one just before) — the store is per process (D-0)",
        flush=True,
    )
    print(
        "REQUIRES_PERSISTENCE  E2E_RESTART_DURABILITY — a prompt surviving a restart is "
        "not provided by the M1 in-memory store (D-0); NOT run as PASS",
        flush=True,
    )
    print(
        "REQUIRES_PERSISTENCE  E2E_IMMUTABILITY_AFTER_RESTART — immutability across a "
        "restart cannot be observed without durability (D-0); NOT run as PASS",
        flush=True,
    )


def main() -> int:
    report = Reporter("OneShot prompt-workspace E2E")
    with live_system() as base:
        check_a_and_b(base, report)
        check_d(base, report)
        check_e_and_f(base, report)
        check_edit_after_admission(base, report)
        check_contract(base, report)
        report_persistence_boundary(base)
    return report.finish()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
