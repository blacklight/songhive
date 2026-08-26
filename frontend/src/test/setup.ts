import { config } from "@vue/test-utils";
import { beforeEach, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { i18n } from "@/i18n";

function createFakeStorage(): Storage {
  const store: Record<string, string> = {};
  return {
    getItem(key: string) {
      return store[key] ?? null;
    },
    setItem(key: string, value: string) {
      store[key] = value;
    },
    removeItem(key: string) {
      delete store[key];
    },
    clear() {
      for (const key of Object.keys(store)) {
        delete store[key];
      }
    },
    key(index: number) {
      return Object.keys(store)[index] ?? null;
    },
    get length() {
      return Object.keys(store).length;
    },
  } as Storage;
}

const local = createFakeStorage();
const session = createFakeStorage();

vi.stubGlobal("localStorage", local);
vi.stubGlobal("sessionStorage", session);

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

Object.defineProperty(window, "IntersectionObserver", {
  writable: true,
  value: vi.fn(() => ({
    observe: vi.fn(),
    unobserve: vi.fn(),
    disconnect: vi.fn(),
  })),
});

function defineMediaProperty(
  name: string,
  defaultValue: unknown,
  writable = true,
) {
  Object.defineProperty(window.HTMLMediaElement.prototype, name, {
    configurable: true,
    get(this: HTMLMediaElement & Record<string, unknown>) {
      return this[`__${name}`] ?? defaultValue;
    },
    set: writable
      ? function (
          this: HTMLMediaElement & Record<string, unknown>,
          value: unknown,
        ) {
          this[`__${name}`] = value;
        }
      : undefined,
  });
}

if (!("HTMLMediaElement" in window)) {
  Object.defineProperty(window, "HTMLMediaElement", {
    writable: true,
    value: function HTMLMediaElement() {},
  });
}

Object.setPrototypeOf(
  window.HTMLMediaElement.prototype,
  window.HTMLElement.prototype,
);

defineMediaProperty("src", "");
defineMediaProperty("currentTime", 0);
defineMediaProperty("duration", NaN);
defineMediaProperty("paused", true);
defineMediaProperty("volume", 1);
defineMediaProperty("muted", false);

Object.defineProperty(window.HTMLMediaElement.prototype, "play", {
  writable: true,
  value: vi.fn(() => Promise.resolve()),
});
Object.defineProperty(window.HTMLMediaElement.prototype, "pause", {
  writable: true,
  value: vi.fn(),
});
Object.defineProperty(window.HTMLMediaElement.prototype, "load", {
  writable: true,
  value: vi.fn(),
});

beforeEach(() => {
  setActivePinia(createPinia());
  localStorage.clear();
});

config.global.plugins = [i18n];
config.global.stubs = {
  teleport: false,
};
