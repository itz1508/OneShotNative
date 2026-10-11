/**
 * Run orchestration for the UI.
 *
 * The System owns the workflow; this hook only *observes* it. It subscribes to
 * the live channel, starts the run, and applies workflow commands on request.
 * It never decides that a stage ran, and it never fills a gap the System left —
 * a failed run shows the System's own explanation.
 */

import { useCallback, useRef, useState } from "react";

import { newRunId, openStream, sendCommand, startRun } from "./api";
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

  const reset = useCallback(() => {
    stopRef.current?.();
    stopRef.current = null;
    runIdRef.current = null;
    setPhase("idle");
    setState("Starting");
    setTrace([]);
    setOutput(null);
    setError(null);
    setLive(false);
  }, []);

  const start = useCallback(
    (request: RunRequest) => {
      stopRef.current?.();
      const runId = request.run_id ?? newRunId();
      runIdRef.current = runId;

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
            break;
          case "failed":
            setError(event.message);
            setPhase("failed");
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

      startRun({ ...request, run_id: runId })
        .then((result) => {
          setOutput(result);
          setState(result.state);
          setPhase("delivered");
        })
        .catch((cause: unknown) => {
          setError(cause instanceof Error ? cause.message : "OneShot could not start this run.");
          setPhase("failed");
        })
        .finally(() => {
          setLive(false);
        });
    },
    [],
  );

  const command = useCallback(async (next: WorkflowCommand, optimistic?: WorkflowState) => {
    const runId = runIdRef.current;
    if (!runId) return;
    if (optimistic) setState(optimistic);
    try {
      const result = await sendCommand(runId, next);
      setState(result.state);
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : "The System refused that command.");
    }
  }, []);

  return { phase, state, trace, output, error, live, runId: runIdRef.current, start, command, reset };
}