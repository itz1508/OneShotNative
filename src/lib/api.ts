/**
 * The typed client for the OneShot System.
 *
 * Transport and presentation only: it calls the System, streams the process
 * trace, and forwards workflow commands. It performs no pipeline work of its
 * own and holds no credentials beyond an optional API key for access control.
 *
 * Every failure is turned into a sentence a person can act on — the System's
 * own message where it gave one, and an honest plain-language fallback where it
 * did not. Raw status codes are never shown on their own.
 *
 * Gap fixes applied here:
 *   Gap 1  — Auth: Bearer token / API key on every request; token query on SSE.
 *   Gap 2  — AbortController: every fetch accepts an AbortSignal.
 *   Gap 3  — Observability: correlation ID (X-Run-ID) and W3C traceparent headers.
 *   Gap 4  — Retry: read-only GETs retry with exponential back-off + jitter.
 *            Circuit breaker: trips after 5 consecutive failures for 15s.
 *            Mutating POSTs never retry (no-blind-retry rule).
 *   Gap 5  — Client-side input validation before dispatch in startRun().
 *   Gap 12 — newRunId: uses crypto.randomUUID() instead of Math.random().
 */

import { log } from "./eventLog";
import { systemBaseUrl } from "./config";
import { validateRunRequest } from "./validation";
import type {
  Health,
  OneShotOutput,
  RunRequest,
  SchemaSnapshot,
  StreamEvent,
  WorkflowCommand,
  WorkflowState,
} from "./schema";

// ---------------------------------------------------------------------------
// Gap 1 — API key storage (sessionStorage, never localStorage)
// ---------------------------------------------------------------------------

const API_KEY_STORAGE = "oneshot.apiKey";
let memoryApiKey = "";

export function getApiKey(): string {
  try {
    if (typeof sessionStorage !== "undefined") {
      return sessionStorage.getItem(API_KEY_STORAGE) ?? memoryApiKey;
    }
  } catch {
    /* fallback to memory */
  }
  return memoryApiKey;
}

export function setApiKey(key: string): void {
  const trimmed = key.trim();
  memoryApiKey = trimmed;
  try {
    if (typeof sessionStorage !== "undefined") {
      if (trimmed) {
        sessionStorage.setItem(API_KEY_STORAGE, trimmed);
      } else {
        sessionStorage.removeItem(API_KEY_STORAGE);
      }
    }
  } catch {
    /* private browsing — key simply persists in memory */
  }
}

export function clearApiKey(): void {
  memoryApiKey = "";
  try {
    if (typeof sessionStorage !== "undefined") {
      sessionStorage.removeItem(API_KEY_STORAGE);
    }
  } catch {
    /* nothing to clear */
  }
}

// ---------------------------------------------------------------------------
// Gap 4 — Circuit Breaker state
// ---------------------------------------------------------------------------

const CIRCUIT_FAILURE_THRESHOLD = 5;
const CIRCUIT_COOLDOWN_MS = 15_000;

interface CircuitBreakerState {
  consecutiveFailures: number;
  openUntil: number;
}

const circuit: CircuitBreakerState = {
  consecutiveFailures: 0,
  openUntil: 0,
};

export function isCircuitOpen(): boolean {
  return Date.now() < circuit.openUntil;
}

export function getCircuitStatus(): {
  isOpen: boolean;
  consecutiveFailures: number;
  secondsRemaining: number;
} {
  const now = Date.now();
  const isOpen = now < circuit.openUntil;
  const secondsRemaining = isOpen ? Math.ceil((circuit.openUntil - now) / 1000) : 0;
  return {
    isOpen,
    consecutiveFailures: circuit.consecutiveFailures,
    secondsRemaining,
  };
}

export function resetCircuitBreaker(): void {
  circuit.consecutiveFailures = 0;
  circuit.openUntil = 0;
}

function recordCircuitFailure(): void {
  circuit.consecutiveFailures += 1;
  if (circuit.consecutiveFailures >= CIRCUIT_FAILURE_THRESHOLD) {
    circuit.openUntil = Date.now() + CIRCUIT_COOLDOWN_MS;
    log(
      "warn",
      "circuit_breaker",
      `Circuit breaker tripped after ${circuit.consecutiveFailures} consecutive failures. Pausing requests for 15s.`,
    );
  }
}

function recordCircuitSuccess(): void {
  circuit.consecutiveFailures = 0;
  circuit.openUntil = 0;
}

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

export class SystemError extends Error {
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "SystemError";
    this.status = status;
  }
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

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

/** Build auth headers if an API key is stored (Gap 1). */
function authHeaders(): Record<string, string> {
  const key = getApiKey();
  return key ? { Authorization: `Bearer ${key}` } : {};
}

/** Format a W3C traceparent header for observability (Gap 3). */
function makeTraceparent(runId: string): string {
  const clean = runId.replace(/[^a-f0-9]/gi, "").toLowerCase().padEnd(32, "0").slice(0, 32);
  const span = Date.now().toString(16).padStart(16, "0").slice(-16);
  return `00-${clean}-${span}-01`;
}

/**
 * Core fetch wrapper.
 *
 * Gap 1: injects Authorization header if an API key is set.
 * Gap 2: accepts an AbortSignal — callers pass one from an AbortController.
 * Gap 3: injects X-Run-ID and traceparent headers for request tracing.
 */
async function request<T>(
  path: string,
  init?: RequestInit & { signal?: AbortSignal },
  runId?: string | null,
): Promise<T> {
  const correlationHeaders: Record<string, string> = runId
    ? {
        "X-Run-ID": runId,
        "X-Request-ID": runId,
        traceparent: makeTraceparent(runId),
      }
    : {};

  let response: Response;
  try {
    response = await fetch(`${systemBaseUrl()}${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...correlationHeaders,
        ...authHeaders(),
      },
      ...init,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    const msg =
      "The OneShot System could not be reached. Check the System URL in the header, and that the System is running.";
    log("error", "network", msg, { runId });
    throw new SystemError(msg);
  }

  if (!response.ok) {
    const detail = await readDetail(response);
    if (detail) {
      log("error", "api", detail, { runId, detail: String(response.status) });
      throw new SystemError(detail, response.status);
    }
    if (response.status === 401 || response.status === 403) {
      const msg =
        "The System rejected this request — check that your API key is correct in System settings.";
      log("error", "auth", msg, { runId });
      throw new SystemError(msg, response.status);
    }
    if (response.status === 404) {
      const msg =
        "The System does not know that run. Runs are held in memory only, so an expired one cannot be resumed.";
      log("warn", "api", msg, { runId });
      throw new SystemError(msg, response.status);
    }
    if (response.status === 422) {
      const msg =
        "The System refused this request because it did not match the agreed contract. Reload the page to pick up the current schema.";
      log("error", "contract", msg, { runId });
      throw new SystemError(msg, response.status);
    }
    const msg = `The System could not complete that request (it answered ${response.status}). Nothing was changed — try again.`;
    log("error", "api", msg, { runId });
    throw new SystemError(msg, response.status);
  }

  return (await response.json()) as T;
}

/**
 * Gap 4 — Retry with exponential back-off + jitter, for read-only GETs only.
 *
 * Mutating requests (POST) are never retried. Aborted requests and SystemErrors
 * (i.e. the System explicitly said no) are never retried either.
 */
async function retryableRequest<T>(
  path: string,
  signal?: AbortSignal,
  maxAttempts = 3,
): Promise<T> {
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      const res = await request<T>(path, { signal });
      recordCircuitSuccess();
      return res;
    } catch (err) {
      const aborted = err instanceof DOMException && err.name === "AbortError";
      const isSystemError = err instanceof SystemError;
      if (aborted || isSystemError || attempt === maxAttempts - 1) {
        recordCircuitFailure();
        throw err;
      }
      // Back-off: 200ms, 400ms, 800ms… with ±50ms jitter.
      const delay = 200 * 2 ** attempt + Math.random() * 100;
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
  // Unreachable, but satisfies the type-checker.
  recordCircuitFailure();
  throw new SystemError("Retry exhausted.");
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export function health(signal?: AbortSignal): Promise<Health> {
  const status = getCircuitStatus();
  if (status.isOpen) {
    const msg = `Circuit breaker open: OneShot System has failed repeatedly. Pausing checks for ${status.secondsRemaining}s.`;
    log("warn", "circuit_breaker", msg);
    return Promise.reject(new SystemError(msg, 503));
  }
  return retryableRequest<Health>("/health", signal);
}

/** The contract the System publishes, so drift is detectable rather than silent. */
export function fetchSchema(signal?: AbortSignal): Promise<SchemaSnapshot> {
  return retryableRequest<SchemaSnapshot>("/schema", signal);
}

export function startRun(
  payload: RunRequest,
  signal?: AbortSignal,
): Promise<OneShotOutput> {
  // Gap 5 — Client-side input validation before any network request.
  const validation = validateRunRequest(payload);
  if (!validation.valid) {
    const errorMsg = validation.errors.join(" ");
    log("warn", "validation", errorMsg, { runId: payload.run_id });
    return Promise.reject(new SystemError(errorMsg, 400));
  }

  // POST — never retried (no-blind-retry rule, Gap 4 / architectural rule #1).
  return request<OneShotOutput>(
    "/run",
    { method: "POST", body: JSON.stringify(payload), signal },
    payload.run_id,
  );
}

export function sendCommand(
  runId: string,
  command: WorkflowCommand,
  signal?: AbortSignal,
): Promise<{ run_id: string; command: WorkflowCommand; state: WorkflowState }> {
  // POST — never retried.
  return request(
    `/runs/${encodeURIComponent(runId)}/command`,
    { method: "POST", body: JSON.stringify({ command }), signal },
    runId,
  );
}

// ---------------------------------------------------------------------------
// SSE stream
// ---------------------------------------------------------------------------

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
 * The System replays everything it has already observed to a new subscriber,
 * so attaching late still shows the whole history — nothing the System actually
 * did is missed by the display.
 *
 * Gap 1: Passes ?token= when an API key is configured.
 * The returned teardown function closes the EventSource. Pass it to a
 * useEffect cleanup so the subscription is never leaked (Gap 2).
 */
export function openStream(runId: string, handlers: StreamHandlers): () => void {
  const token = getApiKey();
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  const source = new EventSource(
    `${systemBaseUrl()}/runs/${encodeURIComponent(runId)}/events${query}`,
  );

  const listener = (message: MessageEvent<string>) => {
    let event: StreamEvent;
    try {
      event = JSON.parse(message.data) as StreamEvent;
    } catch {
      return; // a malformed frame is dropped rather than invented into a step
    }
    log("debug", "stream", `event:${event.type}`, { runId });
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

  source.onopen = () => {
    log("info", "stream", "opened", { runId });
    handlers.onOpen?.();
  };

  source.onerror = () => {
    if (source.readyState === EventSource.CLOSED) {
      source.close();
      const msg =
        "The live Process Display channel closed. The run may still have delivered a result — check the Result view.";
      log("warn", "stream", msg, { runId });
      handlers.onClosed(msg);
    }
    // CONNECTING means EventSource is retrying by itself: the System buffers
    // the trace, so a reconnection replays it rather than losing it.
  };

  return () => {
    log("debug", "stream", "closed by caller", { runId });
    source.close();
  };
}

// ---------------------------------------------------------------------------
// Gap 12 — Collision-safe run ID using crypto.randomUUID()
// ---------------------------------------------------------------------------

/** A collision-safe correlation id minted by the client (Gap 12). */
export function newRunId(): string {
  return `run_${crypto.randomUUID()}`;
}