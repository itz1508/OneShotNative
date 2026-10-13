/**
 * The typed client for the OneShot System.
 *
 * Transport and presentation only: it calls the System, streams the process
 * trace, and forwards workflow commands. It performs no pipeline work of its own
 * and holds no credentials.
 *
 * Every failure is turned into a sentence a person can act on — the System's own
 * message where it gave one, and an honest plain-language fallback where it did
 * not. Raw status codes are never shown on their own.
 */

import { systemBaseUrl } from "./config";
import type {
  Health,
  OneShotOutput,
  PromptCreateRequest,
  PromptDetail,
  PromptDraftRequest,
  PromptSummary,
  PromptVersionView,
  RunRequest,
  SchemaSnapshot,
  StreamEvent,
  WorkflowCommand,
  WorkflowState,
} from "./schema";

export class SystemError extends Error {
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "SystemError";
    this.status = status;
  }
}

/** Turn whatever the System said into one readable sentence. */
async function readDetail(response: Response): Promise<string | null> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string") return detail;
      if (Array.isArray(detail)) {
        const parts = detail
          .map((item) => {
            const entry = item as { loc?: unknown[]; msg?: string };
            const where = Array.isArray(entry.loc)
              ? entry.loc.filter((p) => p !== "body").join(".")
              : "";
            return where ? `${where}: ${entry.msg ?? "invalid"}` : entry.msg ?? "invalid";
          })
          .filter(Boolean);
        if (parts.length) {
          return `OneShot refused this request — ${parts.join("; ")}.`;
        }
      }
    }
  } catch {
    /* no JSON body to read */
  }
  return null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${systemBaseUrl()}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new SystemError(
      "The OneShot System could not be reached. Check the System URL in the header, and that the System is running.",
    );
  }

  if (!response.ok) {
    const detail = await readDetail(response);
    if (detail) throw new SystemError(detail, response.status);
    if (response.status === 404) {
      throw new SystemError(
        "The System does not know that run. Runs are held in memory only, so an expired one cannot be resumed.",
        response.status,
      );
    }
    if (response.status === 422) {
      throw new SystemError(
        "The System refused this request because it did not match the agreed contract. Reload the page to pick up the current schema.",
        response.status,
      );
    }
    throw new SystemError(
      `The System could not complete that request (it answered ${response.status}). Nothing was changed — try again.`,
      response.status,
    );
  }

  return (await response.json()) as T;
}

export function health(): Promise<Health> {
  return request<Health>("/health");
}

/** The contract the System publishes, so drift is detectable rather than silent. */
export function fetchSchema(): Promise<SchemaSnapshot> {
  return request<SchemaSnapshot>("/schema");
}

export function startRun(payload: RunRequest): Promise<OneShotOutput> {
  return request<OneShotOutput>("/run", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/* -------------------------------------------------------------------------- *
 * Prompt Workspace
 *
 * The System owns prompts and their versions; the client only reads and writes
 * them. Saving a draft never publishes and never runs — that is the whole point
 * of the two distinct calls below.
 * -------------------------------------------------------------------------- */

export function listPrompts(): Promise<PromptSummary[]> {
  return request<PromptSummary[]>("/prompts");
}

export function createPrompt(payload: PromptCreateRequest): Promise<PromptDetail> {
  return request<PromptDetail>("/prompts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getPrompt(promptId: string): Promise<PromptDetail> {
  return request<PromptDetail>(`/prompts/${encodeURIComponent(promptId)}`);
}

/** Save the draft. Never publishes a version and never starts a run. */
export function savePromptDraft(
  promptId: string,
  payload: PromptDraftRequest,
): Promise<PromptDetail> {
  return request<PromptDetail>(`/prompts/${encodeURIComponent(promptId)}/draft`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function publishPromptVersion(promptId: string): Promise<PromptVersionView> {
  return request<PromptVersionView>(`/prompts/${encodeURIComponent(promptId)}/versions`, {
    method: "POST",
  });
}

export function listPromptVersions(promptId: string): Promise<PromptVersionView[]> {
  return request<PromptVersionView[]>(`/prompts/${encodeURIComponent(promptId)}/versions`);
}

export function getPromptVersion(promptId: string, number: number): Promise<PromptVersionView> {
  return request<PromptVersionView>(
    `/prompts/${encodeURIComponent(promptId)}/versions/${number}`,
  );
}

export function sendCommand(
  runId: string,
  command: WorkflowCommand,
): Promise<{ run_id: string; command: WorkflowCommand; state: WorkflowState }> {
  return request(`/runs/${encodeURIComponent(runId)}/command`, {
    method: "POST",
    body: JSON.stringify({ command }),
  });
}

export interface StreamHandlers {
  onEvent: (event: StreamEvent) => void;
  onClosed: (message: string) => void;
  onOpen?: () => void;
}

const STREAM_EVENTS: StreamEvent["type"][] = [
  "hello",
  "state",
  "trace",
  "output",
  "failed",
  "heartbeat",
  "end",
];

/**
 * Subscribe to a run's process trace.
 *
 * The System replays everything it has already observed to a new subscriber, so
 * attaching late still shows the whole history — nothing the System actually did
 * is missed by the display.
 */
export function openStream(runId: string, handlers: StreamHandlers): () => void {
  const source = new EventSource(
    `${systemBaseUrl()}/runs/${encodeURIComponent(runId)}/events`,
  );

  const listener = (message: MessageEvent<string>) => {
    let event: StreamEvent;
    try {
      event = JSON.parse(message.data) as StreamEvent;
    } catch {
      return; // a malformed frame is dropped rather than invented into a step
    }
    handlers.onEvent(event);
    if (event.type === "end" || event.type === "failed") {
      // The System has closed the run; stop here so EventSource does not
      // reconnect and replay the history a second time.
      source.close();
    }
  };

  for (const name of STREAM_EVENTS) {
    source.addEventListener(name, listener as EventListener);
  }

  source.onopen = () => handlers.onOpen?.();

  source.onerror = () => {
    if (source.readyState === EventSource.CLOSED) {
      source.close();
      handlers.onClosed(
        "The live Process Display channel closed. The run may still have delivered a result — check the Result view.",
      );
    }
    // CONNECTING means EventSource is retrying by itself: the System buffers the
    // trace, so a reconnection replays it rather than losing it.
  };

  return () => source.close();
}

/** A correlation id for the run, minted by the client so the channel can open first. */
export function newRunId(): string {
  const random = Math.random().toString(36).slice(2, 8);
  return `run_${Date.now().toString(36)}_${random}`;
}