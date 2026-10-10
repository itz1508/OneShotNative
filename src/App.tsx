/**
 * OneShot — the client.
 *
 * One objective, the sources it applies to, and a process you can watch. The
 * System is a separate Python service that owns the pipeline; this app declares
 * the request, observes the run, and presents what came back. It holds no keys,
 * performs no pipeline work, and never invents a step or a result the System did
 * not report.
 */

import { useCallback, useState } from "react";

import { EditorReview } from "./components/EditorReview";
import { ProcessDisplay } from "./components/ProcessDisplay";
import { ResultPanel } from "./components/ResultPanel";
import { SourcePanel } from "./components/SourcePanel";
import { SystemStatus } from "./components/SystemStatus";
import { IconAlert, IconCheck, IconInfo } from "./components/icons";
import { Button, Callout, EmptyState, Panel, Pill } from "./components/ui";
import type { RunRequest, WorkflowState } from "./lib/schema";
import { useRun } from "./lib/useRun";

const STATE_TONE: Record<WorkflowState, "neutral" | "accent" | "success" | "warning"> = {
  Starting: "neutral",
  "Process Display": "accent",
  Pausing: "warning",
  Paused: "warning",
  "Editor Review": "accent",
  Finalized: "success",
};

export default function App() {
  const run = useRun();
  const [statusOpen, setStatusOpen] = useState(false);
  const [lastRequest, setLastRequest] = useState<RunRequest | null>(null);

  const running = run.phase === "running";
  const finalized = run.state === "Finalized";
  const pausing = run.state === "Pausing";
  const reviewOpen = run.state === "Editor Review" || finalized;
  const showProcess = running || run.trace.length > 0 || run.phase === "failed";

  const handleRun = useCallback(
    (request: RunRequest) => {
      setLastRequest(request);
      run.start(request);
    },
    [run],
  );

  const goToReview = useCallback(() => {
    void run.command("review", "Editor Review");
  }, [run]);

  const finalize = useCallback(() => {
    void run.command("finalize", "Finalized");
  }, [run]);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-20 border-b border-border bg-background/85 backdrop-blur">
        <div className="mx-auto flex w-full max-w-[100rem] flex-wrap items-center gap-3 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-2.5">
            <span
              aria-hidden="true"
              className="grid size-8 place-items-center rounded-md border border-accent/45 text-accent"
            >
              <IconCheck className="size-4" />
            </span>
            <div>
              <h1 className="font-heading text-base leading-tight text-foreground">OneShot</h1>
              <p className="text-xs text-muted-foreground">
                one objective · declared sources · an inspectable process
              </p>
            </div>
          </div>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            <Pill tone={STATE_TONE[run.state]}>{run.state}</Pill>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setStatusOpen((open) => !open)}
              aria-expanded={statusOpen}
              aria-controls="system-status"
            >
              System
            </Button>
          </div>
        </div>

        {statusOpen ? (
          <div id="system-status" className="mx-auto w-full max-w-[100rem] px-4 pb-4 sm:px-6">
            <Panel>
              <SystemStatus />
            </Panel>
          </div>
        ) : null}
      </header>

      <main id="main" className="mx-auto w-full max-w-[100rem] px-4 py-6 sm:px-6 lg:py-8">
        <div className="grid gap-6 lg:grid-cols-[minmax(0,24rem)_minmax(0,1fr)] lg:items-start">
          <div className="space-y-4 lg:sticky lg:top-24">
            <SourcePanel running={running} onRun={handleRun} hasResult={run.output !== null} />
          </div>

          <div className="space-y-4">
            {run.error ? (
              <Callout tone="danger" icon={<IconAlert />} title="This run did not deliver">
                <p>{run.error}</p>
                <p>
                  Nothing was invented to cover the failure. The trace above shows how far the System
                  got.
                </p>
                {lastRequest ? (
                  <div className="pt-1">
                    <Button size="sm" variant="secondary" onClick={() => run.start(lastRequest)}>
                      Run it again
                    </Button>
                  </div>
                ) : null}
              </Callout>
            ) : null}

            {!run.error && run.phase === "delivered" && !finalized ? (
              <Callout tone="success" icon={<IconCheck />} title="Run delivered">
                <p>
                  The System finished and reported an output. Read it below, then review and finalise
                  when you are happy with it.
                </p>
              </Callout>
            ) : null}

            {showProcess ? (
              <Panel>
                <ProcessDisplay
                  state={run.state}
                  trace={run.trace}
                  live={run.live}
                  running={running}
                  error={null}
                  onPause={() => void run.command("pause", "Pausing")}
                  onResume={() => void run.command("resume", "Process Display")}
                  pausePending={pausing}
                />
              </Panel>
            ) : null}

            {run.output && !reviewOpen ? (
              <ResultPanel output={run.output} canReview={!finalized} onReview={goToReview} />
            ) : null}

            {run.output && reviewOpen ? (
              <EditorReview
                output={run.output}
                finalized={finalized}
                busy={run.state === "Editor Review" && run.phase !== "delivered"}
                onFinalize={finalize}
                onStartAnother={run.reset}
              />
            ) : null}

            {!run.output && !showProcess && !run.error ? (
              <Panel>
                <EmptyState icon={<IconInfo />} title="Nothing has run yet">
                  Describe the objective on the left, hand over the sources it applies to, and press
                  Run. OneShot will Scan what you gave it, Read each source, Prepare the context, run
                  a Quality &amp; Coverage Check, and only then Generate — so a thin context shows up
                  as a thin result rather than a confident guess.
                </EmptyState>
              </Panel>
            ) : null}
          </div>
        </div>
      </main>

      <footer className="border-t border-border">
        <div className="mx-auto w-full max-w-[100rem] px-4 py-5 sm:px-6">
          <p className="text-xs leading-relaxed text-muted-foreground">
            This app is a client. The System is a separate service that holds its own provider keys;
            no credential passes through the browser. Runs are held in the System's memory only, so a
            run that has expired cannot be resumed — the trace and the result you see are what it
            delivered.
          </p>
        </div>
      </footer>
    </div>
  );
}
