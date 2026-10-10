"""Stage, pipeline and HTTP-boundary tests.

These assert the product's guarantees, not just its shapes:

* Scan classifies rather than prose-describes, and a deferred inspection mode is
  reported instead of traversed.
* Read never invents content: every failure is a recorded limitation.
* Prepare records inclusion/exclusion and keeps instructions away from content.
* Quality is only ``Sufficient`` with positive evidence.
* Generate is conditional, grounded, and truthful when no provider is configured.
* The trace records observed activity only, ordered and contiguous from 0.

No test touches the network: every outbound call is served by ``httpx.MockTransport``
or never made at all.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from systems import generator, operations
from systems import read as read_module
from systems.api import app
from systems.contracts import ContractError
from systems.generator import (
    GenerationFailed,
    GenerationUnavailable,
    _gemini_schema,
    _strict_schema,
)
from systems.prepare import classify_task, prepare
from systems.quality import MIN_CONTEXT_CHARS
from systems.quality import check as quality_check
from systems.read import read
from systems.scan import V1_INSPECTION_MODES, scan
from systems.schemas import (
    GeneratedResult,
    InspectionMode,
    OneShotOutput,
    PreparedInput,
    RunRequest,
    ScanOutcome,
    SourceInput,
    TaskKind,
    TraceStatus,
    WorkflowCommand,
    WorkflowState,
)

LONG_TEXT = (
    "The session token expires thirty minutes after it is issued. "
    "Refresh tokens last fourteen days and are rotated on every use. "
    "Revocation is immediate once an operator disables the account. "
) * 3


# --------------------------------------------------------------------------- #
# Fixtures + helpers
# --------------------------------------------------------------------------- #


def text_source(source_id: str = "s1", text: str = LONG_TEXT) -> SourceInput:
    return SourceInput(id=source_id, type="text", text=text)


def text_request(text: str = LONG_TEXT, intent: str = "summarise the expiry rules") -> RunRequest:
    return RunRequest(intent=intent, sources=[text_source(text=text)])


def pdf_bytes(body: bytes = b"BT /F1 12 Tf 72 720 Td (Hello OneShot) Tj ET") -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /Contents 4 0 R >> endobj\n"
        b"4 0 obj << /Length 44 >>\nstream\n" + body + b"\nendstream\nendobj\n"
        b"trailer << /Root 1 0 R >>\n%%EOF\n"
    )


def docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            "<w:p><w:r><w:t>OneShot docx body</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Second </w:t></w:r><w:r><w:tab/><w:t>paragraph</w:t></w:r></w:p>"
            "</w:body></w:document>",
        )
    return buffer.getvalue()


def upload(source_id: str, kind: str, data: bytes, filename: str, mime: str | None = None) -> SourceInput:
    return SourceInput(
        id=source_id,
        type=kind,
        content_base64=base64.b64encode(data).decode(),
        filename=filename,
        mime_type=mime,
    )


def client_factory(handler):
    """Patch target for ``httpx.Client`` that never opens a socket."""

    real = httpx.Client

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return real(transport=httpx.MockTransport(handler), **kwargs)

    return factory


def not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ONESHOT_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("ONESHOT_MODEL", raising=False)


def prepared_for(text: str = LONG_TEXT, intent: str = "summarise the expiry rules") -> PreparedInput:
    request = text_request(text=text, intent=intent)
    scan_result = scan(request, mode=InspectionMode.TEXT)
    read_result = read(request, scan_result, mode=InspectionMode.TEXT)
    return prepare(request, scan_result, read_result, mode=InspectionMode.TEXT)


def channel_for(run_id: str = "run_test") -> operations.RunChannel:
    loop = asyncio.new_event_loop()
    try:
        return operations.RunChannel(run_id, loop)
    finally:
        loop.close()


def execute(request: RunRequest, run_id: str = "run_test") -> OneShotOutput:
    admission = operations.admit(request.model_copy(update={"run_id": run_id}))
    return operations.execute(admission, channel_for(run_id))


def assert_contiguous(output: OneShotOutput) -> None:
    assert [event.index for event in output.trace] == list(range(len(output.trace)))


# --------------------------------------------------------------------------- #
# Scan (plan C7)
# --------------------------------------------------------------------------- #


def test_scan_classifies_supplied_text_as_accessible() -> None:
    request = text_request()
    result = scan(request)
    observation = result.observations[0]
    assert observation.outcome is ScanOutcome.ACCESSIBLE
    assert observation.reason.value == "supported"
    assert result.accessible_count == 1
    assert result.inaccessible_count == 0


def test_scan_separates_excluded_from_unreadable() -> None:
    request = RunRequest(
        intent="compare these",
        sources=[
            SourceInput(id="s1", type="text", text="a", operation="REMOVE_SOURCE"),
            SourceInput(id="s2", type="text", text="b", operation="REFERENCE_SOURCE"),
            upload("s3", "pdf", b"", "empty.pdf"),
        ],
    )
    result = scan(request)
    assert [o.outcome.value for o in result.observations] == [
        "excluded",
        "excluded",
        "unreadable",
    ]
    assert [o.reason.value for o in result.observations] == [
        "excluded_from_scope",
        "content_not_imported",
        "empty_content",
    ]
    assert result.excluded_count == 2
    assert result.accessible_count == 0


def test_v1_selects_only_text_and_mixed_inspection() -> None:
    assert V1_INSPECTION_MODES == {InspectionMode.TEXT, InspectionMode.MIXED}


def test_a_deferred_inspection_mode_is_reported_not_traversed() -> None:
    request = text_request()
    result = scan(request, mode=InspectionMode.FILES_AND_FOLDERS)
    observation = result.observations[0]
    assert observation.outcome is ScanOutcome.UNSUPPORTED
    assert observation.reason.value == "unsupported_inspection_mode"
    assert "not supported in v1" in observation.limitation.message
    assert "No source was read" in observation.detail


def test_scan_emits_an_event_per_source() -> None:
    events: list[tuple] = []
    scan(text_request(), emit=lambda *args, **kwargs: events.append((args, kwargs)))
    assert events and events[0][1]["source_id"] == "s1"


# --------------------------------------------------------------------------- #
# Read (plan C10)
# --------------------------------------------------------------------------- #


def test_read_extracts_supplied_text_with_a_locator_and_reported_digest() -> None:
    request = text_request(text="hello world")
    result = read(request, scan(request), mode=InspectionMode.TEXT)
    segment = result.segments[0]
    assert segment.text == "hello world"
    assert segment.locator.startswith("chars 0-")
    assert len(segment.reported_content_sha256) == 64
    assert segment.reported_source_version == ""  # nothing is claimed
    assert not segment.truncated


def test_read_strips_markup_and_scripts() -> None:
    request = RunRequest(
        intent="summarise this page",
        sources=[
            SourceInput(
                id="s1",
                type="raw_html",
                text=(
                    "<html><head><style>p{color:red}</style></head><body>"
                    "<h1>Title</h1><script>evil()</script><p>Body text</p>"
                    "</body></html>"
                ),
            )
        ],
    )
    segment = read(request, scan(request), mode=InspectionMode.TEXT).segments[0]
    assert "Title" in segment.text and "Body text" in segment.text
    assert "evil()" not in segment.text
    assert "color:red" not in segment.text


def test_read_extracts_a_docx_body() -> None:
    request = RunRequest(
        intent="summarise the document",
        sources=[upload("s1", "docx", docx_bytes(), "notes.docx")],
    )
    segment = read(request, scan(request), mode=InspectionMode.TEXT).segments[0]
    assert "OneShot docx body" in segment.text
    assert "Second" in segment.text and "paragraph" in segment.text
    assert segment.locator.startswith("document body")


def test_read_extracts_a_pdf_text_layer() -> None:
    request = RunRequest(
        intent="summarise the pdf",
        sources=[upload("s1", "pdf", pdf_bytes(), "report.pdf", "application/pdf")],
    )
    segment = read(request, scan(request), mode=InspectionMode.TEXT).segments[0]
    assert "Hello OneShot" in segment.text
    assert segment.locator.startswith("page(s) 1-1")


def test_an_image_only_pdf_is_reported_rather_than_guessed() -> None:
    request = RunRequest(
        intent="summarise the pdf",
        sources=[upload("s1", "pdf", pdf_bytes(b"BT /F1 12 Tf ET"), "scan.pdf", "application/pdf")],
    )
    result = read(request, scan(request), mode=InspectionMode.TEXT)
    assert not result.segments
    assert result.limitations[0].code == "read_failed"
    assert "Visual Inspection is deferred in v1" in result.limitations[0].message


def test_a_legacy_doc_is_reported_not_parsed() -> None:
    request = RunRequest(
        intent="summarise the doc",
        sources=[upload("s1", "docx", b"\xd0\xcf\x11\xe0legacy", "old.doc")],
    )
    result = read(request, scan(request), mode=InspectionMode.TEXT)
    assert "Word 97-2003" in result.limitations[0].message


def test_missing_transcription_provider_is_reported_not_invented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ONESHOT_TRANSCRIBER", raising=False)
    request = RunRequest(
        intent="summarise the recording",
        sources=[upload("s1", "audio", b"ID3fake-audio", "briefing.mp3", "audio/mpeg")],
    )
    result = read(request, scan(request), mode=InspectionMode.TEXT)
    assert not result.segments
    limitation = result.limitations[0]
    assert limitation.code == "transcription_not_configured"
    assert "no text was invented" in limitation.message
    assert limitation.source_id == "s1"


def test_transcription_uses_the_configured_provider_server_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ONESHOT_TRANSCRIBER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"text": "Spoken words become text."})

    monkeypatch.setattr(read_module.httpx, "Client", client_factory(handler))
    request = RunRequest(
        intent="summarise the recording",
        sources=[upload("s1", "audio", b"ID3fake-audio", "briefing.mp3", "audio/mpeg")],
    )
    segment = read(request, scan(request), mode=InspectionMode.TEXT).segments[0]
    assert segment.text == "Spoken words become text."
    assert segment.locator.startswith("transcript")
    # The key travels from the System to the provider — never to the browser.
    assert captured["url"] == "https://api.openai.com/v1/audio/transcriptions"
    assert captured["auth"] == "Bearer test-key"


def test_read_refuses_to_fetch_a_private_address() -> None:
    request = RunRequest(
        intent="read this",
        sources=[SourceInput(id="s1", type="public_url", url="http://127.0.0.1:8000/health")],
    )
    result = read(request, scan(request), mode=InspectionMode.TEXT)
    assert not result.segments
    assert "not a public web address" in result.limitations[0].message


def test_read_fetches_a_public_url_and_dispatches_on_content_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html><head><title>Docs</title></head><body><p>Fetch me</p>"
            b"<script>track()</script></body></html>",
        )

    monkeypatch.setattr(read_module.httpx, "Client", client_factory(handler))
    request = RunRequest(
        intent="summarise the page",
        sources=[SourceInput(id="s1", type="public_url", url="https://example.com/page")],
    )
    segment = read(request, scan(request), mode=InspectionMode.TEXT).segments[0]
    assert "Fetch me" in segment.text and "Docs" in segment.text
    assert "track()" not in segment.text


@pytest.mark.parametrize(
    ("status", "expected"),
    [(500, "HTTP 500"), (404, "HTTP 404")],
)
def test_read_reports_an_http_failure_truthfully(
    monkeypatch: pytest.MonkeyPatch, status: int, expected: str
) -> None:
    monkeypatch.setattr(
        read_module.httpx,
        "Client",
        client_factory(lambda request: httpx.Response(status, content=b"nope")),
    )
    request = RunRequest(
        intent="summarise the page",
        sources=[SourceInput(id="s1", type="public_url", url="https://example.com/missing")],
    )
    result = read(request, scan(request), mode=InspectionMode.TEXT)
    assert not result.segments
    assert expected in result.limitations[0].message


def test_read_never_reports_the_same_source_twice_when_scan_already_excluded_it() -> None:
    request = RunRequest(
        intent="compare",
        sources=[
            SourceInput(id="s1", type="text", text="a", operation="REMOVE_SOURCE"),
            text_source("s2", "hello world"),
        ],
    )
    result = read(request, scan(request), mode=InspectionMode.TEXT)
    assert [segment.source_id for segment in result.segments] == ["s2"]


# --------------------------------------------------------------------------- #
# Prepare (plan C5, C9, C10)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        ("summarise these requirements", TaskKind.EVALUATE_REQUIREMENTS),
        ("what are the acceptance criteria here", TaskKind.EVALUATE_REQUIREMENTS),
        ("explain what this config does", TaskKind.EXPLAIN_SOURCE),
        ("give me an overview", TaskKind.SUMMARISE),
        ("write a migration report", TaskKind.GENERATE),
        ("do the thing", TaskKind.UNCLASSIFIED),
    ],
)
def test_the_system_identifies_the_requested_action(intent: str, expected: TaskKind) -> None:
    kind, rationale = classify_task(intent, TaskKind.UNCLASSIFIED)
    assert kind is expected
    assert rationale


def test_an_explicit_task_kind_is_never_second_guessed() -> None:
    kind, rationale = classify_task("write a migration report", TaskKind.SUMMARISE)
    assert kind is TaskKind.SUMMARISE
    assert "declared" in rationale


def test_prepare_keeps_instructions_and_content_structurally_apart() -> None:
    prepared = prepared_for()
    block = prepared.context_blocks[0]
    assert block.trust == "untrusted_source_content"
    for instruction in prepared.instructions:
        assert instruction not in block.text
    assert prepared.intent == "summarise the expiry rules"


def test_prepare_records_inclusion_with_its_chars() -> None:
    prepared = prepared_for()
    decision = prepared.decisions[0]
    assert decision.included
    assert decision.reason.value == "included"
    assert decision.chars_included == prepared.context_chars
    assert decision.chars_dropped == 0


def test_prepare_records_truncation_and_why() -> None:
    request = RunRequest(
        intent="summarise",
        sources=[text_source(text=LONG_TEXT)],
        options={"max_chars_per_source": 200},
    )
    prepared = prepare(
        request,
        scan(request),
        read(request, scan(request), mode=InspectionMode.TEXT),
        mode=InspectionMode.TEXT,
    )
    decision = prepared.decisions[0]
    assert decision.reason.value == "included_truncated_for_budget"
    assert decision.chars_included == 200
    assert decision.chars_dropped > 0
    assert "this source's share" in decision.note


def test_prepare_records_an_excluded_source_instead_of_dropping_it_silently() -> None:
    request = RunRequest(
        intent="compare",
        sources=[
            text_source("s1", LONG_TEXT),
            SourceInput(id="s2", type="text", text="x", operation="REMOVE_SOURCE"),
        ],
    )
    scan_result = scan(request)
    prepared = prepare(
        request, scan_result, read(request, scan_result, mode=InspectionMode.MIXED),
        mode=InspectionMode.MIXED,
    )
    dropped = [d for d in prepared.decisions if not d.included]
    assert dropped and dropped[0].reason.value == "excluded_from_scope"
    assert prepared.scope == ["s1"]


def test_mixed_inspection_is_selected_for_more_than_one_source() -> None:
    request = RunRequest(
        intent="compare",
        sources=[text_source("s1", LONG_TEXT), text_source("s2", LONG_TEXT)],
    )
    assert operations.select_inspection_mode(request) is InspectionMode.MIXED
    prepared = prepare(
        request, scan(request), read(request, scan(request), mode=InspectionMode.MIXED),
        mode=InspectionMode.MIXED,
    )
    assert {block.mode for block in prepared.context_blocks} == {InspectionMode.MIXED}
    assert {block.source_id for block in prepared.context_blocks} == {"s1", "s2"}


# --------------------------------------------------------------------------- #
# Quality & Coverage Check (plan C8)
# --------------------------------------------------------------------------- #


def quality_for(request: RunRequest):
    mode = operations.select_inspection_mode(request)
    scan_result = scan(request, mode=mode)
    read_result = read(request, scan_result, mode=mode)
    prepared = prepare(request, scan_result, read_result, mode=mode)
    return quality_check(request, scan_result, read_result, prepared)


def test_quality_is_sufficient_only_with_positive_evidence() -> None:
    result = quality_for(text_request())
    assert result.outcome.value == "Sufficient"
    assert result.allows_generation
    assert all(dimension.covered for dimension in result.coverage)
    assert len(result.coverage) == 5  # evidence, not an assertion


def test_quality_is_incomplete_when_an_in_scope_source_could_not_be_read() -> None:
    request = RunRequest(
        intent="compare",
        sources=[
            text_source("s1", LONG_TEXT),
            upload("s2", "pdf", pdf_bytes(b"BT /F1 12 Tf ET"), "scan.pdf", "application/pdf"),
        ],
    )
    result = quality_for(request)
    assert result.outcome.value == "Incomplete"
    assert not result.allows_generation
    assert any("could not be read" in reason for reason in result.reasons)
    dimension = next(d for d in result.coverage if d.name == "every source in scope was read")
    assert not dimension.covered


def test_quality_is_unusable_when_nothing_could_be_read() -> None:
    request = RunRequest(
        intent="summarise",
        sources=[upload("s1", "pdf", pdf_bytes(b"BT /F1 12 Tf ET"), "scan.pdf", "application/pdf")],
    )
    result = quality_for(request)
    assert result.outcome.value == "Unusable"
    assert result.reasons


def test_quality_is_not_sufficient_when_no_supported_task_is_identified() -> None:
    result = quality_for(text_request(intent="do the thing"))
    assert result.outcome.value == "Incomplete"
    assert any("No supported task" in reason for reason in result.reasons)


def test_quality_reports_a_thin_context_with_a_limitation() -> None:
    result = quality_for(text_request(text="x" * (MIN_CONTEXT_CHARS // 2)))
    assert result.outcome.value == "Incomplete"
    assert result.limitations[0].code == "context_too_thin"


# --------------------------------------------------------------------------- #
# Generate (ADR-001 D3, plan C4)
# --------------------------------------------------------------------------- #


class StubAdapter:
    name = "stub"
    model = "stub-1"

    def __init__(self, payload: dict | None = None, error: Exception | None = None) -> None:
        self._payload = payload
        self._error = error

    def generate(self, prepared: PreparedInput, *, timeout: float) -> GeneratedResult:
        if self._error:
            raise self._error
        return GeneratedResult.model_validate(self._payload or {})


def result_payload() -> dict:
    return {
        "title": "Session expiry",
        "summary": "Tokens expire quickly.",
        "sections": [
            {
                "heading": "Expiry",
                "body": "The session token expires thirty minutes after issue.",
                "citations": [{"source_id": "s1", "locator": "chars 0-120"}],
            }
        ],
    }


def test_generate_reports_an_unconfigured_provider_truthfully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    not_configured(monkeypatch)
    with pytest.raises(GenerationUnavailable) as excinfo:
        generator.generate(prepared_for())
    assert "ONESHOT_MODEL_PROVIDER" in str(excinfo.value)
    assert "not" in str(excinfo.value)


def test_a_provider_without_its_key_is_reported_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ONESHOT_MODEL_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(GenerationUnavailable, match="GEMINI_API_KEY"):
        generator.generate(prepared_for())


def test_an_unknown_provider_is_reported_not_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ONESHOT_MODEL_PROVIDER", "mystery-cloud")
    with pytest.raises(GenerationUnavailable, match="does not support"):
        generator.generate(prepared_for())


def test_provider_status_reports_configuration_without_leaking_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ONESHOT_MODEL_PROVIDER", "openai")
    monkeypatch.setenv("ONESHOT_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    status = generator.provider_status()
    assert status["configured"] is True
    assert status["provider"] == "openai"
    assert "sk-secret-value" not in json.dumps(status)


def test_generate_returns_a_grounded_result_through_an_adapter() -> None:
    result = generator.generate(prepared_for(), adapter_override=StubAdapter(result_payload()))
    assert result.title == "Session expiry"
    assert result.sections[0].citations[0].source_id == "s1"


def test_an_ungrounded_result_is_discarded_not_partially_returned() -> None:
    payload = result_payload()
    payload["sections"][0]["citations"] = []
    with pytest.raises(Exception):
        generator.generate(prepared_for(), adapter_override=StubAdapter(payload))


def test_provider_json_that_is_not_json_is_reported() -> None:
    with pytest.raises(GenerationFailed, match="not valid JSON"):
        generator._parse("I'm afraid I can't do that.", label="stub:1")


def test_provider_json_that_fails_the_schema_is_reported_with_the_reason() -> None:
    payload = result_payload()
    payload["sections"][0]["citations"] = []
    with pytest.raises(GenerationFailed, match="discarded rather than partially returned"):
        generator._parse(json.dumps(payload), label="stub:1")


def test_strict_schema_is_self_contained_and_strict() -> None:
    schema = _strict_schema(GeneratedResult.model_json_schema())
    assert "$defs" not in schema
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert "title" not in json.dumps(schema)


def test_gemini_schema_drops_what_that_dialect_rejects() -> None:
    schema = _gemini_schema(GeneratedResult.model_json_schema())
    rendered = json.dumps(schema)
    assert "$defs" not in rendered
    assert "anyOf" not in rendered
    assert "additionalProperties" not in rendered
    assert schema["properties"]["model"] == {"type": "string", "nullable": True}


def test_openai_adapter_sends_a_strict_schema_and_parses_the_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(result_payload())}}]},
        )

    monkeypatch.setattr(generator.httpx, "Client", client_factory(handler))
    adapter = generator._OpenAIAdapter(
        api_key="sk-test", model="gpt-4o-mini", base_url="https://api.openai.com/v1"
    )
    result = adapter.generate(prepared_for(), timeout=5.0)
    assert result.title == "Session expiry"
    assert result.model == "openai:gpt-4o-mini"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["response_format"]["json_schema"]["strict"] is True
    # Untrusted content is delimited, and the instructions stay in the system slot.
    assert "<source id='s1'" in seen["body"]["messages"][1]["content"]
    assert "<source" not in seen["body"]["messages"][0]["content"]


def test_openai_adapter_retries_in_json_mode_when_the_model_refuses_the_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["response_format"]["type"])
        if len(calls) == 1:
            return httpx.Response(400, json={"error": {"message": "unsupported response_format"}})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(result_payload())}}]}
        )

    monkeypatch.setattr(generator.httpx, "Client", client_factory(handler))
    adapter = generator._OpenAIAdapter(
        api_key="sk-test", model="gpt-4o-mini", base_url="https://api.openai.com/v1"
    )
    assert adapter.generate(prepared_for(), timeout=5.0).title == "Session expiry"
    assert calls == ["json_schema", "json_object"]


def test_openai_adapter_surfaces_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        generator.httpx,
        "Client",
        client_factory(
            lambda request: httpx.Response(
                200, json={"choices": [{"message": {"refusal": "cannot help with that"}}]}
            )
        ),
    )
    adapter = generator._OpenAIAdapter(
        api_key="sk-test", model="gpt-4o-mini", base_url="https://api.openai.com/v1"
    )
    with pytest.raises(GenerationFailed, match="declined"):
        adapter.generate(prepared_for(), timeout=5.0)


def test_gemini_adapter_parses_a_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": json.dumps(result_payload())}]}}]},
        )

    monkeypatch.setattr(generator.httpx, "Client", client_factory(handler))
    adapter = generator._GeminiAdapter(
        api_key="gm-test", model="gemini-2.0-flash", base_url="https://generativelanguage.googleapis.com/v1beta"
    )
    assert adapter.generate(prepared_for(), timeout=5.0).title == "Session expiry"
    assert seen["url"].endswith("/models/gemini-2.0-flash:generateContent")
    assert seen["key"] == "gm-test"
    config = seen["body"]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"]["type"] == "object"


def test_gemini_blocked_prompt_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        generator.httpx,
        "Client",
        client_factory(
            lambda request: httpx.Response(
                200, json={"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
            )
        ),
    )
    adapter = generator._GeminiAdapter(
        api_key="gm-test", model="gemini-2.0-flash", base_url="https://example.invalid"
    )
    with pytest.raises(GenerationFailed, match="SAFETY"):
        adapter.generate(prepared_for(), timeout=5.0)


# --------------------------------------------------------------------------- #
# The pipeline (operations)
# --------------------------------------------------------------------------- #


def test_admission_mints_a_run_id_and_carries_no_job_without_identity() -> None:
    admission = operations.admit(text_request())
    assert admission.run_id.startswith("run_")
    assert admission.job is None


def test_admission_builds_the_job_contract_when_identity_is_supplied() -> None:
    request = RunRequest(
        intent="summarise",
        sources=[text_source()],
        run_id="run_given",
        conversation_id="conv_1",
        prompt_message_id="msg_1",
        idempotency_key="idem_1",
        expected_versions={"s1": "v1"},
    )
    admission = operations.admit(request)
    assert admission.run_id == "run_given"
    assert admission.job is not None
    assert admission.job.selected_source_ids == ("s1",)


def test_partial_identity_is_refused_rather_than_half_honoured() -> None:
    request = RunRequest(intent="summarise", sources=[text_source()], conversation_id="conv_1")
    with pytest.raises(ContractError, match="idempotency_key"):
        operations.admit(request)


def test_the_pipeline_reports_a_sufficient_run_it_could_not_generate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    not_configured(monkeypatch)
    output = execute(text_request())
    assert output.quality.outcome.value == "Sufficient"
    assert output.generated is None
    assert output.state is WorkflowState.EDITOR_REVIEW
    assert [limitation.code for limitation in output.limitations] == ["generator_not_configured"]
    assert_contiguous(output)
    generate_events = [e for e in output.trace if e.stage and e.stage.value == "Generate"]
    assert [e.status for e in generate_events] == [TraceStatus.STARTED, TraceStatus.FAILED]
    assert output.sources[0].provenance_status == "reported_unverified"


def test_the_pipeline_delivers_a_grounded_result(monkeypatch: pytest.MonkeyPatch) -> None:
    not_configured(monkeypatch)
    monkeypatch.setattr(
        operations, "generate",
        lambda prepared, **kwargs: GeneratedResult.model_validate(result_payload()),
    )
    output = execute(text_request())
    assert output.is_generated
    assert output.generated.sections[0].citations[0].source_id == "s1"
    assert_contiguous(output)
    assert any(
        event.stage and event.stage.value == "Generate" and event.status is TraceStatus.COMPLETED
        for event in output.trace
    )


def test_the_pipeline_never_generates_over_insufficient_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("Generate must not run")

    monkeypatch.setattr(operations, "generate", fail_if_called)
    request = RunRequest(
        intent="summarise",
        sources=[upload("s1", "pdf", pdf_bytes(b"BT /F1 12 Tf ET"), "scan.pdf", "application/pdf")],
    )
    output = execute(request)
    assert called is False
    assert output.quality.outcome.value == "Unusable"
    assert output.generated is None
    assert output.source_needed
    assert_contiguous(output)
    generate_events = [e for e in output.trace if e.stage and e.stage.value == "Generate"]
    assert [e.status for e in generate_events] == [TraceStatus.SKIPPED]
    assert "Nothing was generated rather than guessed" in generate_events[0].message


def test_the_trace_records_every_stage_and_only_observed_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    not_configured(monkeypatch)
    output = execute(text_request())
    stages = [event.stage.value for event in output.trace if event.stage]
    for stage in ("Scan", "Read", "Prepare", "Quality & Coverage Check", "Generate", "Output"):
        assert stage in stages
    assert output.trace[0].at_ms >= 0
    assert all(event.message for event in output.trace)


def test_write_output_validates_and_serializes(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    not_configured(monkeypatch)
    output = execute(text_request())
    monkeypatch.setenv("ONESHOT_OUTPUT_DIR", str(tmp_path))
    document = operations.write_output(output)
    assert OneShotOutput.model_validate_json(document).run_id == output.run_id
    assert (tmp_path / f"{output.run_id}.json").exists()


def test_a_busy_run_id_is_refused() -> None:
    async def scenario() -> None:
        registry = operations.RunRegistry()
        registry.create("run_x")
        with pytest.raises(ContractError, match="already in progress"):
            registry.create("run_x")
        assert registry.get("run_unknown") is None

    asyncio.run(scenario())


def test_pause_holds_the_pipeline_and_resume_releases_it() -> None:
    async def scenario() -> None:
        channel = operations.RunChannel("run_pause", asyncio.get_running_loop())
        trace = operations._Trace(channel)
        assert channel.state is WorkflowState.STARTING

        assert operations.apply_command(channel, WorkflowCommand.PAUSE) is WorkflowState.STARTING
        worker = asyncio.create_task(asyncio.to_thread(channel.checkpoint, trace))
        await asyncio.sleep(0.1)
        assert channel.state is WorkflowState.PAUSED

        assert operations.apply_command(channel, WorkflowCommand.RESUME) is WorkflowState.PAUSED
        await asyncio.wait_for(worker, timeout=2)
        assert channel.state is WorkflowState.PROCESS_DISPLAY
        messages = [event.message for event in trace.events]
        assert any("Pausing" in message for message in messages)
        assert any("Resumed" in message for message in messages)

    asyncio.run(scenario())


def test_workflow_transitions_are_owned_by_the_system() -> None:
    channel = channel_for("run_state")
    with pytest.raises(ContractError, match="still going"):
        operations.apply_command(channel, WorkflowCommand.FINALIZE)
    with pytest.raises(ContractError, match="still going"):
        operations.apply_command(channel, WorkflowCommand.REVIEW)

    channel.finish({"type": "output"})
    assert operations.apply_command(channel, WorkflowCommand.FINALIZE) is WorkflowState.FINALIZED
    assert operations.apply_command(channel, WorkflowCommand.REVIEW) is WorkflowState.EDITOR_REVIEW
    with pytest.raises(ContractError, match="cannot be paused"):
        operations.apply_command(channel, WorkflowCommand.PAUSE)
    with pytest.raises(ContractError, match="nothing to resume"):
        operations.apply_command(channel, WorkflowCommand.RESUME)


# --------------------------------------------------------------------------- #
# HTTP boundary
# --------------------------------------------------------------------------- #


client = TestClient(app)


def run_payload(run_id: str = "run_http", intent: str = "summarise the expiry rules") -> dict:
    return {
        "run_id": run_id,
        "intent": intent,
        "task_kind": "summarise",
        "sources": [{"id": "s1", "type": "text", "text": LONG_TEXT, "label": "session notes"}],
    }


def test_health_reports_configuration_truth(monkeypatch: pytest.MonkeyPatch) -> None:
    not_configured(monkeypatch)
    monkeypatch.delenv("ONESHOT_TRANSCRIBER", raising=False)
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["stateless"] is True
    assert body["generate"]["configured"] is False
    assert body["transcribe"]["configured"] is False
    assert body["inspection_modes_v1"] == ["Mixed", "Text"]
    assert body["workflow_states"][0] == "Starting"


def test_schema_endpoint_matches_the_contract() -> None:
    body = client.get("/schema").json()
    assert body["schema_version"] == "1.1"
    assert "PreparedInput" in body["models"]


def test_run_returns_a_complete_document(monkeypatch: pytest.MonkeyPatch) -> None:
    not_configured(monkeypatch)
    response = client.post("/run", json=run_payload())
    assert response.status_code == 200, response.text
    output = OneShotOutput.model_validate(response.json())
    assert output.schema_version == "1.1"
    assert output.quality.outcome.value == "Sufficient"
    assert output.is_generated is False  # no provider configured on this host
    assert_contiguous(output)
    assert output.sources[0].label == "session notes"


@pytest.mark.parametrize(
    "payload",
    [
        {"intent": "x", "sources": [{"type": "text", "text": "hi"}]},
        {"intent": "x", "sources": [{"id": "s1", "type": "text", "text": "hi", "operation": "DELETE_SOURCE"}]},
        {"intent": "x", "sources": []},
        {"intent": "x", "sources": [{"id": "s1", "type": "text", "text": "hi"}], "provider": "openai"},
    ],
)
def test_run_refuses_a_request_that_breaks_the_contract(payload: dict) -> None:
    assert client.post("/run", json=payload).status_code == 422


def test_the_events_endpoint_replays_the_observed_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    not_configured(monkeypatch)
    assert client.post("/run", json=run_payload("run_events")).status_code == 200
    response = client.get("/runs/run_events/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: trace" in body
    assert "event: output" in body
    assert '"schema_version": "1.1"' in body


def test_the_events_endpoint_is_truthful_about_an_unknown_run() -> None:
    response = client.get("/runs/run_nope/events")
    assert response.status_code == 404
    assert "keeps no history" in response.json()["detail"]


def test_finalize_moves_the_workflow_state(monkeypatch: pytest.MonkeyPatch) -> None:
    not_configured(monkeypatch)
    assert client.post("/run", json=run_payload("run_finalize")).status_code == 200
    response = client.post("/runs/run_finalize/command", json={"command": "finalize"})
    assert response.status_code == 200
    assert response.json()["state"] == "Finalized"


def test_an_unknown_command_is_refused() -> None:
    response = client.post("/runs/run_finalize/command", json={"command": "teleport"})
    assert response.status_code == 422


def test_pausing_a_finished_run_is_refused_with_a_human_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    not_configured(monkeypatch)
    assert client.post("/run", json=run_payload("run_pause_http")).status_code == 200
    response = client.post("/runs/run_pause_http/command", json={"command": "pause"})
    assert response.status_code == 400
    assert "already finished" in response.json()["detail"]
