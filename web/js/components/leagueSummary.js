// components/leagueSummary.js — renders the `league_summary` block from a
// `simulate_season` projection (league mean wins, strongest/weakest
// projected team, conference mean wins) as a stat-tile card. Shared between
// the Season Simulator and League Predictions pages so this card isn't
// duplicated across both.

export function renderLeagueSummaryCard(summary, teamById, season, nSimulations) {
  const bestTeam = teamById.get(summary.best_team.teamId);
  const worstTeam = teamById.get(summary.worst_team.teamId);
  return `
    <div class="card fade-in">
      <div class="section-title">League summary — ${season}-${String(season + 1).slice(-2)} season (${nSimulations.toLocaleString()} simulations)</div>
      <div class="stat-grid">
        <div class="stat-tile">
          <div class="stat-label">League mean wins</div>
          <div class="stat-value">${summary.league_mean_wins.toFixed(1)}</div>
        </div>
        <div class="stat-tile">
          <div class="stat-label">Strongest projected team</div>
          <div class="stat-value" style="font-size:1.05rem;">${escapeHtml(bestTeam?.full_name || summary.best_team.teamId)}</div>
          <div class="text-muted" style="font-size:0.76rem;">${summary.best_team.mean_wins.toFixed(1)} mean wins</div>
        </div>
        <div class="stat-tile">
          <div class="stat-label">Weakest projected team</div>
          <div class="stat-value" style="font-size:1.05rem;">${escapeHtml(worstTeam?.full_name || summary.worst_team.teamId)}</div>
          <div class="text-muted" style="font-size:0.76rem;">${summary.worst_team.mean_wins.toFixed(1)} mean wins</div>
        </div>
        <div class="stat-tile">
          <div class="stat-label">Conference mean wins</div>
          <div class="stat-value" style="font-size:1rem;">
            E ${summary.conference_mean_wins.East?.toFixed(1) ?? "—"} · W ${summary.conference_mean_wins.West?.toFixed(1) ?? "—"}
          </div>
        </div>
      </div>
    </div>
  `;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
