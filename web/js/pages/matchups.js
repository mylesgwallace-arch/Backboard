// pages/matchups.js — the Matchup Predictor: the primary visual centerpiece
// of the application. Consumes the existing POST /tools/predict_matchup
// endpoint (src/tools.py -> src/main.py predict_matchup, frozen production
// elo_boosted_ensemble model). No prediction logic is duplicated here — this
// module only renders whatever the backend envelope returns.

import { predictMatchup } from "../api.js";
import { fetchTeams, createTeamSelect } from "../components/teamSelect.js";
import { renderProbabilityBar } from "../components/probabilityBar.js";
import { renderComparisonRow } from "../components/comparisonBar.js";
import { teamColor } from "../teamColors.js";

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
    resultMount.innerHTML = renderResult({ prediction, homeTeam, awayTeam });

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

function renderResult({ prediction, homeTeam, awayTeam }) {
  const homeColor = teamColor(prediction.home_team_id).primary;
  const awayColor = teamColor(prediction.away_team_id).primary;
  const homeIsFavorite =
    prediction.home_win_probability >= prediction.away_win_probability;
  const snapshotLabel = describeSnapshotDate(prediction.feature_snapshot_date);

  return `
    <div class="card fade-in">
      <div class="card-header">
        <div>
          <h2>Prediction</h2>
          <p class="card-subtitle">
            ${escapeHtml(prediction.model)}${snapshotLabel ? " · " + escapeHtml(snapshotLabel) : ""}
          </p>
        </div>
        ${
          prediction.game_date
            ? `<span class="chip">As of <strong>${escapeHtml(prediction.game_date)}</strong></span>`
            : ""
        }
      </div>

      <div class="result-scoreboard">
        <div class="result-team ${homeIsFavorite ? "is-favorite" : ""}" style="--team-color:${homeColor};">
          <div class="team-side-label">Home</div>
          <div class="team-name">${escapeHtml(homeTeam?.full_name || `Team ${prediction.home_team_id}`)}</div>
          <div class="team-abbrev">${escapeHtml(homeTeam?.abbreviation || "")}</div>
          <div class="win-prob">${formatPct(prediction.home_win_probability)}</div>
          <div class="win-prob-label">Win probability</div>
          ${homeIsFavorite ? `<div class="favorite-badge"><span class="badge positive">Predicted winner</span></div>` : ""}
          <button class="link-btn mt-1" data-view-team="${prediction.home_team_id}">View team ▸</button>
        </div>
        <div class="vs-divider"><span>VS</span></div>
        <div class="result-team ${!homeIsFavorite ? "is-favorite" : ""}" style="--team-color:${awayColor};">
          <div class="team-side-label">Away</div>
          <div class="team-name">${escapeHtml(awayTeam?.full_name || `Team ${prediction.away_team_id}`)}</div>
          <div class="team-abbrev">${escapeHtml(awayTeam?.abbreviation || "")}</div>
          <div class="win-prob">${formatPct(prediction.away_win_probability)}</div>
          <div class="win-prob-label">Win probability</div>
          ${!homeIsFavorite ? `<div class="favorite-badge"><span class="badge positive">Predicted winner</span></div>` : ""}
          <button class="link-btn mt-1" data-view-team="${prediction.away_team_id}">View team ▸</button>
        </div>
      </div>

      ${renderProbabilityBar({
        homePct: prediction.home_win_probability,
        awayPct: prediction.away_win_probability,
        homeColor,
        awayColor,
      })}
      <div class="prob-bar-labels">
        <span>${escapeHtml(homeTeam?.abbreviation || "HOME")}</span>
        <span>${escapeHtml(awayTeam?.abbreviation || "AWAY")}</span>
      </div>

      ${
        prediction.matchup_summary
          ? `<div class="info-banner mt-2"><span>💡</span><p class="model-insight">${escapeHtml(prediction.matchup_summary)}</p></div>`
          : ""
      }
    </div>

    ${renderTeamStrengthCard({ prediction, homeTeam, awayTeam, homeColor, awayColor })}

    ${renderModelInfoCard(prediction)}
  `;
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
      <div class="compare-values" style="margin-bottom:0.6rem;">
        <span>${escapeHtml(homeTeam?.abbreviation || "HOME")}</span>
        <span>${escapeHtml(awayTeam?.abbreviation || "AWAY")}</span>
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

function formatPct(value) {
  return `${(value * 100).toFixed(1)}%`;
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
