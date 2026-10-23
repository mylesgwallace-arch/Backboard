// pages/gallery.js — Scenario Gallery: ready-made Sandbox scenarios and the
// ones saved in this browser, each one click from the Sandbox.
//
// "From this season's rosters" scenarios are built on the spot from the
// sandbox_rosters tool (the same rosters and box-impact values the Sandbox
// uses), so they always name real players on the current rosters: the two
// best players swapping places, the best player joining the weakest roster,
// the best player missing half a season. The hand-picked classics show only
// when their players are on the rosters. Nothing is simulated here; the
// Sandbox runs the season when you open a scenario.

import { getSandboxRosters } from "../api.js";
import { teamColor } from "../teamColors.js";
import { FEATURED_SCENARIOS, encodeScenario } from "../components/scenario.js";
import { deleteScenario, savedScenarios } from "../components/prefs.js";
import { copyText } from "../components/toast.js";
import { emptyState, errorBanner, escapeHtml, safe, seasonLabel, unwrap } from "../components/format.js";

export const meta = {
  title: "Scenario Gallery",
  subtitle: "Ready-made what-ifs for the Sandbox, and the scenarios you saved. Open one, change anything, play the season out.",
};

function teamsOf(moves) {
  return [...new Set(moves.flatMap((m) => [m.to_team_id, m.team_id, ...(m.assets || []).map((a) => a.to_team_id)]).filter(Boolean))];
}

function value(player) {
  return (player.box_impact ?? 0) * (player.minutes_share ?? 0);
}

/** Scenarios built from the current rosters. */
function rosterScenarios(rosters) {
  const teams = rosters.teams.map((team) => ({
    ...team,
    strength: team.players.reduce((sum, p) => sum + value(p), 0),
    best: [...team.players].sort((a, b) => value(b) - value(a))[0],
  })).filter((team) => team.best);
  if (teams.length < 2) return [];
  const byStrength = [...teams].sort((a, b) => b.strength - a.strength);
  const strongest = byStrength[0];
  const weakest = byStrength[byStrength.length - 1];
  const stars = teams.map((t) => ({ team: t, player: t.best })).sort((a, b) => value(b.player) - value(a.player));
  const [first, second] = stars;
  const list = [
    {
      label: `${first.player.name} ↔ ${second.player.name}`,
      blurb: `The two most valuable players on the rosters (by the box-impact model) trade places: ${first.team.abbreviation} and ${second.team.abbreviation} swap stars.`,
      tag: "Star swap",
      teams: [first.team.team_id, second.team.team_id],
      moves: [{ type: "trade", assets: [
        { person_id: first.player.person_id, from_team_id: first.team.team_id, to_team_id: second.team.team_id, name: first.player.name },
        { person_id: second.player.person_id, from_team_id: second.team.team_id, to_team_id: first.team.team_id, name: second.player.name },
      ] }],
    },
    {
      label: `${first.player.name} to the ${weakest.team_name.split(" ").pop()}`,
      blurb: `The league's most valuable player joins the weakest roster (${weakest.team_name}). How far does one star lift a team?`,
      tag: "Rescue mission",
      teams: [first.team.team_id, weakest.team_id],
      moves: [{ type: "trade", assets: [
        { person_id: first.player.person_id, from_team_id: first.team.team_id, to_team_id: weakest.team_id, name: first.player.name },
      ] }],
    },
    {
      label: `${strongest.best.name} misses half the season`,
      blurb: `The best player on the strongest roster (${strongest.team_name}) is out for 41 games. Do they still finish on top?`,
      tag: "Injury",
      teams: [strongest.team_id],
      moves: [{ type: "injury", person_id: strongest.best.person_id, team_id: strongest.team_id, games_missed: 41, name: strongest.best.name }],
    },
  ];
  if (first.team.team_id === weakest.team_id) list.splice(1, 1);
  return list;
}

function scenarioCard({ label, blurb, tag, teams = [] }, scenario, { id, saved } = {}) {
  const { moves } = scenario;
  const link = `/sandbox?s=${encodeScenario(scenario)}`;
  return `
    <article class="scenario-card lift ${saved ? "is-saved" : ""}" ${id ? `data-id="${id}"` : ""}>
      <div class="scenario-stripe" aria-hidden="true">${(teams.length ? teams : [0]).map((t) => `<span style="background:${t ? teamColor(t).primary : "var(--red)"}"></span>`).join("")}</div>
      ${tag ? `<p class="scenario-tag">${escapeHtml(tag)}</p>` : ""}
      <h3 class="scenario-title">${escapeHtml(label)}</h3>
      ${blurb ? `<p class="scenario-blurb">${escapeHtml(blurb)}</p>` : ""}
      <p class="scenario-moves">${moves.length} move${moves.length === 1 ? "" : "s"}</p>
      <div class="scenario-actions">
        <a class="btn" href="${link}">Open ▸</a>
        <button type="button" class="link-btn" data-copy="${escapeHtml(link)}">Copy link</button>
        ${saved ? `<button type="button" class="link-btn" data-delete="${id}">Delete</button>` : ""}
      </div>
    </article>`;
}

export function render(container) {
  container.innerHTML = `
    <section class="card fade-in" aria-labelledby="gal-yours">
      <div class="card-header"><div><h2 id="gal-yours">Saved by you</h2>
        <p class="card-subtitle">Scenarios saved from the Sandbox's ledger ("Save to gallery"). They live in this browser only; copy a link to share one.</p></div></div>
      <div class="scenario-grid" id="gal-saved"></div>
    </section>
    <section class="card fade-in" aria-labelledby="gal-now">
      <div class="card-header"><div><h2 id="gal-now">From the current rosters</h2>
        <p class="card-subtitle" id="gal-now-note">Built from the current rosters and the box-impact model.</p></div></div>
      <div class="scenario-grid" id="gal-now-grid"><div class="skeleton" style="height:220px;"></div></div>
    </section>
    <section class="card fade-in" aria-labelledby="gal-classic" id="gal-classic-card" hidden>
      <div class="card-header"><h2 id="gal-classic">Classics</h2></div>
      <div class="scenario-grid" id="gal-classic-grid"></div>
    </section>`;
  paintSaved(container);
  loadRosterScenarios(container);
}

function paintSaved(container) {
  const mount = container.querySelector("#gal-saved");
  const saved = savedScenarios();
  mount.innerHTML = saved.length
    ? saved.map((entry) => scenarioCard({
      label: entry.name,
      blurb: `${entry.scenario.mode === "replay" ? `Replay of ${seasonLabel(entry.scenario.season)}` : "Next-season projection"} · saved ${entry.saved_at.slice(0, 10)}`,
      teams: teamsOf(entry.scenario.moves).slice(0, 4),
    }, entry.scenario, { id: entry.id, saved: true })).join("")
    : emptyState("Nothing saved yet. Build a scenario in the Sandbox and press “Save to gallery”.");
  wireActions(container, mount);
}

function wireActions(container, mount) {
  mount.querySelectorAll("[data-copy]").forEach((button) => button.addEventListener("click", () =>
    copyText(`${location.origin}${button.dataset.copy}`)));
  mount.querySelectorAll("[data-delete]").forEach((button) => button.addEventListener("click", () => {
    if (!window.confirm("Delete this saved scenario?")) return;
    deleteScenario(button.dataset.delete);
    paintSaved(container);
  }));
}

async function loadRosterScenarios(container) {
  const res = unwrap(await safe(getSandboxRosters({ mode: "next" })));
  if (!container.isConnected) return;
  const mount = container.querySelector("#gal-now-grid");
  if (!res.data) {
    mount.innerHTML = errorBanner(res.message);
    return;
  }
  const rosters = res.data;
  container.querySelector("#gal-now-note").textContent =
    `Built from the rosters as last season ended and the box-impact model, for the Sandbox's ${rosters.season_label} projection.`;
  const next = (moves) => ({ mode: "next", season: rosters.season, moves });
  mount.innerHTML = rosterScenarios(rosters).map((s) => scenarioCard(s, next(s.moves))).join("");
  wireActions(container, mount);

  const onRoster = new Set(rosters.teams.flatMap((t) => t.players.map((p) => p.person_id)));
  const classics = FEATURED_SCENARIOS.filter((s) => s.moves.every((m) =>
    m.type === "sign" || (m.assets ? m.assets.every((a) => onRoster.has(a.person_id)) : onRoster.has(m.person_id))));
  if (classics.length) {
    container.querySelector("#gal-classic-card").hidden = false;
    const grid = container.querySelector("#gal-classic-grid");
    grid.innerHTML = classics.map((s) => scenarioCard({ ...s, tag: "Classic", teams: teamsOf(s.moves) }, next(s.moves))).join("");
    wireActions(container, grid);
  }
}
