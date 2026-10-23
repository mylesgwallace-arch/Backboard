// pages/standings.js — Standings: each conference's record table (wins,
// losses, games behind, home/road, conference record, last 10, streak, point
// differential) from recorded results, with the playoff (top 6) and play-in
// (7-10) lines, and a second view of every team's schedule difficulty: the
// strength of the opponents played and still to play, and expected wins left.
//
// Numbers come from the standings and strength_of_schedule tools. Your team
// (the star) is remembered in this browser and highlighted across the site.

import { getStandings, getStrengthOfSchedule } from "../api.js";
import { teamColor } from "../teamColors.js";
import { readableInk } from "../colorInk.js";
import { csvButton, wireCsv } from "../components/csv.js";
import { favoriteTeam, setFavoriteTeam } from "../components/prefs.js";
import { toast } from "../components/toast.js";
import { errorBanner, escapeHtml, fixed, pct, safe, seasonLabel, signed, unwrap } from "../components/format.js";

export const meta = {
  title: "Standings",
  subtitle: "Where every team stands: records, games behind, form, and how hard the road ahead is.",
};

const VIEWS = [["table", "Standings"], ["schedule", "Schedule difficulty"]];

export function render(container, ctx = {}) {
  const season = Number(ctx.query?.get?.("season")) || null;
  const view = ctx.query?.get?.("view") === "schedule" ? "schedule" : "table";
  container.innerHTML = `
    <div class="card fade-in">
      <div class="standings-bar">
        <div class="segmented" role="group" aria-label="View">
          ${VIEWS.map(([key, label]) => `<button class="seg ${key === view ? "is-on" : ""}" aria-pressed="${key === view}" data-view="${key}">${label}</button>`).join("")}
        </div>
        <div class="field compact-field"><label for="st-season">Season</label>
          <div class="select-wrap"><select class="select" id="st-season"></select></div></div>
        <div class="card-tools">${csvButton("st-csv")}</div>
      </div>
      <p class="text-muted standings-note" id="st-note"></p>
    </div>
    <div id="st-body"><div class="card"><div class="skeleton" style="height:420px;"></div></div></div>`;
  container.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () =>
    ctx.navigate("/standings", { ...(season ? { season } : {}), ...(button.dataset.view === "schedule" ? { view: "schedule" } : {}) })));
  load(container, ctx, season, view);
}

async function load(container, ctx, season, view) {
  const res = unwrap(await safe(getStandings({ season })));
  if (!container.isConnected) return;
  const body = container.querySelector("#st-body");
  if (!res.data) {
    body.innerHTML = errorBanner(res.message);
    return;
  }
  const data = res.data;
  const select = container.querySelector("#st-season");
  const seasons = Array.from({ length: 12 }, (_, i) => data.season - i).filter((s) => s >= 1985);
  if (season && !seasons.includes(season)) seasons.unshift(season);
  select.innerHTML = seasons.map((s) => `<option value="${s}" ${s === data.season ? "selected" : ""}>${seasonLabel(s)}</option>`).join("");
  select.onchange = () => ctx.navigate("/standings", { season: select.value, ...(view === "schedule" ? { view } : {}) });
  container.querySelector("#st-note").textContent = data.games_counted
    ? `${data.games_counted} games counted, through ${data.latest_result}. Ties are listed by wins, then point differential (the NBA's tiebreakers are not applied).`
    : "No results recorded for this season yet.";

  if (view === "schedule") {
    body.innerHTML = `<div class="card"><div class="skeleton" style="height:420px;"></div></div>`;
    const sos = unwrap(await safe(getStrengthOfSchedule({ season: data.season })));
    if (!container.isConnected) return;
    if (!sos.data) {
      body.innerHTML = errorBanner(sos.message);
      return;
    }
    renderSchedule(body, sos.data, data, ctx);
    wireCsv(container.querySelector("#st-csv"), () => body.querySelector("table"), `backboard-schedule-difficulty-${data.season}`);
  } else {
    renderTables(body, data, ctx);
    wireCsv(container.querySelector("#st-csv"), () => {
      // Both conferences in one file.
      const tables = body.querySelectorAll("table");
      const merged = document.createElement("table");
      tables.forEach((table, i) => merged.append(...[...table.querySelectorAll(i ? "tbody tr" : "tr")].map((tr) => tr.cloneNode(true))));
      return merged;
    }, `backboard-standings-${data.season}`);
  }
}

function starButton(teamId, favorite) {
  const on = teamId === favorite;
  return `<button type="button" class="star-btn ${on ? "is-on" : ""}" data-star="${teamId}" aria-pressed="${on}"
    title="${on ? "Your team (click to clear)" : "Make this your team"}" aria-label="${on ? "Your team" : "Make this your team"}">★</button>`;
}

function renderTables(body, data, ctx) {
  const favorite = favoriteTeam();
  body.innerHTML = `<div class="grid-2 standings-grid">
    ${["East", "West"].map((conference) => `
      <section class="card" aria-labelledby="st-${conference}">
        <div class="card-header"><h2 id="st-${conference}">${conference}ern Conference</h2></div>
        <div class="table-scroll"><table class="data-table standings-table sticky-first">
          <caption class="sr-only">${conference}ern Conference standings, ${seasonLabel(data.season)}</caption>
          <thead><tr><th scope="col">Team</th><th class="num" scope="col">W</th><th class="num" scope="col">L</th><th class="num" scope="col">Pct</th>
            <th class="num" scope="col">GB</th><th scope="col">Home</th><th scope="col">Road</th><th scope="col">Conf</th>
            <th scope="col">L10</th><th scope="col">Strk</th><th class="num" scope="col">Diff</th></tr></thead>
          <tbody>${(data.conferences[conference] || []).map((row) => {
            const color = teamColor(row.teamId).primary;
            const line = row.conference_rank === 7 ? "is-below-cutoff" : row.conference_rank === 11 ? "is-below-playin" : "";
            return `<tr class="${line} ${row.teamId === favorite ? "is-favorite" : ""}" data-team-link="${row.teamId}" tabindex="0">
              <th scope="row" class="team" data-csv="${escapeHtml(row.teamName)}"><span class="standings-team">
                <span class="rank">${row.conference_rank}</span>
                <span class="team-dot" style="background:${color}; color:${readableInk(color)};">${escapeHtml(row.abbreviation)}</span>
                <span class="standings-name">${escapeHtml(row.teamName)}</span>${starButton(row.teamId, favorite)}</span></th>
              <td class="num">${row.wins}</td><td class="num">${row.losses}</td>
              <td class="num">${row.win_pct == null ? "—" : row.win_pct.toFixed(3).replace(/^0/, "")}</td>
              <td class="num">${row.games_behind ? fixed(row.games_behind) : "—"}</td>
              <td>${row.home}</td><td>${row.road}</td><td>${row.conference_record}</td><td>${row.last_10}</td>
              <td class="${row.streak?.startsWith("W") ? "result-w" : "result-l"} streak-cell">${row.streak || "—"}</td>
              <td class="num ${row.point_differential > 0 ? "is-up" : row.point_differential < 0 ? "is-down" : ""}">${row.point_differential == null ? "—" : signed(row.point_differential)}</td>
            </tr>`;
          }).join("")}</tbody>
        </table></div>
        <p class="table-note"><span class="cutoff-key"></span> top 6 make the playoffs · <span class="cutoff-key is-playin"></span> 7-10 go to the play-in</p>
      </section>`).join("")}
  </div>`;
  wireRows(body, ctx);
}

function renderSchedule(body, sos, standings, ctx) {
  const favorite = favoriteTeam();
  const record = {};
  standings.league.forEach((row) => { record[row.teamId] = `${row.wins}-${row.losses}`; });
  const remaining = sos.ranked_by === "remaining_opponent_strength";
  const values = sos.teams.map((t) => t[sos.ranked_by]).filter((v) => v != null);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  body.innerHTML = `<section class="card" aria-labelledby="st-sos">
    <div class="card-header"><div><h2 id="st-sos">${remaining ? "The road ahead" : "Who had it hardest"}</h2>
      <p class="card-subtitle">Opponent strength is each opponent's chance to beat an average team today (production model, home and road averaged).
        ${remaining ? "Expected wins add up the team's win chance in every game it has left." : "The season is over, so this ranks the games already played."}
        Today's strength is used for past opponents too.</p></div></div>
    <div class="table-scroll"><table class="data-table sticky-first sos-table">
      <thead><tr><th scope="col">Team</th><th class="num" scope="col">Record</th>
        <th scope="col">${remaining ? "Opponents left" : "Opponents played"}</th>
        ${remaining ? `<th class="num" scope="col">Games left</th><th class="num" scope="col">Home</th><th class="num" scope="col">B2B</th><th class="num" scope="col">Exp. wins left</th>` : ""}
        <th class="num" scope="col">Own strength</th></tr></thead>
      <tbody>${sos.teams.map((t) => {
        const value = t[sos.ranked_by];
        const width = value == null || hi === lo ? 0 : 8 + ((value - lo) / (hi - lo)) * 92;
        return `<tr data-team-link="${t.teamId}" tabindex="0" class="${t.teamId === favorite ? "is-favorite" : ""}">
          <th scope="row" class="team" data-csv="${escapeHtml(t.teamName)}"><span class="standings-team"><span class="rank">${t.difficulty_rank}</span>
            <span class="team-dot" style="background:${teamColor(t.teamId).primary}; color:${readableInk(teamColor(t.teamId).primary)};">${escapeHtml(t.abbreviation)}</span>
            <span class="standings-name">${escapeHtml(t.teamName)}</span></span></th>
          <td class="num">${record[t.teamId] || "—"}</td>
          <td data-csv="${value ?? ""}"><span class="sos-cell"><span class="sos-track"><span class="sos-fill" style="width:${width}%"></span></span><b>${value == null ? "—" : pct(value)}</b></span></td>
          ${remaining ? `<td class="num">${t.remaining_games}</td><td class="num">${t.remaining_home_games}</td><td class="num">${t.remaining_back_to_backs}</td><td class="num"><b>${fixed(t.expected_remaining_wins)}</b></td>` : ""}
          <td class="num">${pct(t.strength)}</td></tr>`;
      }).join("")}</tbody>
    </table></div>
    <p class="table-note">Rank 1 is the hardest schedule. B2B: back-to-backs left.</p>
  </section>`;
  wireRows(body, ctx);
}

function wireRows(body, ctx) {
  body.querySelectorAll("tr[data-team-link]").forEach((row) => {
    const open = (event) => {
      if (event.target.closest("[data-star]")) return;
      ctx.navigate("/teams", { team: row.dataset.teamLink });
    };
    row.addEventListener("click", open);
    row.addEventListener("keydown", (event) => event.key === "Enter" && open(event));
  });
  body.querySelectorAll("[data-star]").forEach((button) => button.addEventListener("click", (event) => {
    event.stopPropagation();
    const id = Number(button.dataset.star);
    const clearing = favoriteTeam() === id;
    setFavoriteTeam(clearing ? null : id);
    body.querySelectorAll("[data-star]").forEach((b) => {
      const on = !clearing && Number(b.dataset.star) === id;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-pressed", String(on));
      b.closest("tr")?.classList.toggle("is-favorite", on);
    });
    toast(clearing ? "Your team is cleared" : "Saved as your team: it leads Home and Tonight");
  }));
}
