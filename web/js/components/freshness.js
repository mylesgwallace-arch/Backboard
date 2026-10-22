// components/freshness.js — the "how current is this data" strip shown at the
// top of the pages that talk about the current season. It reads the existing
// `data_status` tool (its `live_refresh` block) and never claims more than the
// database holds: a stale refresh gets a plain warning, and results that are
// newer than the model's team-strength inputs are called out.

import { getDataStatus } from "../api.js";
import { escapeHtml } from "./format.js";

let cached = null;

function loadStatus() {
  if (!cached || Date.now() - cached.at > 60000) {
    cached = { at: Date.now(), promise: getDataStatus().catch(() => null) };
  }
  return cached.promise;
}

function day(value) {
  return value ? String(value).slice(0, 10) : "—";
}

/** Build the strip's HTML from a data_status result, or "" when unavailable. */
export function freshnessHtml(res) {
  const s = res?.data?.status === "success" ? res.data.data : null;
  if (!s) return "";
  const live = s.live_refresh || {};
  const ahead = live.results_ahead_of_model_inputs || 0;
  const refreshed = live.last_refresh_at
    ? `Last refreshed ${escapeHtml(day(live.last_refresh_at))}`
    : "Never refreshed";
  const facts = `${refreshed} · latest game on file ${escapeHtml(day(s.latest_completed_game))}`;
  const lag = ahead > 0
    ? `<p class="model-insight">${ahead.toLocaleString("en-US")} played games are newer than the model's
       team-strength inputs (frozen at ${escapeHtml(day(s.latest_feature_row))}). Records and standings are
       current; win probabilities are not updated for those games.</p>`
    : "";
  if (live.stale) {
    return `<div class="error-banner" role="status" data-freshness="stale">
      <div><strong>Data may be out of date.</strong> ${escapeHtml(live.stale_reason || "")}
      ${facts}.${lag}</div></div>`;
  }
  return `<div class="info-banner scouting-note" role="status" data-freshness="ok">
    <span class="note-label">Data as of</span>
    <p class="model-insight">${facts}.</p>${lag}</div>`;
}

/** Insert the strip as the first child of `container` once the status loads. */
export async function mountFreshness(container) {
  const html = freshnessHtml(await loadStatus());
  if (!html || !container.isConnected) return;
  const slot = document.createElement("div");
  slot.className = "freshness-slot";
  slot.style.marginBottom = "1.3rem";
  slot.innerHTML = html;
  container.prepend(slot);
}
