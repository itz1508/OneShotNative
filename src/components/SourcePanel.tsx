/**
 * The input surface: what to do, and which sources to do it with.
 *
 * This component builds the `RunRequest` and does nothing else with it. Choosing
 * a source type, scope operation and inspection behaviour is the System's
 * business; the user only declares what they want and what they are handing over.
 *
 * Client-side validation mirrors the wire contract so mistakes are caught early,
 * never to reimplement a pipeline decision.
 */

import { useRef, useState } from "react";

import { SOURCE_ICONS, IconArrowRight, IconClose, IconPlus } from "./icons";
import {
  Button,
  Callout,
  Field,
  Hint,
  IconButton,
  Label,
  Mono,
  Panel,
  Pill,
  Select,
  TextArea,
  TextInput,
  cx,
} from "./ui";
import {
  SOURCE_TYPES,
  TASK_KINDS,
  type RunRequest,
  type SourceOperation,
  type SourceType,
  type TaskKind,
} from "../lib/schema";

const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

const OPERATIONS: { value: SourceOperation; label: string; hint: string }[] = [
  { value: "ADD_SOURCE", label: "Add to scope", hint: "Read and used in this run." },
  { value: "INPUT_SOURCE", label: "Input source", hint: "Brought into the System for this run." },
  { value: "IMPORT_SOURCE", label: "Import content", hint: "Ingest the content and read it." },
  {
    value: "REFERENCE_SOURCE",
    label: "Reference only",
    hint: "Registered by reference — content is not imported, so it is not read or cited.",
  },
  {
    value: "REMOVE_SOURCE",
    label: "Remove from scope",
    hint: "Takes it out of scope. This never deletes anything — deletion is a separate authority.",
  },
];

interface Draft {
  key: string;
  id: string;
  type: SourceType;
  label: string;
  url: string;
  text: string;
  filename: string;
  mimeType: string;
  contentBase64: string;
  operation: SourceOperation;
  error: string | null;
}

let counter = 0;

function newDraft(type: SourceType = "text"): Draft {
  counter += 1;
  return {
    key: `draft-${counter}`,
    id: `src_${counter}`,
    type,
    label: "",
    url: "",
    text: "",
    filename: "",
    mimeType: "",
    contentBase64: "",
    operation: "ADD_SOURCE",
    error: null,
  };
}

function validate(draft: Draft): string | null {
  if (draft.type === "public_url") {
    const url = draft.url.trim();
    if (!url) return "A public URL source needs an address.";
    if (!/^https?:\/\//i.test(url)) return "The address must start with http:// or https://.";
    return null;
  }
  if (draft.type === "text" || draft.type === "raw_html") {
    if (!draft.text.trim()) return "Add the text for this source, or remove the source.";
    return null;
  }
  if (!draft.contentBase64) return "Choose a file to upload for this source.";
  return null;
}

export interface SourcePanelProps {
  running: boolean;
  onRun: (request: RunRequest) => void;
  hasResult: boolean;
}

export function SourcePanel({ running, onRun, hasResult }: SourcePanelProps) {
  const [intent, setIntent] = useState("");
  const [taskKind, setTaskKind] = useState<TaskKind>("unclassified");
  const [drafts, setDrafts] = useState<Draft[]>([newDraft("text")]);
  const [pauseBeforeGenerate, setPauseBeforeGenerate] = useState(true);
  const [maxContextChars, setMaxContextChars] = useState(200_000);
  const [maxPerSource, setMaxPerSource] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const fileInputs = useRef<Record<string, HTMLInputElement | null>>({});

  function update(key: string, patch: Partial<Draft>) {
    setDrafts((current) =>
      current.map((draft) => (draft.key === key ? { ...draft, ...patch, error: null } : draft)),
    );
  }

  function removeDraft(key: string) {
    setDrafts((current) => current.filter((draft) => draft.key !== key));
  }

  function addDraft() {
    setDrafts((current) => [...current, newDraft("text")]);
  }

  function readFile(draft: Draft, file: File | undefined) {
    if (!file) return;
    if (file.size > MAX_UPLOAD_BYTES) {
      update(draft.key, {
        error: `That file is ${(file.size / 1024 / 1024).toFixed(1)} MB — the System accepts up to 25 MB.`,
      });
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      update(draft.key, {
        contentBase64: result,
        filename: file.name,
        mimeType: file.type,
        error: null,
      });
    };
    reader.onerror = () => update(draft.key, { error: "That file could not be read. Try again." });
    reader.readAsDataURL(file);
  }

  function submit() {
    const trimmed = intent.trim();
    if (!trimmed) {
      setFormError("Tell OneShot what you want done with these sources.");
      return;
    }
    if (drafts.length === 0) {
      setFormError("Add at least one source — OneShot reads only what you give it.");
      return;
    }

    const problems = drafts.map(validate);
    if (problems.some(Boolean)) {
      setDrafts((current) =>
        current.map((draft) => ({ ...draft, error: validate(draft) })),
      );
      setFormError("Fix the highlighted sources, then run again.");
      return;
    }

    setFormError(null);
    const parsedPerSource = Number.parseInt(maxPerSource, 10);

    onRun({
      intent: trimmed,
      task_kind: taskKind,
      sources: drafts.map((draft) => {
        const base = {
          id: draft.id,
          type: draft.type,
          operation: draft.operation,
          ...(draft.label.trim() ? { label: draft.label.trim() } : {}),
        };
        if (draft.type === "public_url") return { ...base, url: draft.url.trim() };
        if (draft.type === "text" || draft.type === "raw_html") {
          return { ...base, text: draft.text };
        }
        return {
          ...base,
          filename: draft.filename,
          mime_type: draft.mimeType || null,
          content_base64: draft.contentBase64,
        };
      }),
      options: {
        pause_before_generate: pauseBeforeGenerate,
        max_context_chars: Number.isFinite(maxContextChars) ? maxContextChars : 200_000,
        max_chars_per_source:
          Number.isFinite(parsedPerSource) && parsedPerSource >= 200 ? parsedPerSource : null,
      },
    });
  }

  return (
    <div className="space-y-4">
      <Panel className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="intent" hint="One objective, the sources it applies to.">
            What should OneShot do?
          </Label>
          <TextArea
            id="intent"
            rows={3}
            value={intent}
            placeholder="Summarise the session expiry rules and flag anything the sources leave unclear."
            aria-invalid={formError !== null}
            aria-describedby={formError ? "intent-error" : undefined}
            onChange={(event) => setIntent(event.target.value)}
          />
          <Hint>
            The objective is handed to the System whole. It is never rewritten, classified or
            answered in the browser.
          </Hint>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Task kind" htmlFor="task-kind">
            <Select
              id="task-kind"
              value={taskKind}
              onChange={(event) => setTaskKind(event.target.value as TaskKind)}
            >
              {TASK_KINDS.map((kind) => (
                <option key={kind.value} value={kind.value}>
                  {kind.label}
                </option>
              ))}
            </Select>
          </Field>

          <Field
            label="Context budget"
            htmlFor="max-context"
            hint="characters across all sources"
          >
            <TextInput
              id="max-context"
              type="number"
              min={1000}
              step={1000}
              value={maxContextChars}
              onChange={(event) => setMaxContextChars(Number(event.target.value))}
            />
          </Field>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Per-source share"
            htmlFor="max-per-source"
            hint="optional — blank shares the budget equally"
          >
            <TextInput
              id="max-per-source"
              type="number"
              min={200}
              step={100}
              value={maxPerSource}
              placeholder="equal share"
              onChange={(event) => setMaxPerSource(event.target.value)}
            />
          </Field>

          <div className="flex items-end">
            <label className="flex cursor-pointer items-center gap-2.5 pb-2 text-sm text-foreground">
              <input
                type="checkbox"
                className="size-4 cursor-pointer accent-[var(--color-primary)]"
                checked={pauseBeforeGenerate}
                onChange={(event) => setPauseBeforeGenerate(event.target.checked)}
              />
              Pause before Generate
              <Mono>so you can resume it yourself</Mono>
            </label>
          </div>
        </div>
      </Panel>

      <Panel className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-heading text-sm text-foreground">Sources</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              OneShot reads exactly these — it discovers nothing on its own.
            </p>
          </div>
          <Pill tone="neutral">{drafts.length} in scope</Pill>
        </div>

        {drafts.length === 0 ? (
          <Callout
            tone="warning"
            icon={<IconPlus />}
            title="No sources yet"
          >
            <p>
              Add a page, some text, a document or a recording. A run needs at least one source —
              without one there is nothing to work from, and OneShot will not invent it.
            </p>
          </Callout>
        ) : null}

        <ul className="space-y-3">
          {drafts.map((draft) => {
            const TypeIcon = SOURCE_ICONS[draft.type];
            const meta = SOURCE_TYPES.find((entry) => entry.value === draft.type);
            return (
              <li
                key={draft.key}
                className={cx(
                  "space-y-3 rounded-md border bg-background/40 p-3.5",
                  draft.error ? "border-destructive/50" : "border-border",
                )}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <TypeIcon className="size-4 text-muted-foreground" />
                  <Select
                    aria-label={`Source type for ${draft.id}`}
                    className="w-auto min-w-[9.5rem] flex-none py-1.5 text-xs"
                    value={draft.type}
                    onChange={(event) =>
                      update(draft.key, {
                        type: event.target.value as SourceType,
                        url: "",
                        text: "",
                        filename: "",
                        mimeType: "",
                        contentBase64: "",
                        error: null,
                      })
                    }
                  >
                    {SOURCE_TYPES.map((entry) => (
                      <option key={entry.value} value={entry.value}>
                        {entry.label}
                      </option>
                    ))}
                  </Select>
                  <Mono>{draft.id}</Mono>
                  <div className="ml-auto flex items-center gap-1.5">
                    <IconButton
                      label={`Remove source ${draft.id} from this run`}
                      onClick={() => removeDraft(draft.key)}
                    >
                      <IconClose className="size-4" />
                    </IconButton>
                  </div>
                </div>

                {draft.type === "public_url" ? (
                  <div className="space-y-1.5">
                    <Label htmlFor={`url-${draft.key}`}>Address</Label>
                    <TextInput
                      id={`url-${draft.key}`}
                      value={draft.url}
                      inputMode="url"
                      spellCheck={false}
                      placeholder="https://example.com/article"
                      onChange={(event) => update(draft.key, { url: event.target.value })}
                    />
                  </div>
                ) : null}

                {draft.type === "text" || draft.type === "raw_html" ? (
                  <div className="space-y-1.5">
                    <Label htmlFor={`text-${draft.key}`}>
                      {draft.type === "raw_html" ? "Markup" : "Text"}
                    </Label>
                    <TextArea
                      id={`text-${draft.key}`}
                      rows={5}
                      value={draft.text}
                      spellCheck={draft.type === "text"}
                      className={draft.type === "raw_html" ? "font-mono text-xs" : undefined}
                      placeholder={
                        draft.type === "raw_html"
                          ? "<article><h1>Heading</h1><p>Body…</p></article>"
                          : "Paste the text OneShot should work from…"
                      }
                      onChange={(event) => update(draft.key, { text: event.target.value })}
                    />
                  </div>
                ) : null}

                {draft.type === "pdf" || draft.type === "docx" || draft.type === "audio" ? (
                  <div className="space-y-2">
                    <Label htmlFor={`file-${draft.key}`} hint={meta?.hint}>
                      File
                    </Label>
                    <input
                      id={`file-${draft.key}`}
                      ref={(node) => {
                        fileInputs.current[draft.key] = node;
                      }}
                      type="file"
                      accept={meta?.accepts}
                      className="w-full cursor-pointer rounded-md border border-border bg-background px-3 py-2 text-sm file:mr-3 file:cursor-pointer file:rounded file:border-0 file:bg-elevated file:px-3 file:py-1.5 file:text-xs file:text-foreground"
                      onChange={(event) => readFile(draft, event.target.files?.[0])}
                    />
                    {draft.filename ? (
                      <div className="flex flex-wrap items-center gap-2">
                        <Pill tone="accent">{draft.filename}</Pill>
                        <Mono>
                          {(draft.contentBase64.length / 1024).toFixed(0)} KB encoded
                        </Mono>
                      </div>
                    ) : null}
                  </div>
                ) : null}

                <div className="space-y-1.5">
                  <Label htmlFor={`op-${draft.key}`} hint={OPERATIONS.find((op) => op.value === draft.operation)?.hint}>
                    Scope operation
                  </Label>
                  <Select
                    id={`op-${draft.key}`}
                    value={draft.operation}
                    onChange={(event) =>
                      update(draft.key, { operation: event.target.value as SourceOperation })
                    }
                  >
                    {OPERATIONS.map((operation) => (
                      <option key={operation.value} value={operation.value}>
                        {operation.label}
                      </option>
                    ))}
                  </Select>
                </div>

                {draft.error ? (
                  <p role="alert" className="text-xs leading-relaxed text-destructive">
                    {draft.error}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>

        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" size="sm" onClick={addDraft}>
            <IconPlus className="size-3.5" />
            Add source
          </Button>
          {hasResult ? (
            <Hint>Running again replaces the result shown in the Result view.</Hint>
          ) : null}
        </div>
      </Panel>

      <div className="space-y-3">
        {formError ? (
          <p
            role="alert"
            className="rounded-md border border-destructive/45 bg-destructive/5 px-3 py-2.5 text-sm leading-relaxed text-destructive"
          >
            {formError}
          </p>
        ) : null}

        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="primary"
            onClick={submit}
            disabled={running}
            aria-busy={running}
            className="min-w-[13rem]"
          >
            {running ? "Running…" : "Run OneShot"}
            <IconArrowRight className="size-4" />
          </Button>
          <Hint>
            The System scans, reads, prepares, checks coverage, and only then generates — and only
            if the context is sufficient.
          </Hint>
        </div>
      </div>
    </div>
  );
}