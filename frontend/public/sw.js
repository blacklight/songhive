const CACHE_NAME = "songhive-shell-v1";

const SHELL_ASSETS = [
  "/",
  "/index.html",
  "/manifest.webmanifest",
  "/favicon.ico",
  "/pwa/pwa-192x192.png",
  "/pwa/pwa-512x512.png",
  "/pwa/maskable-192x192.png",
  "/pwa/maskable-512x512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

function isNavigationRequest(request) {
  return request.mode === "navigate";
}

function isAssetRequest(request) {
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) {
    return false;
  }
  if (url.pathname.startsWith("/api/")) {
    return false;
  }
  if (url.pathname.startsWith("/ws")) {
    return false;
  }
  if (url.pathname.startsWith("/stream/")) {
    return false;
  }
  if (url.pathname.endsWith("manifest.webmanifest")) {
    return false;
  }
  return request.method === "GET";
}

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    if (event.data) {
      payload = event.data.json();
    }
  } catch {
    payload = {};
  }

  const title = payload.title || "Songhive";
  const options = {
    body: payload.body || "",
    icon: payload.icon || "/pwa/pwa-192x192.png",
    badge: payload.badge || "/pwa/pwa-192x192.png",
    tag: payload.tag || "songhive-push",
    data: { url: payload.url || "/" },
    requireInteraction: false,
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

function safeLocalUrl(input) {
  try {
    const url = new URL(input, self.location.origin);
    if (url.origin !== self.location.origin) {
      return "/";
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return "/";
    }
    return url.pathname + url.search + url.hash;
  } catch {
    return "/";
  }
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const target = safeLocalUrl(event.notification.data?.url);

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true })
      .then((clientList) => {
        const existing = clientList.find(
          (client) => client.url === target && "focus" in client,
        );
        if (existing) {
          return existing.focus();
        }
        if (self.clients.openWindow) {
          return self.clients.openWindow(target);
        }
        return Promise.resolve();
      }),
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") {
    return;
  }

  const url = new URL(event.request.url);
  if (url.pathname.endsWith("manifest.webmanifest")) {
    event.respondWith(fetch(event.request));
    return;
  }

  if (isNavigationRequest(event.request)) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (response && response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put("/", clone));
          }
          return response;
        })
        .catch(() => caches.match("/")),
    );
    return;
  }

  if (isAssetRequest(event.request)) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        const fetchPromise = fetch(event.request)
          .then((response) => {
            if (response && response.status === 200) {
              const clone = response.clone();
              caches.open(CACHE_NAME).then((cache) =>
                cache.put(event.request, clone),
              );
            }
            return response;
          })
          .catch(() => cached);

        return cached || fetchPromise;
      }),
    );
  }
});
