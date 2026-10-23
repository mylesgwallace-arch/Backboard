// pages/pickem.js — Pick'em: pick the winners of a day's games against the
// model. Picks are kept in this browser (localStorage); once games are final
// the page scores you and the model on the same games: a point for each
// right pick and a bonus point when you went against the model and won.
//
// Games, the model's pregame numbers and results come from the scoreboard
// tool; the scoring is a count over those.

import { getScoreboard } from "../api.js";
import { teamColor } from "../teamColors.js";
import { readableInk } from "../colorInk.js";
import { pickemPicks, setPick } from "../components/prefs.js";
import { toast } from "../components/toast.js";
import { emptyState, errorBanner, escapeHtml, safe, unwrap } from "../components/format.js";

export const meta = {
  title: "Pick'em",
  subtitle: "Pick tonight's winners against the model. Beat its pick and win, and you get a bonus point.",
};

const HISTORY_DATES = 40;

export function render(container, ctx = {}) {
  const day = ctx.query?.get?.("date") || null;
  container.innerHTML = `
    <section class="card fade-in pk-score" id="pk-score" aria-live="polite"><div class="skeleton" style="height:140px;"></div></section>
    <section class="card fade-in slate-bar" aria-labelledby="pk-date">
      <div class="slate-nav">
        <button type="button" class="slate-step" id="pk-prev" aria-label="Previous day with games">◂</button>
        <h2 class="slate-date" id="pk-date">…</h2>
        <button type="button" class="slate-step" id="pk-next" aria-label="Next day with games">▸</button>
      </div>
      <div class="slate-tools"><button type="button" class="btn btn-secondary" id="pk-today">Today</button></div>
    </section>
    <div id="pk-slate"><div class="card"><div class="skeleton" style="height:320px;"></div></div></div>
    <section class="card fade-in" id="pk-history"></section>`;
  const go = (date) => ctx.navigate("/pickem", date ? { date } : null);
  container.querySelector("#pk-today").addEventListener("click", () => go(null));
  const refresh = () => {
    loadSlate(container, day, go, refresh);
    loadScore(container, refresh);
  };
  refresh();
}

async function loadSlate(container, day, go, refresh) {
  const res = unwrap(await safe(getScoreboard(day)));
  if (!container.isConnected) return;
  const mount = container.querySelector("#pk-slate");
  if (!res.data) {
    mount.innerHTML = errorBanner(res.message);
    return;
  }
  const board = res.data;
  container.querySelector("#pk-date").textContent = board.date;
  const prev = container.querySelector("#pk-prev");
  const next = container.querySelector("#pk-next");
  prev.disabled = !board.previous_date;
  next.disabled = !board.next_date;
  prev.onclick = () => go(board.previous_date);
  next.onclick = () => go(board.next_date);
  if (!board.games.length) {
    mount.innerHTML = emptyState("No games on this day.");
    return;
  }
  const paint = () => {
    const picks = pickemPicks();
    mount.innerHTML = `<div class="pk-grid">${board.games.map((game) => pickCard(game, picks[game.gameId])).join("")}</div>`;
    mount.querySelectorAll("[data-pick]").forEach((button) => button.addEventListener("click", () => {
      const game = board.games.find((g) => String(g.gameId) === button.dataset.game);
      const team = Number(button.dataset.pick);
      const current = pickemPicks()[game.gameId];
      if (current?.team === team) setPick(game.gameId, null);
      else setPick(game.gameId, { team, date: board.date, home: game.home.teamId, away: game.away.teamId });
      paint();
      loadScore(container, refresh);
    }));
  };
  paint();
}

function pickCard(game, pick) {
  const final = game.status === "final";
  const pHome = game.home_win_probability;
  const modelPick = pHome >= 0.5 ? game.home.teamId : game.away.teamId;
  const side = (team, probability) => {
    const colors = teamColor(team.teamId);
    const chosen = pick?.team === team.teamId;
    const won = final && game.winner_team_id === team.teamId;
    return `<button type="button" class="pk-team ${chosen ? "is-picked" : ""} ${won ? "is-winner" : ""}" data-game="${game.gameId}" data-pick="${team.teamId}"
        ${final ? "disabled" : ""} aria-pressed="${chosen}" style="--team:${colors.primary}; --team-ink:${readableInk(colors.primary)}">
      <span class="pk-abbr">${escapeHtml(team.abbreviation)}</span>
      <span class="pk-name">${escapeHtml(team.name)}</span>
      <span class="pk-model">${modelPick === team.teamId ? "Model's pick · " : ""}${Math.round(probability * 100)}%</span>
      ${final ? `<b class="pk-score-num">${team.teamId === game.home.teamId ? game.home_score : game.away_score}</b>` : ""}
    </button>`;
  };
  let verdict = "";
  if (final && pick) {
    const right = pick.team === game.winner_team_id;
    const bonus = right && pick.team !== modelPick;
    verdict = `<p class="stamp ${right ? "stamp--ink" : "stamp--tilt"} pk-verdict">${right ? (bonus ? "Beat the model +2" : "Right +1") : "Wrong"}</p>`;
  } else if (final) {
    verdict = `<p class="pk-locked">Final · no pick</p>`;
  }
  return `<article class="pk-card ${pick ? "has-pick" : ""}">
    <div class="pk-sides">${side(game.away, 1 - pHome)}<span class="pk-at" aria-hidden="true">@</span>${side(game.home, pHome)}</div>
    ${verdict}
  </article>`;
}

async function loadScore(container, refresh) {
  const picks = pickemPicks();
  const dates = [...new Set(Object.values(picks).map((p) => p.date))].sort().reverse().slice(0, HISTORY_DATES);
  const scoreMount = container.querySelector("#pk-score");
  const history = container.querySelector("#pk-history");
  if (!dates.length) {
    scoreMount.innerHTML = `<h2 class="tabloid-headline">You vs the model</h2>
      <p class="text-muted">Pick a winner below to start. Picks stay in this browser; results score themselves once games are final.</p>`;
    history.hidden = true;
    return;
  }
  const boards = await Promise.all(dates.map((date) => safe(getScoreboard(date)).then(unwrap)));
  if (!container.isConnected) return;
  const rows = [];
  boards.forEach((res) => (res.data?.games || []).forEach((game) => {
    const pick = picks[game.gameId];
    if (!pick) return;
    const modelPick = game.home_win_probability >= 0.5 ? game.home.teamId : game.away.teamId;
    rows.push({ game, pick, modelPick, final: game.status === "final" });
  }));
  const graded = rows.filter((r) => r.final);
  const mine = graded.filter((r) => r.pick.team === r.game.winner_team_id);
  const model = graded.filter((r) => r.modelPick === r.game.winner_team_id);
  const bonus = mine.filter((r) => r.pick.team !== r.modelPick);
  const points = mine.length + bonus.length;
  const lead = mine.length - model.length;
  scoreMount.innerHTML = `
    <p class="kicker">${graded.length} graded pick${graded.length === 1 ? "" : "s"} · ${rows.length - graded.length} pending</p>
    <h2 class="tabloid-headline">${graded.length ? (lead > 0 ? `You lead the model by ${lead}` : lead < 0 ? `The model leads by ${-lead}` : "Level with the model") : "Waiting on results"}</h2>
    <div class="pk-tally">
      <div><span class="kicker">You</span><b data-count="${mine.length}">${mine.length}</b><small>of ${graded.length}</small></div>
      <div><span class="kicker">Model</span><b data-count="${model.length}">${model.length}</b><small>same games</small></div>
      <div><span class="kicker">Points</span><b data-count="${points}">${points}</b><small>${bonus.length} upset bonus</small></div>
    </div>
    <button type="button" class="link-btn" id="pk-clear">Clear all picks</button>`;
  scoreMount.querySelector("#pk-clear").addEventListener("click", () => {
    if (!window.confirm("Clear every pick in this browser?")) return;
    Object.keys(pickemPicks()).forEach((id) => setPick(id, null));
    toast("Picks cleared.");
    refresh();
  });
  history.hidden = false;
  history.innerHTML = `<h2>Your picks</h2>
    <div class="table-scroll"><table class="data-table compact-table"><thead><tr><th>Date</th><th>Game</th><th>You</th><th>Model</th><th>Result</th></tr></thead>
    <tbody>${rows.sort((a, b) => b.game.tip.localeCompare(a.game.tip)).map((r) => {
      const abbr = (id) => (id === r.game.home.teamId ? r.game.home.abbreviation : r.game.away.abbreviation);
      const result = !r.final ? "pending" : r.pick.team === r.game.winner_team_id ? (r.pick.team !== r.modelPick ? "✓ +2" : "✓") : "✗";
      return `<tr><td>${escapeHtml(r.game.tip.slice(0, 10))}</td><td>${escapeHtml(r.game.away.abbreviation)} @ ${escapeHtml(r.game.home.abbreviation)}</td>
        <td><b>${escapeHtml(abbr(r.pick.team))}</b></td><td>${escapeHtml(abbr(r.modelPick))}</td><td>${result}</td></tr>`;
    }).join("")}</tbody></table></div>`;
}
