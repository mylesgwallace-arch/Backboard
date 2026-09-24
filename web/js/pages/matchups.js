// pages/matchups.js — the Matchup Predictor: the primary visual centerpiece
// of the application. Consumes the existing POST /tools/predict_matchup
// endpoint (src/tools.py -> src/main.py predict_matchup, frozen production
// elo_boosted_ensemble model). No prediction logic is duplicated here — this
// module only renders whatever the backend envelope returns.

import { predictMatchup, predictMargin } from "../api.js";
import { fetchTeams, createTeamSelect } from "../components/teamSelect.js";
import { renderProbabilityBar } from "../components/probabilityBar.js";
import { renderComparisonRow } from "../components/comparisonBar.js";
import { teamColor } from "../teamColors.js";
import { clashingPair, teamSlabStyle } from "../colorInk.js";

// Tabloid callout thresholds (see web/DESIGN-BRIEF.md). Every callout is
// derived from the predict_matchup envelope already on this page.
const HIGH_CONFIDENCE_MIN = 0.7; // favorite win probability → HIGH-CONFIDENCE PICK
const TOSS_UP_BELOW = 0.55; // favorite win probability → TOSS-UP
const UPSET_UNDERDOG_MIN = 0.35; // underdog win probability, AND the underdog has
//                                  the better last-10 win rate → UPSET ALERT
const SWING_MIN = 0.1; // home win-probability move vs. this session's previous
//                        run of the same home/away pairing → BIG SWING

// Previous home win probability per "home-away" pairing (this page session).
const previousHomeProbability = new Map();

export const meta = {
  title: "Matchups",
  subtitle:
    "Predict the winner of any NBA matchup using the frozen production model.",
};

export function render(container, ctx = {}) {
  container.innerHTML = `
    <div class="card fade-in">
      <div class="card-header">
        <div>
          <h2>Predict a matchup</h2>
          <p class="card-subtitle">
            Runs the validated production model (<code>elo_boosted_ensemble</code>)
            through the existing prediction API — nothing is estimated in the browser.
          </p>
        </div>
      </div>
      <div id="matchup-form-mount"></div>
      <div id="matchup-error-mount" class="mt-1"></div>
      <div class="predict-actions">
        <button class="btn" id="predict-btn" disabled>Predict Matchup</button>
        <span id="predict-status" class="text-muted"></span>
      </div>
    </div>
    <div id="matchup-result-mount"></div>
  `;

  const formMount = container.querySelector("#matchup-form-mount");
  const errorMount = container.querySelector("#matchup-error-mount");
  const resultMount = container.querySelector("#matchup-result-mount");
  const predictBtn = container.querySelector("#predict-btn");
  const statusEl = container.querySelector("#predict-status");

  const home = createTeamSelect({
    id: "home-team-select",
    labelText: "Home team",
    placeholder: "Select home team…",
  });
  const away = createTeamSelect({
    id: "away-team-select",
    labelText: "Away team",
    placeholder: "Select away team…",
  });

  const formGrid = document.createElement("div");
  formGrid.className = "matchup-form-grid";

  const swapBtn = document.createElement("button");
  swapBtn.type = "button";
  swapBtn.className = "swap-btn";
  swapBtn.title = "Swap home and away";
  swapBtn.setAttribute("aria-label", "Swap home and away teams");
  swapBtn.textContent = "⇄";

  formGrid.appendChild(home.root);
  formGrid.appendChild(swapBtn);
  formGrid.appendChild(away.root);
  formMount.appendChild(formGrid);

  const advancedToggle = document.createElement("button");
  advancedToggle.type = "button";
  advancedToggle.className = "link-btn advanced-toggle";
  advancedToggle.textContent = "Advanced options ▾";

  const advancedPanel = document.createElement("div");
  advancedPanel.className = "advanced-panel";
  advancedPanel.innerHTML = `
    <div class="field">
      <label for="game-date-input">As-of date (optional)</label>
      <input type="date" class="input" id="game-date-input">
    </div>
    <div class="field">
      <label>&nbsp;</label>
      <p class="text-muted" style="font-size:0.78rem; margin:0;">
        Restricts the prediction to pregame information available on or before
        this date. Leave blank to use each team's most recent data.
      </p>
    </div>
  `;

  advancedToggle.addEventListener("click", () => {
    advancedPanel.classList.toggle("open");
    advancedToggle.textContent = advancedPanel.classList.contains("open")
      ? "Advanced options ▴"
      : "Advanced options ▾";
  });

  formMount.appendChild(advancedToggle);
  formMount.appendChild(advancedPanel);

  let teams = [];

  function refreshPredictAvailability() {
    const homeVal = home.selectEl.value;
    const awayVal = away.selectEl.value;
    predictBtn.disabled = !(homeVal && awayVal && homeVal !== awayVal);
  }

  home.selectEl.addEventListener("change", refreshPredictAvailability);
  away.selectEl.addEventListener("change", refreshPredictAvailability);

  swapBtn.addEventListener("click", () => {
    const tmp = home.selectEl.value;
    home.selectEl.value = away.selectEl.value;
    away.selectEl.value = tmp;
    refreshPredictAvailability();
  });

  statusEl.textContent = "Loading teams…";
  fetchTeams()
    .then((fetchedTeams) => {
      teams = fetchedTeams;
      home.setTeams(teams);
      away.setTeams(teams);
      statusEl.textContent = "";
      // Support deep-linking from other pages (e.g. Team Explorer's
      // "Predict a matchup" button) via ?home=<teamId>&away=<teamId>.
      const presetHome = ctx.query?.get ? ctx.query.get("home") : null;
      const presetAway = ctx.query?.get ? ctx.query.get("away") : null;
      if (presetHome && teams.some((t) => String(t.team_id) === presetHome)) {
        home.selectEl.value = presetHome;
      }
      if (presetAway && teams.some((t) => String(t.team_id) === presetAway)) {
        away.selectEl.value = presetAway;
      }
      refreshPredictAvailability();
    })
    .catch((err) => {
      statusEl.textContent = "";
      errorMount.innerHTML = errorBanner(
        `Could not load the team list: ${err.message}`
      );
    });

  predictBtn.addEventListener("click", async () => {
    errorMount.innerHTML = "";
    const homeTeamId = Number(home.selectEl.value);
    const awayTeamId = Number(away.selectEl.value);
    const gameDate =
      advancedPanel.querySelector("#game-date-input").value || null;

    predictBtn.disabled = true;
    const originalLabel = predictBtn.textContent;
    predictBtn.innerHTML = '<span class="spinner"></span> Predicting…';
    statusEl.textContent = "";
    resultMount.innerHTML = renderSkeleton();

    let res;
    try {
      res = await predictMatchup({ homeTeamId, awayTeamId, gameDate });
    } catch (err) {
      resultMount.innerHTML = "";
      errorMount.innerHTML = errorBanner(
        `Network error while contacting the API: ${err.message}`
      );
      predictBtn.disabled = false;
      predictBtn.textContent = originalLabel;
      refreshPredictAvailability();
      return;
    }

    predictBtn.textContent = originalLabel;
    refreshPredictAvailability();

    const envelope = res.data;
    if (!res.ok || !envelope || envelope.status !== "success") {
      resultMount.innerHTML = "";
      const message =
        envelope?.error?.message ||
        "The prediction request failed. Please try again.";
      errorMount.innerHTML = errorBanner(message);
      return;
    }

    const prediction = envelope.data.prediction;
    const homeTeam = teams.find((t) => t.team_id === homeTeamId);
    const awayTeam = teams.find((t) => t.team_id === awayTeamId);
    const swing = trackSwing(prediction);
    resultMount.innerHTML = renderResult({ prediction, homeTeam, awayTeam, swing });
    loadMarginCard(resultMount.querySelector("#margin-card-mount"), {
      homeTeamId, awayTeamId, gameDate, homeTeam, awayTeam,
    });

    if (ctx.navigate) {
      resultMount.querySelectorAll("[data-view-team]").forEach((el) => {
        el.addEventListener("click", () => {
          ctx.navigate("/teams", { team: el.dataset.viewTeam });
        });
      });
    }
  });
}

// ---------------------------------------------------------------------------
// Rendering helpers
// ---------------------------------------------------------------------------

function renderSkeleton() {
  return `
    <div class="card fade-in">
      <div class="skeleton" style="height:150px;"></div>
      <div class="skeleton mt-2" style="height:10px;"></div>
      <div class="skeleton mt-2" style="height:14px; width:40%;"></div>
    </div>
  `;
}

function errorBanner(message) {
  return `<div class="error-banner">⚠ ${escapeHtml(message)}</div>`;
}

function renderResult({ prediction, homeTeam, awayTeam, swing }) {
  // "Clash, don't blend": identical primaries fall back to the away team's
  // own secondary color from teamColors.js.
  const { first: homeColor, second: awayColor } = clashingPair(
    teamColor(prediction.home_team_id),
    teamColor(prediction.away_team_id)
  );
  const homeIsFavorite =
    prediction.home_win_probability >= prediction.away_win_probability;
  const snapshotLabel = describeSnapshotDate(prediction.feature_snapshot_date);
  const story = deriveStory(prediction, homeIsFavorite);

  const home = {
    team: homeTeam,
    id: prediction.home_team_id,
    color: homeColor,
    probability: prediction.home_win_probability,
    side: "Home",
  };
  const away = {
    team: awayTeam,
    id: prediction.away_team_id,
    color: awayColor,
    probability: prediction.away_win_probability,
    side: "Away",
  };
  const favorite = homeIsFavorite ? home : away;
  const underdog = homeIsFavorite ? away : home;

  return `
    <article class="card clipping matchup-clipping fade-in" aria-labelledby="matchup-headline">
      <div class="clipping-masthead">
        <span class="kicker">The pick</span>
        <span class="clipping-meta">
          ${escapeHtml(prediction.model)}${snapshotLabel ? " · " + escapeHtml(snapshotLabel) : ""}
        </span>
        ${
          prediction.game_date
            ? `<span class="chip">As of <strong>${escapeHtml(prediction.game_date)}</strong></span>`
            : ""
        }
      </div>

      <div class="matchup-headline-row">
        ${renderHeadline(story, favorite, underdog)}
        ${renderStamp(story)}
      </div>

      ${story.kind === "upset" ? renderUpsetCallout(story, favorite, underdog) : ""}
      ${swing ? renderSwingCallout(swing) : ""}

      <div class="result-scoreboard">
        ${renderTeamHalf(home, homeIsFavorite)}
        <div class="vs-divider" aria-hidden="true"><span>VS</span></div>
        ${renderTeamHalf(away, !homeIsFavorite)}
      </div>

      ${renderProbabilityBar({
        homePct: prediction.home_win_probability,
        awayPct: prediction.away_win_probability,
        homeColor,
        awayColor,
      })}
      <div class="prob-bar-labels">
        <span>${escapeHtml(homeTeam?.abbreviation || "HOME")} · ${formatPct(prediction.home_win_probability)}</span>
        <span>${formatPct(prediction.away_win_probability)} · ${escapeHtml(awayTeam?.abbreviation || "AWAY")}</span>
      </div>

      ${
        prediction.matchup_summary
          ? `<div class="info-banner scouting-note mt-2"><span class="note-label">Scouting report</span><p class="model-insight">${escapeHtml(prediction.matchup_summary)}</p></div>`
          : ""
      }
    </article>

    <div id="margin-card-mount"></div>

    ${renderTeamStrengthCard({ prediction, homeTeam, awayTeam, homeColor, awayColor })}

    ${renderModelInfoCard(prediction)}
  `;
}

/**
 * Predicted score card from the separate margin/total model (predict_margin).
 * Loaded after the win probability so a slow or missing margin model never
 * blocks the pick itself.
 */
async function loadMarginCard(mount, { homeTeamId, awayTeamId, gameDate, homeTeam, awayTeam }) {
  if (!mount) return;
  mount.innerHTML = `<div class="card fade-in"><div class="skeleton" style="height:90px;"></div></div>`;
  let res;
  try {
    res = await predictMargin({ homeTeamId, awayTeamId, gameDate });
  } catch (err) {
    mount.innerHTML = "";
    return;
  }
  if (!res.ok || res.data?.status !== "success") {
    mount.innerHTML = "";
    return;
  }
  const m = res.data.data;
  const homeLabel = homeTeam?.abbreviation || "HOME";
  const awayLabel = awayTeam?.abbreviation || "AWAY";
  const marginFavorsHome = m.predicted_home_margin >= 0;
  const probabilityFavorsHome = m.production_home_win_probability >= 0.5;
  const disagreement = marginFavorsHome !== probabilityFavorsHome
    ? `<p class="text-muted mt-1">The margin model and the win-probability model disagree on the favorite here (they do in about 7% of games); the win probability above is the validated production output.</p>`
    : "";
  mount.innerHTML = `
    <div class="card fade-in">
      <div class="card-header">
        <div>
          <h2>Predicted score</h2>
          <p class="card-subtitle">From a separate margin/total model validated on 13,332 holdout games (margin MAE 10.6 points). Ranges are 80% intervals.</p>
        </div>
      </div>
      <div class="stat-grid">
        <div class="stat-tile">
          <div class="stat-label">${escapeHtml(homeLabel)} – ${escapeHtml(awayLabel)}</div>
          <div class="stat-value">${formatNumber(m.predicted_home_points)} – ${formatNumber(m.predicted_away_points)}</div>
        </div>
        <div class="stat-tile">
          <div class="stat-label">Home margin (80% range)</div>
          <div class="stat-value">${formatSigned(m.predicted_home_margin)}</div>
          <div class="text-muted" style="font-size:0.8rem;">${formatSigned(m.margin_interval_80[0])} to ${formatSigned(m.margin_interval_80[1])}</div>
        </div>
        <div class="stat-tile">
          <div class="stat-label">Total points (80% range)</div>
          <div class="stat-value">${formatNumber(m.predicted_total_points)}</div>
          <div class="text-muted" style="font-size:0.8rem;">${formatNumber(m.total_interval_80[0])} to ${formatNumber(m.total_interval_80[1])}</div>
        </div>
      </div>
      <p class="text-muted mt-1">The total is no better than averaging both teams' recent scoring, and single-game margins are mostly noise: treat these as rough expectations.</p>
      ${disagreement}
    </div>`;
}

function renderTeamHalf(side, isFavorite) {
  const name = side.team?.full_name || `Team ${side.id}`;
  return `
    <div class="result-team ${isFavorite ? "is-favorite" : ""}" style="${teamSlabStyle(side.color)}">
      <div class="team-side-label">${side.side}</div>
      <div class="team-name">${escapeHtml(name)}</div>
      <div class="team-abbrev">${escapeHtml(side.team?.abbreviation || "")}</div>
      <div class="win-stamp">
        <div class="win-prob">${formatPct(side.probability)}</div>
        <div class="win-prob-label">Win probability</div>
      </div>
      ${isFavorite ? `<div class="favorite-badge"><span class="badge positive">Predicted winner</span></div>` : ""}
      <button class="link-btn mt-1" data-view-team="${side.id}">View team ▸</button>
    </div>
  `;
}

/**
 * Classify the prediction into one tabloid "story" using only fields in the
 * envelope: the two win probabilities and team_context's last-10 win rates.
 */
function deriveStory(prediction, homeIsFavorite) {
  const favoriteP = Math.max(prediction.home_win_probability, prediction.away_win_probability);
  const underdogP = Math.min(prediction.home_win_probability, prediction.away_win_probability);
  const context = prediction.team_context || {};
  const favoriteForm = (homeIsFavorite ? context.home : context.away)?.win_rate_rolling_10;
  const underdogForm = (homeIsFavorite ? context.away : context.home)?.win_rate_rolling_10;
  const underdogIsHotter =
    typeof favoriteForm === "number" &&
    typeof underdogForm === "number" &&
    underdogForm > favoriteForm;

  let kind = "pick";
  if (underdogP >= UPSET_UNDERDOG_MIN && underdogIsHotter) kind = "upset";
  else if (favoriteP >= HIGH_CONFIDENCE_MIN) kind = "lock";
  else if (favoriteP < TOSS_UP_BELOW) kind = "tossup";

  return { kind, favoriteP, underdogP, favoriteForm, underdogForm };
}

function nickname(side) {
  return side.team?.name || side.team?.abbreviation || side.side;
}

function renderHeadline(story, favorite, underdog) {
  // Team names in Anton on their own color slabs; the verb scrawled in marker.
  const verb = story.kind === "tossup" ? "edge" : "over";
  const doubt = story.kind === "upset" ? "?" : "";
  return `
    <h2 class="tabloid-headline" id="matchup-headline">
      <span class="hl-team" style="${teamSlabStyle(favorite.color)}">${escapeHtml(nickname(favorite))}</span>
      <span class="scrawl">${verb}</span>
      <span class="hl-team" style="${teamSlabStyle(underdog.color)}">${escapeHtml(nickname(underdog))}</span>${doubt}
    </h2>
  `;
}

function renderStamp(story) {
  if (story.kind === "lock") {
    return `<p class="stamp stamp--tilt matchup-stamp">High-confidence pick</p>`;
  }
  if (story.kind === "tossup") {
    return `<p class="stamp stamp--tilt stamp--ink matchup-stamp">Toss-up</p>`;
  }
  return "";
}

function renderUpsetCallout(story, favorite, underdog) {
  const underdogName = underdog.team?.full_name || nickname(underdog);
  const favoriteName = favorite.team?.full_name || nickname(favorite);
  return `
    <div class="callout callout--upset" role="note">
      <span class="callout-title">Upset alert!</span>
      <p class="callout-body">
        The underdog ${escapeHtml(underdogName)} still carry a
        <strong>${formatPct(story.underdogP)}</strong> win chance and the better recent form:
        <strong>${formatPct(story.underdogForm, 0)}</strong> of their last 10 won, vs
        <strong>${formatPct(story.favoriteForm, 0)}</strong> for the ${escapeHtml(favoriteName)}.
      </p>
    </div>
  `;
}

function renderSwingCallout(swing) {
  const points = (swing.delta * 100).toFixed(1);
  return `
    <div class="callout callout--swing" role="note">
      <span class="callout-title">Big swing</span>
      <p class="callout-body">
        Home win probability moved <strong>${swing.delta > 0 ? "+" : ""}${points} pts</strong>
        since your last run of this matchup
        (<strong>${formatPct(swing.previous)}</strong> → <strong>${formatPct(swing.current)}</strong>).
      </p>
    </div>
  `;
}

/** Compare against this session's previous run of the same pairing. */
function trackSwing(prediction) {
  const key = `${prediction.home_team_id}-${prediction.away_team_id}`;
  const current = prediction.home_win_probability;
  const previous = previousHomeProbability.get(key);
  previousHomeProbability.set(key, current);
  if (typeof previous !== "number") return null;
  const delta = current - previous;
  return Math.abs(delta) >= SWING_MIN ? { previous, current, delta } : null;
}

function renderTeamStrengthCard({ prediction, homeTeam, awayTeam, homeColor, awayColor }) {
  const ctx = prediction.team_context;
  if (!ctx || !ctx.home || !ctx.away) return "";
  const home = ctx.home;
  const away = ctx.away;

  const rows = [
    renderComparisonRow({
      label: "Recent win rate (last 10 games)",
      homeValue: home.win_rate_rolling_10,
      awayValue: away.win_rate_rolling_10,
      homeColor,
      awayColor,
      formatValue: formatPct,
    }),
    renderComparisonRow({
      label: "Point differential (last 10 games)",
      homeValue: home.plusMinusPoints_rolling_10,
      awayValue: away.plusMinusPoints_rolling_10,
      homeColor,
      awayColor,
      formatValue: (v) => formatSigned(v),
    }),
    renderComparisonRow({
      label: "Points scored / game (last 10)",
      homeValue: home.teamScore_rolling_10,
      awayValue: away.teamScore_rolling_10,
      homeColor,
      awayColor,
      formatValue: (v) => formatNumber(v),
    }),
    renderComparisonRow({
      label: "Points allowed / game (last 10)",
      homeValue: home.opponentScore_rolling_10,
      awayValue: away.opponentScore_rolling_10,
      homeColor,
      awayColor,
      formatValue: (v) => formatNumber(v),
    }),
    renderComparisonRow({
      label: "Rest days entering the game",
      homeValue: home.rest_days,
      awayValue: away.rest_days,
      homeColor,
      awayColor,
      formatValue: (v) => formatNumber(v),
    }),
    renderComparisonRow({
      label: "Active players (last game)",
      homeValue: home.active_players_last_game,
      awayValue: away.active_players_last_game,
      homeColor,
      awayColor,
      formatValue: (v) => formatNumber(v, 0),
    }),
  ].join("");

  return `
    <div class="card fade-in">
      <div class="card-header">
        <div>
          <h2>Team strength comparison</h2>
          <p class="card-subtitle">Rolling pregame form used by the model (last 10 games before this matchup).</p>
        </div>
      </div>
      <div class="compare-legend">
        <span class="team-chip" style="${teamSlabStyle(homeColor)}">${escapeHtml(homeTeam?.abbreviation || "HOME")}</span>
        <span class="compare-legend-label">Home · Away</span>
        <span class="team-chip" style="${teamSlabStyle(awayColor)}">${escapeHtml(awayTeam?.abbreviation || "AWAY")}</span>
      </div>
      ${rows}
      ${prediction.feature_importance ? renderFeatureChips(prediction.feature_importance) : ""}
    </div>
  `;
}

function renderFeatureChips(features) {
  const chips = features
    .map(
      (f) =>
        `<span class="chip">${escapeHtml(f.feature)} <strong>${formatPct(f.importance)}</strong></span>`
    )
    .join("");
  return `
    <div class="section-title mt-2">Top model drivers</div>
    <div class="feature-chip-row">${chips}</div>
  `;
}

function renderModelInfoCard(prediction) {
  const summary = prediction.model_summary;
  if (!summary) return "";
  const metrics = summary.metrics || {};
  const calibration = summary.calibration || {};

  return `
    <div class="card fade-in">
      <details class="collapsible">
        <summary>About this model &amp; confidence</summary>
        <div class="stat-grid">
          <div class="stat-tile">
            <div class="stat-label">Holdout accuracy</div>
            <div class="stat-value">${metrics.accuracy != null ? formatPct(metrics.accuracy) : "—"}</div>
          </div>
          <div class="stat-tile">
            <div class="stat-label">Log loss</div>
            <div class="stat-value">${metrics.log_loss != null ? metrics.log_loss.toFixed(3) : "—"}</div>
          </div>
          <div class="stat-tile">
            <div class="stat-label">Brier score</div>
            <div class="stat-value">${metrics.brier_score != null ? metrics.brier_score.toFixed(3) : "—"}</div>
          </div>
          <div class="stat-tile">
            <div class="stat-label">Calibration error</div>
            <div class="stat-value">${calibration.expected_calibration_error != null ? calibration.expected_calibration_error.toFixed(3) : "—"}</div>
          </div>
        </div>
        <p class="text-muted mt-2">
          Recommended model: <strong>${escapeHtml(summary.recommended_model)}</strong>,
          selected by ${escapeHtml(summary.recommendation_metric || "log_loss")} on a
          chronological holdout. These metrics describe historical validation
          performance, not a guarantee for this specific matchup.
        </p>
      </details>
    </div>
  `;
}

function describeSnapshotDate(snapshot) {
  if (!snapshot) return "";
  if (typeof snapshot === "string") return `Data as of ${snapshot}`;
  if (snapshot.home && snapshot.away) {
    return snapshot.home === snapshot.away
      ? `Data as of ${snapshot.home}`
      : `Home data as of ${snapshot.home} · away data as of ${snapshot.away}`;
  }
  return "";
}

function formatPct(value, digits = 1) {
  return `${(value * 100).toFixed(digits)}%`;
}

function formatSigned(value, digits = 1) {
  const rounded = value.toFixed(digits);
  return value > 0 ? `+${rounded}` : rounded;
}

function formatNumber(value, digits = 1) {
  return value.toFixed(digits);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
