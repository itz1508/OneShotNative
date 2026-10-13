"""E2E for the model-provider path against a **local protocol responder**.

.. warning::

   The responder below speaks the OpenAI chat-completions *protocol* so the whole
   provider code path (selection, key handling, request shape, response parsing,
   grounding validation, attribution) is exercised over real HTTP. It is **not** a
   real OpenAI, Gemini or Ollama model, and passing this gate is **not** evidence
   of semantic quality. A real-provider run remains required to claim that.

Run::

    uv run --locked --python 3.11 python -m tests.e2e_multi_provider_http
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import httpx

from systems.schemas import OneShotOutput
from tests.e2e_support import GROUNDED_RESULT, LONG_TEXT, Reporter, free_port, live_system


class _Responder(BaseHTTPRequestHandler):
    """Answers one OpenAI-protocol POST with a grounded, schema-valid payload."""

    calls = 0

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        type(self).calls += 1
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(length)
        payload = {"choices": [{"message": {"content": json.dumps(GROUNDED_RESULT)}}]}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args) -> None:  # silence the default stderr spew
        return


def main() -> int:
    report = Reporter("e2e_multi_provider_http")

    responder_port = free_port()
    responder = ThreadingHTTPServer(("127.0.0.1", responder_port), _Responder)
    Thread(target=responder.serve_forever, daemon=True).start()

    os.environ["ONESHOT_MODEL_PROVIDER"] = "openai"
    os.environ["ONESHOT_MODEL"] = "local-responder"
    os.environ["OPENAI_API_KEY"] = "local-responder-key"
    os.environ["OPENAI_BASE_URL"] = f"http://127.0.0.1:{responder_port}/v1"

    try:
        with tempfile.TemporaryDirectory() as artifacts:
            os.environ["ONESHOT_OUTPUT_DIR"] = artifacts
            with live_system() as base:
                with httpx.Client(base_url=base, timeout=60.0) as client:
                    health = client.get("/health").json()
                    report.check(
                        "health reports the provider configured",
                        health["generate"]["configured"] is True,
                    )
                    report.check(
                        "health names the provider without leaking the key",
                        health["generate"]["provider"] == "openai"
                        and "local-responder-key" not in json.dumps(health),
                    )

                    response = client.post(
                        "/run",
                        json={
                            "run_id": "run_e2e_provider",
                            "intent": "summarise the session expiry rules",
                            "sources": [
                                {
                                    "id": "s1",
                                    "type": "text",
                                    "text": LONG_TEXT,
                                    "label": "session notes",
                                }
                            ],
                            "options": {"pause_before_generate": False},
                        },
                    )
                    report.check("POST /run answers 200", response.status_code == 200, response.text[:200])

                    output = OneShotOutput.model_validate(response.json())
                    report.check("the run produced a grounded result", output.is_generated is True)
                    if output.generated is not None:
                        report.check(
                            "the result cites its source",
                            output.generated.sections[0].citations[0].source_id == "s1",
                        )
                        report.check(
                            "the result attributes its model",
                            output.generated.model == "openai:local-responder",
                            output.generated.model,
                        )
                    report.check(
                        "the provider responder was actually called",
                        _Responder.calls >= 1,
                        f"calls={_Responder.calls}",
                    )
                    report.check(
                        "output.json was persisted for the provider run",
                        (Path(artifacts) / "run_e2e_provider.json").exists(),
                    )
    finally:
        responder.shutdown()
        for key in ("ONESHOT_MODEL_PROVIDER", "ONESHOT_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
            os.environ.pop(key, None)

    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
