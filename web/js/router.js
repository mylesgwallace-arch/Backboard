// router.js — a minimal hash-based router. Deliberately dependency-free
// (no framework) since the project has no Node/bundler toolchain; pages are
// native ES modules loaded with dynamic import().

const listeners = [];

function normalizePath(path) {
  return path && path.startsWith("/") ? path : "/" + (path || "");
}

export function currentPath() {
  const hash = window.location.hash.replace(/^#/, "");
  return normalizePath(hash || "/dashboard");
}

export function onRouteChange(handler) {
  listeners.push(handler);
}

export function navigate(path) {
  const target = "#" + normalizePath(path);
  if (window.location.hash === target) {
    listeners.forEach((handler) => handler(currentPath()));
  } else {
    window.location.hash = target;
  }
}

export function startRouter(defaultPath) {
  if (!window.location.hash) {
    window.location.hash = "#" + normalizePath(defaultPath);
  }
  window.addEventListener("hashchange", () => {
    listeners.forEach((handler) => handler(currentPath()));
  });
  listeners.forEach((handler) => handler(currentPath()));
}
