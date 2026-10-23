// pages/recap.js — Season Recap: a season told from data the site already
// stores. The champion and the best records, the biggest upsets, the
// model's worst misses and its best calls (where it beat plain Elo), the
// teams that beat or missed their preseason projection by the most, and the
// biggest swings in the stored playoff odds.
//
// Every number comes from the season_recap tool.

import { getSeasonRecap } from "../api.js";
import { teamColor } from "../teamColors.js";
import { readableInk } from "../colorInk.js";
import { uncrumpleOnView } from "../components/motion.js";
import { errorBanner, escapeHtml, fixed, safe, seasonLabel, signed, unwrap } from "../components/format.js";

export const meta = {
  title: "Season Recap",
  subtitle: "The season in data: who won, who shocked everyone, and where the model got it right and wrong.",
};

export function render(container, ctx = {}) {
  const season = Number(ctx.query?.get?.("season")) || null;
  container.innerHTML = `
    <div class="card fade-in leaders-controls">
      <div class="field compact-field"><label for="rc-season">Season (start year)</label>
        <input class="input" id="rc-season" type="number" min="1990" max="2100" value="${season ?? ""}" placeholder="latest"></div>
    </div>
    <div id="rc-body"><div class="card"><div class="skeleton" style="height:520px;"></div></div></div>`;
  container.querySelector("#rc-season").addEventListener("change", (event) =>
    ctx.navigate("/recap", Number(event.target.value) ? { season: Number(event.target.value) } : null));
  load(container, season);
}

const team = (t) => `<span class="team-dot" style="background:${teamColor(t.teamId).primary}; color:${readableInk(teamColor(t.teamId).primary)};">${escapeHtml(t.abbreviation)}</span>`;

function gameLine(g) {
  const homeWon = g.winner_team_id === g.home.teamId;
  return `<li class="recap-game">
    <span class="recap-date">${escapeHtml(g.date)}</span>
    <span class="recap-teams">${team(g.away)} <b class="${homeWon ? "" : "is-winner"}">${g.away_score}</b> – <b class="${homeWon ? "is-winner" : ""}">${g.home_score}</b> ${team(g.home)}</span>
    <span class="recap-odds">winner had <b>${Math.round(g.winner_probability * 100)}%</b></span>
  </li>`;
}

async function load(container, season) {
  const res = unwrap(await safe(getSeasonRecap(season)));
  if (!container.isConnected) return;
  const body = container.querySelector("#rc-body");
  if (!res.data) {
    body.innerHTML = errorBanner(res.message);
    return;
  }
  const data = res.data;
  container.querySelector("#rc-season").value = data.season;
  document.title = `${data.label} recap · Backboard`;
  const champion = data.champion;
  const championColor = champion ? teamColor(champion.teamId) : null;
  const surpriseRow = (s) => {
    const width = Math.min(100, Math.abs(s.difference) * 6);
    return `<li class="recap-surprise">
      ${team(s)} <span class="recap-surprise-text"><b>${s.wins}-${s.losses}</b> <small>vs ${fixed(s.expected_to_date, 1)} expected (preseason ${fixed(s.preseason_wins, 1)})</small></span>
      <span class="recap-surprise-bar"><i class="${s.difference > 0 ? "is-pos" : "is-neg"}" style="width:${width}%"></i></span>
      <b class="recap-surprise-diff">${signed(s.difference)}</b></li>`;
  };
  body.innerHTML = `
    <section class="card fade-in recap-cover" id="rc-cover" ${championColor ? `style="--team:${championColor.primary}; --team-ink:${readableInk(championColor.primary)}"` : ""}>
      <p class="kicker">${escapeHtml(data.label)} · ${data.games_played.toLocaleString("en-US")} games played${data.complete ? "" : " so far"}</p>
      ${champion ? `<h2 class="recap-champion"><span>Champions</span>${escapeHtml(champion.name)}</h2>`
        : `<h2 class="tabloid-headline">The season ${data.complete ? "is over" : "so far"}</h2>`}
      <div class="recap-best">${Object.entries(data.best_records).map(([conference, t]) =>
        `<p><span class="kicker">Best in the ${escapeHtml(conference)}</span> ${team(t)} <b>${t.wins}-${t.losses}</b></p>`).join("")}</div>
    </section>
    <div class="grid-2">
      <section class="card fade-in"><h2>Biggest upsets</h2><ol class="recap-list">${data.upsets.map(gameLine).join("")}</ol></section>
      <section class="card fade-in"><h2>The model's best calls</h2>
        <p class="card-subtitle">Games where the model and plain Elo picked different winners, and the model was right.</p>
        ${data.best_calls.length ? `<ol class="recap-list">${data.best_calls.map((g) => `${gameLine(g).replace("</li>",
          `<span class="recap-odds">model ${Math.round(g.model_home_probability * 100)}% · Elo ${Math.round(g.elo_home_probability * 100)}% home</span></li>`)}`).join("")}</ol>`
          : `<p class="text-muted">None this season.</p>`}
      </section>
    </div>
    <div class="grid-2">
      <section class="card fade-in"><h2>Beat the forecast</h2>
        <ol class="recap-list">${data.surprises.over.map(surpriseRow).join("") || "<li class=\"text-muted\">Nobody yet.</li>"}</ol></section>
      <section class="card fade-in"><h2>Fell short</h2>
        <ol class="recap-list">${data.surprises.under.map(surpriseRow).join("") || "<li class=\"text-muted\">Nobody yet.</li>"}</ol></section>
    </div>
    <section class="card fade-in"><h2>Turning points</h2>
      ${data.turning_points.swings.length ? `<p class="card-subtitle">The biggest moves between stored odds snapshots (${data.turning_points.snapshots} snapshots).</p>
      <ol class="recap-list">${data.turning_points.swings.map((swing) => `<li class="recap-swing">${team(swing)}
        <span>${escapeHtml(swing.measure)} <b>${Math.round(swing.before * 100)}% → ${Math.round(swing.after * 100)}%</b></span>
        <small class="text-muted">${escapeHtml(swing.from)} to ${escapeHtml(swing.to)}</small>
        <b class="${swing.change > 0 ? "delta-up" : "delta-down"}">${swing.change > 0 ? "▲" : "▼"} ${Math.round(Math.abs(swing.change) * 100)}</b></li>`).join("")}</ol>`
        : `<p class="text-muted">No odds snapshots are stored for this season. The daily refresh stores one a day; <code>python src/odds_history.py backfill --season ${data.season}</code> rebuilds a season's history.</p>`}
    </section>
    ${data.model?.overall ? `<section class="card fade-in"><h2>How the model did</h2>
      <p class="recap-grade"><b>${Math.round(data.model.overall.accuracy * 100)}%</b> of picks right · Brier ${fixed(data.model.overall.brier, 3)} · log loss ${fixed(data.model.overall.log_loss, 3)}
        over ${data.model.overall.games.toLocaleString("en-US")} games. <a href="/report-card?season=${data.season}">Full report card ▸</a></p></section>` : ""}`;
  if (champion) uncrumpleOnView(body.querySelector("#rc-cover"), { seed: data.season });
}
