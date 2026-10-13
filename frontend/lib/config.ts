/**
 * Where the System lives.
 *
 * The base URL is **publishable configuration, not a secret**: it is the address
 * of the OneShot System that this UI is a client of. There are no `.env` files on
 * this platform, so resolution falls back through:
 *
 *   1. a runtime override kept in this browser (set it from the header), then
 *   2. a build-time `VITE_SYSTEM_BASE_URL` if one was configured, then
 *   3. the local default.
 *
 * The default is **same-origin**. The OneShot System is a separate Python service
 * that runs alongside the dev server, and the dev server proxies `/oneshot` to it
 * (see `vite.config.ts`). One origin means nothing to configure and no CORS to get
 * right — while a System hosted anywhere else is still reached by setting an
 * absolute URL in the header.
 *
 * Nothing here is privileged, and no API key is ever held by the client.
 */

/** Same-origin path the dev server proxies to the System (see `vite.config.ts`). */
export const SAME_ORIGIN_BASE_URL = "/oneshot";

export const DEFAULT_SYSTEM_BASE_URL = SAME_ORIGIN_BASE_URL;

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