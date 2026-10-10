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

import { health } from "../lib/api";
import { clearOverride, isUsingOverride, systemBaseUrl, writeOverride } from "../lib/config";
import { SCHEMA_VERSION, type Health } from "../lib/schema";
import { IconAlert, IconCheck, IconRefresh } from "./icons";
import { Button, Callout, Field, Hint, Mono, Pill, TextInput, cx } from "./ui";

export function SystemStatus() {
  const [url, setUrl] = useState<string>(systemBaseUrl());
  const [draftUrl, setDraftUrl] = useState<string>(systemBaseUrl());
  const [state, setState] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [editing, setEditing] = useState(false);

  const probe = useCallback(async () => {
    setChecking(true);
    setError(null);
    try {
      setState(await health());
    } catch (cause) {
      setState(null);
      setError(
        cause instanceof Error
          ? cause.message
          : "The OneShot System could not be reached.",
      );
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    void probe();
  }, [probe]);

  const reachable = state !== null;
  const schemaMatches = state?.schema_version === SCHEMA_VERSION;

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

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone={reachable ? "success" : error ? "danger" : "neutral"}>
          {reachable ? <IconCheck className="size-3" /> : <IconAlert className="size-3" />}
          {reachable ? "System reachable" : error ? "System unreachable" : "checking…"}
        </Pill>
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
          {editing ? "Close" : "Change URL"}
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
            Stored in this browser only. Set <Mono>VITE_SYSTEM_BASE_URL</Mono> at build time to
            change the default for everyone.
          </Hint>
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
            The System is a separate Python service — this published app only talks to it. Start
            it on your host, then set the URL above.
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
    </div>
  );
}
