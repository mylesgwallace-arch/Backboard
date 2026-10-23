// components/alerts.js — browser alerts while the site is open (or installed
// as an app): a close game heading into its last minutes, and a big move in
// your team's playoff odds. Off until the viewer turns them on; uses the
// Notifications API (through the service worker when there is one, so it
// also works for the installed app) and falls back to an on-page toast.
//
// Sources: live_scoreboard (polled every minute while alerts are on) and the
// stored odds history (checked every few hours). Nothing is sent anywhere.

import { getLiveScoreboard, getOddsHistory } from "../api.js";
import { alertSettings, favoriteTeam, setAlertSettings } from "./prefs.js";
import { toast } from "./toast.js";
import { safe, unwrap } from "./format.js";

const LIVE_POLL_MS = 60000;
const ODDS_POLL_MS = 3 * 60 * 60 * 1000;
const CLOSE_MINUTES = 5;
const CLOSE_MARGIN = 5;
const SWING = 0.1;

let liveTimer = null;
let oddsTimer = null;
const notified = new Set();

export function alertsSupported() {
  return "Notification" in window;
}

export function alertsOn() {
  return alertSettings().enabled;
}

async function notify(title, body, url) {
  const permitted = alertsSupported() && Notification.permission === "granted";
  if (!permitted) {
    toast(`${title} · ${body}`, { tone: "warn", duration: 6000 });
    return;
  }
  const options = { body, icon: "/media/icons/icon-192.png", badge: "/media/icons/icon-192.png", tag: title, data: { url } };
  try {
    const registration = await navigator.serviceWorker?.getRegistration?.();
    if (registration) {
      await registration.showNotification(title, options);
      return;
    }
  } catch (err) {
    // fall through to a page notification
  }
  const note = new Notification(title, options);
  note.onclick = () => {
    window.focus();
    if (url) window.location.assign(url);
  };
}

async function checkLive() {
  const settings = alertSettings();
  if (!settings.enabled || settings.closeGames === "off") return;
  const res = unwrap(await safe(getLiveScoreboard()));
  const favorite = favoriteTeam();
  (res.data?.games || []).forEach((game) => {
    if (game.state !== "in" || game.minutes_remaining == null) return;
    if (settings.closeGames === "favorite" && ![game.home_id, game.away_id].includes(favorite)) return;
    const lead = Math.abs((game.home_score ?? 0) - (game.away_score ?? 0));
    const key = `close:${game.espn_id}`;
    if (game.minutes_remaining <= CLOSE_MINUTES && lead <= CLOSE_MARGIN && !notified.has(key)) {
      notified.add(key);
      const p = game.live_home_win_probability;
      notify(`Close finish: ${game.away.abbreviation} ${game.away_score} – ${game.home_score} ${game.home.abbreviation}`,
        `${game.status_detail || "Late"} · ${game.home.abbreviation} win chance ${p == null ? "—" : `${Math.round(p * 100)}%`}`,
        "/tonight");
    }
  });
}

async function checkOdds() {
  const settings = alertSettings();
  const favorite = favoriteTeam();
  if (!settings.enabled || !settings.favoriteSwings || !favorite) return;
  const res = unwrap(await safe(getOddsHistory({ teamId: favorite })));
  const points = res.data?.teams?.[0]?.points || [];
  if (points.length < 2) return;
  const latest = points[points.length - 1];
  const previous = points[points.length - 2];
  const seen = settings.lastOdds?.[favorite];
  if (seen === latest.as_of || latest.p_made_playoffs == null || previous.p_made_playoffs == null) return;
  setAlertSettings({ lastOdds: { ...settings.lastOdds, [favorite]: latest.as_of } });
  const change = latest.p_made_playoffs - previous.p_made_playoffs;
  if (Math.abs(change) >= SWING) {
    const name = res.data.teams[0].teamName;
    notify(`${name}: playoff odds ${change > 0 ? "up" : "down"} ${Math.round(Math.abs(change) * 100)} points`,
      `${Math.round(previous.p_made_playoffs * 100)}% → ${Math.round(latest.p_made_playoffs * 100)}% (${latest.as_of})`,
      `/teams?team=${favorite}`);
  }
}

function schedule() {
  clearInterval(liveTimer);
  clearInterval(oddsTimer);
  if (!alertsOn()) return;
  checkLive();
  checkOdds();
  liveTimer = setInterval(checkLive, LIVE_POLL_MS);
  oddsTimer = setInterval(checkOdds, ODDS_POLL_MS);
}

export function initAlerts() {
  schedule();
}

/** Turn alerts on (asking for permission) or off. Resolves to the new state. */
export async function toggleAlerts(on = !alertsOn()) {
  if (on && alertsSupported() && Notification.permission === "default") {
    try {
      await Notification.requestPermission();
    } catch (err) {
      // older Safari: callback form only; toasts still work
    }
  }
  setAlertSettings({ enabled: on });
  schedule();
  if (on) {
    const granted = alertsSupported() && Notification.permission === "granted";
    toast(granted ? "Alerts on: close finishes and big moves in your team's odds."
      : "Alerts on, shown on this page (the browser blocked notifications).", { tone: granted ? "ok" : "warn" });
  } else {
    toast("Alerts off.");
  }
  return on;
}

/** The settings sheet: which close games, and whether to watch your team's odds. */
export function openAlertSettings() {
  const settings = alertSettings();
  const dialog = document.createElement("dialog");
  dialog.className = "sheet-dialog";
  dialog.innerHTML = `
    <form method="dialog" class="card">
      <h2>Alerts</h2>
      <p class="text-muted">While Backboard is open (or installed as an app). Nothing leaves this browser.</p>
      <label class="check-row"><input type="checkbox" name="enabled" ${settings.enabled ? "checked" : ""}> Alerts on</label>
      <fieldset class="check-group"><legend>Close finishes (within ${CLOSE_MARGIN} points, last ${CLOSE_MINUTES} minutes)</legend>
        ${[["all", "Every game"], ["favorite", "Only your team"], ["off", "None"]].map(([value, label]) =>
          `<label class="check-row"><input type="radio" name="close" value="${value}" ${settings.closeGames === value || (value === "all" && settings.closeGames === true) ? "checked" : ""}> ${label}</label>`).join("")}
      </fieldset>
      <label class="check-row"><input type="checkbox" name="swings" ${settings.favoriteSwings ? "checked" : ""}> Your team's playoff odds move ${Math.round(SWING * 100)}+ points in a day</label>
      ${favoriteTeam() ? "" : `<p class="table-note">Star a team on its page or in Standings to pick "your team".</p>`}
      <div class="predict-actions"><button class="btn" value="save">Save</button><button class="btn btn-secondary" value="cancel">Cancel</button></div>
    </form>`;
  document.body.appendChild(dialog);
  dialog.addEventListener("close", async () => {
    if (dialog.returnValue === "save") {
      const form = dialog.querySelector("form");
      setAlertSettings({ closeGames: form.close.value, favoriteSwings: form.swings.checked });
      await toggleAlerts(form.enabled.checked);
    }
    dialog.remove();
  });
  dialog.showModal();
}
