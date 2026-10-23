// sw.js — Backboard's service worker: the site works offline with the last
// data it loaded, and can be installed as an app.
//
// * The app shell (HTML, styles, scripts, icons) is network-first with the
//   cache as the offline fallback: after an update every module comes from
//   the same version (a cache-first shell could mix old and new modules).
// * Tool results (POST /tools/<name>) go to the network first; a successful
//   answer is kept, keyed by tool and parameters, and served when the network
//   is down, marked with X-Backboard-Offline so the page can say how old it is.
// * The assistant (/ask) and ingestion are never cached.
//
// Bump VERSION when the shell list changes.

const VERSION = "v1";
const SHELL = `backboard-shell-${VERSION}`;
const DATA = "backboard-data-v1";
const DATA_LIMIT = 250;
const SHELL_FILES = [
  "/", "/styles.css", "/manifest.webmanifest",
  "/js/main.js", "/js/router.js", "/js/api.js", "/js/teamColors.js", "/js/colorInk.js",
  "/js/components/format.js", "/js/components/motion.js", "/js/components/prefs.js",
  "/js/components/search.js", "/js/components/toast.js", "/js/components/alerts.js",
  "/js/components/teamSelect.js", "/js/components/charts.js",
  "/js/pages/dashboard.js", "/js/pages/tonight.js",
  "/media/icons/icon-192.png", "/media/icons/icon.svg",
];

self.addEventListener("install", (event) => {
  // Network-first means a new worker can take over at once.
  self.skipWaiting();
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(SHELL_FILES)).catch(() => {}));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((key) => key.startsWith("backboard-shell-") && key !== SHELL).map((key) => caches.delete(key)));
    await self.clients.claim();
  })());
});

async function digest(text) {
  const bytes = new TextEncoder().encode(text);
  const hash = await crypto.subtle.digest("SHA-1", bytes);
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function trim(cache) {
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - DATA_LIMIT; i += 1) await cache.delete(keys[i]);
}

async function toolResult(request) {
  const body = await request.clone().text();
  const key = new Request(`/__offline__${new URL(request.url).pathname}?k=${await digest(body)}`);
  const cache = await caches.open(DATA);
  try {
    const response = await fetch(request);
    if (response.ok) {
      const copy = response.clone();
      const headers = new Headers(copy.headers);
      headers.set("X-Backboard-Saved", new Date().toISOString());
      cache.delete(key).then(async () => {
        await cache.put(key, new Response(await copy.blob(), { status: copy.status, headers }));
        trim(cache);
      });
    }
    return response;
  } catch (err) {
    const saved = await cache.match(key);
    if (!saved) throw err;
    const headers = new Headers(saved.headers);
    headers.set("X-Backboard-Offline", headers.get("X-Backboard-Saved") || "unknown");
    return new Response(await saved.blob(), { status: saved.status, headers });
  }
}

async function shell(request) {
  const cache = await caches.open(SHELL);
  const isPage = request.mode === "navigate";
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(isPage ? "/" : request, response.clone());
    return response;
  } catch (err) {
    const cached = await cache.match(isPage ? "/" : request);
    if (cached) return cached;
    throw err;
  }
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.method === "POST" && url.pathname.startsWith("/tools/")) {
    event.respondWith(toolResult(request));
    return;
  }
  if (request.method !== "GET" || url.pathname.startsWith("/ask") || url.pathname === "/health"
      || url.pathname.startsWith("/embed/")) return;
  if (request.mode === "navigate" || /\.(js|css|png|svg|webmanifest|woff2)$/.test(url.pathname) || url.pathname === "/") {
    event.respondWith(shell(request));
  }
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = event.notification.data?.url || "/tonight";
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const open = windows.find((client) => new URL(client.url).origin === self.location.origin);
    if (open) {
      await open.focus();
      open.navigate(target);
    } else {
      await self.clients.openWindow(target);
    }
  })());
});
