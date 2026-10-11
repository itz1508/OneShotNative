/**
 * Browser connection state (Gap 6 — Offline / Disconnected Handling).
 *
 * Subscribes to the browser's native online/offline events and exports a hook
 * that returns the current connection state. The hook also persists the last
 * known OneShotOutput to sessionStorage so a page reload does not lose a
 * completed result.
 *
 * sessionStorage is scoped to the current tab and origin — it is cleared when
 * the tab is closed, which is the right retention window for a stateless run.
 */

import { useEffect, useState } from "react";

import type { OneShotOutput } from "./schema";

export type ConnectionState = "online" | "offline";

export function useConnection(): ConnectionState {
  const [state, setState] = useState<ConnectionState>(
    navigator.onLine ? "online" : "offline",
  );

  useEffect(() => {
    const onOnline = () => setState("online");
    const onOffline = () => setState("offline");
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, []);

  return state;
}

const OUTPUT_KEY = "oneshot.lastOutput";

/** Persist a completed output to sessionStorage (survives page reload, not tab close). */
export function saveOutput(runId: string, output: OneShotOutput): void {
  try {
    sessionStorage.setItem(OUTPUT_KEY, JSON.stringify({ runId, output }));
  } catch {
    /* Private browsing or storage quota — silently skip. */
  }
}

/** Restore the last persisted output, if any. Returns null if nothing is stored. */
export function restoreOutput(): { runId: string; output: OneShotOutput } | null {
  try {
    const raw = sessionStorage.getItem(OUTPUT_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as { runId: string; output: OneShotOutput };
  } catch {
    return null;
  }
}

export function clearSavedOutput(): void {
  try {
    sessionStorage.removeItem(OUTPUT_KEY);
  } catch {
    /* nothing to clear */
  }
}
