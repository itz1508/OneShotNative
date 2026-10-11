/** Presentation helpers. Pure formatting — no interpretation of System output. */

export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

export function formatCount(value: number): string {
  return value.toLocaleString();
}

export function shortDigest(digest: string): string {
  if (!digest) return "not reported";
  return `${digest.slice(0, 12)}…`;
}

export function sourceTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    public_url: "public URL",
    text: "pasted text",
    raw_html: "raw HTML",
    pdf: "PDF",
    docx: "DOCX",
    audio: "audio",
  };
  return labels[type] ?? type;
}

/** Hand a string to the browser as a file download. */
export function downloadText(filename: string, text: string, mime = "text/plain"): void {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** Copy to the clipboard, resolving `false` rather than throwing when refused. */
export function copyText(text: string): Promise<boolean> {
  if (typeof navigator === "undefined" || !navigator.clipboard) {
    return Promise.resolve(false);
  }
  return navigator.clipboard.writeText(text).then(
    () => true,
    () => false,
  );
}