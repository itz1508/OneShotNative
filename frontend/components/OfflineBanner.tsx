/**
 * Offline/disconnected banner (Gap 6).
 *
 * Renders a sticky banner at the top of the viewport when the browser reports
 * that the network connection is lost. Auto-dismisses when the connection is
 * restored. Uses aria-live="assertive" so screen readers announce the change
 * immediately (WCAG 2.1 SC 4.1.3).
 */

import { useConnection } from "../lib/useConnection";
import { IconAlert } from "./icons";

export function OfflineBanner() {
  const connection = useConnection();

  if (connection === "online") return null;

  return (
    <div
      role="status"
      aria-live="assertive"
      aria-atomic="true"
      className="fixed inset-x-0 top-0 z-50 flex items-center justify-center gap-2 border-b border-destructive/50 bg-destructive/90 px-4 py-2 text-sm font-medium text-white backdrop-blur"
    >
      <IconAlert className="size-4 shrink-0" aria-hidden="true" />
      You are offline — the System cannot be reached. Results from this session
      are preserved and will appear again once you reconnect.
    </div>
  );
}
