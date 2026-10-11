#!/usr/bin/env python3
"""Recover Mermaid source from exported diagram PNGs — a one-off authoring tool.

NOT part of the OneShot System, and ``systems/`` never imports it.

The System's v1 contract fixes which inspection modes it may execute, and
``InspectionMode.VISUAL`` is deliberately *not* among them — v1 performs "no
image or OCR interpretation" (``systems/contracts.py``, ADR-001 D3). Nothing
here changes that. This script runs **against** the repository, as an authoring
step, turning the exported PNGs in ``diagrams/source/`` back into reviewable
``.mmd`` text. Adding vision *inside* the pipeline is a separate spec change;
see ``docs/prd/vision-capability-plan.md``.

What it does
------------
For every ``*.png`` in the input directory it asks a **self-hosted llama.cpp
multimodal server** — through the Strands Agents SDK — to transcribe the
diagram, then writes the result to ``diagrams/<name>.mmd``.

Honesty note
------------
An exported image is a *render* of its source, and rendering is not invertible.
The ``.mmd`` written here is a faithful **reconstruction** of what the model can
see, not a byte-exact copy of the original file. Treat the output as
machine-readable diagram text to be reviewed by a human, never as recovered
truth.

Egress
------
The model runs locally, so there is no API key anywhere and nothing is sent to a
third party — unless you deliberately point ``--base-url`` at a remote server, in
which case the diagram images leave this machine.

Usage
-----
::

    # 1. start a multimodal llama.cpp server  (see tools/vision/README.md)
    # 2. recover every diagram
    python tools/vision/recover_diagrams.py

    python tools/vision/recover_diagrams.py --check          # just probe the server
    python tools/vision/recover_diagrams.py --only boundary.png --force
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_INPUT_DIR = REPO_ROOT / "diagrams" / "source"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "diagrams"

#: A recovered file is only accepted if its first real line opens with one of
#: these. It is a cheap gate against the model returning prose.
_MERMAID_KEYWORDS = frozenset(
    {
        "flowchart", "graph", "sequencediagram", "classdiagram", "statediagram",
        "statediagram-v2", "erdiagram", "journey", "gantt", "pie", "mindmap",
        "timeline", "gitgraph", "quadrantchart", "requirementdiagram",
        "c4context", "c4container", "c4component", "block-beta", "sankey-beta",
        "xychart-beta", "architecture-beta", "packet-beta", "kanban",
        "radar-beta", "treemap", "zenuml",
    }
)

SYSTEM_PROMPT = (
    "You are a precise diagram transcriber. You convert an exported Mermaid "
    "diagram image back into its Mermaid source. You output Mermaid source and "
    "nothing else — no commentary, no explanation, no notes about your "
    "confidence."
)

TRANSCRIBE_PROMPT = """Transcribe the Mermaid diagram in this image into Mermaid source.

Rules:
- Reply with ONLY a single fenced ```mermaid code block containing the source.
- Preserve every node and edge label exactly as shown, including capitalisation
  and punctuation.
- Preserve the diagram type and direction, every arrow, and every edge label.
- Preserve subgraphs / containers and their nesting, if present.
- Do NOT invent nodes, edges, subgraphs or labels that are not visible.
- Do NOT summarise, tidy, or "improve" the diagram.
- If the image is not a readable diagram, reply with exactly: UNREADABLE
"""


# --------------------------------------------------------------------------- #
# Settings (host environment, matching the codebase convention)
# --------------------------------------------------------------------------- #


def _env(name: str, default: str) -> str:
    return (os.environ.get(name) or "").strip() or default


@dataclass(frozen=True, slots=True)
class Settings:
    base_url: str
    model: str
    input_dir: Path
    output_dir: Path
    timeout: float
    max_tokens: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            base_url=_env("ONESHOT_VISION_BASE_URL", "http://127.0.0.1:8080").rstrip("/"),
            model=_env("ONESHOT_VISION_MODEL", "default"),
            input_dir=Path(_env("ONESHOT_VISION_INPUT_DIR", str(DEFAULT_INPUT_DIR))),
            output_dir=Path(_env("ONESHOT_VISION_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))),
            timeout=float(_env("ONESHOT_VISION_TIMEOUT", "300")),
            max_tokens=int(_env("ONESHOT_VISION_MAX_TOKENS", "2048")),
        )


# --------------------------------------------------------------------------- #
# Response handling
# --------------------------------------------------------------------------- #


def extract_mermaid(raw: str) -> str:
    """Pull the Mermaid source out of a model reply, fences and all."""
    text = (raw or "").strip()
    if "```" in text:
        blocks = re.findall(
            r"```(?:mermaid)?[ \t]*\r?\n(.*?)```", text, re.DOTALL | re.IGNORECASE
        )
        if blocks:
            text = blocks[0]
    return text.strip()


def looks_like_mermaid(source: str) -> bool:
    """True when the first meaningful line opens with a Mermaid diagram keyword."""
    if not source or source.strip().upper() == "UNREADABLE":
        return False
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("%%"):
            continue
        token = stripped.split()[0].lower().rstrip(":")
        return token in _MERMAID_KEYWORDS
    return False


def _result_text(result: Any) -> str:
    """Best-effort text out of a Strands agent result."""
    message = getattr(result, "message", None)
    if isinstance(message, dict):
        joined = "".join(
            block.get("text", "")
            for block in message.get("content", [])
            if isinstance(block, dict)
        )
        if joined.strip():
            return joined
    return str(result)


# --------------------------------------------------------------------------- #
# Server + agent
# --------------------------------------------------------------------------- #


def probe_server(base_url: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Is a llama.cpp server answering here? Returns (ok, human-readable reason)."""
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx ships with strands-agents
        return True, ""

    last = ""
    for path in ("/health", "/v1/models"):
        try:
            response = httpx.get(f"{base_url}{path}", timeout=timeout)
            if response.status_code < 500:
                return True, ""
            last = f"{path} answered HTTP {response.status_code}"
        except Exception as exc:  # noqa: BLE001 - any failure means "not reachable"
            last = f"{path}: {type(exc).__name__}"
    return False, last


def _unreachable_message(base_url: str, reason: str) -> str:
    return (
        f"No llama.cpp server is answering at {base_url}"
        + (f" ({reason})" if reason else "")
        + ".\n\n"
        "Start one with a multimodal model before running this tool:\n\n"
        "  llama-server -m models/Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf \\\n"
        "    --mmproj models/mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf --port 8080\n\n"
        "The --mmproj file is what gives the model eyes; without it the diagram "
        "images are ignored. See tools/vision/README.md."
    )


def build_agent(settings: Settings) -> Any:
    """Construct the Strands agent bound to the local llama.cpp server."""
    try:
        from strands import Agent
        from strands.models.llamacpp import LlamaCppModel
    except ImportError as exc:
        raise SystemExit(
            "The Strands Agents SDK is not installed, so this tool cannot run.\n"
            "Install it with:\n\n"
            "  pip install -r tools/vision/requirements.txt\n"
        ) from exc

    model = LlamaCppModel(
        base_url=settings.base_url,
        timeout=settings.timeout,
        model_id=settings.model,
        params={
            "max_tokens": settings.max_tokens,
            "temperature": 0.1,
            "repeat_penalty": 1.05,
        },
    )
    # ``callback_handler=None`` keeps the SDK from streaming tokens to stdout —
    # this tool prints its own progress and nothing else.
    return Agent(model=model, system_prompt=SYSTEM_PROMPT, callback_handler=None)


def transcribe(agent: Any, png: Path) -> str:
    """Ask the model to transcribe one diagram image."""
    content = [
        {"image": {"format": "png", "source": {"bytes": png.read_bytes()}}},
        {"text": TRANSCRIBE_PROMPT},
    ]
    return extract_mermaid(_result_text(agent(content)))


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="recover_diagrams",
        description=(
            "Transcribe exported Mermaid diagram PNGs back into .mmd source using "
            "a self-hosted llama.cpp multimodal server. Runs outside the OneShot "
            "System; the pipeline's v1 contract is untouched."
        ),
    )
    parser.add_argument("--input-dir", help="where the PNGs are (default: diagrams/source)")
    parser.add_argument("--output-dir", help="where the .mmd files go (default: diagrams)")
    parser.add_argument("--base-url", help="llama.cpp server URL (default: http://127.0.0.1:8080)")
    parser.add_argument("--model", help="llama.cpp model id (default: default)")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="FILE",
        help="recover just this PNG (repeatable)",
    )
    parser.add_argument("--force", action="store_true", help="overwrite existing .mmd files")
    parser.add_argument(
        "--check",
        action="store_true",
        help="only probe the server, then exit — useful before a long run",
    )
    parser.add_argument("--dry-run", action="store_true", help="transcribe but write nothing")
    return parser.parse_args(argv)


def _resolve(settings: Settings, args: argparse.Namespace) -> Settings:
    return Settings(
        base_url=(args.base_url or settings.base_url).rstrip("/"),
        model=args.model or settings.model,
        input_dir=Path(args.input_dir) if args.input_dir else settings.input_dir,
        output_dir=Path(args.output_dir) if args.output_dir else settings.output_dir,
        timeout=settings.timeout,
        max_tokens=settings.max_tokens,
    )


def main(argv: list[str] | None = None) -> int:
    from_env = Settings.from_env()
    args = _parse_args(argv)
    settings = _resolve(from_env, args)

    ok, reason = probe_server(settings.base_url)
    if not ok:
        print(_unreachable_message(settings.base_url, reason), file=sys.stderr)
        return 2

    if args.check:
        print(f"✓ llama.cpp server is up at {settings.base_url}")
        return 0

    if not settings.input_dir.is_dir():
        print(
            f"No directory at {settings.input_dir}. Put the exported diagram PNGs "
            "there (they are the input to this recovery), or pass --input-dir.",
            file=sys.stderr,
        )
        return 2

    pngs = sorted(p for p in settings.input_dir.glob("*.png") if p.is_file())
    if args.only:
        wanted = {name.lower() for name in args.only}
        pngs = [p for p in pngs if p.name.lower() in wanted]
        missing = wanted - {p.name.lower() for p in pngs}
        for name in sorted(missing):
            print(f"! no such PNG: {name}", file=sys.stderr)

    if not pngs:
        print(
            f"Nothing to recover — no PNG files in {settings.input_dir}.\n\n"
            "Export your Mermaid diagrams to PNG, drop them in that folder, and "
            "run this again. (Rendering runs Mermaid → PNG; this tool runs the "
            "other way, so PNGs are the only valid input.)"
        )
        return 0

    settings.output_dir.mkdir(parents=True, exist_ok=True)
    print(f"→ server {settings.base_url} · model {settings.model}")
    print(f"→ {len(pngs)} diagram(s) from {settings.input_dir}\n")

    agent = build_agent(settings)
    report: list[dict[str, Any]] = []
    recovered = skipped = failed = 0

    for png in pngs:
        target = settings.output_dir / f"{png.stem}.mmd"
        if target.exists() and not args.force:
            print(f"  · {png.name} → {target.name} already exists (use --force)")
            report.append({"png": png.name, "mmd": target.name, "status": "skipped"})
            skipped += 1
            continue

        started = time.monotonic()
        try:
            source = transcribe(agent, png)
        except Exception as exc:  # noqa: BLE001 - report per file, never abort the batch
            print(f"  ✗ {png.name} — the model call failed ({type(exc).__name__}: {exc})")
            report.append({"png": png.name, "status": "failed", "reason": str(exc)})
            failed += 1
            continue
        elapsed = round(time.monotonic() - started, 1)

        if not looks_like_mermaid(source):
            print(
                f"  ✗ {png.name} — no usable Mermaid came back "
                f"({elapsed}s). Nothing was written."
            )
            report.append(
                {
                    "png": png.name,
                    "status": "failed",
                    "reason": "response was not recognisable Mermaid",
                    "raw": source[:500],
                    "seconds": elapsed,
                }
            )
            failed += 1
            continue

        entry = {
            "png": png.name,
            "mmd": target.name,
            "status": "recovered",
            "lines": len(source.splitlines()),
            "seconds": elapsed,
        }
        if args.dry_run:
            print(f"  ~ {png.name} — would write {target.name} ({len(source.splitlines())} lines)")
            entry["status"] = "dry-run"
            report.append(entry)
            continue

        target.write_text(source.rstrip() + "\n", encoding="utf-8")
        print(f"  ✓ {png.name} → {target.name} ({len(source.splitlines())} lines, {elapsed}s)")
        report.append(entry)
        recovered += 1

    if not args.dry_run:
        report_path = settings.output_dir / "recovery-report.json"
        report_path.write_text(
            json.dumps(
                {
                    "tool": "tools/vision/recover_diagrams.py",
                    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "server": settings.base_url,
                    "model": settings.model,
                    "notice": (
                        "Reconstructed from exported images by a local vision model. "
                        "Review before relying on it; this is not the original source."
                    ),
                    "results": report,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    print(f"\n  {recovered} recovered, {skipped} skipped, {failed} failed.")
    if failed:
        print(
            "  Failed diagrams were left alone rather than filled with a guess. "
            "Re-run with --force once the model reads them, or raise "
            "ONESHOT_VISION_MAX_TOKENS for very large diagrams."
        )
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
