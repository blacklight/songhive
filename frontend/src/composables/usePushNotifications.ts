import { ref, type Ref } from "vue";
import { getApiErrorMessage } from "@/api/client";
import {
  getPushConfig,
  registerPushSubscription,
  unregisterPushSubscription,
  type PushConfigResponse,
} from "@/api/notifications";

export type PushNotificationState =
  | "unsupported"
  | "unavailable"
  | "denied"
  | "prompt"
  | "subscribed"
  | "loading";

export interface UsePushNotifications {
  state: Ref<PushNotificationState>;
  error: Ref<string | null>;
  isSupported: Ref<boolean>;
  isSubscribed: Ref<boolean>;
  load: () => Promise<void>;
  enable: () => Promise<void>;
  disable: () => Promise<void>;
}

function urlBase64ToUint8Array(base64url: string): Uint8Array {
  // Add missing padding and replace URL-safe characters.
  const padding = "=".repeat((4 - (base64url.length % 4)) % 4);
  const base64 = (base64url + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) {
    bytes[i] = raw.charCodeAt(i);
  }
  return bytes;
}

async function getRegistration(): Promise<
  ServiceWorkerRegistration | undefined
> {
  if (!("serviceWorker" in navigator)) {
    return undefined;
  }
  try {
    return await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  } catch {
    return undefined;
  }
}

function subscriptionToRequest(sub: PushSubscription) {
  const json = sub.toJSON();
  const keys = json.keys as { p256dh: string; auth: string } | undefined;
  if (!json.endpoint || !keys?.p256dh || !keys?.auth) {
    throw new Error("Invalid push subscription");
  }
  return {
    endpoint: json.endpoint,
    p256dh: keys.p256dh,
    auth: keys.auth,
  };
}

export function usePushNotifications(): UsePushNotifications {
  const state: Ref<PushNotificationState> = ref("loading");
  const error: Ref<string | null> = ref(null);
  const isSupported = ref(false);
  const isSubscribed = ref(false);

  let config: PushConfigResponse | null = null;
  let registration: ServiceWorkerRegistration | undefined;

  async function fetchConfig(): Promise<PushConfigResponse | null> {
    try {
      return await getPushConfig();
    } catch (err) {
      error.value =
        getApiErrorMessage(err) || "Could not load push configuration";
      return null;
    }
  }

  async function getCurrentSubscription(): Promise<PushSubscription | null> {
    if (!registration?.pushManager) {
      return null;
    }
    return registration.pushManager.getSubscription();
  }

  async function syncBackend(sub: PushSubscription | null) {
    if (!sub) {
      isSubscribed.value = false;
      return;
    }
    try {
      await registerPushSubscription(subscriptionToRequest(sub));
      isSubscribed.value = true;
    } catch (err) {
      error.value =
        getApiErrorMessage(err) || "Could not register push subscription";
      isSubscribed.value = false;
    }
  }

  async function load() {
    error.value = null;
    state.value = "loading";

    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      state.value = "unsupported";
      isSupported.value = false;
      return;
    }

    isSupported.value = true;

    if (Notification.permission === "denied") {
      state.value = "denied";
      isSubscribed.value = false;
      return;
    }

    [config, registration] = await Promise.all([
      fetchConfig(),
      getRegistration(),
    ]);

    if (!config?.enabled || !config?.public_key || !registration?.pushManager) {
      state.value = config?.enabled ? "unsupported" : "unavailable";
      isSubscribed.value = false;
      return;
    }

    const sub = await getCurrentSubscription();
    if (sub) {
      await syncBackend(sub);
      state.value = isSubscribed.value ? "subscribed" : "prompt";
    } else {
      isSubscribed.value = false;
      state.value = "prompt";
    }
  }

  async function enable() {
    error.value = null;
    if (!isSupported.value || !registration?.pushManager) {
      state.value = "unsupported";
      return;
    }

    if (!config?.enabled || !config.public_key) {
      state.value = "unavailable";
      return;
    }

    state.value = "loading";

    const permission = await Notification.requestPermission();
    if (permission === "denied") {
      state.value = "denied";
      return;
    }

    try {
      const sub = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(
          config.public_key,
        ) as BufferSource,
      });
      await registerPushSubscription(subscriptionToRequest(sub));
      isSubscribed.value = true;
      state.value = "subscribed";
    } catch (err) {
      isSubscribed.value = false;
      error.value =
        getApiErrorMessage(err) || "Could not enable push notifications";
      state.value = "prompt";
    }
  }

  async function disable() {
    error.value = null;
    state.value = "loading";

    try {
      const sub = await getCurrentSubscription();
      if (sub) {
        const json = sub.toJSON();
        await sub.unsubscribe();
        if (json.endpoint) {
          await unregisterPushSubscription(json.endpoint);
        }
      }
      isSubscribed.value = false;
      state.value = "prompt";
    } catch (err) {
      error.value =
        getApiErrorMessage(err) || "Could not disable push notifications";
      state.value = isSubscribed.value ? "subscribed" : "prompt";
    }
  }

  return {
    state,
    error,
    isSupported,
    isSubscribed,
    load,
    enable,
    disable,
  };
}
