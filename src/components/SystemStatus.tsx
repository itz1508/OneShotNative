/**
 * The System connection panel: where the UI is pointed, and what that System can
 * truthfully do right now.
 *
 * The base URL is publishable configuration, so it is editable here rather than
 * hidden in a build step — the published app works with no `.env` file at all.
 * No credential ever passes through this component: the System holds its own
 * provider keys on its own host.
 */

import { useCallback, useEffect, useState } from "react";

import { clearApiKey, fetchSchema, getApiKey, getCircuitStatus, health, setApiKey } from "../lib/api";
import { clearOverride, isUsingOverride, systemBaseUrl, writeOverride } from "../lib/config";
import { clearLog, getLog } from "../lib/eventLog";
import { SCHEMA_VERSION, compareContract, type Health, type SchemaSnapshot } from "../lib/schema";
import { IconAlert, IconCheck, IconRefresh } from "./icons";
import { Button, Callout, Field, Hint, Mono, Pill, TextInput, cx } from "./ui";

export function SystemStatus() {
  const [url, setUrl] = useState<string>(systemBaseUrl());
  const [draftUrl, setDraftUrl] = useState<string>(systemBaseUrl());
  const [apiKey, setApiKeyState] = useState<string>(getApiKey());
  const [draftApiKey, setDraftApiKey] = useState<string>(getApiKey());
  const [state, setState] = useState<Health | null>(null);
  const [report, setReport] = useState<SchemaSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [editing, setEditing] = useState(false);
  const [showLogs, setShowLogs] = useState(false);

  const probe = useCallback(async (): Promise<boolean> => {
    setChecking(true);
    setError(null);
    try {
      setState(await health());
      try {
        setReport(await fetchSchema());
      } catch {
        // Reachable, but the contract could not be read: report nothing rather
        // than invent a drift verdict out of a missing answer.
        setReport(null);
      }
      return true;
    } catch (cause) {
      setState(null);
      setReport(null);
      setError(
        cause instanceof Error
          ? cause.message
          : "The OneShot System could not be reached.",
      );
      return false;
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let handle: number | undefined;

    // The System is a separate process and may still be starting when this page
    // loads, so the first probe retries a few times before it is called
    // unreachable. After that, "Re-check" is the only trigger.
    const attempt = async (remaining: number) => {
      const ok = await probe();
      if (cancelled || ok || remaining <= 0) return;
      handle = window.setTimeout(() => void attempt(remaining - 1), 1500);
    };

    void attempt(4);

    return () => {
      cancelled = true;
      if (handle !== undefined) window.clearTimeout(handle);
    };
  }, [probe]);

  const reachable = state !== null;
  const schemaMatches = state?.schema_version === SCHEMA_VERSION;
  const drift = report ? compareContract(report) : null;

  function applyUrl() {
    const saved = writeOverride(draftUrl);
    setUrl(saved || systemBaseUrl());
    setEditing(false);
    void probe();
  }

  function resetUrl() {
    clearOverride();
    const next = systemBaseUrl();
    setUrl(next);
    setDraftUrl(next);
    setEditing(false);
    void probe();
  }

  function applyApiKey() {
    setApiKey(draftApiKey);
    setApiKeyState(draftApiKey.trim());
  }

  function clearKey() {
    clearApiKey();
    setApiKeyState("");
    setDraftApiKey("");
  }

  const circuitStatus = getCircuitStatus();

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone={reachable ? "success" : error ? "danger" : "neutral"}>
          {reachable ? <IconCheck className="size-3" /> : <IconAlert className="size-3" />}
          {reachable ? "System reachable" : error ? "System unreachable" : "checking…"}
        </Pill>
        {circuitStatus.isOpen ? (
          <Pill tone="warning">Circuit open ({circuitStatus.secondsRemaining}s)</Pill>
        ) : null}
        <Mono>{url}</Mono>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => void probe()}
          disabled={checking}
          aria-label="Re-check the System"
        >
          <IconRefresh className={cx("size-3.5", checking && "animate-spin")} />
          Re-check
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setEditing((open) => !open)}
          aria-expanded={editing}
        >
          {editing ? "Close" : "Change URL / Auth"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setShowLogs((open) => !open)}
          aria-expanded={showLogs}
        >
          {showLogs ? "Hide logs" : "Diagnostics"}
        </Button>
      </div>

      {editing ? (
        <div className="space-y-2 rounded-md border border-border bg-background/40 p-3">
          <Field
            label="System base URL"
            htmlFor="system-url"
            hint="Publishable — this is not a secret."
          >
            <TextInput
              id="system-url"
              value={draftUrl}
              inputMode="url"
              spellCheck={false}
              placeholder="https://your-system-host"
              onChange={(event) => setDraftUrl(event.target.value)}
            />
          </Field>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="primary" onClick={applyUrl}>
              Use this System
            </Button>
            <Button size="sm" variant="ghost" onClick={resetUrl}>
              Reset to default
            </Button>
            {isUsingOverride() ? <Mono>browser override active</Mono> : null}
          </div>
          <Hint>
            Stored in this browser only. Leave it blank to use the System this dev server proxies
            to, or set <Mono>VITE_SYSTEM_BASE_URL</Mono> at build time to change the default for
            everyone.
          </Hint>

          {/* Gap 1 — API key */}
          <Field
            label="API key (optional)"
            htmlFor="api-key"
            hint="Stored in sessionStorage only — cleared when the tab is closed."
          >
            <div className="flex gap-2">
              <TextInput
                id="api-key"
                type="password"
                value={draftApiKey}
                placeholder="sk-…"
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => setDraftApiKey(event.target.value)}
              />
              <Button size="sm" variant="primary" onClick={applyApiKey}>
                Save
              </Button>
              {apiKey ? (
                <Button size="sm" variant="ghost" onClick={clearKey}>
                  Clear
                </Button>
              ) : null}
            </div>
          </Field>
          {apiKey ? (
            <Hint>API key active — sent as a Bearer token on every request.</Hint>
          ) : (
            <Hint>No key set. If the System requires one, add it here.</Hint>
          )}
        </div>
      ) : null}

      {error ? (
        <Callout
          tone="danger"
          icon={<IconAlert />}
          title="The UI cannot reach the System"
        >
          <p>{error}</p>
          <p>
            The System is a separate Python service — this UI only talks to it. It normally starts
            alongside the dev server; if it is not running, start it and use Re-check. If it is
            hosted somewhere else, set the URL above.
          </p>
        </Callout>
      ) : null}

      {state ? (
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          <div className="flex items-center justify-between gap-3 border-b border-border/60 pb-1.5">
            <dt className="text-muted-foreground">Schema</dt>
            <dd>
              {schemaMatches ? (
                <Pill tone="success">{state.schema_version} matches</Pill>
              ) : (
                <Pill tone="warning">
                  System {state.schema_version} · UI {SCHEMA_VERSION}
                </Pill>
              )}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-3 border-b border-border/60 pb-1.5">
            <dt className="text-muted-foreground">State kept</dt>
            <dd>
              {state.stateless ? (
                <Pill tone="accent">stateless — no history</Pill>
              ) : (
                <Pill tone="warning">unknown</Pill>
              )}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-3 border-b border-border/60 pb-1.5">
            <dt className="text-muted-foreground">Generate</dt>
            <dd>
              {state.generate.configured ? (
                <Pill tone="success">
                  {state.generate.provider}:{state.generate.model}
                </Pill>
              ) : (
                <Pill tone="warning">no provider configured</Pill>
              )}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-3 border-b border-border/60 pb-1.5">
            <dt className="text-muted-foreground">Transcription</dt>
            <dd>
              {state.transcribe.configured ? (
                <Pill tone="success">{state.transcribe.provider}</Pill>
              ) : (
                <Pill tone="warning">not configured</Pill>
              )}
            </dd>
          </div>
          <div className="flex items-center justify-between gap-3 sm:col-span-2">
            <dt className="text-muted-foreground">Inspection modes in v1</dt>
            <dd className="flex flex-wrap gap-1.5">
              {state.inspection_modes_v1.map((mode) => (
                <Pill key={mode}>{mode}</Pill>
              ))}
            </dd>
          </div>
        </dl>
      ) : null}

      {drift ? (
        drift.inSync ? (
          <div className="flex flex-wrap items-center gap-2">
            <Pill tone="success">
              <IconCheck className="size-3" />
              contract in sync
            </Pill>
            <Mono>{drift.serverVersion}</Mono>
            <Hint>
              GET /schema matches the client's mirror — {drift.publishedValues} vocabulary values
              checked.
            </Hint>
          </div>
        ) : (
          <Callout
            tone="warning"
            icon={<IconAlert />}
            title="The client and the System disagree on the contract"
          >
            <p>
              {drift.versionMismatch
                ? `The System reports schema ${drift.serverVersion}; this client mirrors ${drift.clientVersion}.`
                : `Both sides report schema ${drift.serverVersion}, but what the System publishes no longer matches this client:`}
            </p>
            {drift.missingModels.length > 0 ? (
              <p>
                Models the System no longer publishes:{" "}
                <Mono>{drift.missingModels.join(", ")}</Mono>.
              </p>
            ) : null}
            {drift.vocabularyGaps.length > 0 ? (
              <ul className="space-y-1">
                {drift.vocabularyGaps.map((gap) => (
                  <li key={gap.name}>
                    <Mono>{gap.name}</Mono> missing: {gap.values.join(", ")}
                  </li>
                ))}
              </ul>
            ) : null}
            <p>
              Reload the page to pick up the current contract. If it still disagrees, this client
              needs updating before its results can be trusted.
            </p>
          </Callout>
        )
      ) : null}

      {state && !state.generate.configured ? (
        <Callout tone="warning" icon={<IconAlert />} title="Generation is switched off on this System">
          <p>
            The pipeline still runs end to end and reports its full trace, but no result is
            generated — OneShot will not guess one. Set{" "}
            <Mono>ONESHOT_MODEL_PROVIDER</Mono>, <Mono>ONESHOT_MODEL</Mono> and the matching API
            key on the System host.
          </p>
        </Callout>
      ) : null}

      {showLogs ? (
        <div className="space-y-2 rounded-md border border-border bg-background/50 p-3">
          <div className="flex items-center justify-between">
            <h3 className="font-heading text-xs uppercase tracking-wider text-muted-foreground">
              Client Diagnostics & Event Log (Gap 3)
            </h3>
            <Button size="sm" variant="ghost" onClick={() => clearLog()}>
              Clear logs
            </Button>
          </div>
          <div className="max-h-48 overflow-y-auto space-y-1 font-mono text-[11px]">
            {getLog().length === 0 ? (
              <p className="text-muted-foreground">No events logged yet.</p>
            ) : (
              getLog()
                .slice(-25)
                .map((entry, idx) => (
                  <div key={idx} className="flex flex-wrap items-baseline gap-2 border-b border-border/40 pb-0.5">
                    <span className="text-foreground/50">
                      {new Date(entry.at).toLocaleTimeString()}
                    </span>
                    <span
                      className={
                        entry.level === "error"
                          ? "text-destructive font-medium"
                          : entry.level === "warn"
                            ? "text-warning"
                            : "text-accent"
                      }
                    >
                      [{entry.type}]
                    </span>
                    {entry.runId ? (
                      <span className="text-foreground/60">{entry.runId.slice(0, 12)}…</span>
                    ) : null}
                    <span className="text-foreground">{entry.message}</span>
                  </div>
                ))
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}