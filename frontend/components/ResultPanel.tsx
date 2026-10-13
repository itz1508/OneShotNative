/**
 * The Result view: the System's own output, presented and nothing more.
 *
 * Every field here is read straight from `output.json`. The view never
 * regenerates, reorders or repairs what the System produced — an absent section
 * stays absent, and the quality check that governed the run is shown beside the
 * result rather than quietly summarised into a single verdict.
 */

import { useState } from "react";

import type {
  Citation,
  LimitationSeverity,
  OneShotOutput,
  QualityOutcome,
  ScanOutcome,
} from "../lib/schema";
import {
  copyText,
  downloadText,
  formatCount,
  shortDigest,
  sourceTypeLabel,
} from "../lib/format";
import {
  IconAlert,
  IconCheck,
  IconDownload,
  IconEdit,
  IconInfo,
  IconLock,
  SOURCE_ICONS,
} from "./icons";
import { Button, Callout, EmptyState, Panel, Pill, cx } from "./ui";

const QUALITY_TONE: Record<QualityOutcome, "success" | "warning" | "danger"> = {
  Sufficient: "success",
  Incomplete: "warning",
  Unusable: "danger",
};

const OUTCOME_TONE: Record<ScanOutcome, "success" | "warning" | "danger" | "neutral"> = {
  accessible: "success",
  unsupported: "warning",
  unreadable: "danger",
  excluded: "neutral",
};

const SEVERITY_TONE: Record<LimitationSeverity, "accent" | "warning" | "danger"> = {
  info: "accent",
  warning: "warning",
  error: "danger",
};

export function CitationList({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null;
  return (
    <ul className="space-y-1.5">
      {citations.map((citation, index) => (
        <li
          key={`${citation.source_id}-${citation.locator}-${index}`}
          className="flex flex-wrap items-baseline gap-x-2 gap-y-1 border-l-2 border-border pl-2.5 font-mono text-[11px] text-muted-foreground"
        >
          <span className="text-accent">{citation.source_id}</span>
          <span className="text-foreground/80">{citation.source_label}</span>
          <span aria-hidden="true">·</span>
          <span>{citation.locator}</span>
          <span aria-hidden="true">·</span>
          <span title={citation.reported_content_sha256}>
            sha {shortDigest(citation.reported_content_sha256)}
          </span>
        </li>
      ))}
    </ul>
  );
}

export interface ResultPanelProps {
  output: OneShotOutput;
  canReview: boolean;
  onReview: () => void;
}

export function ResultPanel({ output, canReview, onReview }: ResultPanelProps) {
  const [copied, setCopied] = useState(false);
  const { quality, supported_task: task, generated } = output;

  async function copyJson() {
    const ok = await copyText(JSON.stringify(output, null, 2));
    setCopied(ok);
    window.setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="space-y-4">
      <Panel className="space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <h2 className="font-heading text-sm text-foreground">Result</h2>
            <p className="text-sm text-muted-foreground">
              The System's output for this run, exactly as delivered.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <Pill tone={QUALITY_TONE[quality.outcome]}>quality · {quality.outcome}</Pill>
            <Pill tone={task.supported ? "accent" : "warning"}>
              {task.supported ? "task supported" : "task not supported"}
            </Pill>
            {generated?.model ? <Pill tone="neutral">{generated.model}</Pill> : null}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[11px] text-muted-foreground">
          <span>{output.run_id}</span>
          <span aria-hidden="true">·</span>
          <span>schema {output.schema_version}</span>
          <span aria-hidden="true">·</span>
          <span>{output.trace.length} steps · {output.sources.length} sources</span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="primary" onClick={onReview} disabled={!canReview}>
            <IconEdit className="size-3.5" />
            Review &amp; finalise
          </Button>
          <Button size="sm" variant="secondary" onClick={() => void copyJson()}>
            {copied ? <IconCheck className="size-3.5" /> : null}
            {copied ? "Copied" : "Copy output.json"}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => downloadText(`${output.run_id}-output.json`, JSON.stringify(output, null, 2), "application/json")}
          >
            <IconDownload className="size-3.5" />
            Download
          </Button>
        </div>
      </Panel>

      {!task.supported ? (
        <Callout tone="warning" icon={<IconAlert />} title="The System judged this objective unsupported">
          <p>{task.statement}</p>
          <p>
            No result was invented to cover the gap. Narrow the objective, or hand over sources that
            speak to it directly.
          </p>
        </Callout>
      ) : null}

      {quality.outcome !== "Sufficient" ? (
        <Callout
          tone={quality.outcome === "Unusable" ? "danger" : "warning"}
          icon={<IconAlert />}
          title={`Coverage was ${quality.outcome.toLowerCase()}`}
        >
          {quality.reasons.length ? (
            <ul className="list-disc space-y-1 pl-4">
              {quality.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : (
            <p>The System did not record a reason for this verdict.</p>
          )}
        </Callout>
      ) : null}

      <Panel className="space-y-4">
        {generated ? (
          <article className="space-y-5">
            <header className="space-y-2">
              <h2 className="font-heading text-lg leading-snug text-foreground">{generated.title}</h2>
              {generated.summary ? (
                <p className="max-w-prose text-sm leading-relaxed text-muted-foreground">
                  {generated.summary}
                </p>
              ) : null}
            </header>

            {generated.sections.length === 0 ? (
              <EmptyState
                icon={<IconInfo />}
                title="The result carries no sections"
              >
                The System returned a title but no body sections. What you see here is the whole of
                it — nothing has been filled in on the client.
              </EmptyState>
            ) : (
              generated.sections.map((section, index) => (
                <section key={`${section.heading}-${index}`} className="space-y-2.5">
                  <h3 className="font-heading text-sm uppercase tracking-[0.12em] text-muted-foreground">
                    {section.heading}
                  </h3>
                  <p className="max-w-prose whitespace-pre-line text-sm leading-relaxed text-foreground">
                    {section.body}
                  </p>
                  <CitationList citations={section.citations} />
                </section>
              ))
            )}

            {generated.citations.length ? (
              <section className="space-y-2 border-t border-border pt-4">
                <h3 className="font-heading text-xs uppercase tracking-[0.14em] text-muted-foreground">
                  All citations
                </h3>
                <CitationList citations={generated.citations} />
              </section>
            ) : null}
          </article>
        ) : (
          <EmptyState icon={<IconInfo />} title="No result was generated">
            The pipeline ran and reported its process, but generation produced nothing — most often
            because no model provider is configured on the System, or the context did not clear the
            coverage check. OneShot leaves the gap visible rather than guessing to fill it.
          </EmptyState>
        )}
      </Panel>

      {quality.coverage.length ? (
        <Panel className="space-y-3">
          <h3 className="font-heading text-xs uppercase tracking-[0.14em] text-muted-foreground">
            Coverage
          </h3>
          <ul className="grid gap-2 sm:grid-cols-2">
            {quality.coverage.map((dimension) => (
              <li key={dimension.name} className="flex items-start gap-2.5">
                <span className={dimension.covered ? "mt-0.5 shrink-0 text-success" : "mt-0.5 shrink-0 text-warning"}>
                  {dimension.covered ? <IconCheck className="size-4" /> : <IconAlert className="size-4" />}
                </span>
                <div className="min-w-0 space-y-0.5">
                  <p className="text-sm text-foreground">{dimension.name}</p>
                  <p className="text-xs leading-relaxed text-muted-foreground">{dimension.detail}</p>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      <Panel className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-heading text-xs uppercase tracking-[0.14em] text-muted-foreground">
            Sources read
          </h3>
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <IconLock className="size-3.5" />
            provenance self-reported, unverified
          </span>
        </div>

        {output.sources.length === 0 ? (
          <EmptyState icon={<IconInfo />} title="No source provenance was reported">
            This run recorded no sources. That is unusual — check the process trace above for what
            the Scan stage actually observed.
          </EmptyState>
        ) : (
          <ul className="space-y-2">
            {output.sources.map((source) => {
              const TypeIcon = SOURCE_ICONS[source.type];
              return (
                <li
                  key={source.source_id}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-md border border-border bg-background/40 px-3 py-2.5"
                >
                  <TypeIcon className="size-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm text-foreground">{source.label}</p>
                    <p className="font-mono text-[11px] text-muted-foreground">
                      {source.source_id} · {sourceTypeLabel(source.type)} · {source.mode}
                    </p>
                  </div>
                  <Pill tone={OUTCOME_TONE[source.outcome]}>{source.outcome}</Pill>
                  <span
                    className="font-mono text-xs text-muted-foreground"
                    title={source.reported_content_sha256}
                  >
                    {shortDigest(source.reported_content_sha256)} · v{source.reported_source_version}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </Panel>

      {output.context_decisions.length ? (
        <Panel className="space-y-3">
          <h3 className="font-heading text-xs uppercase tracking-[0.14em] text-muted-foreground">
            What went into the context
          </h3>
          <ul className="space-y-2">
            {output.context_decisions.map((decision) => (
              <li key={decision.source_id} className="space-y-1 border-b border-border/60 pb-2 last:border-b-0 last:pb-0">
                <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                  <span className={decision.included ? "text-success" : "text-muted-foreground"}>
                    {decision.included ? <IconCheck className="size-4" /> : <IconInfo className="size-4" />}
                  </span>
                  <span className="font-mono text-[11px] text-accent">{decision.source_id}</span>
                  <span className="text-sm text-foreground">{decision.included ? "included" : "left out"}</span>
                  <span className="font-mono text-[11px] text-muted-foreground">
                    +{formatCount(decision.chars_included)} / −{formatCount(decision.chars_dropped)} chars
                  </span>
                </div>
                <p className="pl-6 text-xs leading-relaxed text-muted-foreground">{decision.reason}</p>
                {decision.note ? (
                  <p className="pl-6 text-xs leading-relaxed text-muted-foreground/80">{decision.note}</p>
                ) : null}
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      {output.limitations.length ? (
        <Panel className="space-y-3">
          <h3 className="font-heading text-xs uppercase tracking-[0.14em] text-muted-foreground">
            Limitations
          </h3>
          <ul className="space-y-2">
            {output.limitations.map((limitation, index) => (
              <li key={`${limitation.code}-${index}`} className="flex items-start gap-2.5">
                <span
                  className={cx(
                    "mt-0.5 shrink-0",
                    limitation.severity === "error"
                      ? "text-destructive"
                      : limitation.severity === "warning"
                        ? "text-warning"
                        : "text-accent",
                  )}
                >
                  {limitation.severity === "info" ? <IconInfo className="size-4" /> : <IconAlert className="size-4" />}
                </span>
                <div className="min-w-0 space-y-0.5">
                  <p className="text-sm leading-snug text-foreground">{limitation.message}</p>
                  <p className="font-mono text-[11px] text-muted-foreground">
                    <Pill tone={SEVERITY_TONE[limitation.severity]}>{limitation.severity}</Pill>{" "}
                    {limitation.code}
                    {limitation.source_id ? ` · ${limitation.source_id}` : ""}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}
    </div>
  );
}