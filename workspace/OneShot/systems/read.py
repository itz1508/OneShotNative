"""Read — content extraction for the v1 source types (spec §3.5, §12.1).

One reader per source type:

===============  ==========================================================
``public_url``   fetch, then dispatch on the served ``Content-Type``
``text``         the text exactly as supplied
``raw_html``     markup stripped to readable text
``pdf``          best-effort text-layer extraction (stdlib ``zlib``)
``docx``         ``word/document.xml`` parsed with the stdlib
``audio``        transcription through a provider adapter
===============  ==========================================================

Deliberate constraints:

* **No new dependency for parsing.** PDF and DOCX extraction use the standard
  library only. They are honest, bounded extractors: when they find no text they
  say so instead of guessing (plan §12.3).
* **Fetches are guarded.** Only public web addresses are fetched, so a request
  cannot make the System read its own network.
* **Nothing is invented.** An unreachable URL, an encrypted PDF, a corrupt
  archive or a provider that refuses the audio all produce a recorded
  limitation and no text — never placeholder content.

The digest and version carried on a segment are **self-reported informational
metadata** (ADR-001 D3): v1 keeps no store, so nothing here is recorded,
independently verified or protected against drift. ``reported_content_sha256``
is the digest of the text the System actually read, not of the original bytes,
and no version is ever claimed.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from ipaddress import ip_address
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from systems.schemas import (
    MAX_UPLOAD_BYTES,
    InspectionMode,
    Limitation,
    LimitationSeverity,
    ReadResult,
    ReadSegment,
    RunRequest,
    ScanOutcome,
    ScanResult,
    SourceInput,
    SourceType,
    Stage,
    TraceStatus,
    decode_upload,
)

READ_TIMEOUT_SECONDS = 20.0

#: Bounds what one source can contribute to memory. Anything beyond this is
#: recorded as dropped, never silently discarded (plan C10).
READ_HARD_CAP_CHARS = 4_000_000

TRANSCRIPTION_TIMEOUT_SECONDS = 120.0
_SPEECHMATICS_BASE = "https://asr.api.speechmatics.com/v2"
_USER_AGENT = "OneShot/1.1 (system-owned content pipeline)"

_DOCX_MAIN = "word/document.xml"
_DOCX_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class _Unreadable(Exception):
    """Content the System genuinely could not read.

    The message is written to be shown to the user verbatim: it says what
    happened and what to do next.
    """


class _TranscriberUnavailable(Exception):
    """Audio transcription is not configured, so nothing was transcribed."""


@dataclass(frozen=True, slots=True)
class _Extracted:
    text: str
    origin: str = ""


# --------------------------------------------------------------------------- #
# Stage entry point
# --------------------------------------------------------------------------- #


def read(
    request: RunRequest,
    scan_result: ScanResult,
    *,
    mode: InspectionMode,
    emit: Callable[..., None] | None = None,
) -> ReadResult:
    """Read every source the Scan stage found ``ACCESSIBLE``.

    Sources Scan already accounted for — excluded, unsupported, unreadable — are
    not read here and are not re-reported: their outcome is already on the
    record, and Process Display must not narrate the same activity twice.
    """
    by_id = {observation.source_id: observation for observation in scan_result.observations}
    segments: list[ReadSegment] = []
    limitations: list[Limitation] = []

    for source in request.sources:
        observation = by_id.get(source.id)
        if observation is None or observation.outcome is not ScanOutcome.ACCESSIBLE:
            continue

        if emit is not None:
            emit(Stage.READ, TraceStatus.STARTED, f"Reading {source.label} …", "", source.id)

        try:
            segment = _read_one(source, mode=mode)
        except _TranscriberUnavailable as exc:
            limitations.append(
                _limitation("transcription_not_configured", str(exc), source, LimitationSeverity.ERROR)
            )
            if emit is not None:
                emit(Stage.READ, TraceStatus.FAILED, str(exc), "", source.id)
            continue
        except _Unreadable as exc:
            limitations.append(
                _limitation("read_failed", str(exc), source, LimitationSeverity.ERROR)
            )
            if emit is not None:
                emit(Stage.READ, TraceStatus.FAILED, str(exc), "", source.id)
            continue

        segments.append(segment)
        if emit is not None:
            emit(
                Stage.READ,
                TraceStatus.COMPLETED,
                f"Read {source.label}: {segment.char_count:,} characters ({segment.locator}).",
                "",
                source.id,
            )

    return ReadResult(segments=segments, limitations=limitations)


def _limitation(
    code: str, message: str, source: SourceInput, severity: LimitationSeverity
) -> Limitation:
    return Limitation(code=code, message=message, severity=severity, source_id=source.id)


def _read_one(source: SourceInput, *, mode: InspectionMode) -> ReadSegment:
    kind = source.type
    if kind is SourceType.PUBLIC_URL:
        extracted = _read_public_url(source)
    elif kind is SourceType.TEXT:
        extracted = _Extracted(_normalise(source.text or ""))
    elif kind is SourceType.RAW_HTML:
        extracted = _Extracted(_normalise(_extract_html(source.text or "")), origin="markup stripped · ")
    elif kind is SourceType.FILE_PDF:
        extracted = _read_pdf(_upload_bytes(source))
    elif kind is SourceType.FILE_DOCX:
        extracted = _read_docx(_upload_bytes(source))
    elif kind is SourceType.AUDIO:
        extracted = _Extracted(
            _normalise(_transcribe(_upload_bytes(source), source)),
            origin="transcript · ",
        )
    else:  # pragma: no cover - SourceType is a closed enum
        raise _Unreadable(
            f"OneShot has no reader for {kind.value!r}, so it read nothing rather "
            "than guessing."
        )

    text = _normalise(extracted.text)
    if not text.strip():
        raise _Unreadable(
            f"No readable text was found in {source.label}. Nothing was invented "
            "to fill the gap."
        )

    dropped = 0
    truncated = False
    if len(text) > READ_HARD_CAP_CHARS:
        dropped = len(text) - READ_HARD_CAP_CHARS
        text = text[:READ_HARD_CAP_CHARS]
        truncated = True

    return ReadSegment(
        source_id=source.id,
        mode=mode,
        text=text,
        char_count=len(text),
        truncated=truncated,
        dropped_chars=dropped,
        locator=f"{extracted.origin}chars 0-{len(text):,}",
        reported_content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        reported_source_version="",
    )


def _upload_bytes(source: SourceInput) -> bytes:
    try:
        return decode_upload(source.content_base64 or "")
    except ValueError as exc:
        raise _Unreadable(str(exc)) from exc


def _normalise(text: str) -> str:
    """Tidy extracted text without altering its meaning."""
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# --------------------------------------------------------------------------- #
# Public URL
# --------------------------------------------------------------------------- #


def _read_public_url(source: SourceInput) -> _Extracted:
    url = source.url or ""
    host = urlsplit(url).hostname or ""
    if not _is_public_host(host):
        raise _Unreadable(
            f"{url} is not a public web address. OneShot reads public pages only, "
            "so it did not fetch it."
        )

    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=READ_TIMEOUT_SECONDS,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise _Unreadable(
            f"OneShot could not reach {url} ({type(exc).__name__}). Check the "
            "address, or paste the text instead."
        ) from exc

    if response.status_code >= 400:
        raise _Unreadable(
            f"{url} answered HTTP {response.status_code}, so there was nothing to "
            "read. Check the address, or paste the text instead."
        )

    if len(response.content) > MAX_UPLOAD_BYTES:
        limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise _Unreadable(
            f"{url} served more than {limit_mb} MB, which OneShot will not read."
        )

    content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()

    if content_type in ("text/html", "application/xhtml+xml", ""):
        return _Extracted(_extract_html(response.text), origin="markup stripped · ")
    if content_type.startswith("text/") or content_type in (
        "application/json",
        "application/xml",
        "application/ld+json",
    ):
        return _Extracted(response.text)
    if content_type == "application/pdf":
        return _read_pdf(response.content)
    if content_type == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ):
        return _read_docx(response.content)

    raise _Unreadable(
        f"{url} served {content_type or 'an unknown content type'}, which OneShot "
        "cannot read as text."
    )


def _is_public_host(host: str) -> bool:
    """Guard against fetching the System's own network (SSRF).

    A DNS name cannot be checked without resolving it, so the guard covers what
    is knowable here: explicit local names and private, loopback, link-local,
    reserved or unspecified addresses.
    """
    host = host.strip().lower().rstrip(".")
    if not host:
        return False
    if host in {"localhost", "localhost.localdomain"} or host.endswith(
        (".local", ".internal", ".localhost")
    ):
        return False
    try:
        address = ip_address(host)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #

_SKIPPED_ELEMENTS = frozenset(
    {"script", "style", "noscript", "template", "svg", "canvas", "iframe"}
)
_BLOCK_ELEMENTS = frozenset(
    {
        "p", "div", "br", "li", "tr", "td", "th", "section", "article", "header",
        "footer", "main", "aside", "blockquote", "pre", "figure", "figcaption",
        "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "hr", "form",
    }
)


class _HtmlText(HTMLParser):
    """Collect readable text, dropping scripts/styles and keeping block breaks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skipped = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_ELEMENTS:
            self._skipped += 1
        elif tag in _BLOCK_ELEMENTS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_ELEMENTS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_ELEMENTS:
            self._skipped = max(0, self._skipped - 1)
        elif tag in _BLOCK_ELEMENTS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipped:
            self._parts.append(data)

    @property
    def text(self) -> str:
        return "".join(self._parts)


def _extract_html(markup: str) -> str:
    parser = _HtmlText()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        # Malformed markup still yields whatever was parsed before the error;
        # the extracted text is what was actually read, so it is kept.
        pass
    return parser.text


# --------------------------------------------------------------------------- #
# PDF (stdlib text-layer extraction)
# --------------------------------------------------------------------------- #

_PDF_PAGES = re.compile(rb"/Type\s*/Page[^s]")
_PDF_STREAMS = re.compile(rb"stream\r?\n?(.*?)endstream", re.DOTALL)
_PDF_TEXTOPS = re.compile(
    rb"\((?:\\.|[^\\()])*\)\s*(?:Tj|TJ|'|\")|\[(?:[^\[\]]|\\.)*\]\s*TJ"
)
_PDF_STRINGS = re.compile(rb"\((?:\\.|[^\\()])*\)")
_PDF_ESCAPES = {
    b"n": b"\n", b"r": b"\r", b"t": b"\t",
    b"b": b"\b", b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\",
}


def _read_pdf(data: bytes) -> _Extracted:
    if not data[:1024].startswith(b"%PDF") and b"%PDF" not in data[:1024]:
        raise _Unreadable(
            "This file is not a PDF (no PDF header). It was not parsed."
        )
    if b"/Encrypt" in data:
        raise _Unreadable(
            "This PDF is protected (encrypted), so OneShot cannot open it. Remove "
            "the password and upload it again."
        )

    pages = max(1, len(_PDF_PAGES.findall(data)))
    pieces: list[str] = []
    for match in _PDF_STREAMS.finditer(data):
        raw = match.group(1)
        try:
            chunk = zlib.decompress(raw)
        except zlib.error:
            chunk = raw.strip(b"\r\n")
        piece = _pdf_text(chunk)
        if piece.strip():
            pieces.append(piece)

    text = "\n".join(pieces)
    if not text.strip():
        raise _Unreadable(
            "This PDF has no extractable text layer — it is most likely a scan or "
            "an image-only document. Visual Inspection is deferred in v1, so "
            "OneShot read nothing rather than guessing at it."
        )
    return _Extracted(text, origin=f"page(s) 1-{pages} · ")


def _pdf_text(content_stream: bytes) -> str:
    out: list[str] = []
    for match in _PDF_TEXTOPS.finditer(content_stream):
        op = match.group(0)
        strings = _PDF_STRINGS.findall(op) if op.rstrip().endswith(b"TJ") else [op[: op.rfind(b")") + 1]]
        for item in strings:
            out.append(_pdf_unescape(item[1:-1]))
        out.append("\n")
    return b"".join(out).decode("latin-1", "replace")


def _pdf_unescape(raw: bytes) -> str:
    out = bytearray()
    index = 0
    while index < len(raw):
        char = raw[index : index + 1]
        if char == b"\\" and index + 1 < len(raw):
            nxt = raw[index + 1 : index + 2]
            if nxt.isdigit():  # \ddd octal
                digits = raw[index + 1 : index + 4]
                out.append(int(digits, 8) & 0xFF)
                index += 1 + len(digits)
                continue
            out.extend(_PDF_ESCAPES.get(nxt, nxt))
            index += 2
            continue
        out.extend(char)
        index += 1
    return out.decode("latin-1", "replace")


# --------------------------------------------------------------------------- #
# DOCX (stdlib)
# --------------------------------------------------------------------------- #


def _read_docx(data: bytes) -> _Extracted:
    if data[:4] == b"\xd0\xcf\x11\xe0":
        raise _Unreadable(
            "This is a Word 97-2003 (.doc) document, which OneShot does not read. "
            "Save it as .docx and upload it again."
        )
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if _DOCX_MAIN not in names:
                raise _Unreadable(
                    "This .docx does not contain a Word document body, so there was "
                    "nothing to read."
                )
            xml = archive.read(_DOCX_MAIN)
    except zipfile.BadZipFile as exc:
        raise _Unreadable(
            "This file is not a valid .docx archive, so it was not read."
        ) from exc

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise _Unreadable(
            "This .docx is damaged — its document XML could not be parsed."
        ) from exc

    paragraphs: list[str] = []
    for paragraph in root.iter(f"{_DOCX_NS}p"):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{_DOCX_NS}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_DOCX_NS}tab":
                parts.append("\t")
            elif node.tag in (f"{_DOCX_NS}br", f"{_DOCX_NS}cr"):
                parts.append("\n")
        paragraphs.append("".join(parts))

    return _Extracted("\n".join(paragraphs), origin="document body · ")


# --------------------------------------------------------------------------- #
# Audio transcription (provider adapters)
# --------------------------------------------------------------------------- #


def transcriber_status() -> dict[str, object]:
    """What the /health endpoint can truthfully report about transcription."""
    provider = (os.environ.get("ONESHOT_TRANSCRIBER") or "").strip().lower()
    key_name = _TRANSCRIBER_KEYS.get(provider)
    configured = bool(provider and key_name and (os.environ.get(key_name) or "").strip())
    return {
        "provider": provider or None,
        "configured": configured,
        "supported": sorted(_TRANSCRIBER_KEYS),
    }


_TRANSCRIBER_KEYS = {
    "speechmatics": "SPEECHMATICS_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def _transcribe(data: bytes, source: SourceInput) -> str:
    provider = (os.environ.get("ONESHOT_TRANSCRIBER") or "").strip().lower()
    if not provider:
        raise _TranscriberUnavailable(
            "Audio transcription is not configured on this host, so the audio was "
            "not transcribed and no text was invented in its place. Set "
            "ONESHOT_TRANSCRIBER (speechmatics or openai) plus the matching API key "
            "on the System host to enable the audio source type."
        )
    if provider == "speechmatics":
        return _transcribe_speechmatics(data, source)
    if provider == "openai":
        return _transcribe_openai(data, source)
    raise _TranscriberUnavailable(
        f"ONESHOT_TRANSCRIBER is set to {provider!r}, which OneShot does not "
        "support. Use 'speechmatics' or 'openai'."
    )


def _require_key(variable: str, provider: str) -> str:
    key = (os.environ.get(variable) or "").strip()
    if not key:
        raise _TranscriberUnavailable(
            f"{variable} is not set on the System host, so {provider} could not be "
            "called and the audio was not transcribed."
        )
    return key


def _transcribe_speechmatics(data: bytes, source: SourceInput) -> str:
    """Speechmatics batch: submit a job, poll it, fetch the transcript.

    Server-side only — the raw key never leaves the System and no browser-side
    token exchange is involved (PRD: transcription is System-side).
    """
    key = _require_key("SPEECHMATICS_API_KEY", "Speechmatics")
    base = (os.environ.get("SPEECHMATICS_BASE_URL") or _SPEECHMATICS_BASE).rstrip("/")
    headers = {"Authorization": f"Bearer {key}"}
    config = {
        "type": "transcription",
        "transcription_config": {
            "language": (os.environ.get("SPEECHMATICS_LANGUAGE") or "en").strip(),
            "operating_point": "enhanced",
        },
    }
    filename = source.filename or "upload"
    mime = source.mime_type or "application/octet-stream"

    try:
        with httpx.Client(timeout=TRANSCRIPTION_TIMEOUT_SECONDS) as client:
            submit = client.post(
                f"{base}/jobs/",
                headers=headers,
                files={"data_file": (filename, data, mime)},
                data={"config": json.dumps(config)},
            )
            if submit.status_code >= 400:
                raise _Unreadable(_provider_refusal("Speechmatics", submit))
            job_id = (submit.json() or {}).get("id")
            if not job_id:
                raise _Unreadable(
                    "Speechmatics accepted the audio but returned no job id, so "
                    "there was no transcript to fetch."
                )

            deadline = time.monotonic() + TRANSCRIPTION_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                status_response = client.get(f"{base}/jobs/{job_id}", headers=headers)
                if status_response.status_code >= 400:
                    raise _Unreadable(_provider_refusal("Speechmatics", status_response))
                status = ((status_response.json() or {}).get("job") or {}).get("status")
                if status == "done":
                    break
                if status in {"rejected", "failed", "expired"}:
                    raise _Unreadable(
                        f"Speechmatics could not transcribe this audio (job status "
                        f"{status})."
                    )
                time.sleep(2.0)
            else:
                raise _Unreadable(
                    "Speechmatics did not finish transcribing this audio in time."
                )

            transcript = client.get(
                f"{base}/jobs/{job_id}/transcript",
                headers=headers,
                params={"format": "txt"},
            )
            if transcript.status_code >= 400:
                raise _Unreadable(_provider_refusal("Speechmatics", transcript))
            return transcript.text
    except httpx.HTTPError as exc:
        raise _Unreadable(
            f"OneShot could not reach Speechmatics ({type(exc).__name__}), so the "
            "audio was not transcribed."
        ) from exc


def _transcribe_openai(data: bytes, source: SourceInput) -> str:
    """OpenAI transcription, server-side only."""
    key = _require_key("OPENAI_API_KEY", "OpenAI")
    base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = (os.environ.get("ONESHOT_TRANSCRIBE_MODEL") or "whisper-1").strip()
    filename = source.filename or "upload"
    mime = source.mime_type or "application/octet-stream"

    try:
        with httpx.Client(timeout=TRANSCRIPTION_TIMEOUT_SECONDS) as client:
            response = client.post(
                f"{base}/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": (filename, data, mime)},
                data={"model": model},
            )
    except httpx.HTTPError as exc:
        raise _Unreadable(
            f"OneShot could not reach OpenAI ({type(exc).__name__}), so the audio "
            "was not transcribed."
        ) from exc

    if response.status_code >= 400:
        raise _Unreadable(_provider_refusal("OpenAI", response))
    text = (response.json() or {}).get("text")
    if not isinstance(text, str):
        raise _Unreadable("OpenAI returned no transcript text for this audio.")
    return text


def _provider_refusal(provider: str, response: httpx.Response) -> str:
    """A human sentence about a refusal — never a raw payload dump."""
    reason = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                reason = str(error.get("message") or "")
            elif isinstance(error, str):
                reason = error
            elif payload.get("message"):
                reason = str(payload["message"])
    except ValueError:
        reason = (response.text or "")[:160]

    reason = " ".join(reason.split())
    if len(reason) > 200:
        reason = reason[:200].rstrip() + "…"
    suffix = f" — {reason}" if reason else ""
    return (
        f"{provider} refused the request (HTTP {response.status_code}){suffix}"
    )
