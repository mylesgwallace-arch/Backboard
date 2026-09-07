// router.js — a minimal hash-based router. Deliberately dependency-free
// (no framework) since the project has no Node/bundler toolchain; pages are
// native ES modules loaded with dynamic import().
//
// Supports an optional query string after the path (e.g. "#/teams?team=123")
// so pages can deep-link into each other (e.g. "View in Team Explorer" from
// a matchup result) without a full routing library.

const listeners = [];

function normalizePath(path) {
  return path && path.startsWith("/") ? path : "/" + (path || "");
}

function splitHash(hash) {
  const clean = hash.replace(/^#/, "");
  const [path, query] = clean.split("?");
  return { path: normalizePath(path || "/dashboard"), query: query || "" };
}

export function currentPath() {
  return splitHash(window.location.hash).path;
}

export function currentQuery() {
  const { query } = splitHash(window.location.hash);
  return new URLSearchParams(query);
}

export function onRouteChange(handler) {
  listeners.push(handler);
}

function notify() {
  const { path } = splitHash(window.location.hash);
  listeners.forEach((handler) => handler(path));
}

export function navigate(path, query) {
  const search = query
    ? "?" + new URLSearchParams(query).toString()
    : "";
  const target = "#" + normalizePath(path) + search;
  if (window.location.hash === target) {
    notify();
  } else {
    window.location.hash = target;
  }
}

export function startRouter(defaultPath) {
  if (!window.location.hash) {
    window.location.hash = "#" + normalizePath(defaultPath);
  }
  window.addEventListener("hashchange", notify);
  notify();
}

