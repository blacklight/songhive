/**
 * Third-party contexts (a cross-site iframe under Firefox ETP, Tor
 * Browser, LibreWolf hardened mode) block Web Storage: reading the
 * `localStorage`/`sessionStorage` getters themselves throws a
 * SecurityError DOMException, and `document.cookie` reads can throw
 * too. These helpers degrade storage to a best-effort no-op so the
 * app still boots — anonymously and without persistence — inside
 * e.g. `/embed/{type}/{id}`.
 */
export function storageGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function storageSet(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable — persistence silently skipped */
  }
}

export function storageRemove(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    /* storage unavailable */
  }
}

export function sessionGet(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

export function sessionSet(key: string, value: string): void {
  try {
    sessionStorage.setItem(key, value);
  } catch {
    /* storage unavailable */
  }
}

export function sessionRemove(key: string): void {
  try {
    sessionStorage.removeItem(key);
  } catch {
    /* storage unavailable */
  }
}

export function documentCookie(): string {
  try {
    return document.cookie;
  } catch {
    return "";
  }
}
