import { describe, it, expect, beforeEach, vi, type Mock } from "vitest";
import * as notificationsApi from "@/api/notifications";
import { usePushNotifications } from "./usePushNotifications";

const SAMPLE_PUBLIC_KEY =
  "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8gISIjJCUmJygpKissLS4vMDEyMzQ1Njc4OTo7PD0-P0A";

function makeSubscription(endpoint = "https://push.example.com/sub/1") {
  return {
    endpoint,
    toJSON: () => ({
      endpoint,
      keys: {
        p256dh: "p256dh",
        auth: "auth",
      },
    }),
    unsubscribe: vi.fn().mockResolvedValue(true),
  };
}

function mockPushSubscription(
  current: ReturnType<typeof makeSubscription> | null,
) {
  return {
    getSubscription: vi.fn().mockResolvedValue(current),
    subscribe: vi.fn().mockResolvedValue(makeSubscription()),
  };
}

function mockRegistration(sub: ReturnType<typeof makeSubscription> | null) {
  const pushManager = mockPushSubscription(sub);
  return {
    pushManager,
  };
}

let originalNavigator: Navigator | undefined;

vi.mock("@/api/client", () => ({
  getApiErrorMessage: (err: unknown, fallback?: string) =>
    (err instanceof Error ? err.message : fallback) || "",
}));

vi.mock("@/api/notifications", () => ({
  getPushConfig: vi.fn(),
  registerPushSubscription: vi.fn(),
  unregisterPushSubscription: vi.fn(),
}));

const getPushConfig = vi.mocked(notificationsApi.getPushConfig);
const registerPushSubscription = vi.mocked(
  notificationsApi.registerPushSubscription,
);
const unregisterPushSubscription = vi.mocked(
  notificationsApi.unregisterPushSubscription,
);

function installBrowser({
  serviceWorker,
  notificationPermission,
}: {
  serviceWorker?: Record<string, unknown>;
  notificationPermission?: NotificationPermission;
}) {
  originalNavigator = globalThis.navigator;
  const nav = Object.create(originalNavigator);
  if (serviceWorker !== undefined) {
    nav.serviceWorker = serviceWorker;
  }
  Object.defineProperty(globalThis, "navigator", {
    value: nav,
    configurable: true,
    writable: true,
  });
  Object.defineProperty(window, "navigator", {
    value: nav,
    configurable: true,
    writable: true,
  });

  const permission = notificationPermission ?? "default";
  const requestPermission = vi.fn().mockResolvedValue(permission);
  const NotificationClass = class {
    static permission = permission;
    static requestPermission = requestPermission;
  } as unknown as typeof Notification;

  const PushManagerClass = class {};
  Object.defineProperty(globalThis, "PushManager", {
    value: PushManagerClass,
    configurable: true,
    writable: true,
  });
  Object.defineProperty(window, "PushManager", {
    value: PushManagerClass,
    configurable: true,
    writable: true,
  });

  Object.defineProperty(globalThis, "Notification", {
    value: NotificationClass,
    configurable: true,
    writable: true,
  });
  Object.defineProperty(window, "Notification", {
    value: NotificationClass,
    configurable: true,
    writable: true,
  });
}

function restoreBrowser() {
  Object.defineProperty(globalThis, "navigator", {
    value: originalNavigator,
    configurable: true,
    writable: true,
  });
  Object.defineProperty(window, "navigator", {
    value: originalNavigator,
    configurable: true,
    writable: true,
  });
  Object.defineProperty(globalThis, "PushManager", {
    value: undefined,
    configurable: true,
    writable: true,
  });
  Object.defineProperty(window, "PushManager", {
    value: undefined,
    configurable: true,
    writable: true,
  });

  Object.defineProperty(globalThis, "Notification", {
    value: undefined,
    configurable: true,
    writable: true,
  });
  Object.defineProperty(window, "Notification", {
    value: undefined,
    configurable: true,
    writable: true,
  });
}

describe("usePushNotifications", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getPushConfig.mockResolvedValue({
      enabled: true,
      public_key: SAMPLE_PUBLIC_KEY,
    });
    registerPushSubscription.mockResolvedValue(undefined);
    unregisterPushSubscription.mockResolvedValue({ removed: 1 });
  });

  afterEach(() => {
    restoreBrowser();
  });

  it("reports unsupported when the browser lacks Web Push support", async () => {
    installBrowser({});
    const { state, isSupported, load } = usePushNotifications();
    await load();
    expect(isSupported.value).toBe(false);
    expect(state.value).toBe("unsupported");
  });

  it("reports unavailable when the instance is not configured", async () => {
    getPushConfig.mockResolvedValue({ enabled: false, public_key: null });
    const registration = mockRegistration(null);
    installBrowser({
      serviceWorker: {
        register: vi.fn().mockResolvedValue(registration),
        ready: Promise.resolve(registration),
      },
    });
    const { state, isSupported, load } = usePushNotifications();
    await load();
    expect(isSupported.value).toBe(true);
    expect(state.value).toBe("unavailable");
  });

  it("reports denied when notification permission is denied", async () => {
    const registration = mockRegistration(null);
    installBrowser({
      serviceWorker: {
        register: vi.fn().mockResolvedValue(registration),
        ready: Promise.resolve(registration),
      },
      notificationPermission: "denied",
    });
    const { state, load } = usePushNotifications();
    await load();
    expect(state.value).toBe("denied");
  });

  it("reports prompt when push is available and no subscription exists", async () => {
    const registration = mockRegistration(null);
    installBrowser({
      serviceWorker: {
        register: vi.fn().mockResolvedValue(registration),
        ready: Promise.resolve(registration),
      },
    });
    const { state, load } = usePushNotifications();
    await load();
    expect(state.value).toBe("prompt");
  });

  it("subscribes and reports subscribed after permission is granted", async () => {
    const registration = mockRegistration(null);
    installBrowser({
      serviceWorker: {
        register: vi.fn().mockResolvedValue(registration),
        ready: Promise.resolve(registration),
      },
      notificationPermission: "prompt",
    });

    const requestPermission = (
      globalThis.Notification as unknown as { requestPermission: Mock }
    ).requestPermission as Mock;
    requestPermission.mockResolvedValue("granted");

    const { state, error, isSubscribed, load, enable } = usePushNotifications();
    await load();
    expect(state.value).toBe("prompt");

    await enable();
    expect(error.value).toBeNull();
    expect(state.value).toBe("subscribed");
    expect(registerPushSubscription).toHaveBeenCalledWith({
      endpoint: expect.any(String),
      p256dh: expect.any(String),
      auth: expect.any(String),
    });
    expect(isSubscribed.value).toBe(true);
    expect(state.value).toBe("subscribed");
  });

  it("unsubscribes and reports prompt when disabled", async () => {
    const sub = makeSubscription();
    const registration = mockRegistration(sub);
    installBrowser({
      serviceWorker: {
        register: vi.fn().mockResolvedValue(registration),
        ready: Promise.resolve(registration),
      },
    });

    const { state, isSubscribed, load, disable } = usePushNotifications();
    await load();
    expect(state.value).toBe("subscribed");
    expect(isSubscribed.value).toBe(true);

    await disable();
    expect(sub.unsubscribe).toHaveBeenCalled();
    expect(unregisterPushSubscription).toHaveBeenCalledWith(sub.endpoint);
    expect(isSubscribed.value).toBe(false);
    expect(state.value).toBe("prompt");
  });
});
