import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";

// The service worker is a plain script registered at the origin root; it is
// evaluated here inside a stubbed SW global scope so its fetch/activate
// listeners can be driven directly.
const SW_SOURCE = readFileSync(resolve(process.cwd(), "public/sw.js"), "utf-8");
const ORIGIN = "https://songhive.test";
const CURRENT_CACHE = "songhive-shell-v2";

interface FakeRequest {
  url: string;
  method: string;
  mode: string;
  destination: string;
  headers: { has(name: string): boolean };
}

function makeRequest(
  path: string,
  init: { headers?: Record<string, string>; [key: string]: unknown } = {},
): FakeRequest {
  const { headers: headerInit, ...rest } = init;
  const headers = new Map(
    Object.entries(headerInit ?? {}).map(([k, v]) => [k.toLowerCase(), v]),
  );
  return {
    url: `${ORIGIN}${path}`,
    method: "GET",
    mode: "same-origin",
    destination: "",
    ...rest,
    headers: { has: (name: string) => headers.has(name.toLowerCase()) },
  } as FakeRequest;
}

function makeResponse(status: number, headers: Record<string, string> = {}) {
  const h = new Map(
    Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]),
  );
  const response = {
    status,
    headers: { get: (n: string) => h.get(n.toLowerCase()) ?? null },
    clone() {
      return { ...response };
    },
  };
  return response;
}

interface SwEnv {
  listeners: Record<string, (event: never) => void>;
  cachePut: ReturnType<typeof vi.fn>;
  cachesDelete: ReturnType<typeof vi.fn>;
  fetchMock: ReturnType<typeof vi.fn>;
}

function loadSw(fetchImpl?: () => Promise<unknown>): SwEnv {
  const listeners: Record<string, (event: never) => void> = {};
  const store = new Map<string, unknown>();
  const cachePut = vi.fn(
    async (request: FakeRequest | string, res: unknown) => {
      store.set(typeof request === "string" ? request : request.url, res);
    },
  );
  const cachesDelete = vi.fn(async () => true);
  const cacheStub = {
    put: cachePut,
    addAll: vi.fn(async () => undefined),
    match: vi.fn(async (request: FakeRequest | string) =>
      store.get(typeof request === "string" ? request : request.url),
    ),
  };
  const cachesStub = {
    open: vi.fn(async () => cacheStub),
    match: cacheStub.match,
    keys: vi.fn(async () => ["songhive-shell-v1", CURRENT_CACHE]),
    delete: cachesDelete,
  };
  const fetchMock = vi.fn(
    fetchImpl ??
      (async () => makeResponse(200, { "Content-Type": "text/javascript" })),
  );
  const selfStub = {
    location: { origin: ORIGIN },
    addEventListener: (type: string, fn: (event: never) => void) => {
      listeners[type] = fn;
    },
    skipWaiting: vi.fn(),
    clients: {
      claim: vi.fn(async () => undefined),
      matchAll: vi.fn(async () => []),
      openWindow: vi.fn(async () => undefined),
    },
    registration: {},
  };
  new Function("self", "caches", "fetch", SW_SOURCE)(
    selfStub,
    cachesStub,
    fetchMock,
  );
  return { listeners, cachePut, cachesDelete, fetchMock };
}

function fireFetch(env: SwEnv, request: FakeRequest) {
  const respondWith = vi.fn();
  env.listeners.fetch({ request, respondWith } as never);
  return respondWith;
}

function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

describe("service worker", () => {
  it("does not intercept live mountpoint streams", () => {
    const env = loadSw();
    const respondWith = fireFetch(
      env,
      makeRequest("/streams/radio", {
        destination: "audio",
        headers: { range: "bytes=0-" },
      }),
    );
    expect(respondWith).not.toHaveBeenCalled();
  });

  it("does not intercept mountpoint streams even without media markers", () => {
    const env = loadSw();
    const respondWith = fireFetch(env, makeRequest("/streams/radio"));
    expect(respondWith).not.toHaveBeenCalled();
  });

  it("does not intercept Subsonic streaming endpoints", () => {
    const env = loadSw();
    const respondWith = fireFetch(
      env,
      makeRequest("/rest/stream.view?id=t1&u=x"),
    );
    expect(respondWith).not.toHaveBeenCalled();
  });

  it("does not intercept ranged requests on otherwise-static assets", () => {
    const env = loadSw();
    const respondWith = fireFetch(
      env,
      makeRequest("/pwa/pwa-192x192.png", { headers: { range: "bytes=0-" } }),
    );
    expect(respondWith).not.toHaveBeenCalled();
  });

  it("does not intercept media-destination requests", () => {
    const env = loadSw();
    const respondWith = fireFetch(
      env,
      makeRequest("/assets/theme-song.mp3", { destination: "audio" }),
    );
    expect(respondWith).not.toHaveBeenCalled();
  });

  it("keeps the streams directory page itself cacheable", () => {
    const env = loadSw();
    const respondWith = fireFetch(env, makeRequest("/streams"));
    expect(respondWith).toHaveBeenCalled();
  });

  it("serves regular assets stale-while-revalidate", async () => {
    const env = loadSw();
    const respondWith = fireFetch(env, makeRequest("/assets/index-abc123.js"));
    expect(respondWith).toHaveBeenCalled();
    await flush();
    expect(env.fetchMock).toHaveBeenCalled();
    expect(env.cachePut).toHaveBeenCalled();
  });

  it("never stores a response the server marked no-store", async () => {
    const env = loadSw(async () =>
      makeResponse(200, { "Cache-Control": "no-cache, no-store" }),
    );
    fireFetch(env, makeRequest("/assets/index-abc123.js"));
    await flush();
    expect(env.fetchMock).toHaveBeenCalled();
    expect(env.cachePut).not.toHaveBeenCalled();
  });

  it("does not overwrite the shell fallback on non-HTML navigations", async () => {
    const env = loadSw(async () =>
      makeResponse(200, { "Content-Type": "audio/mpeg" }),
    );
    const respondWith = fireFetch(
      env,
      makeRequest("/streams/radio", { mode: "navigate" }),
    );
    expect(respondWith).toHaveBeenCalled();
    await flush();
    expect(env.cachePut).not.toHaveBeenCalled();
  });

  it("stores the shell fallback for HTML navigations", async () => {
    const env = loadSw(async () =>
      makeResponse(200, { "Content-Type": "text/html" }),
    );
    fireFetch(env, makeRequest("/streams", { mode: "navigate" }));
    await flush();
    expect(env.cachePut).toHaveBeenCalled();
  });

  it("drops previous caches on activate", async () => {
    const env = loadSw();
    let waited: Promise<unknown> | undefined;
    env.listeners.activate({
      waitUntil: (promise: Promise<unknown>) => {
        waited = promise;
      },
    } as never);
    await waited;
    expect(env.cachesDelete).toHaveBeenCalledWith("songhive-shell-v1");
    expect(env.cachesDelete).not.toHaveBeenCalledWith(CURRENT_CACHE);
  });
});
