// router.js — a minimal History-API router. Deliberately dependency-free
// (no framework) since the project has no Node/bundler toolchain; pages are
// native ES modules.
//
// Pages live at real paths with an optional query string ("/teams?team=123"),
// which src/api.py serves with the page's own title and description, so a
// link can be shared, bookmarked and indexed. Old "#/teams?team=123" links
// are rewritten to the path form on load. Same-origin <a href="/..."> clicks
// on a known page path are routed in place (no reload).

const listeners = [];
let knownPaths = null;

function normalizePath(path) {
  return path && path.startsWith("/") ? path : "/" + (path || "");
}

export function currentPath() {
  return normalizePath(window.location.pathname.replace(/\/+$/, "") || "/");
}

export function currentQuery() {
  return new URLSearchParams(window.location.search);
}

export function onRouteChange(handler) {
  listeners.push(handler);
}

function notify(kind) {
  const path = currentPath();
  listeners.forEach((handler) => handler(path, kind));
}

function href(path, query) {
  const search = query
    ? "?" + (query instanceof URLSearchParams ? query : new URLSearchParams(query)).toString()
    : "";
  return normalizePath(path) + (search === "?" ? "" : search);
}

/** Go to a page. `replace` swaps the history entry instead of adding one. */
export function navigate(path, query, { replace = false } = {}) {
  const target = href(path, query);
  const now = window.location.pathname + window.location.search;
  if (target !== now) {
    window.history[replace ? "replaceState" : "pushState"]({}, "", target);
  }
  notify(replace ? "replace" : "push");
}

/** Update the query string of the current page without re-rendering it. */
export function setQuery(query) {
  window.history.replaceState({}, "", href(currentPath(), query));
}

/** Is `path` (a pathname) one of the app's pages? */
export function isAppPath(path) {
  return Boolean(knownPaths?.has(normalizePath(path.replace(/\/+$/, "") || "/")));
}

function interceptLinks(event) {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const link = event.target.closest?.("a[href]");
  if (!link || link.target === "_blank" || link.hasAttribute("download")) return;
  if (link.getAttribute("href").startsWith("#")) return; // in-page anchors (skip link)
  const url = new URL(link.href, window.location.href);
  if (url.origin !== window.location.origin || !isAppPath(url.pathname)) return;
  event.preventDefault();
  navigate(url.pathname, url.search.slice(1) ? new URLSearchParams(url.search) : null);
}

export function startRouter(defaultPath, paths) {
  knownPaths = new Set(paths);
  // Legacy hash links (#/players?id=1) become real paths.
  const hash = window.location.hash;
  if (hash.startsWith("#/")) {
    const [path, query] = hash.slice(1).split("?");
    window.history.replaceState({}, "", href(path, query ? new URLSearchParams(query) : null));
  }
  if (!isAppPath(window.location.pathname)) {
    window.history.replaceState({}, "", href(defaultPath, window.location.search.slice(1) ? currentQuery() : null));
  }
  window.addEventListener("popstate", () => notify("pop"));
  document.addEventListener("click", interceptLinks);
  notify("load");
}
