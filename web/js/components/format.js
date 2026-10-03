// components/format.js — small formatting helpers shared by the Players and
// Sandbox pages. Display only: no numbers are computed here beyond rounding
// and per-game/per-minute division of totals the backend returned.

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function seasonLabel(season) {
  return `${season}-${String(Number(season) + 1).slice(-2)}`;
}

export function fixed(value, digits = 1) {
  return value == null || !Number.isFinite(Number(value)) ? "—" : Number(value).toFixed(digits);
}

export function signed(value, digits = 1) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const text = Math.abs(Number(value)).toFixed(digits);
  if (Number(text) === 0) return text;
  return value > 0 ? `+${text}` : `−${text}`;
}

/** 0.123 -> "12.3%"; `digits` decimals. */
export function pct(value, digits = 1) {
  return value == null || !Number.isFinite(Number(value)) ? "—" : `${(Number(value) * 100).toFixed(digits)}%`;
}

/** Shooting percentage in the box-score style: .456 */
export function shootingPct(value) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  const text = Number(value).toFixed(3);
  return text.startsWith("0") ? text.slice(1) : text;
}

/** Signed change in percentage points: 0.031 -> "+3.1 pts". */
export function signedPoints(value, digits = 1) {
  if (value == null || !Number.isFinite(Number(value))) return "—";
  return `${signed(Number(value) * 100, digits)} pts`;
}

export function thousands(value) {
  return value == null || !Number.isFinite(Number(value)) ? "—" : Math.round(Number(value)).toLocaleString("en-US");
}

/** Await a fetch-wrapper promise and normalize failures into an envelope-like shape. */
export async function safe(promise) {
  try {
    return await promise;
  } catch (err) {
    return { ok: false, data: { error: { message: `Network error: ${err.message}` } } };
  }
}

/** `{ok, data}` -> the tool's data, or null; `message` explains a failure. */
export function unwrap(res) {
  if (res?.ok && res.data?.status === "success") return { data: res.data.data, message: null };
  return { data: null, message: res?.data?.error?.message || "The request failed." };
}

export function errorBanner(message) {
  return `<div class="error-banner" role="alert">⚠ ${escapeHtml(message)}</div>`;
}

export function emptyState(message) {
  return `<div class="empty-state">${escapeHtml(message)}</div>`;
}

export function debounce(fn, wait = 250) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}
