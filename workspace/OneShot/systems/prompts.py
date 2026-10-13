"""Prompt Workspace — prompt assets, immutable versions, and an in-memory store.

What this module owns
---------------------
A **prompt asset** is a named prompt. It carries a mutable **draft** (name, body,
declared variables) that a user edits freely, and a sequence of **published
versions** that never change once written. A version is *immutable by
construction* — it is a frozen dataclass created exactly once, so no code path can
mutate it after the fact — and versions are numbered monotonically from 1. The
asset's ``latest_version_number`` is a **pointer**: publishing moves the pointer,
it never rewrites a version.

Honesty about the store (ADR-001 D3 / M1 D-0)
---------------------------------------------
This is the **in-memory** store for Milestone 1. It is deliberately the same shape
as the existing :class:`~systems.operations.RunRegistry`: transport state that
lives for the lifetime of the running System and **not a database**. A restart
starts with an empty workspace, nothing here is written to disk, and nothing is
read back across processes. The store sits behind the :class:`PromptStore`
protocol so a durable backend can replace it later without touching the routes or
the client — but **until that swap happens, no durability may be claimed
anywhere**: not in the UI, not in ``/health``, not in the docs.

Pure standard library
---------------------
Like :mod:`systems.contracts`, this module imports no framework, provider or
storage dependency, so the domain can be reasoned about and unit-tested in
isolation. The HTTP wire shapes live in :mod:`systems.schemas`; the routes that
join the two live in :mod:`systems.api`.

Two rules encoded here
----------------------
* **D-2 — immutability by construction.** A :class:`PromptVersion` is frozen and
  created once; the store only ever appends. ``latest`` is a pointer, never a copy.
* **D-3 — idempotent publish.** Publishing a body whose content hash equals the
  prompt's current latest version returns that existing version instead of minting
  a duplicate number. A new version number is minted only for genuinely new
  content, which keeps "publish v1, publish v2" deterministic for tests.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from systems.contracts import ContractError

__all__ = [
    "VariableDecl",
    "PromptAsset",
    "PromptVersion",
    "ResolvedPrompt",
    "PromptStore",
    "InMemoryPromptStore",
    "extract_variable_names",
    "default_store",
]

#: ``{{name}}`` — a variable reference in a prompt body.
_VARIABLE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

#: Variable and prompt-id vocabulary: conservative, so an id is always safe to
#: put in a URL or a log line.
_NAME = re.compile(r"\A[A-Za-z0-9_]{1,64}\Z")


def extract_variable_names(body: str) -> tuple[str, ...]:
    """Every variable a body references, in order of first appearance.

    Duplicates collapse to one entry; the order is stable so a published
    version's variable list is reproducible.
    """
    seen: list[str] = []
    for match in _VARIABLE.finditer(body or ""):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return tuple(seen)


# --------------------------------------------------------------------------- #
# Domain types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class VariableDecl:
    """One variable a prompt declares.

    ``default`` is the value used when a run supplies none. A variable with
    neither a supplied value nor a default is *unresolved*, and a run that needs
    it is refused at admission rather than run with a blank substitution.
    """

    name: str
    default: str | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if not _NAME.match(self.name):
            raise ContractError(
                f"{self.name!r} is not a usable variable name. Use letters, "
                "digits and underscores, up to 64 characters."
            )


@dataclass(slots=True)
class PromptAsset:
    """A named prompt: its mutable draft plus a pointer to its latest version.

    The draft is the only mutable part. ``latest_version_number`` is a pointer
    into the immutable version list; it is ``None`` until the first publish.
    """

    id: str
    name: str
    body: str
    variables: tuple[VariableDecl, ...]
    created_at: float
    updated_at: float
    latest_version_number: int | None = None


@dataclass(frozen=True, slots=True)
class PromptVersion:
    """A published, immutable version of a prompt (D-2).

    Frozen by construction: the store creates one exactly once and never mutates
    it. ``content_hash`` is the identity used for idempotent publishing (D-3).
    """

    id: str
    prompt_id: str
    number: int
    name: str
    body: str
    variables: tuple[VariableDecl, ...]
    content_hash: str
    created_at: float

    @property
    def variable_names(self) -> tuple[str, ...]:
        return tuple(v.name for v in self.variables)


@dataclass(frozen=True, slots=True)
class ResolvedPrompt:
    """The frozen snapshot a run executes against (D-4).

    Produced once, at admission, by :meth:`PromptStore.resolve`. The pipeline
    reads only this object, which is *why* editing a prompt after a run has
    started cannot change what that run executed.
    """

    prompt_id: str
    prompt_name: str
    version_number: int
    version_id: str
    version_hash: str
    #: The version the caller asked for; ``None`` means "latest".
    requested_version: int | None
    #: The concrete values that were frozen — every declared variable, resolved.
    variables: Mapping[str, str]
    #: The body with variables substituted, fixed at the moment of admission.
    rendered: str
    frozen_at: float


# --------------------------------------------------------------------------- #
# Store interface
# --------------------------------------------------------------------------- #


class PromptStore(Protocol):
    """What a prompt store must do, so a durable backend can replace the
    in-memory one without changing any caller."""

    def create(
        self,
        name: str,
        *,
        body: str = "",
        variables: Sequence[VariableDecl] = (),
    ) -> PromptAsset: ...

    def list(self) -> list[PromptAsset]: ...

    def find(self, prompt_id: str) -> PromptAsset | None: ...

    def update_draft(
        self,
        prompt_id: str,
        *,
        name: str | None = None,
        body: str | None = None,
        variables: Sequence[VariableDecl] | None = None,
    ) -> PromptAsset: ...

    def publish(self, prompt_id: str) -> PromptVersion: ...

    def versions(self, prompt_id: str) -> list[PromptVersion]: ...

    def find_version(self, prompt_id: str, number: int) -> PromptVersion | None: ...

    def latest(self, prompt_id: str) -> PromptVersion | None: ...

    def resolve(
        self,
        prompt_id: str,
        *,
        version: int | None,
        variables: Mapping[str, str] | None = None,
    ) -> ResolvedPrompt: ...

    def state(self) -> dict[str, Any]: ...


# --------------------------------------------------------------------------- #
# In-memory implementation
# --------------------------------------------------------------------------- #


def _content_hash(body: str, variables: Sequence[VariableDecl]) -> str:
    """A stable hash of exactly what a version means (D-3).

    The body *and* the declared defaults are hashed: changing a default changes
    what a run resolves to, so it is a different version, not a re-publish.
    """
    payload = {
        "body": body,
        "variables": [
            {"name": v.name, "default": v.default} for v in variables
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _merge_variables(
    body: str, declared: Sequence[VariableDecl]
) -> tuple[VariableDecl, ...]:
    """The variables a version declares.

    Referenced variables come first (in body order, so the list is
    reproducible); any declared-but-unreferenced variable is kept afterwards
    rather than dropped, because the author declared it on purpose.
    """
    by_name = {v.name: v for v in declared}
    merged: list[VariableDecl] = []
    seen: set[str] = set()
    for name in extract_variable_names(body):
        merged.append(by_name.get(name, VariableDecl(name=name)))
        seen.add(name)
    for decl in declared:
        if decl.name not in seen:
            merged.append(decl)
            seen.add(decl.name)
    return tuple(merged)


class InMemoryPromptStore:
    """The Milestone 1 store (D-0): in memory, per process, not durable.

    Thread-safe: the pipeline resolves prompts on a worker thread while the
    event loop can publish on another, so every read and write takes the same
    lock.
    """

    def __init__(self) -> None:
        self._assets: dict[str, PromptAsset] = {}
        #: ``prompt_id -> {version_number: PromptVersion}``. Append-only.
        self._versions: dict[str, dict[int, PromptVersion]] = {}
        self._lock = threading.RLock()
        self._counter = 0

    # -- assets ------------------------------------------------------------- #

    def create(
        self,
        name: str,
        *,
        body: str = "",
        variables: Sequence[VariableDecl] = (),
    ) -> PromptAsset:
        clean = (name or "").strip()
        if not clean:
            raise ContractError("Give the prompt a name before saving it.")
        if len(clean) > 120:
            raise ContractError("A prompt name is limited to 120 characters.")
        with self._lock:
            self._counter += 1
            prompt_id = f"prompt_{self._counter}"
            now = time.time()
            asset = PromptAsset(
                id=prompt_id,
                name=clean,
                body=body or "",
                variables=tuple(variables),
                created_at=now,
                updated_at=now,
                latest_version_number=None,
            )
            self._assets[prompt_id] = asset
            self._versions[prompt_id] = {}
            return asset

    def list(self) -> list[PromptAsset]:
        with self._lock:
            return sorted(self._assets.values(), key=lambda a: a.updated_at, reverse=True)

    def find(self, prompt_id: str) -> PromptAsset | None:
        with self._lock:
            return self._assets.get(prompt_id)

    def require(self, prompt_id: str) -> PromptAsset:
        asset = self.find(prompt_id)
        if asset is None:
            raise ContractError(
                f"No prompt with id {prompt_id!r} is known to this System. Prompts "
                "are held in memory only, so a restart clears the workspace."
            )
        return asset

    def update_draft(
        self,
        prompt_id: str,
        *,
        name: str | None = None,
        body: str | None = None,
        variables: Sequence[VariableDecl] | None = None,
    ) -> PromptAsset:
        """Save the draft. **Never** creates a version and never starts a run (A)."""
        with self._lock:
            asset = self.require(prompt_id)
            if name is not None:
                clean = name.strip()
                if not clean:
                    raise ContractError("A prompt needs a name.")
                if len(clean) > 120:
                    raise ContractError("A prompt name is limited to 120 characters.")
                asset.name = clean
            if body is not None:
                asset.body = body
            if variables is not None:
                asset.variables = tuple(variables)
            asset.updated_at = time.time()
            return asset

    # -- versions ----------------------------------------------------------- #

    def publish(self, prompt_id: str) -> PromptVersion:
        """Publish the current draft as the next immutable version (B, D-2/D-3).

        Publishing the same content twice is **idempotent**: the existing latest
        version is returned rather than a duplicate number being minted.
        """
        with self._lock:
            asset = self.require(prompt_id)
            body = asset.body
            if not body.strip():
                raise ContractError(
                    "Add the prompt body before publishing a version — there is "
                    "nothing to freeze yet."
                )
            variables = _merge_variables(body, asset.variables)
            content = _content_hash(body, variables)

            current = self._current_version(asset)
            if current is not None and current.content_hash == content:
                # D-3: same content, same version. Nothing new is minted.
                return current

            number = (asset.latest_version_number or 0) + 1
            version = PromptVersion(
                id=f"{prompt_id}#v{number}",
                prompt_id=prompt_id,
                number=number,
                name=asset.name,
                body=body,
                variables=variables,
                content_hash=content,
                created_at=time.time(),
            )
            self._versions[prompt_id][number] = version
            asset.latest_version_number = number
            asset.updated_at = version.created_at
            return version

    def versions(self, prompt_id: str) -> list[PromptVersion]:
        with self._lock:
            self.require(prompt_id)
            return [self._versions[prompt_id][n] for n in sorted(self._versions[prompt_id])]

    def find_version(self, prompt_id: str, number: int) -> PromptVersion | None:
        with self._lock:
            return self._versions.get(prompt_id, {}).get(number)

    def latest(self, prompt_id: str) -> PromptVersion | None:
        with self._lock:
            asset = self._assets.get(prompt_id)
            if asset is None:
                return None
            return self._current_version(asset)

    def _current_version(self, asset: PromptAsset) -> PromptVersion | None:
        if asset.latest_version_number is None:
            return None
        return self._versions[asset.id].get(asset.latest_version_number)

    # -- resolution (D-4) --------------------------------------------------- #

    def resolve(
        self,
        prompt_id: str,
        *,
        version: int | None,
        variables: Mapping[str, str] | None = None,
    ) -> ResolvedPrompt:
        """Freeze a prompt version + variable values for one run.

        ``version=None`` resolves to the **latest** published version; an int
        pins that exact version. The result is a fixed snapshot — later edits or
        publishes cannot change it (D-4).

        Refused with a sentence naming the problem when the prompt is unknown, has
        no such version, or has a variable with neither a supplied value nor a
        default.
        """
        with self._lock:
            asset = self.require(prompt_id)
            supplied = dict(variables or {})

            if version is None:
                chosen = self._current_version(asset)
                if chosen is None:
                    raise ContractError(
                        f"'{asset.name}' has no published version yet, so there is "
                        "nothing to run. Publish a version first."
                    )
            else:
                chosen = self._versions[prompt_id].get(version)
                if chosen is None:
                    available = ", ".join(
                        f"v{n}" for n in sorted(self._versions[prompt_id])
                    ) or "none"
                    raise ContractError(
                        f"'{asset.name}' has no version {version}. Published "
                        f"versions: {available}."
                    )

            resolved: dict[str, str] = {}
            for decl in chosen.variables:
                given = supplied.get(decl.name)
                if given is not None and given.strip() != "":
                    resolved[decl.name] = given
                elif decl.default is not None:
                    resolved[decl.name] = decl.default
                else:
                    raise ContractError(
                        f"This run is missing a value for '{decl.name}', and the "
                        "prompt gives it no default. Supply it and start again."
                    )

            rendered = _VARIABLE.sub(lambda m: resolved[m.group(1)], chosen.body)

            return ResolvedPrompt(
                prompt_id=prompt_id,
                prompt_name=asset.name,
                version_number=chosen.number,
                version_id=chosen.id,
                version_hash=chosen.content_hash,
                requested_version=version,
                variables=resolved,
                rendered=rendered,
                frozen_at=time.time(),
            )

    # -- truth -------------------------------------------------------------- #

    def state(self) -> dict[str, Any]:
        """What ``GET /health`` reports about the store, honestly (D-0)."""
        with self._lock:
            return {
                "kind": "in-memory",
                "durable": False,
                "prompts": len(self._assets),
                "versions": sum(len(v) for v in self._versions.values()),
                "note": (
                    "Prompts and versions are held in this System's memory only. A "
                    "restart clears the workspace; nothing is written to disk."
                ),
            }


# --------------------------------------------------------------------------- #
# The process-wide store
# --------------------------------------------------------------------------- #

_DEFAULT_STORE: InMemoryPromptStore | None = None
_DEFAULT_LOCK = threading.Lock()


def default_store() -> InMemoryPromptStore:
    """The one store the routes and the pipeline share (D-0).

    A single instance, so a prompt published through ``POST /prompts`` is the
    same prompt ``POST /run`` resolves — otherwise a prompt-driven run could
    never see a version the workspace had just created. Replacing this with a
    durable backend is the whole point of the interface; nothing else changes.
    """
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        with _DEFAULT_LOCK:
            if _DEFAULT_STORE is None:
                _DEFAULT_STORE = InMemoryPromptStore()
    return _DEFAULT_STORE
