/**
 * Editor Review and Finalize.
 *
 * Review is an editorial step, not a pipeline one: the editor chooses which of
 * the System's generated sections to carry into the final document and adds a
 * note of their own. Nothing is rewritten, summarised or re-ordered — the
 * remaining text is byte-for-byte what the System produced, and the finalise
 * command is still the System's to accept or refuse.
 */

import { useMemo, useState } from "react";

import type { GeneratedSection, OneShotOutput } from "../lib/schema";
import { copyText, downloadText } from "../lib/format";
import { IconAlert, IconCheck, IconCopy, IconDownload, IconInfo } from "./icons";
import { CitationList } from "./ResultPanel";
import { Button, Callout, Hint, Mono, Panel, Pill, TextArea, cx } from "./ui";

function buildMarkdown(
  output: OneShotOutput,
  sections: GeneratedSection[],
  note: string,
): string {
  const lines: string[] = [];
  lines.push(`# ${output.generated?.title ?? "OneShot output"}`, "");

  const summary = output.generated?.summary?.trim();
  if (summary) lines.push(summary, "");

  for (const section of sections) {
    lines.push(`## ${section.heading}`, "", section.body.trim(), "");
    for (const citation of section.citations) {
      lines.push(`- source: ${citation.source_id} (${citation.source_label}) — ${citation.locator}`);
    }
    if (section.citations.length) lines.push("");
  }

  if (note.trim()) lines.push("## Editor's note", "", note.trim(), "");

  lines.push(
    "---",
    "",
    `Run ${output.run_id} · schema ${output.schema_version} · quality ${output.quality.outcome}`,
  );
  return lines.join("\n");
}

export interface EditorReviewProps {
  output: OneShotOutput;
  finalized: boolean;
  busy: boolean;
  onFinalize: () => void;
  onStartAnother: () => void;
}

export function EditorReview({
  output,
  finalized,
  busy,
  onFinalize,
  onStartAnother,
}: EditorReviewProps) {
  const sections = output.generated?.sections ?? [];
  const [excluded, setExcluded] = useState<Record<number, boolean>>({});
  const [note, setNote] = useState("");
  const [copied, setCopied] = useState(false);

  const approved = useMemo(
    () => sections.filter((_, index) => !excluded[index]),
    [sections, excluded],
  );
  const markdown = useMemo(() => buildMarkdown(output, approved, note), [output, approved, note]);

  async function copyFinal() {
    const ok = await copyText(markdown);
    setCopied(ok);
    window.setTimeout(() => setCopied(false), 2000);
  }

  if (finalized) {
    return (
      <div className="space-y-4">
        <Callout tone="success" icon={<IconCheck />} title="Finalised — this run is complete">
          <p>
            The System accepted the finalise command. Here is the document you approved
            {approved.length !== sections.length
              ? ` — ${approved.length} of ${sections.length} sections, as you selected.`
              : "."}
          </p>
        </Callout>

        <Panel className="space-y-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-1">
              <h2 className="font-heading text-sm text-foreground">Final document</h2>
              <p className="text-sm text-muted-foreground">
                Assembled from the System's own sections. Nothing was rewritten.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Pill tone="success">finalised</Pill>
              <Pill tone="neutral">
                {approved.length} / {sections.length} sections
              </Pill>
            </div>
          </div>

          {approved.length === 0 ? (
            <Callout tone="warning" icon={<IconInfo />} title="You approved no sections">
              <p>
                The run is finalised, but the document is empty because every section was left out.
                Copy or download it as is, or start another run.
              </p>
            </Callout>
          ) : (
            <article className="space-y-5">
              <h3 className="font-heading text-lg leading-snug text-foreground">
                {output.generated?.title ?? "OneShot output"}
              </h3>
              {approved.map((section, index) => (
                <section key={`${section.heading}-${index}`} className="space-y-2.5">
                  <h4 className="font-heading text-sm uppercase tracking-[0.12em] text-muted-foreground">
                    {section.heading}
                  </h4>
                  <p className="max-w-prose whitespace-pre-line text-sm leading-relaxed text-foreground">
                    {section.body}
                  </p>
                  <CitationList citations={section.citations} />
                </section>
              ))}
              {note.trim() ? (
                <section className="space-y-2 border-t border-border pt-4">
                  <h4 className="font-heading text-sm uppercase tracking-[0.12em] text-muted-foreground">
                    Editor's note
                  </h4>
                  <p className="max-w-prose whitespace-pre-line text-sm leading-relaxed text-foreground">
                    {note.trim()}
                  </p>
                </section>
              ) : null}
            </article>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="primary" onClick={() => void copyFinal()}>
              {copied ? <IconCheck className="size-3.5" /> : <IconCopy className="size-3.5" />}
              {copied ? "Copied" : "Copy as Markdown"}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => downloadText(`${output.run_id}-final.md`, markdown, "text/markdown")}
            >
              <IconDownload className="size-3.5" />
              Download .md
            </Button>
            <Button size="sm" variant="ghost" onClick={onStartAnother}>
              Start another run
            </Button>
          </div>
        </Panel>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Panel className="space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <h2 className="font-heading text-sm text-foreground">Editor review</h2>
            <p className="text-sm text-muted-foreground">
              Choose what carries into the final document. Finalising is the System's decision to
              accept, not the browser's to assume.
            </p>
          </div>
          <Pill tone="accent">editor review</Pill>
        </div>

        {sections.length === 0 ? (
          <Callout tone="warning" icon={<IconAlert />} title="There are no sections to review">
            <p>
              This run generated nothing, so there is nothing to select. You can still finalise it to
              close the run out, or start another one with different sources.
            </p>
          </Callout>
        ) : (
          <fieldset className="space-y-3">
            <legend className="font-heading text-xs uppercase tracking-[0.14em] text-muted-foreground">
              Sections
            </legend>
            {sections.map((section, index) => {
              const isExcluded = Boolean(excluded[index]);
              return (
                <label
                  key={`${section.heading}-${index}`}
                  className={cx(
                    "flex cursor-pointer items-start gap-3 rounded-md border bg-background/40 p-3.5 transition-colors duration-200 ease-out",
                    isExcluded ? "border-border opacity-60" : "border-accent/40",
                  )}
                >
                  <input
                    type="checkbox"
                    className="mt-1 size-4 shrink-0 cursor-pointer accent-[var(--color-primary)]"
                    checked={!isExcluded}
                    onChange={(event) =>
                      setExcluded((current) => ({ ...current, [index]: !event.target.checked }))
                    }
                  />
                  <span className="min-w-0 flex-1 space-y-1.5">
                    <span className="block font-heading text-sm text-foreground">
                      {section.heading}
                    </span>
                    <span className="block max-w-prose text-sm leading-relaxed text-muted-foreground">
                      {section.body.length > 260 ? `${section.body.slice(0, 260)}…` : section.body}
                    </span>
                    <span className="block font-mono text-[11px] text-muted-foreground">
                      {section.citations.length} citation{section.citations.length === 1 ? "" : "s"}
                    </span>
                  </span>
                </label>
              );
            })}
          </fieldset>
        )}

        <div className="space-y-1.5">
          <label
            htmlFor="editor-note"
            className="font-heading text-xs uppercase tracking-[0.14em] text-muted-foreground"
          >
            Editor's note <span className="normal-case tracking-normal">(optional)</span>
          </label>
          <TextArea
            id="editor-note"
            rows={3}
            value={note}
            placeholder="Anything the reader should know about this run, or about what the sources left unresolved."
            onChange={(event) => setNote(event.target.value)}
          />
          <Hint>
            The note is yours and is kept separate from the System's text — it is never merged into a
            generated section.
          </Hint>
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
          <Button
            variant="primary"
            onClick={onFinalize}
            disabled={busy}
            aria-busy={busy}
            className="min-w-[11rem]"
          >
            {busy ? "Finalising…" : "Finalise this run"}
            <IconCheck className="size-4" />
          </Button>
          <Button variant="ghost" onClick={onStartAnother}>
            Start another run
          </Button>
          <Mono>
            {approved.length} of {sections.length} sections selected
          </Mono>
        </div>
      </Panel>
    </div>
  );
}
