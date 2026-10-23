// pages/tonight.js — Tonight: every game on one day with the production
// model's pick, the predicted score and, once a game is played, the final and
// whether the pick held. While games are on, the live scoreboard (ESPN, via
// the live_scoreboard tool) adds the score, the clock and an in-game win
// probability that starts at the pregame pick. Injury listings come from the
// stored injury report.
//
// Numbers come from the scoreboard, live_scoreboard and injury_report tools;
// the page only formats them.

import { getInjuryReport, getLiveScoreboard, getScoreboard } from "../api.js";
import { teamColor } from "../teamColors.js";
import { clashingPair, readableInk } from "../colorInk.js";
import { winProbabilityChart } from "../components/charts.js";
import { favoriteTeam } from "../components/prefs.js";
import { shareImage } from "../components/share.js";
import { uncrumple } from "../components/motion.js";
import { emptyState, errorBanner, escapeHtml, fixed, safe, signed, unwrap } from "../components/format.js";

export const meta = {
  title: "Tonight",
  subtitle: "Every game on the slate: the model's pick before tip-off, the live win probability while it's on, and how the pick held up.",
};

const LIVE_POLL_MS = 30000;
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

let pollTimer = null;
let injuriesPromise = null;

function stopPolling() {
  clearTimeout(pollTimer);
  pollTimer = null;
}

function prettyDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  return { weekday: WEEKDAYS[date.getUTCDay()], month: MONTHS[m - 1], day: d, year: y };
}

function tipTime(tip) {
  const [, time] = String(tip).split(" ");
  if (!time) return "";
  const [h, m] = time.split(":").map(Number);
  return `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"} ET`;
}

export function render(container, ctx = {}) {
  stopPolling();
  container.innerHTML = `
    <section class="card slate-bar fade-in" aria-labelledby="slate-date">
      <div class="slate-nav">
        <button type="button" class="slate-step" id="tn-prev" aria-label="Previous day with games">◂</button>
        <h2 class="slate-date" id="slate-date"><span class="skeleton" style="display:inline-block;width:12ch;height:1em;"></span></h2>
        <button type="button" class="slate-step" id="tn-next" aria-label="Next day with games">▸</button>
      </div>
      <div class="slate-tools">
        <button type="button" class="btn btn-secondary" id="tn-today">Today</button>
        <label class="sr-only" for="tn-date">Pick a date</label>
        <input type="date" class="input slate-picker" id="tn-date">
      </div>
      <div class="slate-summary" id="tn-summary" aria-live="polite"></div>
    </section>
    <div id="tn-live-note"></div>
    <div class="game-grid" id="tn-games" aria-live="polite"></div>
    <details class="card collapsible fade-in tn-method">
      <summary>How to read a game card</summary>
      <ul class="plain-list">
        <li><strong>The pick</strong> is the production model's home win probability before tip-off. A finished game keeps the probability the model gave it that morning; an unplayed game is scored with each team's strength as of that day.</li>
        <li><strong>Predicted score</strong> comes from the separate margin model (about ±16 points either way covers 80% of games). The two models disagree on the favorite in about 7% of games.</li>
        <li><strong>Upset</strong> means the winner had under a 40% chance before the game.</li>
        <li><strong>Live win probability</strong> starts at the pregame pick and moves with the score and the clock (a random-walk model; it has not been fitted to play-by-play data).</li>
        <li><strong>Injuries</strong> are the latest stored report. The model has no injury input, so a listed star does not move the pick.</li>
      </ul>
    </details>`;

  const day = ctx.query?.get?.("date") || null;
  const go = (date) => ctx.navigate("/tonight", date ? { date } : null);
  container.querySelector("#tn-today").addEventListener("click", () => go(null));
  container.querySelector("#tn-date").addEventListener("change", (event) => event.target.value && go(event.target.value));
  injuriesPromise ||= safe(getInjuryReport()).then(unwrap);
  load(container, ctx, day, go);
}

async function load(container, ctx, day, go) {
  const grid = container.querySelector("#tn-games");
  grid.innerHTML = `${'<div class="game-card is-loading"><div class="skeleton" style="height:230px;"></div></div>'.repeat(4)}`;
  const res = unwrap(await safe(getScoreboard(day)));
  if (!container.isConnected) return;
  if (!res.data) {
    grid.innerHTML = errorBanner(res.message);
    return;
  }
  const board = res.data;
  const pretty = prettyDate(board.date);
  container.querySelector("#slate-date").innerHTML =
    `<span class="slate-weekday">${pretty.weekday}</span> ${pretty.month} <span class="hl">${pretty.day}</span> <span class="slate-year">${pretty.year}</span>`;
  container.querySelector("#tn-date").value = board.date;
  const prev = container.querySelector("#tn-prev");
  const next = container.querySelector("#tn-next");
  prev.disabled = !board.previous_date;
  next.disabled = !board.next_date;
  prev.onclick = () => go(board.previous_date);
  next.onclick = () => go(board.next_date);
  document.title = `Tonight · ${pretty.month} ${pretty.day} · Backboard`;

  const injuries = await injuriesPromise;
  const outByTeam = {};
  (injuries?.data?.teams || []).forEach((team) => { outByTeam[team.teamId] = team.players; });
  renderSummary(container.querySelector("#tn-summary"), board.summary);

  if (!board.games.length) {
    grid.innerHTML = emptyState(`No games on ${pretty.month} ${pretty.day}. ${board.next_date ? "Use ▸ for the next game day." : ""}`);
    return;
  }
  const favorite = favoriteTeam();
  const games = [...board.games].sort((a, b) =>
    Number(b.home.teamId === favorite || b.away.teamId === favorite) - Number(a.home.teamId === favorite || a.away.teamId === favorite)
    || a.tip.localeCompare(b.tip));
  grid.innerHTML = games.map((game) => gameCard(game, outByTeam, favorite)).join("");
  wireCards(grid, games);
  const upset = grid.querySelector(".game-card.is-upset");
  if (upset) uncrumple(upset, { seed: Number(upset.dataset.game) });

  const today = new Date().toISOString().slice(0, 10);
  if (board.summary.scheduled > 0 && Math.abs(Date.parse(board.date) - Date.parse(today)) <= 86400000) {
    pollLive(container, board, grid);
  }
}

function renderSummary(mount, summary) {
  const graded = summary.final;
  mount.innerHTML = `
    <span class="slate-chip"><b data-count="${summary.games}">${summary.games}</b> game${summary.games === 1 ? "" : "s"}</span>
    ${graded ? `<span class="slate-chip"><b>${summary.model_correct}–${graded - summary.model_correct}</b> model picks</span>` : ""}
    ${summary.upsets ? `<span class="slate-chip is-hot"><b>${summary.upsets}</b> upset${summary.upsets === 1 ? "" : "s"}</span>` : ""}
    ${summary.scheduled ? `<span class="slate-chip"><b>${summary.scheduled}</b> still to play</span>` : ""}`;
}

function teamSide(team, probability, colors, side, extra = "") {
  const ink = readableInk(colors);
  return `<a class="game-team game-team--${side}" href="/teams?team=${team.teamId}" style="--slab:${colors}; --slab-ink:${ink};">
    <span class="game-team-side">${side === "home" ? "Home" : "Away"}</span>
    <span class="game-team-abbr">${escapeHtml(team.abbreviation || "")}</span>
    <span class="game-team-name">${escapeHtml(team.name)}</span>
    <span class="game-team-prob" data-prob="${side}">${Math.round(probability * 100)}<small>%</small></span>
    ${extra}
  </a>`;
}

function injuryLine(players, abbreviation) {
  const out = (players || []).filter((p) => p.out);
  const doubtful = (players || []).filter((p) => !p.out);
  if (!out.length && !doubtful.length) return "";
  const names = (list) => list.slice(0, 3).map((p) => `${escapeHtml(p.name)}${p.injury ? ` <small>(${escapeHtml(p.injury.toLowerCase())})</small>` : ""}`).join(", ");
  return `<li><b>${escapeHtml(abbreviation)}</b> ${out.length ? `out: ${names(out)}${out.length > 3 ? ` +${out.length - 3}` : ""}` : ""}${out.length && doubtful.length ? "; " : ""}${doubtful.length ? `${escapeHtml(doubtful[0].status.toLowerCase())}: ${names(doubtful)}` : ""}</li>`;
}

function gameCard(game, outByTeam, favorite) {
  const homePalette = teamColor(game.home.teamId);
  const awayPalette = teamColor(game.away.teamId);
  const pair = clashingPair(homePalette, awayPalette);
  const pHome = game.home_win_probability;
  const final = game.status === "final";
  const homeWon = final && game.winner_team_id === game.home.teamId;
  const pickHome = pHome >= 0.5;
  const pick = pickHome ? game.home : game.away;
  const confidence = Math.max(pHome, 1 - pHome);
  const isFavoriteTeam = favorite && (game.home.teamId === favorite || game.away.teamId === favorite);
  const scoreLine = final
    ? `<div class="game-final">
        <span class="${homeWon ? "" : "is-winner"}">${escapeHtml(game.away.abbreviation)} <b>${game.away_score}</b></span>
        <span class="game-final-dash">–</span>
        <span class="${homeWon ? "is-winner" : ""}"><b>${game.home_score}</b> ${escapeHtml(game.home.abbreviation)}</span>
      </div>`
    : game.predicted_home_points != null
      ? `<p class="game-line">Predicted <b>${escapeHtml(game.away.abbreviation)} ${fixed(game.predicted_away_points, 0)}</b> – <b>${fixed(game.predicted_home_points, 0)} ${escapeHtml(game.home.abbreviation)}</b>
          <span class="text-muted">(home ${signed(game.predicted_home_margin)})</span></p>`
      : "";
  const verdict = final
    ? game.upset
      ? `<p class="stamp stamp--tilt game-stamp">Upset · ${Math.round(game.winner_pregame_probability * 100)}% shot</p>`
      : `<p class="stamp game-stamp ${game.model_correct ? "stamp--ink" : ""}">${game.model_correct ? "Pick ✓" : "Pick ✗"}</p>`
    : "";
  const injuries = [injuryLine(outByTeam[game.away.teamId], game.away.abbreviation),
    injuryLine(outByTeam[game.home.teamId], game.home.abbreviation)].join("");
  return `
    <article class="game-card lift ${final ? "is-final" : ""} ${game.upset ? "is-upset" : ""} ${isFavoriteTeam ? "is-favorite" : ""}"
      data-game="${game.gameId}" data-home="${game.home.teamId}" data-away="${game.away.teamId}"
      aria-label="${escapeHtml(game.away.name)} at ${escapeHtml(game.home.name)}">
      <header class="game-meta">
        <span class="game-status" data-status>${final ? "Final" : tipTime(game.tip)}</span>
        <span>${escapeHtml(game.label || (game.game_type === "Regular Season" ? "" : game.game_type))}</span>
        ${isFavoriteTeam ? `<span class="badge accent">Your team</span>` : ""}
      </header>
      <div class="game-teams">
        ${teamSide(game.away, 1 - pHome, pair.second, "away")}
        <span class="game-at" aria-hidden="true">@</span>
        ${teamSide(game.home, pHome, pair.first, "home")}
      </div>
      <div class="game-bar" role="img" aria-label="${escapeHtml(game.away.abbreviation)} ${Math.round((1 - pHome) * 100)}%, ${escapeHtml(game.home.abbreviation)} ${Math.round(pHome * 100)}%">
        <span class="game-bar-fill" data-bar style="width:${(1 - pHome) * 100}%; background:${pair.second};"></span>
        <span class="game-bar-fill" style="flex:1; background:${pair.first};"></span>
      </div>
      <p class="game-pick">Pick: <b>${escapeHtml(pick.name)}</b> · ${Math.round(confidence * 100)}%${game.probability_source === "frozen" ? "" : " <span class=\"text-muted\">(pregame)</span>"}</p>
      ${scoreLine}
      <div class="game-live" data-live hidden></div>
      ${verdict}
      ${injuries ? `<ul class="game-injuries" aria-label="Injury report">${injuries}</ul>` : ""}
      <footer class="game-actions">
        <a class="link-btn" href="/matchups?home=${game.home.teamId}&away=${game.away.teamId}">Full matchup ▸</a>
        <button type="button" class="link-btn" data-share="${game.gameId}">Share</button>
      </footer>
    </article>`;
}

function wireCards(grid, games) {
  grid.querySelectorAll("[data-share]").forEach((button) => button.addEventListener("click", () => {
    const game = games.find((g) => String(g.gameId) === button.dataset.share);
    if (!game) return;
    const pHome = game.home_win_probability;
    const final = game.status === "final";
    shareImage({
      kicker: `${final ? "Final" : "Tonight"} · ${game.tip.slice(0, 10)}`,
      title: final ? `${game.away.abbreviation} ${game.away_score} – ${game.home_score} ${game.home.abbreviation}` : `${game.away.name} at ${game.home.name}`,
      left: { name: game.away.name, sub: "Away", value: `${Math.round((1 - pHome) * 100)}%`, label: "chance before tip-off", color: teamColor(game.away.teamId).primary },
      right: { name: game.home.name, sub: "Home", value: `${Math.round(pHome * 100)}%`, label: "chance before tip-off", color: teamColor(game.home.teamId).primary },
      lines: [final ? (game.upset ? "Upset: the winner was under 40% before the game." : game.model_correct ? "The model's pick won." : "The model's pick lost.") :
        game.predicted_home_points != null ? `Predicted score ${game.away.abbreviation} ${Math.round(game.predicted_away_points)} - ${Math.round(game.predicted_home_points)} ${game.home.abbreviation}` : ""],
    }, { filename: `backboard-${game.away.abbreviation}-at-${game.home.abbreviation}.png`.toLowerCase(),
      url: `${location.origin}/matchups?home=${game.home.teamId}&away=${game.away.teamId}` });
  }));
}

async function pollLive(container, board, grid) {
  stopPolling();
  const note = container.querySelector("#tn-live-note");
  const res = await safe(getLiveScoreboard(board.date));
  if (!container.isConnected) return;
  const live = unwrap(res);
  if (!live.data) {
    note.innerHTML = `<p class="live-note"><span class="live-dot is-off"></span>Live scores are off: ${escapeHtml(live.message.split(":")[0])}. Pregame picks and finals still show.</p>`;
    return;
  }
  const inProgress = live.data.games.filter((g) => g.state === "in");
  note.innerHTML = inProgress.length
    ? `<p class="live-note"><span class="live-dot"></span>${inProgress.length} game${inProgress.length === 1 ? "" : "s"} live · updated ${escapeHtml(live.data.fetched_at.slice(11, 16))} ET · refreshes every 30 s</p>`
    : `<p class="live-note"><span class="live-dot is-off"></span>No games in progress right now · checked ${escapeHtml(live.data.fetched_at.slice(11, 16))} ET</p>`;
  live.data.games.forEach((game) => {
    const card = grid.querySelector(`.game-card[data-home="${game.home_id}"][data-away="${game.away_id}"]`);
    if (!card || game.state === "pre") return;
    const status = card.querySelector("[data-status]");
    const box = card.querySelector("[data-live]");
    const p = game.live_home_win_probability;
    if (game.state === "in") {
      card.classList.add("is-live");
      status.innerHTML = `<span class="live-dot"></span>${escapeHtml(game.status_detail || "Live")}`;
    }
    box.hidden = false;
    box.innerHTML = `
      <div class="game-live-score"><span>${escapeHtml(game.away.abbreviation)} <b>${game.away_score ?? "–"}</b></span>
        <span>${game.state === "post" ? "Final" : escapeHtml(game.status_detail || "")}</span>
        <span><b>${game.home_score ?? "–"}</b> ${escapeHtml(game.home.abbreviation)}</span></div>
      ${p != null ? `<p class="game-live-prob">${escapeHtml(game.home.abbreviation)} win chance now <b>${Math.round(p * 100)}%</b>
        <span class="text-muted">(pregame ${Math.round(game.pregame_home_win_probability * 100)}%)</span></p>
        <div class="chart-wrap game-live-chart">${winProbabilityChart(game.history, { home: game.home.abbreviation, away: game.away.abbreviation })}</div>` : ""}`;
    if (p != null) {
      card.querySelector('[data-prob="home"]').innerHTML = `${Math.round(p * 100)}<small>%</small>`;
      card.querySelector('[data-prob="away"]').innerHTML = `${Math.round((1 - p) * 100)}<small>%</small>`;
      card.querySelector("[data-bar]").style.width = `${(1 - p) * 100}%`;
    }
  });
  if (live.data.games.some((g) => g.state !== "post") && document.visibilityState !== "hidden") {
    pollTimer = setTimeout(() => {
      if (container.isConnected && location.pathname === "/tonight") pollLive(container, board, grid);
    }, LIVE_POLL_MS);
  }
}
