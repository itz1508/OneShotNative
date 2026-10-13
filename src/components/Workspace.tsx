/**
 * The Prompt Workspace — prompts, drafts and immutable versions.
 *
 * The System owns prompts; this view only reads and writes them. Two actions are
 * deliberately kept apart and labelled as such: **Save draft** keeps working
 * without publishing or running anything, and **Publish version** freezes an
 * immutable version. The banner is not decoration — the M1 store is in memory,
 * and the UI must not look more durable than the System actually is.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  createPrompt,
  getPrompt,
  getPromptVersion,
  listPromptVersions,
  listPrompts,
  publishPromptVersion,
  savePromptDraft,
} from "../lib/api";
import type {
  PromptDetail,
  PromptSummary,
  PromptVersionView,
  VariableDecl,
} from "../lib/schema";
import { IconAlert, IconArrowRight, IconCheck, IconClose, IconInfo, IconPlus } from "./icons";
import {
  Button,
  Callout,
  EmptyState,
  Field,
  Hint,
  IconButton,
  Label,
  Mono,
  Panel,
  Pill,
  TextArea,
  TextInput,
  cx,
} from "./ui";

const VARIABLE = /\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g;

/** Variables a body references, in order of first appearance. */
function referencedNames(body: string): string[] {
  const names: string[] = [];
  for (const match of body.matchAll(VARIABLE)) {
    if (!names.includes(match[1])) names.push(match[1]);
  }
  return names;
}

export function Workspace() {
  const [rows, setRows] = useState<PromptSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<PromptDetail | null>(null);
  const [versions, setVersions] = useState<PromptVersionView[]>([]);
  const [viewing, setViewing] = useState<PromptVersionView | null>(null);

  const [name, setName] = useState("");
  const [body, setBody] = useState("");
  const [variables, setVariables] = useState<VariableDecl[]>([]);

  const [newName, setNewName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  const referenced = useMemo(() => referencedNames(body), [body]);

  const reloadList = useCallback(async () => {
    try {
      setRows(await listPrompts());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The workspace could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  const openPrompt = useCallback(async (id: string) => {
    setError(null);
    setNotice(null);
    setViewing(null);
    try {
      const [loaded, published] = await Promise.all([getPrompt(id), listPromptVersions(id)]);
      setSelectedId(id);
      setDetail(loaded);
      setVersions(published);
      setName(loaded.name);
      setBody(loaded.body);
      setVariables(loaded.variables);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "That prompt could not be opened.");
    }
  }, []);

  useEffect(() => {
    void reloadList();
  }, [reloadList]);

  async function handleCreate() {
    const trimmed = newName.trim();
    if (!trimmed) {
      setError("Give the prompt a name before creating it.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await createPrompt({ name: trimmed });
      setNewName("");
      await reloadList();
      await openPrompt(created.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The prompt could not be created.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveDraft() {
    if (!selectedId) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await savePromptDraft(selectedId, { name, body, variables });
      setDetail(saved);
      await reloadList();
      setNotice("Draft saved. Nothing was published and nothing ran.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The draft could not be saved.");
    } finally {
      setBusy(false);
    }
  }

  async function handlePublish() {
    if (!selectedId) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    const before = versions.length;
    try {
      const version = await publishPromptVersion(selectedId);
      const [refreshed, published] = await Promise.all([
        getPrompt(selectedId),
        listPromptVersions(selectedId),
      ]);
      setDetail(refreshed);
      setVersions(published);
      await reloadList();
      setNotice(
        published.length > before
          ? `Published v${version.number}. Earlier versions are unchanged.`
          : `v${version.number} is already the latest version of this exact content — nothing new was published.`,
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The version could not be published.");
    } finally {
      setBusy(false);
    }
  }

  async function handleViewVersion(number: number) {
    if (!selectedId) return;
    try {
      setViewing(await getPromptVersion(selectedId, number));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "That version could not be read.");
    }
  }

  function updateVariable(index: number, patch: Partial<VariableDecl>) {
    setVariables((current) =>
      current.map((entry, i) => (i === index ? { ...entry, ...patch } : entry)),
    );
  }

  function addVariable(preset?: string) {
    setVariables((current) => [
      ...current,
      { name: preset ?? "", default: null, description: "" },
    ]);
  }

  function removeVariable(index: number) {
    setVariables((current) => current.filter((_, i) => i !== index));
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)] lg:items-start">
      {/* ---- prompt list -------------------------------------------------- */}
      <div className="space-y-4 lg:sticky lg:top-24">
        <Panel className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="font-heading text-sm text-foreground">Prompts</h2>
            <Pill tone="neutral">{rows.length}</Pill>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="new-prompt">New prompt</Label>
            <div className="flex items-center gap-2">
              <TextInput
                id="new-prompt"
                value={newName}
                placeholder="Weekly digest"
                onChange={(event) => setNewName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void handleCreate();
                }}
              />
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void handleCreate()}
                disabled={busy}
                className="shrink-0"
              >
                <IconPlus className="size-3.5" />
                Create
              </Button>
            </div>
          </div>

          {loading ? (
            <Hint>Loading the workspace…</Hint>
          ) : rows.length === 0 ? (
            <EmptyState icon={<IconInfo />} title="No prompts yet">
              Name one above to start. You can write it, save the draft, and publish a version
              whenever you are ready — none of that runs anything.
            </EmptyState>
          ) : (
            <ul className="space-y-1.5">
              {rows.map((row) => (
                <li key={row.id}>
                  <button
                    type="button"
                    onClick={() => void openPrompt(row.id)}
                    aria-current={row.id === selectedId ? "true" : undefined}
                    className={cx(
                      "w-full cursor-pointer rounded-md border px-3 py-2 text-left transition-colors duration-200 ease-out",
                      "focus-visible:outline-none",
                      row.id === selectedId
                        ? "border-accent/50 bg-elevated"
                        : "border-border hover:border-secondary hover:bg-elevated",
                    )}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="truncate font-heading text-sm text-foreground">
                        {row.name}
                      </span>
                      <Pill tone={row.latest_version ? "accent" : "neutral"}>
                        {row.latest_version ? `v${row.latest_version}` : "draft"}
                      </Pill>
                    </span>
                    <span className="mt-1 block truncate text-xs text-muted-foreground">
                      {row.version_count} version{row.version_count === 1 ? "" : "s"}
                      {row.variable_names.length
                        ? ` · ${row.variable_names.map((n) => n).join(", ")}`
                        : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Callout tone="warning" icon={<IconAlert />} title="Held in memory only">
          <p>
            Prompts and their versions live in the System&apos;s memory for the life of the
            process. Restarting the System clears this workspace — nothing is written to disk.
          </p>
        </Callout>
      </div>

      {/* ---- editor ------------------------------------------------------- */}
      <div className="space-y-4">
        {error ? (
          <p
            role="alert"
            className="rounded-md border border-destructive/45 bg-destructive/5 px-3 py-2.5 text-sm leading-relaxed text-destructive"
          >
            {error}
          </p>
        ) : null}
        {notice ? (
          <p
            role="status"
            className="rounded-md border border-success/45 bg-background/40 px-3 py-2.5 text-sm leading-relaxed text-success"
          >
            {notice}
          </p>
        ) : null}

        {!selectedId || !detail ? (
          <Panel>
            <EmptyState icon={<IconInfo />} title="Nothing selected">
              Pick a prompt on the left, or create one, and its draft will open here. A draft is
              yours to edit freely — it only becomes fixed when you publish a version.
            </EmptyState>
          </Panel>
        ) : (
          <>
            <Panel className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h2 className="font-heading text-sm text-foreground">Draft</h2>
                  <Mono>{detail.id}</Mono>
                </div>
                <Pill tone={detail.latest_version ? "accent" : "neutral"}>
                  {detail.latest_version ? `latest v${detail.latest_version}` : "not published"}
                </Pill>
              </div>

              <Field label="Name" htmlFor="prompt-name">
                <TextInput
                  id="prompt-name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                />
              </Field>

              <Field
                label="Prompt body"
                htmlFor="prompt-body"
                hint="use {{name}} for a variable"
              >
                <TextArea
                  id="prompt-body"
                  rows={8}
                  value={body}
                  spellCheck
                  placeholder="Summarise the session expiry rules for {{audience}}."
                  onChange={(event) => setBody(event.target.value)}
                />
              </Field>

              {referenced.length ? (
                <Hint>
                  This body uses:{" "}
                  {referenced.map((n) => (
                    <Mono key={n} className="mr-1.5">
                      {n}
                    </Mono>
                  ))}
                </Hint>
              ) : null}

              <div className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <Label>Variables</Label>
                  <Button size="sm" variant="ghost" onClick={() => addVariable()}>
                    <IconPlus className="size-3.5" />
                    Add variable
                  </Button>
                </div>

                {variables.length === 0 ? (
                  <Hint>
                    No variables declared. Add one, or reference <Mono>{"{{name}}"}</Mono> in
                    the body and give it a default here.
                  </Hint>
                ) : (
                  <ul className="space-y-2">
                    {variables.map((entry, index) => (
                      <li
                        key={index}
                        className="grid gap-2 rounded-md border border-border bg-background/40 p-2.5 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]"
                      >
                        <TextInput
                          aria-label={`Variable ${index + 1} name`}
                          value={entry.name}
                          placeholder="name"
                          onChange={(event) => updateVariable(index, { name: event.target.value })}
                        />
                        <TextInput
                          aria-label={`Variable ${entry.name || index + 1} default`}
                          value={entry.default ?? ""}
                          placeholder="default (optional)"
                          onChange={(event) =>
                            updateVariable(index, {
                              default: event.target.value === "" ? null : event.target.value,
                            })
                          }
                        />
                        <IconButton
                          label={`Remove variable ${entry.name || index + 1}`}
                          onClick={() => removeVariable(index)}
                        >
                          <IconClose className="size-4" />
                        </IconButton>
                      </li>
                    ))}
                  </ul>
                )}

                {referenced.some((n) => !variables.some((v) => v.name === n)) ? (
                  <Hint tone="error">
                    Some referenced variables are not declared yet — add them so a run knows what
                    to substitute.
                  </Hint>
                ) : null}
              </div>

              <div className="flex flex-wrap items-center gap-3 pt-1">
                <Button variant="secondary" onClick={() => void handleSaveDraft()} disabled={busy}>
                  Save draft
                </Button>
                <Button variant="primary" onClick={() => void handlePublish()} disabled={busy}>
                  Publish version
                  <IconArrowRight className="size-4" />
                </Button>
                <Hint>
                  Save keeps your work. Publish freezes it as an immutable version — it does not
                  run the prompt.
                </Hint>
              </div>

              {detail.latest_version ? (
                <Hint>
                  Running this prompt will use{" "}
                  <Mono>v{detail.latest_version}</Mono> unless you pin an earlier version.
                </Hint>
              ) : null}
            </Panel>

            <Panel className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="font-heading text-sm text-foreground">Version history</h2>
                <Pill tone="neutral">{versions.length}</Pill>
              </div>

              {versions.length === 0 ? (
                <EmptyState icon={<IconInfo />} title="Nothing published yet">
                  Publish the draft to freeze your first version. Versions never change once
                  written.
                </EmptyState>
              ) : (
                <ul className="space-y-2">
                  {[...versions].reverse().map((version) => (
                    <li
                      key={version.id}
                      className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-background/40 px-3 py-2"
                    >
                      <Pill tone="accent">v{version.number}</Pill>
                      <Mono className="truncate">{version.content_hash.slice(0, 12)}</Mono>
                      <span className="ml-auto flex items-center gap-2">
                        {version.number === detail.latest_version ? (
                          <Pill tone="success">
                            <IconCheck className="size-3" />
                            latest
                          </Pill>
                        ) : null}
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => void handleViewVersion(version.number)}
                        >
                          View
                        </Button>
                      </span>
                    </li>
                  ))}
                </ul>
              )}

              {viewing ? (
                <div className="space-y-2 rounded-md border border-border bg-background/60 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Pill tone="neutral">read-only</Pill>
                    <span className="font-heading text-sm text-foreground">
                      v{viewing.number} · {viewing.name}
                    </span>
                    <IconButton
                      label="Close version preview"
                      className="ml-auto"
                      onClick={() => setViewing(null)}
                    >
                      <IconClose className="size-4" />
                    </IconButton>
                  </div>
                  <pre className="scroll-slim max-h-64 overflow-auto whitespace-pre-wrap font-mono text-xs leading-relaxed text-muted-foreground">
                    {viewing.body}
                  </pre>
                  {viewing.variables.length ? (
                    <div className="flex flex-wrap gap-2">
                      {viewing.variables.map((v) => (
                        <Pill key={v.name} tone="neutral">
                          {v.name}
                          {v.default ? ` = ${v.default}` : " (no default)"}
                        </Pill>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </Panel>
          </>
        )}
      </div>
    </div>
  );
}
