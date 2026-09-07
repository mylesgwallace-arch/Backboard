// components/playoffField.js — renders the `projected_seedings` block from a
// `simulate_season` projection (the single most-probable team for each
// direct-playoff seed, both conferences) as probability bars. Shared
// between the Season Simulator and League Predictions pages.

import { teamColor } from "../teamColors.js";

export function renderPlayoffField(seedings, teamById, { title, subtitle, clickableTeams = false } = {}) {
  if (!seedings || !seedings.length) return "";
  const east = seedings.filter((s) => s.conference === "East");
  const west = seedings.filter((s) => s.conference === "West");
  const renderConference = (rows, label) => `
    <div>
      <div class="section-title">${label}</div>
      ${rows
        .map((row) => {
          const team = teamById.get(row.teamId);
          const color = teamColor(row.teamId).primary;
          const nameAttrs = clickableTeams
            ? ` data-team-link="${row.teamId}" style="cursor:pointer;"`
            : "";
          return `
            <div class="compare-row">
              <div class="compare-row-head">
                <span class="metric-name"${nameAttrs}>Seed ${row.seed} — ${escapeHtml(team?.full_name || row.teamId)}</span>
                <span>${(row.probability * 100).toFixed(0)}%</span>
              </div>
              <div class="compare-track">
                <div class="compare-fill home" style="width:${row.probability * 100}%; background:${color};"></div>
              </div>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
  return `
    <div class="card fade-in">
      <div class="card-header">
        <div>
          <h2>${title || "Projected playoff field"}</h2>
          <p class="card-subtitle">${subtitle || "The most likely team to occupy each direct-playoff seed (top 6 per conference)."}</p>
        </div>
      </div>
      <div class="grid-2">
        ${renderConference(east, "Eastern Conference")}
        ${renderConference(west, "Western Conference")}
      </div>
    </div>
  `;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
