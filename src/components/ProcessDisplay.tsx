/**
 * The running surface: a truthful, live view of what the System actually did.
 *
 * Two rules drive this component:
 *
 * 1. It renders only what the System reported. Steps arrive over the live
 *    channel in the order the System emitted them, with the System's own index —
 *    the display never orders, invents or infers a step.
 * 2. It is a live region. New activity is announced politely to screen readers,
 *    and the state machine is exposed as a navigable list with `aria-current`.
 */

import { useEffect, useMemo, useRef } from "react";

import type { ProcessTraceEvent, Stage, TraceStatus, WorkflowState } from "../lib/schema";
import { WORKFLOW_STATES } from "../lib/schema";
import { formatDuration } from "../lib/format";
import {
  IconAlert,
  IconCheck,
  IconInfo,
  IconPause,
  IconSkip,
  IconSpinner,
} from "./icons";
import { Button, Pill, cx } from "./ui";

const STATUS_STYLES: Record<
  TraceStatus,
  { icon: typeof IconCheck; tone: string; label: string }
> = {
  started: { icon: IconSpinner, tone: "text-primary", label: "started" },
  completed: { icon: IconCheck, tone: "text-success", label: "completed" },
  failed: { icon: IconAlert, tone: "text-destructive", label: "failed" },
  skipped: { icon: IconSkip, tone: "text-muted-foreground", label: "skipped" },
  info: { icon: IconInfo, tone: "text-muted-foreground", label: "observed" },
};

function TraceRow({ event }: { event: ProcessTraceEvent }) {
  const style = STATUS_STYLES[event.status];
  const Icon = style.icon;
  const spinning = event.status === "started";

  return (
    <li className="animate-trace-in border-b border-border/60 px-3 py-2.5 last:border-b-0">
      <div className="flex items-start gap-2.5">
        <span className={cx("mt-0.5 shrink-0", style.tone)}>
          <Icon className={spinning ? "animate-spin" : undefined} />
        </span>
        <div className="min-w-0 flex-1 space-y-1">
          <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className="font-mono text-[11px] tabular-nums text-muted-foreground/70">
              {String(event.index).padStart(2, "0")}
            </span>
            <span className="text-sm leading-snug text-foreground">{event.message}</span>
          </p>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[11px] text-muted-foreground">
            <span>{event.stage ?? "workflow"}</span>
            <span aria-hidden="true">·</span>
            <span>{style.label}</span>
            {event.source_id ? (
              <>
                <span aria-hidden="true">·</span>
                <span className="text-accent">{event.source_id}</span>
              </>
            ) : null}
            <span aria-hidden="true">·</span>
            <span className="tabular-nums">{formatDuration(event.at_ms)}</span>
          </div>
          {event.detail ? (
            <p className="max-w-prose text-xs leading-relaxed text-muted-foreground">
              {event.detail}
            </p>
          ) : null}
        </div>
      </div>
    </li>
  );
}

function Stepper({ state }: { state: WorkflowState }) {
  const activeIndex = WORKFLOW_STATES.indexOf(state);

  return (
    <ol
      aria-label="Workflow state"
      className="flex flex-wrap items-center gap-x-1.5 gap-y-2 font-mono text-[11px]"
    >
      {WORKFLOW_STATES.map((step, index) => {
        const isCurrent = index === activeIndex;
        const isPast = index < activeIndex;
        return (
          <li key={step} className="flex items-center gap-1.5">
            {index > 0 ? (
              <span aria-hidden="true" className="text-border">
                →
              </span>
            ) : null}
            <span
              aria-current={isCurrent ? "step" : undefined}
              className={cx(
                "rounded border px-1.5 py-0.5 transition-colors duration-200 ease-out",
                isCurrent
                  ? "border-primary text-primary"
                  : isPast
                    ? "border-border text-muted-foreground line-through decoration-border"
                    : "border-transparent text-muted-foreground/60",
              )}
            >
              {step}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export interface ProcessDisplayProps {
  state: WorkflowState;
  trace: ProcessTraceEvent[];
  live: boolean;
  running: boolean;
  error: string | null;
  onPause: () => void;
  onResume: () => void;
  pausePending: boolean;
}

export function ProcessDisplay({
  state,
  trace,
  live,
  running,
  error,
  onPause,
  onResume,
  pausePending,
}: ProcessDisplayProps) {
  const endRef = useRef<HTMLDivElement | null>(null);
  const count = trace.length;

  useEffect(() => {
    if (count === 0) return;
    endRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [count]);

  const stages = useMemo(() => {
    const seen: Stage[] = [];
    for (const event of trace) {
      if (event.stage && !seen.includes(event.stage)) seen.push(event.stage);
    }
    return seen;
  }, [trace]);

  const paused = state === "Paused" || state === "Pausing" || pausePending;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Pill tone={live ? "accent" : "neutral"}>
            <span
              aria-hidden="true"
              className={cx(
                "inline-block size-1.5 rounded-full bg-current",
                live && "animate-pulse-soft",
              )}
            />
            {live ? "live channel open" : running ? "connecting" : "channel closed"}
          </Pill>
          <Pill tone={state === "Finalized" ? "success" : state === "Editor Review" ? "accent" : "neutral"}>
            {state}
          </Pill>
          <span className="font-mono text-[11px] text-muted-foreground">
            {count} step{count === 1 ? "" : "s"} observed
            {stages.length ? ` · ${stages.length} stages` : ""}
          </span>
        </div>

        <div className="flex items-center gap-2">
          {paused ? (
            <Button
              variant="primary"
              size="sm"
              onClick={onResume}
              disabled={!running}
              aria-label="Resume the run"
            >
              <IconSpinner className="size-3.5" />
              Resume
            </Button>
          ) : (
            <Button
              variant="secondary"
              size="sm"
              onClick={onPause}
              disabled={!running || state === "Editor Review" || state === "Finalized"}
              aria-label="Pause the run"
            >
              <IconPause className="size-3.5" />
              Pause
            </Button>
          )}
        </div>
      </div>

      <Stepper state={state} />

      {error ? (
        <p
          role="alert"
          className="rounded-md border border-destructive/45 bg-destructive/5 px-3 py-2.5 text-sm leading-relaxed text-destructive"
        >
          {error}
        </p>
      ) : null}

      <div className="overflow-hidden rounded-md border border-border bg-background/40">
        {count === 0 ? (
          <p className="px-4 py-6 text-sm leading-relaxed text-muted-foreground">
            Nothing observed yet. This list fills in as the System works — and only with
            steps it actually performs.
          </p>
        ) : (
          <ol
            role="log"
            aria-live="polite"
            aria-atomic="false"
            aria-label="Process trace"
            className="scroll-slim max-h-[26rem] overflow-y-auto lg:max-h-[32rem]"
          >
            {trace.map((event) => (
              <TraceRow key={`${event.index}-${event.at_ms}`} event={event} />
            ))}
          </ol>
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}
