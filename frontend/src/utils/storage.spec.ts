import { afterEach, describe, expect, it, vi } from "vitest";
import {
  documentCookie,
  sessionGet,
  sessionRemove,
  sessionSet,
  storageGet,
  storageRemove,
  storageSet,
} from "./storage";

// Firefox ETP / Tor Browser deny all Web Storage in third-party frames
// (e.g. a /embed/{type}/{id} iframe): the properties exist but every
// access throws a SecurityError DOMException.
const realLocal = localStorage;
const realSession = sessionStorage;
const realCookie = Object.getOwnPropertyDescriptor(
  Document.prototype,
  "cookie",
);

function stubBlockedStorage() {
  const denied = () => {
    throw new DOMException("The operation is insecure.", "SecurityError");
  };
  const stub = {
    getItem: denied,
    setItem: denied,
    removeItem: denied,
    clear: denied,
    key: denied,
    length: 0,
  };
  vi.stubGlobal("localStorage", stub);
  vi.stubGlobal("sessionStorage", stub);
  Object.defineProperty(document, "cookie", {
    configurable: true,
    get: denied,
    set: denied,
  });
}

afterEach(() => {
  vi.stubGlobal("localStorage", realLocal);
  vi.stubGlobal("sessionStorage", realSession);
  if (realCookie) Object.defineProperty(document, "cookie", realCookie);
});

describe("storage helpers", () => {
  it("read, write and remove values when storage is available", () => {
    storageSet("k", "v");
    sessionSet("s", "w");
    expect(storageGet("k")).toBe("v");
    expect(sessionGet("s")).toBe("w");
    storageRemove("k");
    sessionRemove("s");
    expect(storageGet("k")).toBeNull();
    expect(sessionGet("s")).toBeNull();
  });

  it("degrade to no-ops when storage access is blocked", () => {
    stubBlockedStorage();
    expect(storageGet("k")).toBeNull();
    expect(sessionGet("s")).toBeNull();
    expect(documentCookie()).toBe("");
    expect(() => storageSet("k", "v")).not.toThrow();
    expect(() => storageRemove("k")).not.toThrow();
    expect(() => sessionSet("s", "w")).not.toThrow();
    expect(() => sessionRemove("s")).not.toThrow();
  });
});

describe("boot path with third-party storage blocked", () => {
  it("imports stores/auth without throwing (module-level cleanup)", async () => {
    stubBlockedStorage();
    vi.resetModules();
    await expect(import("@/stores/auth")).resolves.toBeTruthy();
  });

  it("initializes the theme store with defaults", async () => {
    stubBlockedStorage();
    vi.resetModules();
    const { useThemeStore } = await import("@/stores/theme");
    const store = useThemeStore();
    expect(store.mode).toBe("system");
    expect(store.resolvedMode).toBe("light");
  });

  it("resolves the i18n locale without storage", async () => {
    stubBlockedStorage();
    vi.resetModules();
    const i18nModule = await import("@/i18n");
    expect(i18nModule.getStoredLocale()).toBeNull();
    await expect(i18nModule.initializeI18n()).resolves.toBeUndefined();
  });

  it("initializes the player store without persisted state", async () => {
    stubBlockedStorage();
    vi.resetModules();
    const { usePlayerStore } = await import("@/stores/player");
    expect(() => usePlayerStore()).not.toThrow();
  });

  it("reads no CSRF token when cookies are blocked", async () => {
    stubBlockedStorage();
    vi.resetModules();
    const { getCsrfToken } = await import("@/api/client");
    expect(getCsrfToken()).toBeNull();
  });
});
