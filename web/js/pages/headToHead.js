// pages/headToHead.js — Head-to-Head: a whole rivalry in one view. Every
// meeting since the franchises first played (regular season, play-in and
// playoffs), the all-time tally, streaks, the biggest wins, season-by-season
// bars, playoff series, and the production model's record on these games.
// Deep links: /head-to-head?a=<teamId>&b=<teamId>.
//
// Every number comes from the rivalry tool.

import { getRivalry } from "../api.js";
import { fetchTeams, createTeamSelect } from "../components/teamSelect.js";
import { teamColor } from "../teamColors.js";
import { clashingPair, readableInk } from "../colorInk.js";
import { favoriteTeam } from "../components/prefs.js";
import { csvButton, wireCsv } from "../components/csv.js";
import { emptyState, errorBanner, escapeHtml, fixed, safe, signed, unwrap } from "../components/format.js";

export const meta = {
  title: "Head-to-Head",
  subtitle: "Any two franchises: every meeting, the streaks, the playoff series, and how the model called them.",
};

const SHOWN_MEETINGS = 25;

export function render(container, ctx = {}) {
  container.innerHTML = `
    <div class="card fade-in">
      <div id="h2h-form-mount"></div>
      <div id="h2h-error-mount" class="mt-1"></div>
    </div>
    <div id="h2h-result-mount"></div>`;
  const formMount = container.querySelector("#h2h-form-mount");
  const resultMount = container.querySelector("#h2h-result-mount");
  const teamA = createTeamSelect({ id: "h2h-a", labelText: "Team", placeholder: "Select a team…" });
  const teamB = createTeamSelect({ id: "h2h-b", labelText: "Opponent", placeholder: "Select an opponent…" });
  const grid = document.createElement("div");
  grid.className = "matchup-form-grid";
  const swap = document.createElement("button");
  swap.type = "button";
  swap.className = "swap-btn";
  swap.title = "Swap teams";
  swap.setAttribute("aria-label", "Swap the two teams");
  swap.textContent = "⇄";
  grid.append(teamA.root, swap, teamB.root);
  formMount.appendChild(grid);

  const go = () => {
    const a = teamA.selectEl.value;
    const b = teamB.selectEl.value;
    if (a && b && a !== b) ctx.navigate("/head-to-head", { a, b });
  };
  teamA.selectEl.addEventListener("change", go);
  teamB.selectEl.addEventListener("change", go);
  swap.addEventListener("click", () => {
    const a = teamA.selectEl.value;
    teamA.selectEl.value = teamB.selectEl.value;
    teamB.selectEl.value = a;
    go();
  });

  fetchTeams().then((teams) => {
    teamA.setTeams(teams);
    teamB.setTeams(teams);
    const has = (id) => id && teams.some((t) => String(t.team_id) === String(id));
    const a = ctx.query?.get?.("a") || favoriteTeam();
    const b = ctx.query?.get?.("b");
    if (has(a)) teamA.selectEl.value = String(a);
    if (has(b)) teamB.selectEl.value = String(b);
    if (has(a) && has(b) && String(a) !== String(b)) load(resultMount, Number(a), Number(b));
    else resultMount.innerHTML = emptyState("Pick two teams to see their whole history.");
  }).catch((err) => {
    container.querySelector("#h2h-error-mount").innerHTML = errorBanner(`Could not load the team list: ${err.message}`);
  });
}

async function load(mount, a, b) {
  mount.innerHTML = `<div class="card"><div class="skeleton" style="height:360px;"></div></div>`;
  const res = unwrap(await safe(getRivalry({ teamAId: a, teamBId: b })));
  if (!mount.isConnected) return;
  if (!res.data) {
    mount.innerHTML = errorBanner(res.message);
    return;
  }
  const data = res.data;
  document.title = `${data.team_a.abbreviation} vs ${data.team_b.abbreviation} · Head-to-Head · Backboard`;
  if (!data.games) {
    mount.innerHTML = emptyState(`${data.team_a.name} and ${data.team_b.name} have no recorded meetings.`);
    return;
  }
  const pair = clashingPair(teamColor(a), teamColor(b));
  const colors = { A: pair.first, B: pair.second };
  const name = { A: data.team_a, B: data.team_b };
  const s = data.summary;
  const streak = s.streaks.current;
  const total = s.a_wins + s.b_wins;
  mount.style.cssText = `--a:${colors.A}; --a-ink:${readableInk(colors.A)}; --b:${colors.B}; --b-ink:${readableInk(colors.B)}`;
  mount.innerHTML = `
    <section class="card fade-in h2h-hero">
      <div class="h2h-sides">
        <a class="h2h-side h2h-side--a" href="/teams?team=${a}"><span class="h2h-abbr">${escapeHtml(data.team_a.abbreviation)}</span>
          <b class="h2h-wins" data-count="${s.a_wins}">${s.a_wins}</b><span>${escapeHtml(data.team_a.name)}</span></a>
        <span class="h2h-vs" aria-hidden="true">vs</span>
        <a class="h2h-side h2h-side--b" href="/teams?team=${b}"><span class="h2h-abbr">${escapeHtml(data.team_b.abbreviation)}</span>
          <b class="h2h-wins" data-count="${s.b_wins}">${s.b_wins}</b><span>${escapeHtml(data.team_b.name)}</span></a>
      </div>
      <div class="h2h-split" role="img" aria-label="${s.a_wins} wins to ${s.b_wins}">
        <span style="flex:${s.a_wins || 0.001}; background:var(--a)"></span><span style="flex:${s.b_wins || 0.001}; background:var(--b)"></span></div>
      <ul class="h2h-facts">
        <li><b>${total}</b> meetings since ${escapeHtml(s.first_meeting.slice(0, 4))}</li>
        <li>Regular season <b>${s.regular_season.a_wins}–${s.regular_season.b_wins}</b></li>
        ${s.playoffs.a_wins + s.playoffs.b_wins ? `<li>Playoffs <b>${s.playoffs.a_wins}–${s.playoffs.b_wins}</b></li>` : ""}
        <li>Average margin <b>${signed(s.mean_margin)}</b> ${escapeHtml(data.team_a.abbreviation)}</li>
        ${streak ? `<li>Streak <b>${escapeHtml(name[streak.side].abbreviation)} ${streak.games}</b></li>` : ""}
        <li>Longest runs <b>${escapeHtml(data.team_a.abbreviation)} ${s.streaks.longest.A}</b> · <b>${escapeHtml(data.team_b.abbreviation)} ${s.streaks.longest.B}</b></li>
      </ul>
    </section>
    <div class="grid-2">
      <section class="card fade-in"><h2>Season by season</h2>
        <div class="h2h-seasons">${data.seasons.slice(-20).map((season) => {
          const n = season.a_wins + season.b_wins;
          return `<div class="h2h-season" title="${escapeHtml(season.label)}: ${season.a_wins}-${season.b_wins}${season.mean_margin != null ? `, avg ${signed(season.mean_margin)}` : ""}">
            <span class="h2h-season-label">${escapeHtml(season.label.slice(2))}</span>
            <span class="h2h-season-bar"><i style="height:${(season.a_wins / Math.max(n, 1)) * 100}%; background:var(--a)"></i></span>
            <small>${season.a_wins}-${season.b_wins}</small></div>`;
        }).join("")}</div>
        <p class="table-note">Share of each season's meetings won by ${escapeHtml(data.team_a.abbreviation)} (last 20 seasons).</p>
      </section>
      <section class="card fade-in"><h2>The model on this rivalry</h2>
        ${s.model.games ? `<p class="h2h-model"><b data-count="${s.model.correct}">${s.model.correct}</b> of ${s.model.games} picked right
          <span class="text-muted">(${Math.round(s.model.accuracy * 100)}%, Brier ${fixed(s.model.brier, 3)})</span></p>
          <p class="text-muted">Graded on the games with a stored pregame probability.</p>` : `<p class="text-muted">No meeting has a stored pregame probability.</p>`}
        ${["A", "B"].map((side) => s.biggest_win[side] ? `<p class="h2h-big"><span class="kicker">Biggest ${escapeHtml(name[side].abbreviation)} win</span>
          <b>${Math.abs(s.biggest_win[side].margin)} points</b> · ${escapeHtml(s.biggest_win[side].date)} (${s.biggest_win[side].a_score}-${s.biggest_win[side].b_score})</p>` : "").join("")}
        ${data.series.length ? `<div class="section-title mt-1">Playoff series</div><ul class="plain-list">${data.series.map((series) =>
          `<li><b>${escapeHtml(series.label)}</b> ${escapeHtml(series.round || "")}: ${escapeHtml(name[series.winner].name)} ${Math.max(series.a_wins, series.b_wins)}-${Math.min(series.a_wins, series.b_wins)}</li>`).join("")}</ul>` : ""}
      </section>
    </div>
    <section class="card fade-in" aria-labelledby="h2h-games-title">
      <div class="card-header"><div><h2 id="h2h-games-title">Every meeting</h2>
        <p class="card-subtitle">Newest first. Model: ${escapeHtml(data.team_a.abbreviation)}'s pregame win chance.</p></div>
        <div class="card-tools">${csvButton("h2h-csv")}</div></div>
      <div class="table-scroll"><table class="data-table compact-table" id="h2h-table"><thead><tr><th>Date</th><th>Type</th><th>Where</th><th class="num">Score</th><th class="num">Model</th><th></th></tr></thead>
        <tbody>${data.meetings.map((m, i) => `<tr class="${i >= SHOWN_MEETINGS ? "is-extra" : ""}" ${i >= SHOWN_MEETINGS ? "hidden" : ""}>
          <td>${escapeHtml(m.date)}</td><td>${escapeHtml(m.game_type === "Regular Season" ? "Regular" : m.game_type)}${m.label ? ` <small>${escapeHtml(m.label)}</small>` : ""}</td>
          <td>${m.a_home ? "Home" : "Away"}</td>
          <td class="num"><b style="color:${m.winner === "A" ? "inherit" : "var(--fg-3)"}">${m.a_score}</b>–<b style="color:${m.winner === "B" ? "inherit" : "var(--fg-3)"}">${m.b_score}</b></td>
          <td class="num">${m.model_p_a == null ? "—" : `${Math.round(m.model_p_a * 100)}%`}</td>
          <td>${m.model_correct == null ? "" : m.model_correct ? "✓" : "✗"}</td></tr>`).join("")}</tbody></table></div>
      ${data.meetings.length > SHOWN_MEETINGS ? `<button type="button" class="btn btn-secondary mt-1" id="h2h-more">Show all ${data.meetings.length}</button>` : ""}
    </section>`;
  mount.querySelector("#h2h-more")?.addEventListener("click", (event) => {
    mount.querySelectorAll("tr.is-extra").forEach((row) => { row.hidden = false; });
    event.target.remove();
  });
  wireCsv(mount.querySelector("#h2h-csv"), mount.querySelector("#h2h-table"), `backboard-${data.team_a.abbreviation}-vs-${data.team_b.abbreviation}`.toLowerCase());
}
