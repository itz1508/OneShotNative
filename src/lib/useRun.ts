/**
 * Run orchestration for the UI.
 *
 * The System owns the workflow; this hook only *observes* it. It subscribes to
 * the live channel, starts the run, and applies workflow commands on request.
 * It never decides that a stage ran, and it never fills a gap the System left —
 * a failed run shows the System's own explanation.
 *
 * Gap fixes applied here:
 *   Gap 2  — AbortController: the startRun fetch is cancelled on reset / new run.
 *   Gap 6  — Output persistence: completed outputs are saved to sessionStorage
 *            so a page reload does not lose a delivered result.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { newRunId, openStream, sendCommand, startRun } from "./api";
import { log } from "./eventLog";
import { clearSavedOutput, restoreOutput, saveOutput } from "./useConnection";
import { validateRunRequest } from "./validation";
import type {
  OneShotOutput,
  ProcessTraceEvent,
  RunRequest,
  StreamEvent,
  WorkflowCommand,
  WorkflowState,
} from "./schema";

export type RunPhase = "idle" | "running" | "delivered" | "failed";

export interface RunController {
  phase: RunPhase;
  state: WorkflowState;
  trace: ProcessTraceEvent[];
  output: OneShotOutput | null;
  error: string | null;
  live: boolean;
  runId: string | null;
  start: (request: RunRequest) => void;
  command: (command: WorkflowCommand, next?: WorkflowState) => Promise<void>;
  reset: () => void;
}

export function useRun(): RunController {
  const [phase, setPhase] = useState<RunPhase>("idle");
  const [state, setState] = useState<WorkflowState>("Starting");
  const [trace, setTrace] = useState<ProcessTraceEvent[]>([]);
  const [output, setOutput] = useState<OneShotOutput | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(false);

  const runIdRef = useRef<string | null>(null);
  const stopRef = useRef<(() => void) | null>(null);
  // Gap 2 — AbortController for the startRun fetch.
  const abortRef = useRef<AbortController | null>(null);

  // Gap 6 — Restore a persisted output on first mount (page-reload recovery).
  useEffect(() => {
    const saved = restoreOutput();
    if (!saved) return;
    runIdRef.current = saved.runId;
    setOutput(saved.output);
    setState(saved.output.state);
    setPhase("delivered");
    log("info", "restore", "Restored output from sessionStorage", {
      runId: saved.runId,
    });
  }, []);

  const reset = useCallback(() => {
    stopRef.current?.();
    stopRef.current = null;
    abortRef.current?.abort();
    abortRef.current = null;
    runIdRef.current = null;
    clearSavedOutput();
    setPhase("idle");
    setState("Starting");
    setTrace([]);
    setOutput(null);
    setError(null);
    setLive(false);
  }, []);

  const start = useCallback(
    (request: RunRequest) => {
      // Cancel any in-flight fetch from a previous run (Gap 2).
      stopRef.current?.();
      abortRef.current?.abort();

      // Gap 5 — Pre-flight validation before opening stream or network fetch.
      const validation = validateRunRequest(request);
      if (!validation.valid) {
        const errorMsg = validation.errors.join(" ");
        setError(errorMsg);
        setPhase("failed");
        log("warn", "validation", errorMsg, { runId: request.run_id });
        return;
      }

      const runId = request.run_id ?? newRunId();
      runIdRef.current = runId;
      const controller = new AbortController();
      abortRef.current = controller;

      log("info", "run", "Starting run", { runId });

      setPhase("running");
      setState("Starting");
      setTrace([]);
      setOutput(null);
      setError(null);
      setLive(false);

      const handle = (event: StreamEvent) => {
        switch (event.type) {
          case "hello":
            setState(event.state);
            setLive(true);
            break;
          case "state":
            setState(event.state);
            break;
          case "trace":
            // Appended in the order the System emitted it. The index comes from
            // the System, so the display cannot invent an ordering.
            setTrace((previous) => [...previous, event.event]);
            break;
          case "output":
            setOutput(event.output);
            setState(event.output.state);
            // Gap 6 — persist so a page reload does not lose the result.
            saveOutput(runId, event.output);
            break;
          case "failed":
            setError(event.message);
            setPhase("failed");
            log("error", "run", event.message, { runId });
            break;
          default:
            break;
        }
      };

      // Open the channel first: the System replays anything it has already
      // observed, so subscribing ahead of the run cannot lose a step.
      stopRef.current = openStream(runId, {
        onEvent: handle,
        onOpen: () => setLive(true),
        onClosed: () => setLive(false),
      });

      startRun({ ...request, run_id: runId }, controller.signal)
        .then((result) => {
          setOutput(result);
          setState(result.state);
          setPhase("delivered");
          // Gap 6 — persist final result.
          saveOutput(runId, result);
          log("info", "run", "Delivered", { runId });
        })
        .catch((cause: unknown) => {
          // AbortError means the user triggered a reset — not a real failure.
          if (cause instanceof DOMException && cause.name === "AbortError") return;
          const msg =
            cause instanceof Error ? cause.message : "OneShot could not start this run.";
          setError(msg);
          setPhase("failed");
          log("error", "run", msg, { runId });
        })
        .finally(() => {
          setLive(false);
        });
    },
    [],
  );

  const command = useCallback(
    async (cmd: WorkflowCommand, optimistic?: WorkflowState) => {
      const runId = runIdRef.current;
      if (!runId) return;
      let previousState: WorkflowState = "Starting";
      setState((prev) => {
        previousState = prev;
        return optimistic ?? prev;
      });
      log("info", "command", cmd, { runId });
      try {
        const result = await sendCommand(runId, cmd);
        setState(result.state);
      } catch (cause: unknown) {
        // Gap 2: Rollback optimistic update on server error
        if (optimistic) {
          setState(previousState);
        }
        const msg =
          cause instanceof Error ? cause.message : "The System refused that command.";
        setError(msg);
        log("error", "command", msg, { runId });
      }
    },
    [],
  );

  return { phase, state, trace, output, error, live, runId: runIdRef.current, start, command, reset };
}