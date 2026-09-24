// pages/headToHead.js — Head-to-Head: a standalone version of Team Explorer's
// Head-to-Head tab, promoted per the roadmap's "only worth promoting if it
// needs deeper treatment" note — the deeper treatment here is a season-by-
// season trend, not just the all-time total. Consumes the existing
// head_to_head tool (unchanged) once for the all-time total and once per
// season for the trend; nothing is computed client-side beyond summing the
// backend's own per-season win counts for the chart bar widths.

import { getHeadToHead } from "../api.js";
import { fetchTeams, createTeamSelect } from "../components/teamSelect.js";
import { renderComparisonRow } from "../components/comparisonBar.js";
import { teamColor } from "../teamColors.js";
import { clashingPair, teamSlabStyle } from "../colorInk.js";

export const meta = {
  title: "Head-to-Head",
  subtitle: "Compare any two franchises' regular-season record, all-time and season by season.",
};

// Bounded lookback window for the season-by-season trend: one head_to_head
// call per season (factual DB query, near-instant per Team Explorer's
// measured timings), run in parallel. 20 seasons keeps the request count
// small while covering the current core of most franchises' rivalries; this
// is a stated window, not a claim of full franchise history.
const CURRENT_SEASON = 2025;
const TREND_SEASONS = 20;

export function render(container, ctx = {}) {
  container.innerHTML = `
    <div class="card fade-in">
      <div class="card-header">
        <div>
          <h2>Compare two teams</h2>
          <p class="card-subtitle">
            Pulls the factual regular-season record straight from the database &mdash;
            the same <code>head_to_head</code> tool Team Explorer uses.
          </p>
        </div>
      </div>
      <div id="h2h-form-mount"></div>
      <div id="h2h-error-mount" class="mt-1"></div>
      <div class="predict-actions">
        <button class="btn" id="h2h-compare-btn" disabled>Compare</button>
        <span id="h2h-status" class="text-muted"></span>
      </div>
    </div>
    <div id="h2h-result-mount"></div>
  `;

  const formMount = container.querySelector("#h2h-form-mount");
  const errorMount = container.querySelector("#h2h-error-mount");
  const resultMount = container.querySelector("#h2h-result-mount");
  const compareBtn = container.querySelector("#h2h-compare-btn");
  const statusEl = container.querySelector("#h2h-status");

  const teamA = createTeamSelect({ id: "h2h-team-a-select", labelText: "Team", placeholder: "Select a team…" });
  const teamB = createTeamSelect({ id: "h2h-team-b-select", labelText: "Opponent", placeholder: "Select an opponent…" });

  const formGrid = document.createElement("div");
  formGrid.className = "matchup-form-grid";

  const swapBtn = document.createElement("button");
  swapBtn.type = "button";
  swapBtn.className = "swap-btn";
  swapBtn.title = "Swap teams";
  swapBtn.setAttribute("aria-label", "Swap the two teams");
  swapBtn.textContent = "⇄";

  formGrid.appendChild(teamA.root);
  formGrid.appendChild(swapBtn);
  formGrid.appendChild(teamB.root);
  formMount.appendChild(formGrid);

  let teams = [];

  function refreshCompareAvailability() {
    const aVal = teamA.selectEl.value;
    const bVal = teamB.selectEl.value;
    compareBtn.disabled = !(aVal && bVal && aVal !== bVal);
  }

  teamA.selectEl.addEventListener("change", refreshCompareAvailability);
  teamB.selectEl.addEventListener("change", refreshCompareAvailability);

  swapBtn.addEventListener("click", () => {
    const tmp = teamA.selectEl.value;
    teamA.selectEl.value = teamB.selectEl.value;
    teamB.selectEl.value = tmp;
    refreshCompareAvailability();
  });

  statusEl.textContent = "Loading teams…";
  fetchTeams()
    .then((fetchedTeams) => {
      teams = fetchedTeams;
      teamA.setTeams(teams);
      teamB.setTeams(teams);
      statusEl.textContent = "";

      // Deep-link support (e.g. from Team Explorer's Head-to-Head tab):
      // #/head-to-head?a=<teamId>&b=<teamId>
      const presetA = ctx.query?.get ? ctx.query.get("a") : null;
      const presetB = ctx.query?.get ? ctx.query.get("b") : null;
      if (presetA && teams.some((t) => String(t.team_id) === presetA)) {
        teamA.selectEl.value = presetA;
      }
      if (presetB && teams.some((t) => String(t.team_id) === presetB)) {
        teamB.selectEl.value = presetB;
      }
      refreshCompareAvailability();
      if (presetA && presetB && !compareBtn.disabled) {
        runComparison();
      }
    })
    .catch((err) => {
      statusEl.textContent = "";
      errorMount.innerHTML = errorBanner(`Could not load the team list: ${err.message}`);
    });

  compareBtn.addEventListener("click", runComparison);

  async function runComparison() {
    errorMount.innerHTML = "";
    const teamAId = Number(teamA.selectEl.value);
    const teamBId = Number(teamB.selectEl.value);

    compareBtn.disabled = true;
    const originalLabel = compareBtn.textContent;
    compareBtn.innerHTML = '<span class="spinner"></span> Comparing…';
    resultMount.innerHTML = renderSkeleton();

    let allTimeRes;
    try {
      allTimeRes = await getHeadToHead({ teamAId, teamBId });
    } catch (err) {
      resultMount.innerHTML = "";
      errorMount.innerHTML = errorBanner(`Network error while contacting the API: ${err.message}`);
      compareBtn.textContent = originalLabel;
      refreshCompareAvailability();
      return;
    }

    if (!allTimeRes.ok || allTimeRes.data?.status !== "success") {
      resultMount.innerHTML = "";
      errorMount.innerHTML = errorBanner(
        allTimeRes.data?.error?.message || "The head-to-head request failed. Please try again."
      );
      compareBtn.textContent = originalLabel;
      refreshCompareAvailability();
      return;
    }

    const teamAInfo = teams.find((t) => t.team_id === teamAId);
    const teamBInfo = teams.find((t) => t.team_id === teamBId);

    resultMount.innerHTML = renderAllTime({
      allTime: allTimeRes.data.data,
      teamAId,
      teamBId,
      teamAInfo,
      teamBInfo,
    });
    wireTeamLinks(resultMount, ctx);
    compareBtn.textContent = originalLabel;
    refreshCompareAvailability();

    // Season-by-season trend: one call per season in the lookback window,
    // in parallel. Rendered into its own mount so the (fast) all-time total
    // above isn't blocked waiting on the (slightly slower, 20-call) trend.
    const trendMount = resultMount.querySelector("#h2h-trend-mount");
    trendMount.innerHTML = `<div class="skeleton" style="height:160px;"></div>`;
    const seasons = Array.from({ length: TREND_SEASONS }, (_, i) => CURRENT_SEASON - i);
    let seasonResults;
    try {
      seasonResults = await Promise.all(
        seasons.map((season) => getHeadToHead({ teamAId, teamBId, season }))
      );
    } catch (err) {
      trendMount.innerHTML = errorBanner(`Could not load the season-by-season trend: ${err.message}`);
      return;
    }

    const trendRows = seasons
      .map((season, i) => ({ season, res: seasonResults[i] }))
      .filter(({ res }) => res.ok && res.data?.status === "success" && res.data.data.games > 0)
      .map(({ season, res }) => ({ season, ...res.data.data }));

    trendMount.innerHTML = renderTrend({
      trendRows,
      teamAInfo,
      teamBInfo,
      teamAId,
      teamBId,
    });
  }
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function renderSkeleton() {
  return `
    <div class="card fade-in">
      <div class="skeleton" style="height:90px;"></div>
    </div>
  `;
}

function renderAllTime({ allTime, teamAId, teamBId, teamAInfo, teamBInfo }) {
  const teamAName = teamAInfo?.full_name || `Team ${teamAId}`;
  const teamBName = teamBInfo?.full_name || `Team ${teamBId}`;
  const { first: colorA, second: colorB } = clashingPair(teamColor(teamAId), teamColor(teamBId));

  return `
    <div class="card fade-in">
      <div class="compare-legend">
        <span class="team-chip" style="${teamSlabStyle(colorA)}" data-team-link="${teamAId}" role="link" tabindex="0">${escapeHtml(teamAName)}</span>
        <span class="team-chip" style="${teamSlabStyle(colorB)}" data-team-link="${teamBId}" role="link" tabindex="0">${escapeHtml(teamBName)}</span>
      </div>
      ${renderComparisonRow({
        label: `All-time regular-season head-to-head (${allTime.games} games)`,
        homeValue: allTime.team_a_wins,
        awayValue: allTime.team_b_wins,
        homeColor: colorA,
        awayColor: colorB,
        formatValue: (v) => `${v} wins`,
      })}
      <p class="text-muted mt-1" style="font-size:0.76rem;">
        Regular-season games only, from the repository database. Click either team name to open its full profile in Team Explorer.
      </p>
    </div>

    <div class="card fade-in">
      <div class="section-title">Season-by-season (last ${TREND_SEASONS} seasons)</div>
      <div id="h2h-trend-mount"></div>
    </div>
  `;
}

function renderTrend({ trendRows, teamAInfo, teamBInfo, teamAId, teamBId }) {
  if (trendRows.length === 0) {
    return `<div class="empty-state">These teams have not met in the regular season in the last ${TREND_SEASONS} seasons.</div>`;
  }
  const teamAName = teamAInfo?.full_name || `Team ${teamAId}`;
  const teamBName = teamBInfo?.full_name || `Team ${teamBId}`;
  const maxWins = Math.max(...trendRows.map((r) => Math.max(r.team_a_wins, r.team_b_wins)), 1);

  return `
    <table class="data-table h2h-trend-table">
      <thead>
        <tr>
          <th scope="col">Season</th>
          <th scope="col" class="num">${escapeHtml(teamAName)}</th>
          <th scope="col" class="num">${escapeHtml(teamBName)}</th>
          <th scope="col">Series</th>
        </tr>
      </thead>
      <tbody>
        ${trendRows
          .map((row) => {
            const aShare = (row.team_a_wins / maxWins) * 100;
            const bShare = (row.team_b_wins / maxWins) * 100;
            return `
              <tr>
                <td>${row.season}</td>
                <td class="num">${row.team_a_wins}</td>
                <td class="num">${row.team_b_wins}</td>
                <td>
                  <div class="h2h-trend-bars">
                    <div class="h2h-trend-bar" style="width:${aShare}%; background:${teamColor(teamAId).primary};"></div>
                    <div class="h2h-trend-bar" style="width:${bShare}%; background:${teamColor(teamBId).primary};"></div>
                  </div>
                </td>
              </tr>
            `;
          })
          .join("")}
      </tbody>
    </table>
    <p class="text-muted mt-1" style="font-size:0.76rem;">
      Only seasons with at least one regular-season meeting are shown.
    </p>
  `;
}

// ---------------------------------------------------------------------------
// Interaction wiring
// ---------------------------------------------------------------------------

function wireTeamLinks(resultMount, ctx) {
  if (!ctx.navigate) return;
  resultMount.querySelectorAll("[data-team-link]").forEach((el) => {
    el.addEventListener("click", () => {
      ctx.navigate("/teams", { team: el.dataset.teamLink });
    });
    el.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        ctx.navigate("/teams", { team: el.dataset.teamLink });
      }
    });
  });
}

function errorBanner(message) {
  return `<div class="error-banner">⚠ ${escapeHtml(message)}</div>`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
