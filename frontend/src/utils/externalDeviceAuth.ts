/**
 * Helpers for the external-provider device-authorization flow (TIDAL).
 *
 * Unlike the OAuth connect flow the SPA never leaves the page: the backend
 * begins a device session, the user authorizes on the provider's site with a
 * short code, and the SPA polls until the session is granted, denied, or
 * expires. The granted config fragment is merged into the form and submitted
 * through the normal create/PATCH path.
 */
import {
  beginExternalDeviceAuth,
  completeExternalDeviceAuth,
  pollExternalDeviceAuth,
} from "@/api/externalLibraries";
import type {
  ExternalDeviceAuthBeginResponse,
  ExternalDeviceAuthCompleteResponse,
  ExternalDeviceAuthMode,
  ExternalDeviceAuthPollStatus,
} from "@/api/externalLibraries";

export type DeviceAuthFlowStatus =
  "pending" | "slow_down" | "granted" | "expired" | "denied" | "error";

export interface DeviceAuthPrompt {
  /** Opaque backend state passed to poll/complete calls. */
  state: string;
  mode: ExternalDeviceAuthMode;
  userCode: string | null;
  verificationUri: string | null;
  verificationUriComplete: string | null;
  /** PKCE authorize URL (mode "pkce"). */
  authorizeUrl: string | null;
  expiresIn: number;
  interval: number;
}

export interface DeviceAuthUpdate {
  status: DeviceAuthFlowStatus;
  /** Seconds until the next scheduled poll (for countdown display). */
  nextPollIn?: number;
  /** Granted config fragment; set once when status flips to "granted". */
  granted?: ExternalDeviceAuthCompleteResponse;
  detail?: string;
}

export interface DeviceAuthFlow {
  prompt: DeviceAuthPrompt;
  /** Cancel polling; safe to call multiple times. */
  cancel(): void;
}

const DEFAULT_INTERVAL = 5;
const DEFAULT_EXPIRES_IN = 300;
/** Extra seconds added to the poll interval on a slow_down response. */
const SLOW_DOWN_BACKOFF = 5;

export async function beginDeviceAuth(
  providerType: string,
  config: Record<string, unknown>,
  opts: {
    externalLibraryId?: string;
    mode?: ExternalDeviceAuthMode;
  } = {},
): Promise<DeviceAuthPrompt> {
  const response: ExternalDeviceAuthBeginResponse =
    await beginExternalDeviceAuth({
      provider_type: providerType,
      config,
      external_library_id: opts.externalLibraryId,
      mode: opts.mode,
    });
  return {
    state: response.state,
    mode: response.mode,
    userCode: response.user_code ?? null,
    verificationUri: response.verification_uri ?? null,
    verificationUriComplete: response.verification_uri_complete ?? null,
    authorizeUrl: response.authorize_url ?? null,
    expiresIn: response.expires_in ?? DEFAULT_EXPIRES_IN,
    interval: response.interval ?? DEFAULT_INTERVAL,
  };
}

/**
 * Poll ``state`` until the device session reaches a terminal status.
 *
 * ``onUpdate`` receives every status change, including the granted config
 * fragment once the flow completes. ``retry_after`` from slow_down responses
 * is honored when present; otherwise the interval grows by
 * ``SLOW_DOWN_BACKOFF`` each time. Returns a handle whose ``cancel()`` stops
 * polling (used when the user closes the panel or picks PKCE instead).
 */
export function pollDeviceAuth(
  prompt: DeviceAuthPrompt,
  onUpdate: (update: DeviceAuthUpdate) => void,
): DeviceAuthFlow {
  let cancelled = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let interval = prompt.interval;
  const deadline = Date.now() + prompt.expiresIn * 1000;

  const schedule = (delaySeconds: number) => {
    if (cancelled) return;
    timer = setTimeout(tick, delaySeconds * 1000);
    onUpdate({ status: "pending", nextPollIn: delaySeconds });
  };

  const tick = async () => {
    if (cancelled) return;
    if (Date.now() >= deadline) {
      onUpdate({ status: "expired" });
      return;
    }
    let status: ExternalDeviceAuthPollStatus;
    let retryAfter: number | null | undefined;
    let detail: string | null | undefined;
    try {
      const response = await pollExternalDeviceAuth(prompt.state);
      status = response.status;
      retryAfter = response.retry_after;
      detail = response.detail;
    } catch {
      onUpdate({ status: "error", detail: "poll_failed" });
      return;
    }

    switch (status) {
      case "pending":
        schedule(interval);
        return;
      case "slow_down":
        interval = retryAfter ?? interval + SLOW_DOWN_BACKOFF;
        onUpdate({ status: "slow_down", nextPollIn: interval });
        if (!cancelled) timer = setTimeout(tick, interval * 1000);
        return;
      case "granted": {
        try {
          const granted = await completeExternalDeviceAuth({
            state: prompt.state,
          });
          onUpdate({ status: "granted", granted });
        } catch {
          onUpdate({ status: "error", detail: "complete_failed" });
        }
        return;
      }
      case "expired":
        onUpdate({ status: "expired" });
        return;
      case "denied":
        onUpdate({ status: "denied", detail: detail ?? undefined });
        return;
      default:
        onUpdate({ status: "error", detail: detail ?? "unknown_status" });
    }
  };

  void tick();
  return {
    prompt,
    cancel() {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    },
  };
}

/**
 * Complete a PKCE device-auth session from the user-pasted redirect URL.
 * Stub for the Section 12 advanced flow — the backend endpoint exists, this
 * just wraps it.
 */
export function completePkceDeviceAuth(
  state: string,
  redirectUrl: string,
): Promise<ExternalDeviceAuthCompleteResponse> {
  return completeExternalDeviceAuth({ state, redirect_url: redirectUrl });
}
