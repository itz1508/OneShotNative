/**
 * Lightweight structured event log (Gap 3 — Observability).
 *
 * An in-memory ring buffer of timestamped log entries. The log is scoped by
 * runId so entries can be correlated with a specific run. The buffer is capped
 * at MAX_ENTRIES to avoid unbounded memory growth.
 *
 * No external sink — this is for in-browser diagnostics only. A future step
 * could drain these entries to an OpenTelemetry collector.
 */

export type LogLevel = "debug" | "info" | "warn" | "error";

export interface LogEntry {
  at: number;
  level: LogLevel;
  runId: string | null;
  type: string;
  message: string;
  detail?: string;
}

const MAX_ENTRIES = 500;

const entries: LogEntry[] = [];

export function log(
  level: LogLevel,
  type: string,
  message: string,
  opts: { runId?: string | null; detail?: string } = {},
): void {
  const entry: LogEntry = {
    at: Date.now(),
    level,
    type,
    message,
    runId: opts.runId ?? null,
    ...(opts.detail !== undefined ? { detail: opts.detail } : {}),
  };
  entries.push(entry);
  if (entries.length > MAX_ENTRIES) {
    entries.splice(0, entries.length - MAX_ENTRIES);
  }
  if (level === "error" || level === "warn") {
    console[level](`[oneshot:${type}]`, message, opts.detail ?? "");
  }
}

export function getLog(): readonly LogEntry[] {
  return entries;
}

export function clearLog(): void {
  entries.splice(0, entries.length);
}
