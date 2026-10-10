/**
 * Where the System lives.
 *
 * The base URL is **publishable configuration, not a secret**: it is the address
 * of the externally hosted OneShot System that this published UI is a client of.
 * There are no `.env` files on this platform, so resolution falls back through:
 *
 *   1. a runtime override kept in this browser (set it from the header), then
 *   2. a build-time `VITE_SYSTEM_BASE_URL` if one was configured, then
 *   3. the local default.
 *
 * Nothing here is privileged, and no API key is ever held by the client.
 */

export const DEFAULT_SYSTEM_BASE_URL = "http://127.0.0.1:8000";

const OVERRIDE_KEY = "oneshot.systemBaseUrl";

function tidy(value: string): string {
  return value.trim().replace(/\/+$/, "");
}

export function readOverride(): string {
  try {
    return window.localStorage.getItem(OVERRIDE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function writeOverride(value: string): string {
  const tidied = tidy(value);
  try {
    if (tidied) {
      window.localStorage.setItem(OVERRIDE_KEY, tidied);
    } else {
      window.localStorage.removeItem(OVERRIDE_KEY);
    }
  } catch {
    /* private browsing: the override simply does not persist */
  }
  return tidied;
}

export function clearOverride(): void {
  try {
    window.localStorage.removeItem(OVERRIDE_KEY);
  } catch {
    /* nothing to clear */
  }
}

export function systemBaseUrl(): string {
  const override = tidy(readOverride());
  if (override) return override;

  const fromEnv = import.meta.env.VITE_SYSTEM_BASE_URL;
  if (typeof fromEnv === "string" && fromEnv.trim()) return tidy(fromEnv);

  return DEFAULT_SYSTEM_BASE_URL;
}

export function isUsingOverride(): boolean {
  return tidy(readOverride()).length > 0;
}
