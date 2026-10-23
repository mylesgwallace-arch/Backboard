// components/prefs.js — per-browser conveniences: the favorite team, the
// color theme, Sandbox scenarios saved to the gallery, Pick'em picks, alert
// settings and the arcade-sound switch. Everything lives in
// localStorage behind try/catch, so a private window or blocked storage just
// means nothing is remembered; no page depends on these values existing.

const KEYS = {
  favorite: "backboard.favoriteTeam.v1",
  theme: "backboard.theme.v1",
  gallery: "backboard.gallery.v1",
  pickem: "backboard.pickem.v1",
  alerts: "backboard.alerts.v1",
  sound: "backboard.sound.v1",
};

function read(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw == null ? fallback : JSON.parse(raw);
  } catch (err) {
    return fallback;
  }
}

function write(key, value) {
  try {
    if (value == null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch (err) {
    return false;
  }
}

const listeners = new Set();

/** Call `fn(prefName)` whenever a preference changes (this tab). */
export function onPrefChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function changed(name) {
  listeners.forEach((fn) => fn(name));
}

export function favoriteTeam() {
  const id = Number(read(KEYS.favorite, null));
  return Number.isFinite(id) && id > 0 ? id : null;
}

export function setFavoriteTeam(teamId) {
  write(KEYS.favorite, teamId ? Number(teamId) : null);
  changed("favorite");
}

export function theme() {
  const value = read(KEYS.theme, null);
  return value === "light" || value === "dark" ? value : "dark";
}

export function setTheme(value) {
  write(KEYS.theme, value);
  applyTheme();
  changed("theme");
}

export function applyTheme() {
  const value = theme();
  document.documentElement.dataset.theme = value;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", value === "light" ? "#e9e5da" : "#141414");
  document.querySelector('meta[name="color-scheme"]')?.setAttribute("content", value);
}

/** Saved scenarios: [{id, name, saved_at, scenario: {mode, season, moves}}], newest first. */
export function savedScenarios() {
  const list = read(KEYS.gallery, []);
  return Array.isArray(list) ? list : [];
}

export function saveScenario(name, scenario) {
  const entry = { id: `s${Date.now().toString(36)}`, name, saved_at: new Date().toISOString(), scenario };
  const ok = write(KEYS.gallery, [entry, ...savedScenarios()].slice(0, 40));
  changed("gallery");
  return ok ? entry : null;
}

export function deleteScenario(id) {
  write(KEYS.gallery, savedScenarios().filter((entry) => entry.id !== id));
  changed("gallery");
}

/** Pick'em: {gameId: {team, date, home, away, picked_at}}. */
export function pickemPicks() {
  const picks = read(KEYS.pickem, {});
  return picks && typeof picks === "object" && !Array.isArray(picks) ? picks : {};
}

export function setPick(gameId, pick) {
  const picks = pickemPicks();
  if (pick) picks[gameId] = { ...pick, picked_at: new Date().toISOString() };
  else delete picks[gameId];
  write(KEYS.pickem, picks);
  changed("pickem");
}

/** Alert settings: {enabled, closeGames, favoriteSwings, lastOdds: {teamId: p}}. */
export function alertSettings() {
  const value = read(KEYS.alerts, {});
  return { enabled: false, closeGames: true, favoriteSwings: true, lastOdds: {}, ...(value || {}) };
}

export function setAlertSettings(patch) {
  write(KEYS.alerts, { ...alertSettings(), ...patch });
  changed("alerts");
}

export function soundOn() {
  return read(KEYS.sound, false) === true;
}

export function setSound(on) {
  write(KEYS.sound, Boolean(on));
  changed("sound");
}
