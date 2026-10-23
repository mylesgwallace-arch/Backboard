// pages/reportCard.js — the Model Report Card: how the production model's
// pregame picks have done in a season, graded against what happened. Pick
// accuracy, Brier score and log-loss next to simple baselines (always pick
// the home team, Elo alone, a coin flip), calibration (when it said 70%, did
// the favorite win 70%?), accuracy by confidence and by month, the running
// record, and the biggest misses.
//
// Every number comes from the model_report_card tool; the stored holdout
// evidence is one link away (validation_report via the Matchups page).

import { getReportCard, getValidationReport } from "../api.js";
import { calibrationChart, calibrationTooltip, emphasisLineChart, wireChartTooltips, wireEmphasisChart } from "../components/charts.js";
import { uncrumpleOnView } from "../components/motion.js";
import { csvButton, wireCsv } from "../components/csv.js";
import { teamColor } from "../teamColors.js";
import { errorBanner, escapeHtml, fixed, pct, safe, seasonLabel, unwrap } from "../components/format.js";

export const meta = {
  title: "Model Report Card",
  subtitle: "Every pregame pick, graded. How often the favorite won, how honest the percentages were, and the games the model got most wrong.",
};

export function render(container, ctx = {}) {
  const season = Number(ctx.query?.get?.("season")) || null;
  container.innerHTML = `<div class="card fade-in"><div class="skeleton" style="height:320px;"></div></div>`;
  load(container, ctx, season);
}

async function load(container, ctx, season) {
  const [reportRes, holdoutRes] = await Promise.all([safe(getReportCard(season)), safe(getValidationReport("production_model"))]);
  const res = unwrap(reportRes);
  const holdout = unwrap(holdoutRes).data?.report?.metrics?.elo_boosted_ensemble?.accuracy ?? null;
  if (!container.isConnected) return;
  if (!res.data) {
    container.innerHTML = errorBanner(res.message);
    return;
  }
  const card = res.data;
  const o = card.overall;
  const elo = card.baselines.elo_only;
  const beatHome = o.accuracy - card.baselines.always_home;
  const grade = holdout != null ? letterGrade(o.accuracy, holdout) : null;
  const months = card.by_month;
  container.innerHTML = `
    <div class="report-head">
      <div class="field compact-field"><label for="rc-season">Season</label>
        <div class="select-wrap"><select class="select" id="rc-season">
          ${card.seasons_available.slice().reverse().map((s) => `<option value="${s.season}" ${s.season === card.season ? "selected" : ""}>${seasonLabel(s.season)} · ${s.games} games</option>`).join("")}
        </select></div></div>
      <p class="text-muted">Graded through ${escapeHtml(card.latest_graded_game)}.
        ${card.all_out_of_sample ? "Every graded game is after the model's training window." :
          card.games_in_holdout != null ? `${card.games_in_holdout} of ${o.games} games are after the training window (holdout from ${escapeHtml(card.holdout_start)}); earlier ones flatter the model.` : ""}</p>
    </div>

    <section class="card clipping receipt" id="rc-receipt" aria-labelledby="rc-title">
      <span class="tape" style="top:-14px; left:40%; --tr:-3deg;"></span>
      <div class="receipt-grid">
        <div>
          <p class="clipping-meta">${seasonLabel(card.season)} · ${o.games} games graded</p>
          <h2 class="tabloid-headline" id="rc-title">Picked the winner <span class="hl-mark">${Math.round(o.accuracy * 100)}%</span> of the time</h2>
          <p class="deck">${o.correct} right, ${o.games - o.correct} wrong. Always picking the home team would have been right
            <strong>${pct(card.baselines.always_home)}</strong>${beatHome >= 0 ? `, so the model is <strong>${fixed(beatHome * 100)} points</strong> better` : `; the model trails that by <strong>${fixed(-beatHome * 100)} points</strong>`}.
            ${card.current_correct_streak > 1 ? `It has called the last <strong>${card.current_correct_streak}</strong> games right.` : ""}</p>
        </div>
        ${grade ? `<div class="grade-stamp" aria-label="Letter grade ${grade}">
          <span class="grade-letter">${grade}</span>
          <span class="grade-note">vs its ${pct(holdout)} holdout accuracy</span>
        </div>` : ""}
      </div>
    </section>

    <section class="card fade-in" aria-labelledby="rc-vs">
      <div class="card-header"><div><h2 id="rc-vs">Against the baselines</h2>
        <p class="card-subtitle">Accuracy counts picks; the Brier score and log-loss also punish confident misses (lower is better).</p></div></div>
      <div class="table-scroll"><table class="data-table">
        <thead><tr><th>Method</th><th class="num">Accuracy</th><th class="num">Brier</th><th class="num">Log-loss</th></tr></thead>
        <tbody>
          <tr class="is-changed"><td class="team">Production model (Elo + boosted)</td><td class="num">${pct(o.accuracy)}</td><td class="num">${fixed(o.brier, 3)}</td><td class="num">${fixed(o.log_loss, 3)}</td></tr>
          <tr><td>Elo alone</td><td class="num">${pct(elo.accuracy)}</td><td class="num">${fixed(elo.brier, 3)}</td><td class="num">${fixed(elo.log_loss, 3)}</td></tr>
          <tr><td>Always the home team</td><td class="num">${pct(card.baselines.always_home)}</td><td class="num">—</td><td class="num">—</td></tr>
          <tr><td>Coin flip</td><td class="num">50.0%</td><td class="num">${fixed(card.baselines.coin_flip_brier, 3)}</td><td class="num">0.693</td></tr>
        </tbody>
      </table></div>
    </section>

    <div class="grid-2">
      <section class="card fade-in" aria-labelledby="rc-cal">
        <div class="card-header"><div><h2 id="rc-cal">Honest percentages?</h2>
          <p class="card-subtitle">Each dot is a confidence bin: how sure the model was of the favorite (across) and how often the favorite actually won (up). On the line is perfectly calibrated.</p></div></div>
        <div class="chart-wrap" id="rc-cal-chart">${calibrationChart(card.calibration)}</div>
      </section>
      <section class="card fade-in" aria-labelledby="rc-tiers">
        <div class="card-header"><div><h2 id="rc-tiers">By confidence</h2>
          <p class="card-subtitle">A toss-up should win about half the time; a lock almost always.</p></div></div>
        <div class="tier-list">
          ${card.confidence_tiers.map((t) => `
            <div class="tier-row">
              <span class="tier-name">${escapeHtml(t.tier)} <small>${Math.round(t.range[0] * 100)}–${Math.round(t.range[1] * 100)}%</small></span>
              <span class="tier-track"><span class="tier-fill" style="width:${(t.favorite_won ?? 0) * 100}%"></span>
                ${t.average_confidence != null ? `<span class="tier-expected" style="left:${t.average_confidence * 100}%" title="Expected ${Math.round(t.average_confidence * 100)}%"></span>` : ""}</span>
              <span class="tier-value">${t.favorite_won == null ? "—" : `${Math.round(t.favorite_won * 100)}%`}<small>${t.games} g</small></span>
            </div>`).join("")}
          <p class="table-note"><span class="tier-key"></span> the tick is what the model expected</p>
        </div>
      </section>
    </div>

    <section class="card fade-in" aria-labelledby="rc-run">
      <div class="card-header"><div><h2 id="rc-run">The season, pick by pick</h2>
        <p class="card-subtitle">Running accuracy after each day of games. Early swings are small samples.</p></div></div>
      <div class="chart-wrap" id="rc-run-chart">${emphasisLineChart(
        [{ id: "model", label: "Model", points: card.cumulative.map((c) => ({ x: c.date, y: c.accuracy })) }],
        { emphasis: "model", yMin: 0.3, yMax: 0.9, label: "Running pick accuracy through the season" })}</div>
      <div class="month-strip">
        ${months.map((m) => `<div class="month-cell"><span class="month-name">${escapeHtml(monthName(m.month))}</span>
          <b>${Math.round(m.accuracy * 100)}%</b><small>${m.correct}/${m.games}</small></div>`).join("")}
      </div>
    </section>

    <section class="card fade-in" aria-labelledby="rc-miss">
      <div class="card-header"><div><h2 id="rc-miss">Biggest misses</h2>
        <p class="card-subtitle">Games the model got most wrong: the winner's chance before tip-off.</p></div>
        <div class="card-tools">${csvButton("rc-csv")}</div></div>
      <div class="table-scroll"><table class="data-table sticky-first" id="rc-miss-table">
        <thead><tr><th>Date</th><th>Matchup</th><th>Final</th><th class="num">Winner's chance</th></tr></thead>
        <tbody>${card.biggest_misses.map((m) => {
          const winner = m.winner_team_id === m.home.teamId ? m.home : m.away;
          return `<tr data-team-link="${winner.teamId}" tabindex="0">
            <td>${escapeHtml(m.date)}</td>
            <td>${escapeHtml(m.away.abbreviation)} @ ${escapeHtml(m.home.abbreviation)}</td>
            <td><span class="team-chip" style="--team-color:${teamColor(winner.teamId).primary}; --team-ink:#fff;">${escapeHtml(winner.abbreviation)}</span> ${m.away_score ?? "?"}–${m.home_score ?? "?"}</td>
            <td class="num"><b>${pct(m.winner_pregame_probability)}</b></td></tr>`;
        }).join("")}</tbody>
      </table></div>
    </section>`;

  container.querySelector("#rc-season").addEventListener("change", (event) =>
    ctx.navigate("/report-card", { season: event.target.value }));
  wireChartTooltips(container.querySelector("#rc-cal-chart"), calibrationTooltip(card.calibration));
  wireEmphasisChart(container.querySelector("#rc-run-chart"),
    [{ id: "model", label: "Model", points: card.cumulative.map((c) => ({ x: c.date, y: c.accuracy })) }], { emphasis: "model" });
  wireCsv(container.querySelector("#rc-csv"), container.querySelector("#rc-miss-table"), `backboard-misses-${card.season}`);
  container.querySelectorAll("tr[data-team-link]").forEach((row) => {
    const open = () => ctx.navigate("/teams", { team: row.dataset.teamLink });
    row.addEventListener("click", open);
    row.addEventListener("keydown", (event) => event.key === "Enter" && open());
  });
  uncrumpleOnView(container.querySelector("#rc-receipt"), { seed: card.season * 7 });
}

function letterGrade(accuracy, holdout) {
  // Against the stored holdout accuracy: within a point of it is a B.
  const delta = accuracy - holdout;
  if (delta >= 0.03) return "A";
  if (delta >= -0.01) return "B";
  if (delta >= -0.04) return "C";
  if (delta >= -0.08) return "D";
  return "F";
}

function monthName(yyyyMm) {
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const [, m] = yyyyMm.split("-").map(Number);
  return names[m - 1] || yyyyMm;
}
