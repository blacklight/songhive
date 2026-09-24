/**
 * Helpers for the external-provider OAuth connect flow.
 *
 * The connect flow leaves the SPA entirely (the user authorizes on the
 * provider's site and is redirected back through the API callback), so the
 * current form state is stashed in sessionStorage keyed by the OAuth state
 * and restored on return. Secret-bearing fields are never stashed — the
 * backend carries client credentials inside the claimed config fragment.
 */

const STASH_PREFIX = "songhive:external-oauth:";

/** Matches config keys that look secret-bearing (mirrors services/secrets.py). */
const SECRET_FIELD_RE = /secret|password|token|key|credential/i;

/**
 * Return a copy of ``config`` without secret-looking keys so nothing
 * sensitive is persisted to browser storage during the redirect.
 */
export function stripSecretConfigFields(
  config: Record<string, unknown>,
): Record<string, unknown> {
  const stripped: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(config)) {
    if (!SECRET_FIELD_RE.test(key)) stripped[key] = value;
  }
  return stripped;
}

/**
 * Return a sanitized JSON config string for stashing; returns the empty
 * object literal when the text does not parse.
 */
export function stripSecretsFromConfigText(configText: string): string {
  try {
    const parsed = JSON.parse(configText);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return JSON.stringify(
        stripSecretConfigFields(parsed as Record<string, unknown>),
      );
    }
  } catch {
    // fall through
  }
  return "{}";
}

export function stashExternalOAuthForm(
  state: string,
  form: Record<string, unknown>,
): void {
  sessionStorage.setItem(STASH_PREFIX + state, JSON.stringify(form));
}

export function popExternalOAuthForm(
  state: string,
): Record<string, unknown> | null {
  const raw = sessionStorage.getItem(STASH_PREFIX + state);
  sessionStorage.removeItem(STASH_PREFIX + state);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object"
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

export function redirectToOAuthProvider(authorizeUrl: string): void {
  window.location.assign(authorizeUrl);
}
