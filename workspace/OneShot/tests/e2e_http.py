"""Real HTTP E2E: an HTTP client -> live System -> persisted ``output.json``.

This is not the in-process ``TestClient``: it boots a real uvicorn server on a
free port and talks to it over the network, so routing, CORS wiring, streaming
and serialization are exercised the way a deployed client exercises them.

Run::

    uv run --locked --python 3.11 python -m tests.e2e_http
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import httpx

from systems.schemas import OneShotOutput
from tests.e2e_support import LONG_TEXT, Reporter, live_system


def main() -> int:
    report = Reporter("e2e_http")

    with tempfile.TemporaryDirectory() as artifacts:
        os.environ["ONESHOT_OUTPUT_DIR"] = artifacts
        os.environ.pop("ONESHOT_MODEL_PROVIDER", None)
        os.environ.pop("ONESHOT_MODEL", None)

        with live_system() as base:
            with httpx.Client(base_url=base, timeout=60.0) as client:
                # -- service truth ------------------------------------------- #
                health = client.get("/health")
                report.check("GET /health answers 200", health.status_code == 200, str(health.status_code))
                body = health.json()
                report.check("health marks the System stateless", body.get("stateless") is True)
                report.check(
                    "health reports no model provider configured",
                    body.get("generate", {}).get("configured") is False,
                )

                schema = client.get("/schema")
                report.check("GET /schema answers 200", schema.status_code == 200)
                report.check(
                    "the published schema is 1.1",
                    schema.json().get("schema_version") == "1.2",
                )

                # -- one complete run ---------------------------------------- #
                run_id = "run_e2e_http"
                payload = {
                    "run_id": run_id,
                    "intent": "summarise the session expiry rules",
                    "sources": [
                        {"id": "s1", "type": "text", "text": LONG_TEXT, "label": "session notes"}
                    ],
                    "options": {"pause_before_generate": False},
                }
                response = client.post("/run", json=payload)
                report.check("POST /run answers 200", response.status_code == 200, response.text[:200])

                output = OneShotOutput.model_validate(response.json())
                report.check("the delivered document validates against the schema", output.schema_version == "1.2")
                report.check(
                    "quality is Sufficient for the declared context",
                    output.quality.outcome.value == "Sufficient",
                )
                report.check(
                    "the trace is ordered and contiguous from 0",
                    [event.index for event in output.trace] == list(range(len(output.trace))),
                )
                report.check(
                    "source provenance is carried through",
                    bool(output.sources) and output.sources[0].source_id == "s1",
                )

                # -- the live channel replays the observed history ----------- #
                with client.stream("GET", f"/runs/{run_id}/events") as stream:
                    report.check(
                        "the events endpoint streams text/event-stream",
                        stream.headers.get("content-type", "").startswith("text/event-stream"),
                    )
                    frames = "".join(stream.iter_text()) if stream.status_code == 200 else ""
                report.check("the stream replays trace frames", "event: trace" in frames)
                report.check("the stream closes with an end frame", "event: end" in frames)

                # -- the persisted result ------------------------------------ #
                artifact = Path(artifacts) / f"{run_id}.json"
                report.check("output.json was persisted for the run", artifact.exists(), str(artifact))
                if artifact.exists():
                    restored = OneShotOutput.model_validate_json(artifact.read_text())
                    report.check("the persisted document round-trips", restored.run_id == run_id)

                # -- a refused contract -------------------------------------- #
                refused = client.post("/run", json={"intent": "x", "sources": []})
                report.check("a contract-breaking request is refused", refused.status_code == 422)

    return report.finish()


if __name__ == "__main__":
    sys.exit(main())