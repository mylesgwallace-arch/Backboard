// pages/players.js — the player page: search any player, then read what the
// database records (bio, every season, game logs, splits, career highs), the
// standard rates calculated from it, and the derived layer: league
// percentiles, box impact (the same box-score model the Sandbox uses), the
// most similar player-seasons in history and a next-season projection.
//
// Every number comes from the player_profile, player_game_log and
// player_outlook tools; the page only divides totals into per-game, per-36
// and per-100 lines. Derived numbers are labeled as estimates next to the
// numbers, with the method one click away.

import {
  getPlayerGameLog, getPlayerOutlook, getPlayerProfile, getSandboxRosters, previewScenario, resolvePlayer,
  simulateScenario,
} from "../api.js";
import { teamColor } from "../teamColors.js";
import { readableInk, teamSlabStyle } from "../colorInk.js";
import { apiMoves, encodeScenario } from "../components/scenario.js";
import { playerThumb, tornPhoto, wireTornPhoto } from "../components/tornPhoto.js";
import {
  agingCurveChart, agingCurveTooltip, careerArcChart, careerArcTooltip, gameTrendChart,
  gameTrendTooltip, percentileBars, wireChartTooltips,
} from "../components/charts.js";
import {
  debounce, emptyState, errorBanner, escapeHtml, fixed, pct, safe, seasonLabel, shootingPct,
  signed, signedPoints, thousands, unwrap,
} from "../components/format.js";

export const meta = {
  title: "Players",
  subtitle: "Every season, every game, and what the numbers say comes next.",
};

const QUICK_PICKS = [
  { id: 1641705, name: "Victor Wembanyama" },
  { id: 203999, name: "Nikola Jokic" },
  { id: 1628983, name: "Shai Gilgeous-Alexander" },
  { id: 201939, name: "Stephen Curry" },
  { id: 2544, name: "LeBron James" },
  { id: 893, name: "Michael Jordan" },
];
const TABS = [
  ["overview", "Overview"],
  ["stats", "Season stats"],
  ["log", "Game log"],
  ["splits", "Splits"],
  ["outlook", "Outlook"],
  ["trade", "Trade & simulate"],
];
const STAT_VIEWS = [
  ["per_game", "Per game"],
  ["totals", "Totals"],
  ["per_36", "Per 36 min"],
  ["per_100", "Per 100 poss"],
  ["advanced", "Advanced"],
];

// One player is shown at a time; this is that player's loaded data.
let state = null;

export function render(container, ctx = {}) {
  container.innerHTML = `
    <div class="card fade-in player-search-card">
      <div class="card-header"><div><h2>Find a player</h2>
        <p class="card-subtitle">Any player in the database, from 1946-47 to today. Full or partial names work
          ("wemby" won't, "wembanyama" will). If several players match, you choose.</p></div></div>
      <form id="pl-form" class="player-search" role="search" autocomplete="off">
        <label class="sr-only" for="pl-name">Player name</label>
        <input class="input" id="pl-name" placeholder="Search players, e.g. Jokic" aria-controls="pl-results">
        <button class="btn" type="submit">Search</button>
      </form>
      <div id="pl-results" class="player-results" aria-live="polite"></div>
      <div class="quick-picks" aria-label="Quick picks">
        <span class="text-muted">Try</span>
        ${QUICK_PICKS.map((p) => `<button type="button" class="chip chip-btn" data-person="${p.id}">${escapeHtml(p.name)}</button>`).join("")}
      </div>
    </div>
    <div id="pl-body"></div>
  `;

  const input = container.querySelector("#pl-name");
  const results = container.querySelector("#pl-results");
  const body = container.querySelector("#pl-body");
  const search = async (name, explicit) => {
    if (name.trim().length < 2) {
      results.innerHTML = "";
      return;
    }
    const res = unwrap(await safe(resolvePlayer({ name: name.trim(), limit: 8 })));
    if (input.value.trim() !== name.trim()) return; // a newer query is in flight
    if (!res.data) {
      results.innerHTML = explicit ? `<p class="text-muted">${escapeHtml(res.message)}</p>` : "";
      return;
    }
    renderResults(results, res.data.candidates, (id) => openPlayer(ctx, id));
  };
  const typed = debounce((value) => search(value, false), 260);
  input.addEventListener("input", () => typed(input.value));
  container.querySelector("#pl-form").addEventListener("submit", (event) => {
    event.preventDefault();
    search(input.value, true);
  });
  container.querySelectorAll(".quick-picks [data-person]").forEach((button) =>
    button.addEventListener("click", () => openPlayer(ctx, Number(button.dataset.person))));

  const presetId = Number(ctx.query?.get?.("id"));
  if (presetId) {
    loadPlayer(body, presetId, ctx, ctx.query.get("tab"));
  } else if (state?.personId) {
    loadPlayer(body, state.personId, ctx);
  } else {
    body.innerHTML = emptyState("Search for a player, or pick one above.");
  }
}

function openPlayer(ctx, personId) {
  ctx.navigate("/players", { id: personId });
}

function renderResults(mount, candidates, onPick) {
  if (!candidates?.length) {
    mount.innerHTML = `<p class="text-muted">No players match.</p>`;
    return;
  }
  mount.innerHTML = `<ul class="player-result-list">
    ${candidates.map((c) => `<li><button type="button" class="player-result" data-person="${c.person_id}">
      ${playerThumb({ personId: c.person_id, name: c.full_name })}
      <span class="player-result-name">${escapeHtml(c.full_name)}</span>
      <span class="player-result-meta">${c.from_year ?? "?"}–${c.to_year ?? "now"} · ${thousands(c.regular_season_games)} games${c.positions?.length ? ` · ${c.positions.join("-")}` : ""}</span>
    </button></li>`).join("")}
  </ul>`;
  wireTornPhoto(mount);
  mount.querySelectorAll("[data-person]").forEach((button) =>
    button.addEventListener("click", () => onPick(Number(button.dataset.person))));
}

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------

async function loadPlayer(body, personId, ctx, initialTab) {
  body.innerHTML = `<div class="card fade-in"><div class="skeleton" style="height:260px;"></div></div>
    <div class="card fade-in"><div class="stat-grid">${'<div class="skeleton" style="height:76px;"></div>'.repeat(4)}</div></div>`;
  const [profileRes, outlookRes] = await Promise.all([
    safe(getPlayerProfile(personId)),
    safe(getPlayerOutlook({ personId })),
  ]);
  const profile = unwrap(profileRes);
  if (!profile.data) {
    body.innerHTML = errorBanner(profile.message);
    return;
  }
  const outlook = unwrap(outlookRes);
  state = {
    personId,
    profile: profile.data,
    outlook: outlook.data,
    outlookMessage: outlook.message,
    outlooks: outlook.data ? { [outlook.data.season]: outlook.data } : {},
    logs: {},
    tab: TABS.some(([key]) => key === initialTab) ? initialTab : "overview",
    statView: "per_game",
    statKind: "regular",
    logSeason: null,
    logKind: "regular",
  };
  document.title = `${state.profile.bio.name} · Backboard`;
  renderPlayer(body, ctx);
}

function renderPlayer(body, ctx) {
  const { profile, outlook } = state;
  if (!profile.has_games) {
    body.innerHTML = `${renderHero(profile, outlook)}${emptyState("This player has no regular-season, play-in or playoff games in the database.")}`;
    wireHero(body, ctx);
    return;
  }
  body.innerHTML = `
    ${renderHero(profile, outlook)}
    ${renderKeyNumbers(profile, outlook)}
    <div class="card fade-in">
      <div class="toolbar tab-strip" role="tablist" aria-label="Player sections">
        ${TABS.map(([key, label]) => `<button class="btn ${key === state.tab ? "" : "btn-secondary"}" role="tab"
          aria-selected="${key === state.tab}" data-tab="${key}">${label}</button>`).join("")}
      </div>
      <div id="pl-panel" role="tabpanel"></div>
    </div>`;
  wireHero(body, ctx);
  body.querySelectorAll("[data-tab]").forEach((button) => button.addEventListener("click", () => {
    state.tab = button.dataset.tab;
    body.querySelectorAll("[data-tab]").forEach((b) => {
      const on = b.dataset.tab === state.tab;
      b.classList.toggle("btn-secondary", !on);
      b.setAttribute("aria-selected", String(on));
    });
    renderPanel(body.querySelector("#pl-panel"), ctx);
  }));
  renderPanel(body.querySelector("#pl-panel"), ctx);
}

function renderPanel(panel, ctx) {
  const renderers = {
    overview: renderOverview, stats: renderStats, log: renderLog, splits: renderSplits, outlook: renderOutlook,
    trade: renderTrade,
  };
  renderers[state.tab](panel, ctx);
}

// ---------------------------------------------------------------------------
// Hero: torn headshot on the team slab
// ---------------------------------------------------------------------------

function renderHero(profile, outlook) {
  const bio = profile.bio;
  const team = profile.last_team;
  const colors = teamColor(team?.team_id);
  const draft = bio.draft
    ? `${bio.draft.year} · round ${bio.draft.round}, pick ${bio.draft.pick}`
    : bio.undrafted ? "Undrafted" : "—";
  const span = profile.seasons_played?.length
    ? `${seasonLabel(profile.seasons_played[0])} to ${seasonLabel(profile.seasons_played[profile.seasons_played.length - 1])}`
    : "—";
  const facts = [
    ["Age", bio.age != null ? `${bio.age}` : bio.birth_date ? `Born ${bio.birth_date.slice(0, 4)}` : "—"],
    ["Height", bio.height ? `${bio.height.replace("-", "′ ")}″` : "—"],
    ["Weight", bio.weight_lbs ? `${bio.weight_lbs} lb` : "—"],
    ["Draft", draft],
    [bio.school ? "School" : "From", bio.school || bio.country || "—"],
    ["Seasons", `${profile.seasons_played?.length || 0} · ${span}`],
  ];
  const tag = outlook?.tags?.[0]?.label;
  const teamLine = [
    profile.active ? team?.name : team ? `Last played for ${team.name} (${seasonLabel(team.season)})` : null,
    bio.jersey ? `#${bio.jersey}` : null,
    bio.position_label,
  ].filter(Boolean).join(" · ");
  return `
    <section class="card team-hero player-hero fade-in" style="${teamSlabStyle(colors.primary)} --team-alt:${colors.secondary};"
      aria-labelledby="player-hero-name">
      <div class="player-hero-grid">
        ${tornPhoto({ personId: bio.person_id, name: bio.name, teamColor: colors.primary, teamAlt: colors.secondary })}
        <div class="player-hero-id">
          <div class="team-hero-city">${escapeHtml(teamLine)}</div>
          <h2 class="team-hero-name" id="player-hero-name">${escapeHtml(bio.name)}</h2>
          <dl class="player-bio">
            ${facts.map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`).join("")}
          </dl>
          <div class="team-hero-actions">
            ${profile.active || profile.seasons_played?.some((s) => s >= 1985)
              ? `<button class="btn" id="pl-sandbox">Trade &amp; simulate ▸</button>` : ""}
            ${profile.active ? `<span class="badge team-abbrev-stamp">${escapeHtml(team?.abbreviation || "")}</span>` : `<span class="badge team-abbrev-stamp">Retired</span>`}
          </div>
        </div>
        ${tag ? `<p class="stamp stamp--tilt player-tag-stamp" title="Derived from league percentiles (Outlook tab)">${escapeHtml(tag)}</p>` : ""}
      </div>
    </section>`;
}

function wireHero(body, ctx) {
  wireTornPhoto(body);
  body.querySelector("#pl-sandbox")?.addEventListener("click", () => {
    state.tab = "trade";
    renderPlayer(body, ctx);
    body.querySelector("#pl-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function renderKeyNumbers(profile, outlook) {
  const regular = profile.seasons.regular;
  const latest = [...regular].reverse().find((row) => !row.is_stint);
  if (!latest) return "";
  const g = latest.g || 1;
  const perGame = (stat) => (latest[stat] == null ? "—" : (latest[stat] / g).toFixed(1));
  const impact = outlook && outlook.season === latest.season ? outlook.impact : null;
  const career = profile.career.regular;
  const tiles = [
    ["Points", perGame("pts"), `per game · ${latest.season_label}`],
    ["Rebounds", perGame("trb"), "per game"],
    ["Assists", perGame("ast"), "per game"],
    ["True shooting", pct(latest.ts_pct), latest.usg_pct != null ? `on ${pct(latest.usg_pct)} usage` : "points per shot attempt"],
  ];
  if (impact) {
    tiles.push(["Box impact", signed(impact.box_impact, 2),
      impact.rank ? `#${impact.rank} of ${outlook.qualified_players} qualified · model estimate` : "model estimate"]);
    tiles.push(["Wins added", signed(impact.wins_added, 1), "vs. a league-average player · model estimate"]);
  }
  tiles.push(["Career points", thousands(career?.pts), `${thousands(career?.g)} games`]);
  tiles.push(["Career line", career ? `${fixed(career.pts / career.g)} / ${fixed(career.trb / career.g)} / ${fixed(career.ast / career.g)}` : "—", "pts / reb / ast per game"]);
  return `
    <div class="card fade-in">
      <div class="section-title">${escapeHtml(latest.season_label)} at a glance</div>
      <div class="stat-grid">
        ${tiles.map(([label, value, note]) => `<div class="stat-tile"><div class="stat-label">${escapeHtml(label)}</div>
          <div class="stat-value">${escapeHtml(value)}</div><div class="stat-note">${escapeHtml(note)}</div></div>`).join("")}
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------

function renderOverview(panel, ctx) {
  const { profile, outlook } = state;
  const colors = teamColor(profile.last_team?.team_id);
  const highs = profile.highs.regular || {};
  const playoffHighs = profile.highs.playoffs || {};
  const miles = profile.milestones.regular;
  const derived = outlook
    ? `
      <div class="grid-2">
        <div>
          <div class="section-title">Scouting read · ${escapeHtml(outlook.season_label)}</div>
          ${renderScoutingRead(outlook)}
        </div>
        <div>
          <div class="section-title">League percentiles</div>
          ${outlook.qualified ? "" : `<p class="text-muted">Fewer than 500 minutes this season: percentiles compare against qualified players and are shown for reference.</p>`}
          ${percentileBars(outlook.percentiles.filter((p) => !["mpg", "orb_100"].includes(p.stat)), colors.primary)}
          <p class="table-note">Bar = percentile among ${outlook.qualified_players} players with 500+ minutes · tick = league median</p>
        </div>
      </div>
      <div class="section-title mt-2">Career arc: box impact by season</div>
      <p class="text-muted chart-intro">Estimated change in team net rating per 100 possessions with this player on the floor instead of a
        league-average player (box-score model, calibrated). Green: above average; red: below.
        Peak: <strong>${escapeHtml(outlook.peak.season_label)}</strong> (${signed(outlook.peak.box_impact, 2)}).</p>
      <div class="chart-wrap" id="pl-arc">${careerArcChart(outlook.trajectory, { peakLabel: outlook.peak.season_label })}</div>`
    : `<div class="info-banner scouting-note"><span class="note-label">Recorded stats only</span>
        <p class="model-insight">${escapeHtml(derivedUnavailableText())}</p></div>`;
  panel.innerHTML = `
    ${derived}
    <div class="grid-2 mt-2">
      <div>
        <div class="section-title">Career highs · regular season</div>
        ${renderHighs(highs)}
      </div>
      <div>
        <div class="section-title">Milestones</div>
        ${miles ? `<div class="milestone-grid">
          ${[["20+ point games", miles.games_20_points], ["30+", miles.games_30_points], ["40+", miles.games_40_points],
             ["50+", miles.games_50_points], ["Double-doubles", miles.double_doubles], ["Triple-doubles", miles.triple_doubles],
             ["Team record in games played", `${thousands(miles.wins)}-${thousands(miles.losses)}`]]
            .map(([k, v]) => `<div><span class="milestone-value">${typeof v === "number" ? thousands(v) : escapeHtml(v)}</span><span class="milestone-label">${escapeHtml(k)}</span></div>`).join("")}
        </div>` : emptyState("No regular-season games.")}
        ${Object.keys(playoffHighs).length ? `<div class="section-title mt-2">Playoff highs</div>${renderHighs(playoffHighs, ["pts", "trb", "ast", "game_score"])}` : ""}
      </div>
    </div>`;
  if (outlook) {
    wireChartTooltips(panel.querySelector("#pl-arc"), careerArcTooltip(outlook.trajectory));
    const shown = outlook.percentiles.filter((p) => !["mpg", "orb_100"].includes(p.stat));
    wireChartTooltips(panel.querySelector(".pctl-list"), (i) => {
      const p = shown[Number(i)];
      if (!p) return null;
      const fmt = (v) => (v == null ? "—" : ["ts", "tp_pct", "ft_pct"].includes(p.stat) ? pct(v) : fixed(v, 1));
      return { title: p.label, rows: [
        { value: p.percentile == null ? "—" : `${Math.round(p.percentile)}th`, label: "percentile" },
        { value: fmt(p.value), label: "this player" },
        { value: fmt(p.league_median), label: "league median" },
      ] };
    });
  }
  panel.querySelectorAll("[data-season-log]").forEach((button) => button.addEventListener("click", () =>
    openLog(Number(button.dataset.seasonLog), button.dataset.kind || "regular", ctx)));
}

function derivedUnavailableText() {
  const seasons = state.profile.seasons_played || [];
  if (seasons.length && Math.max(...seasons) < 1985) {
    return `Percentiles, box impact, comparables and projections need complete team box scores, which start in 1985-86. ${state.profile.bio.name}'s career ended before that, so this page shows the recorded stats and the rates that can be calculated from them.`;
  }
  return state.outlookMessage || "The derived views are unavailable for this player.";
}

function renderScoutingRead(outlook) {
  const tags = outlook.tags || [];
  const strengths = outlook.strengths || [];
  const weaknesses = outlook.weaknesses || [];
  const describe = (item) => `${item.label.charAt(0).toLowerCase()}${item.label.slice(1)} (${Math.round(item.percentile)}th percentile)`;
  return `
    ${tags.length ? `<div class="feature-chip-row">${tags.map((t) => `<span class="chip" title="${escapeHtml(t.evidence.map((e) => `${e.stat}: ${Math.round(e.percentile)}th pct`).join(", "))}"><strong>${escapeHtml(t.label)}</strong></span>`).join("")}</div>` : ""}
    <ul class="plain-list mt-1">
      ${strengths.length ? `<li><strong>Best at:</strong> ${escapeHtml(strengths.map(describe).join("; "))}.</li>` : ""}
      ${weaknesses.length ? `<li><strong>Weakest at:</strong> ${escapeHtml(weaknesses.map(describe).join("; "))}.</li>` : ""}
      <li><strong>Box impact:</strong> ${signed(outlook.impact.box_impact, 2)} per 100 possessions${outlook.impact.rank ? `, #${outlook.impact.rank} of ${outlook.qualified_players}` : ""};
        about ${signed(outlook.impact.wins_added, 1)} wins over a league-average player in the same minutes (model estimate).</li>
      ${outlook.projection?.line ? `<li><strong>Next season (${escapeHtml(outlook.projection.season_label)}):</strong> similar players went on to about
        ${fixed(outlook.projection.line.pts?.mid)} points in ${fixed(outlook.projection.line.mpg?.mid)} minutes a game
        (${Math.round((outlook.projection.share_still_playing || 0) * 100)}% were still in the league). See Outlook.</li>` : ""}
    </ul>
    <p class="text-muted mt-1" style="font-size:0.82rem;">Tags and strengths come from league percentiles this season (players with 500+ minutes). Box impact is a box-score estimate and sees offense far better than defense.</p>`;
}

function renderHighs(highs, keys) {
  const entries = Object.entries(highs).filter(([key]) => !keys || keys.includes(key));
  if (!entries.length) return emptyState("No games recorded.");
  return `<table class="data-table compact-table"><tbody>
    ${entries.map(([, h]) => `<tr><td>${escapeHtml(h.label)}</td><td class="num"><strong>${h.value}</strong></td>
      <td class="text-muted">${escapeHtml(h.date)} ${h.home ? "vs" : "@"} ${escapeHtml(h.opponent)}</td>
      <td class="num"><button class="link-btn" data-season-log="${h.season}">Game log</button></td></tr>`).join("")}
  </tbody></table>`;
}

// ---------------------------------------------------------------------------
// Season stats
// ---------------------------------------------------------------------------

const COUNT_COLUMNS = [
  ["fgm", "FG"], ["fga", "FGA"], ["fg_pct", "FG%", "pct"], ["tpm", "3P"], ["tpa", "3PA"], ["tp_pct", "3P%", "pct"],
  ["ftm", "FT"], ["fta", "FTA"], ["ft_pct", "FT%", "pct"], ["orb", "ORB"], ["drb", "DRB"], ["trb", "TRB"],
  ["ast", "AST"], ["stl", "STL"], ["blk", "BLK"], ["tov", "TOV"], ["pf", "PF"], ["pts", "PTS"],
];
const ADVANCED_COLUMNS = [
  ["ts_pct", "TS%", "pct"], ["efg_pct", "eFG%", "pct"], ["tpar", "3PAr", "pct"], ["ftr", "FTr", "pct"],
  ["usg_pct", "USG%", "rate"], ["ast_pct", "AST%", "rate"], ["orb_pct", "ORB%", "rate"], ["drb_pct", "DRB%", "rate"],
  ["trb_pct", "TRB%", "rate"], ["stl_pct", "STL%", "rate"], ["blk_pct", "BLK%", "rate"], ["tov_pct", "TOV%", "rate"],
  ["game_score", "GmSc", "plain"], ["pm", "+/- per g", "pm"], ["pace", "Pace", "plain"],
];

function statCell(row, key, kind, view) {
  const value = row[key];
  if (kind === "pct") return shootingPct(value);
  if (kind === "rate") return value == null ? "—" : (value * 100).toFixed(1);
  if (kind === "plain") return fixed(value, 1);
  if (kind === "pm") return value == null || !row.g ? "—" : signed(value / row.g, 1);
  if (value == null) return "—";
  if (view === "totals") return thousands(value);
  if (view === "per_game") return (value / row.g).toFixed(1);
  if (view === "per_36") return row.minutes_complete && row.mp ? ((value / row.mp) * 36).toFixed(1) : "—";
  if (view === "per_100") return row.poss ? ((value / row.poss) * 100).toFixed(1) : "—";
  return "—";
}

function renderStats(panel, ctx) {
  const rows = state.profile.seasons[state.statKind] || [];
  const career = state.profile.career[state.statKind];
  const view = state.statView;
  const advanced = view === "advanced";
  const columns = advanced ? ADVANCED_COLUMNS : COUNT_COLUMNS;
  const mpCell = (row) => {
    if (row.mp == null) return "—";
    if (view === "totals") return thousands(row.mp);
    if (view === "per_game") return (row.mp / (row.mp_games || row.g)).toFixed(1);
    return view === "per_36" ? "36.0" : "—";
  };
  const head = `<tr><th scope="col" class="sticky-col">Season</th><th scope="col" class="num">Age</th><th scope="col">Tm</th>
    <th scope="col" class="num">G</th>${advanced ? "" : `<th scope="col" class="num">GS</th><th scope="col" class="num">MP</th>`}
    ${columns.map(([, label]) => `<th scope="col" class="num">${label}</th>`).join("")}</tr>`;
  const line = (row, labelCell, extraClass = "") => `<tr class="${extraClass}">
    ${labelCell}<td class="num">${row.age ?? "—"}</td><td>${escapeHtml(row.team ?? "")}</td>
    <td class="num">${row.g}</td>${advanced ? "" : `<td class="num">${row.gs ?? "—"}</td><td class="num">${mpCell(row)}</td>`}
    ${columns.map(([key, , kind]) => `<td class="num">${statCell(row, key, kind, view)}</td>`).join("")}</tr>`;
  const body = rows.map((row) => line(row,
    `<th scope="row" class="sticky-col">${row.is_stint ? `<span class="stint-indent">${escapeHtml(row.season_label)}</span>`
      : `<button class="link-btn" data-season-log="${row.season}" data-kind="${state.statKind}" title="Open the ${escapeHtml(row.season_label)} game log">${escapeHtml(row.season_label)}</button>`}</th>`,
    row.is_stint ? "stint-row" : row.is_total ? "total-row" : "")).join("");
  const careerRow = career ? line({ ...career, team: "", age: null },
    `<th scope="row" class="sticky-col">Career</th>`, "career-row") : "";
  panel.innerHTML = `
    <div class="toolbar stat-toolbar">
      <div class="segmented" role="group" aria-label="Season type">
        ${[["regular", "Regular season"], ["playoffs", "Playoffs"]].map(([key, label]) =>
          `<button class="seg ${state.statKind === key ? "is-on" : ""}" data-kind="${key}" aria-pressed="${state.statKind === key}">${label}</button>`).join("")}
      </div>
      <div class="segmented" role="group" aria-label="Stat view">
        ${STAT_VIEWS.map(([key, label]) =>
          `<button class="seg ${view === key ? "is-on" : ""}" data-view="${key}" aria-pressed="${view === key}">${label}</button>`).join("")}
      </div>
    </div>
    ${rows.length ? `<div class="table-scroll"><table class="data-table stats-table"><thead>${head}</thead><tbody>${body}${careerRow}</tbody></table></div>`
      : emptyState(`No ${state.statKind === "playoffs" ? "playoff" : "regular-season"} games.`)}
    <p class="table-note">${view === "per_100" ? "Per 100 possessions uses the team's estimated possessions during the player's minutes (1985-86 on). " : ""}${
      advanced ? "Rates are % of team (or opponent) events while the player was on the floor; formulas under Outlook → method. " : ""}TOT = two or more teams. Click a season for its game log.</p>`;
  panel.querySelectorAll("[data-kind]").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.seasonLog) return;
    state.statKind = b.dataset.kind;
    renderStats(panel, ctx);
  }));
  panel.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => {
    state.statView = b.dataset.view;
    renderStats(panel, ctx);
  }));
  panel.querySelectorAll("[data-season-log]").forEach((b) => b.addEventListener("click", (event) => {
    event.stopPropagation();
    openLog(Number(b.dataset.seasonLog), b.dataset.kind || "regular", ctx);
  }));
}

function openLog(season, kind, ctx) {
  state.logSeason = season;
  state.logKind = kind;
  state.tab = "log";
  const body = document.querySelector("#pl-body");
  if (body) renderPlayer(body, ctx);
  document.querySelector("#pl-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------------------------------------------------------------------------
// Game log + splits (one request per season and kind, cached)
// ---------------------------------------------------------------------------

function defaultLogSeason(kind) {
  const rows = state.profile.seasons[kind === "playoffs" ? "playoffs" : "regular"] || [];
  const seasons = rows.map((r) => r.season);
  return seasons.length ? Math.max(...seasons) : state.profile.seasons_played.at(-1);
}

async function fetchLog(season, kind) {
  const key = `${season}:${kind}`;
  if (!state.logs[key]) {
    const res = unwrap(await safe(getPlayerGameLog({ personId: state.personId, season, kind })));
    state.logs[key] = res;
  }
  return state.logs[key];
}

function logControls() {
  const kinds = [["regular", "Regular season"], ["playoffs", "Playoffs"], ["play_in", "Play-in"]];
  const seasons = [...state.profile.seasons_played].reverse();
  return `
    <div class="toolbar stat-toolbar">
      <div class="field compact-field"><label for="pl-log-season">Season</label>
        <div class="select-wrap"><select class="select" id="pl-log-season">
          ${seasons.map((s) => `<option value="${s}" ${s === state.logSeason ? "selected" : ""}>${seasonLabel(s)}</option>`).join("")}
        </select></div></div>
      <div class="segmented" role="group" aria-label="Game type">
        ${kinds.map(([key, label]) => `<button class="seg ${state.logKind === key ? "is-on" : ""}" data-log-kind="${key}" aria-pressed="${state.logKind === key}">${label}</button>`).join("")}
      </div>
    </div>`;
}

function wireLogControls(panel, rerender) {
  panel.querySelector("#pl-log-season")?.addEventListener("change", (event) => {
    state.logSeason = Number(event.target.value);
    rerender();
  });
  panel.querySelectorAll("[data-log-kind]").forEach((b) => b.addEventListener("click", () => {
    state.logKind = b.dataset.logKind;
    rerender();
  }));
}

async function renderLog(panel, ctx) {
  state.logSeason ??= defaultLogSeason(state.logKind);
  panel.innerHTML = `${logControls()}<div class="skeleton" style="height:240px;"></div>`;
  wireLogControls(panel, () => renderLog(panel, ctx));
  const requested = `${state.logSeason}:${state.logKind}`;
  const { data, message } = await fetchLog(state.logSeason, state.logKind);
  if (state.tab !== "log" || requested !== `${state.logSeason}:${state.logKind}`) return;
  if (!data) {
    panel.innerHTML = `${logControls()}${emptyState(message)}`;
    wireLogControls(panel, () => renderLog(panel, ctx));
    return;
  }
  const line = data.season_line;
  panel.innerHTML = `
    ${logControls()}
    <p class="log-summary"><strong>${line.g} games</strong> · ${line.w}-${line.g - line.w} team record ·
      ${fixed(line.pts)} pts, ${fixed(line.trb)} reb, ${fixed(line.ast)} ast in ${fixed(line.mp)} min · TS ${pct(line.ts_pct)} ·
      ${data.consistency.share_20_plus != null ? `${pct(data.consistency.share_20_plus, 0)} of games with 20+ points` : ""}</p>
    <div class="chart-wrap" id="pl-trend">${gameTrendChart(data.games)}</div>
    <div class="table-scroll mt-1"><table class="data-table stats-table log-table">
      <thead><tr><th scope="col" class="sticky-col">#</th><th scope="col">Date</th><th scope="col">Opp</th><th scope="col">Result</th>
        ${data.starts_recorded ? `<th scope="col">GS</th>` : ""}<th scope="col" class="num">MP</th>
        <th scope="col" class="num">PTS</th><th scope="col" class="num">TRB</th><th scope="col" class="num">AST</th>
        <th scope="col" class="num">STL</th><th scope="col" class="num">BLK</th><th scope="col" class="num">TOV</th>
        <th scope="col" class="num">FG</th><th scope="col" class="num">3P</th><th scope="col" class="num">FT</th>
        <th scope="col" class="num">+/-</th><th scope="col" class="num">GmSc</th></tr></thead>
      <tbody>${data.games.map((g) => `<tr>
        <td class="sticky-col">${g.n}</td><td>${escapeHtml(g.date)}</td>
        <td>${g.home ? "vs" : "@"} ${escapeHtml(g.opponent)}</td>
        <td><span class="${g.win ? "result-w" : "result-l"}">${g.win ? "W" : "L"}</span> ${escapeHtml(g.score || "")}</td>
        ${data.starts_recorded ? `<td>${g.started ? "✓" : ""}</td>` : ""}
        <td class="num">${fixed(g.mp, 0)}</td><td class="num"><strong>${g.pts ?? "—"}</strong></td><td class="num">${g.trb ?? "—"}</td>
        <td class="num">${g.ast ?? "—"}</td><td class="num">${g.stl ?? "—"}</td><td class="num">${g.blk ?? "—"}</td><td class="num">${g.tov ?? "—"}</td>
        <td class="num">${g.fgm ?? "—"}-${g.fga ?? "—"}</td><td class="num">${g.tpm ?? "—"}-${g.tpa ?? "—"}</td><td class="num">${g.ftm ?? "—"}-${g.fta ?? "—"}</td>
        <td class="num">${g.pm == null ? "—" : signed(g.pm, 0)}</td><td class="num">${fixed(g.game_score)}</td></tr>`).join("")}
      </tbody></table></div>`;
  wireLogControls(panel, () => renderLog(panel, ctx));
  wireChartTooltips(panel.querySelector("#pl-trend"), gameTrendTooltip(data.games));
}

async function renderSplits(panel, ctx) {
  state.logSeason ??= defaultLogSeason(state.logKind);
  panel.innerHTML = `${logControls()}<div class="skeleton" style="height:240px;"></div>`;
  wireLogControls(panel, () => renderSplits(panel, ctx));
  const requested = `${state.logSeason}:${state.logKind}`;
  const { data, message } = await fetchLog(state.logSeason, state.logKind);
  if (state.tab !== "splits" || requested !== `${state.logSeason}:${state.logKind}`) return;
  if (!data) {
    panel.innerHTML = `${logControls()}${emptyState(message)}`;
    wireLogControls(panel, () => renderSplits(panel, ctx));
    return;
  }
  const groups = [["location", "Home / road"], ["result", "Wins / losses"], ["rest", "Rest"], ["role", "Starter / bench"],
    ["team", "By team"], ["month", "By month"]];
  const table = (rows) => `<div class="table-scroll"><table class="data-table split-table">
    <thead><tr><th scope="col"></th><th scope="col" class="num">G</th><th scope="col" class="num">W-L</th><th scope="col" class="num">MP</th>
      <th scope="col" class="num">PTS</th><th scope="col" class="num">TRB</th><th scope="col" class="num">AST</th>
      <th scope="col" class="num">3PM</th><th scope="col" class="num">TS%</th><th scope="col" class="num">+/-</th><th scope="col" class="num">GmSc</th></tr></thead>
    <tbody>${rows.map((r) => r.g ? `<tr><th scope="row">${escapeHtml(r.label)}</th><td class="num">${r.g}</td><td class="num">${r.w}-${r.g - r.w}</td>
      <td class="num">${fixed(r.mp)}</td><td class="num">${fixed(r.pts)}</td><td class="num">${fixed(r.trb)}</td><td class="num">${fixed(r.ast)}</td>
      <td class="num">${fixed(r.tpm)}</td><td class="num">${pct(r.ts_pct)}</td><td class="num">${r.pm == null ? "—" : signed(r.pm)}</td>
      <td class="num">${fixed(r.game_score)}</td></tr>` : `<tr><th scope="row">${escapeHtml(r.label)}</th><td class="num">0</td><td colspan="9" class="text-muted">No games</td></tr>`).join("")}</tbody>
  </table></div>`;
  const c = data.consistency;
  panel.innerHTML = `
    ${logControls()}
    <div class="stat-grid">
      <div class="stat-tile"><div class="stat-label">Scoring swing</div><div class="stat-value">±${fixed(c.points_sd)}</div><div class="stat-note">points, game to game (SD)</div></div>
      <div class="stat-tile"><div class="stat-label">20-point games</div><div class="stat-value">${pct(c.share_20_plus, 0)}</div><div class="stat-note">of games played</div></div>
      <div class="stat-tile"><div class="stat-label">Longest 20+ streak</div><div class="stat-value">${c.best_streak_20_plus}</div><div class="stat-note">games in a row</div></div>
      <div class="stat-tile"><div class="stat-label">Double-doubles</div><div class="stat-value">${c.double_doubles}</div><div class="stat-note">${escapeHtml(data.season_label)}</div></div>
    </div>
    ${groups.filter(([key]) => data.splits[key]?.length).map(([key, label]) =>
      `<div class="section-title mt-2">${label}</div>${table(data.splits[key])}`).join("")}
    ${data.starts_recorded ? "" : `<p class="table-note">Starter/bench splits are hidden: the source does not mark starters reliably in ${escapeHtml(data.season_label)}.</p>`}`;
  wireLogControls(panel, () => renderSplits(panel, ctx));
}

// ---------------------------------------------------------------------------
// Outlook: comparables, projection, aging, with/without
// ---------------------------------------------------------------------------

async function renderOutlook(panel, ctx) {
  const outlook = state.outlook;
  if (!outlook) {
    panel.innerHTML = `<div class="info-banner scouting-note"><span class="note-label">Not available</span>
      <p class="model-insight">${escapeHtml(derivedUnavailableText())}</p></div>`;
    return;
  }
  const available = outlook.trajectory.map((t) => t.season).reverse();
  const p = outlook.projection;
  const current = (key) => {
    const row = state.profile.seasons.regular.find((r) => r.season === outlook.season && !r.is_stint);
    if (!row) return "—";
    if (key === "ts_pct") return pct(row.ts_pct);
    if (key === "mpg") return fixed(row.mp / (row.mp_games || row.g));
    if (key === "games") return String(row.g);
    return row[key] == null ? "—" : fixed(row[key] / row.g);
  };
  const projRows = p?.line ? [
    ["Minutes", "mpg", 1], ["Games", "games", 0], ["Points", "pts", 1], ["Rebounds", "trb", 1], ["Assists", "ast", 1],
    ["Steals", "stl", 1], ["Blocks", "blk", 1], ["Turnovers", "tov", 1], ["True shooting", "ts_pct", 3],
    ["Box impact", "box_impact", 2], ["Wins added", "wins_added", 1],
  ].filter(([, key]) => p.line[key]) : [];
  const fmt = (v, digits, key) => (key === "ts_pct" ? pct(v) : ["box_impact", "wins_added"].includes(key) ? signed(v, digits) : fixed(v, digits));
  panel.innerHTML = `
    <div class="toolbar stat-toolbar">
      <div class="field compact-field"><label for="pl-outlook-season">Season</label>
        <div class="select-wrap"><select class="select" id="pl-outlook-season">
          ${available.map((s) => `<option value="${s}" ${s === outlook.season ? "selected" : ""}>${seasonLabel(s)}</option>`).join("")}
        </select></div></div>
      <p class="text-muted">Everything on this tab is extrapolated from history. It describes what similar players did; it knows nothing about health, contracts or role.</p>
    </div>
    ${p?.line ? `
      <div class="section-title">If ${escapeHtml(outlook.season_label)} were followed by ${escapeHtml(p.season_label)}: the projection</div>
      <p class="text-muted chart-intro">From the ${p.comparables} most similar player-seasons with a known next season.
        <strong>${Math.round(p.share_still_playing * 100)}%</strong> of them played in the NBA the next season and
        <strong>${Math.round(p.share_500_minutes * 100)}%</strong> played 500+ minutes.
        ${p.aging_curve_check ? `League-wide, players this age change by ${signed(p.aging_curve_check.expected_delta_box_impact, 2)} box impact a year; these comparables changed by ${signed(p.aging_curve_check.comps_delta_box_impact, 2)}.` : ""}</p>
      <div class="table-scroll"><table class="data-table projection-table">
        <thead><tr><th scope="col">Per game</th><th scope="col" class="num">${escapeHtml(outlook.season_label)} (actual)</th>
          <th scope="col" class="num">${escapeHtml(p.season_label)} projection</th><th scope="col">Likely range (20th–80th)</th></tr></thead>
        <tbody>${projRows.map(([label, key, digits]) => {
          const r = p.line[key];
          const actual = key === "box_impact" ? signed(p.current.box_impact, 2) : key === "wins_added" ? signed(p.current.wins_added, 1) : current(key);
          return `<tr><th scope="row">${label}</th><td class="num">${actual}</td><td class="num"><strong>${fmt(r.mid, digits, key)}</strong></td>
            <td><span class="range-text">${fmt(r.low, digits, key)} – ${fmt(r.high, digits, key)}</span></td></tr>`;
        }).join("")}</tbody></table></div>` : `<p class="text-muted">No projection: none of the comparable seasons has a following season in the data.</p>`}
    <div class="section-title mt-2">Most similar seasons in history</div>
    <p class="text-muted chart-intro">Same age (±1), 1,000+ minutes, closest league-relative per-100 profile (scoring, shot volume and accuracy,
      playmaking, turnovers, rebounding, steals, blocks, threes, minutes). One season per player.</p>
    <div class="table-scroll"><table class="data-table stats-table">
      <thead><tr><th scope="col" class="sticky-col">Player</th><th scope="col">Season</th><th scope="col" class="num">Age</th>
        <th scope="col" class="num">Similarity</th><th scope="col" class="num">MPG</th><th scope="col" class="num">PTS/100</th>
        <th scope="col" class="num">TS%</th><th scope="col" class="num">AST/100</th><th scope="col" class="num">TRB/100</th>
        <th scope="col" class="num">Box impact</th><th scope="col">Next season</th></tr></thead>
      <tbody>${outlook.comps.map((c) => `<tr>
        <th scope="row" class="sticky-col"><button class="link-btn" data-open-player="${c.person_id}">${escapeHtml(c.name)}</button></th>
        <td>${escapeHtml(c.season_label)}</td><td class="num">${c.age ?? "—"}</td><td class="num">${fixed(c.similarity, 0)}</td>
        <td class="num">${fixed(c.mpg)}</td><td class="num">${fixed(c.pts_100)}</td><td class="num">${pct(c.ts)}</td>
        <td class="num">${fixed(c.ast_100)}</td><td class="num">${fixed(c.trb_100)}</td><td class="num">${signed(c.box_impact, 2)}</td>
        <td>${c.next_season.status === "played" ? `${fixed(c.next_season.mpg)} mpg, ${c.next_season.games} g, impact ${signed(c.next_season.delta_box_impact, 2)}`
          : `<span class="text-muted">${escapeHtml(c.next_season.status)}</span>`}</td></tr>`).join("")}</tbody></table></div>
    <div class="grid-2 mt-2">
      <div>
        <div class="section-title">Aging curve (league)</div>
        <p class="text-muted chart-intro">Average year-to-year change in box impact by age since 1985-86, chained and shown relative to the peak.
          The orange dot is this player's age in ${escapeHtml(outlook.season_label)}.</p>
        <div class="chart-wrap" id="pl-aging">${agingCurveChart(outlook.aging_curve.curve, outlook.age)}</div>
      </div>
      <div>
        <div class="section-title">Team with and without the player · ${escapeHtml(outlook.season_label)}</div>
        ${outlook.with_without?.length ? `<table class="data-table compact-table">
          <thead><tr><th scope="col">Team</th><th scope="col"></th><th scope="col" class="num">G</th><th scope="col" class="num">W-L</th>
            <th scope="col" class="num">Win %</th><th scope="col" class="num">Avg margin</th></tr></thead>
          <tbody>${outlook.with_without.map((t) => [["Played", t.played], ["Missed", t.missed]].map(([label, line], i) => `<tr>
            ${i === 0 ? `<th scope="row" rowspan="2">${escapeHtml(t.team)}</th>` : ""}<td>${label}</td><td class="num">${line.g}</td>
            <td class="num">${line.w}-${line.g - line.w}</td><td class="num">${pct(line.win_pct, 0)}</td>
            <td class="num">${line.margin == null ? "—" : signed(line.margin)}</td></tr>`).join("")).join("")}</tbody></table>
          <p class="table-note">An association, not a cause: opponents, injuries and rest differ between the two sets of games.</p>` : emptyState("No regular-season games this season.")}
      </div>
    </div>
    <details class="collapsible mt-2"><summary>Method and formulas</summary>
      <div class="mt-1">
        <p>${escapeHtml(outlook.impact.definition)}</p>
        ${p?.method ? `<p class="mt-1">${escapeHtml(p.method)}</p>` : ""}
        <p class="mt-1">${escapeHtml(outlook.aging_curve.method)}</p>
        <ul class="plain-list mt-1">${Object.entries(state.profile.formulas).map(([k, v]) => `<li><strong>${escapeHtml(k)}</strong>: ${escapeHtml(v)}</li>`).join("")}</ul>
      </div>
    </details>`;
  panel.querySelector("#pl-outlook-season").addEventListener("change", async (event) => {
    const season = Number(event.target.value);
    if (!state.outlooks[season]) {
      panel.style.opacity = "0.6";
      const res = unwrap(await safe(getPlayerOutlook({ personId: state.personId, season })));
      panel.style.opacity = "";
      if (!res.data) {
        panel.insertAdjacentHTML("afterbegin", errorBanner(res.message));
        return;
      }
      state.outlooks[season] = res.data;
    }
    state.outlook = state.outlooks[season];
    renderOutlook(panel, ctx);
  });
  panel.querySelectorAll("[data-open-player]").forEach((b) => b.addEventListener("click", () =>
    openPlayer(ctx, Number(b.dataset.openPlayer))));
  wireChartTooltips(panel.querySelector("#pl-aging"), agingCurveTooltip(outlook.aging_curve.curve));
}

// ---------------------------------------------------------------------------
// Trade & simulate: a one-player Sandbox right on the player page
// ---------------------------------------------------------------------------

let nextRosters = null; // one request per page session (sandbox_rosters, next mode)

function loadNextRosters() {
  nextRosters ??= safe(getSandboxRosters({ mode: "next" })).then(unwrap).then((res) => {
    if (!res.data) nextRosters = null; // allow a retry
    return res;
  });
  return nextRosters;
}

function tradeMoves(rosters, home) {
  const deal = state.deal;
  const name = state.profile.bio.name;
  if (home) {
    if (!deal.dest) return [];
    const back = Object.keys(deal.back).map(Number);
    const dest = rosters.teams.find((t) => t.team_id === deal.dest);
    return [{ type: "trade", assets: [
      { person_id: state.personId, from_team_id: home.team_id, to_team_id: deal.dest, name },
      ...back.map((id) => ({ person_id: id, from_team_id: deal.dest, to_team_id: home.team_id,
        name: dest?.players.find((p) => p.person_id === id)?.name })),
    ] }];
  }
  if (!deal.dest || !deal.season) return [];
  return [{ type: "sign", person_id: state.personId, to_team_id: deal.dest, from_season: deal.season, name }];
}

async function renderTrade(panel, ctx) {
  panel.innerHTML = `<div class="skeleton" style="height:220px;"></div>`;
  const { data: rosters, message } = await loadNextRosters();
  if (state.tab !== "trade") return;
  if (!rosters) {
    panel.innerHTML = errorBanner(message);
    return;
  }
  const home = rosters.teams.find((t) => t.players.some((p) => p.person_id === state.personId));
  const eligible = (state.profile.seasons_played || []).filter((s) => s >= rosters.seasons.replay[0]);
  if (!home && !eligible.length) {
    panel.innerHTML = `<div class="info-banner scouting-note"><span class="note-label">Not available</span>
      <p class="model-insight">The Sandbox values players with the box-score model, which needs seasons from
      ${seasonLabel(rosters.seasons.replay[0])} on. ${escapeHtml(state.profile.bio.name)} has none.</p></div>`;
    return;
  }
  // A retired player's default season is their peak by box impact (Outlook), else their last.
  const peak = state.outlook?.peak?.season;
  state.deal ??= { dest: null, back: {}, result: null,
    season: eligible.includes(peak) ? peak : eligible.length ? Math.max(...eligible) : null };
  const deal = state.deal;
  const teams = [...rosters.teams].sort((a, b) => a.team_name.localeCompare(b.team_name))
    .filter((t) => !home || t.team_id !== home.team_id);
  if (!teams.some((t) => t.team_id === deal.dest)) deal.dest = teams[0].team_id;
  const dest = rosters.teams.find((t) => t.team_id === deal.dest);
  const name = escapeHtml(state.profile.bio.name);
  panel.innerHTML = `
    <p class="text-muted chart-intro">A quick, one-player version of the <button class="link-btn" id="pl-open-sandbox">Sandbox</button>:
      the ${escapeHtml(rosters.season_label)} season with rosters as ${escapeHtml(rosters.host_season_label)} ended.
      Each team's change in strength comes from the box-score model (calibrated), then the whole league's season and
      playoffs are simulated with and without the move.</p>
    <div class="grid-2">
      <div>
        ${home ? `
          <div class="section-title">Send ${name} from ${escapeHtml(home.abbreviation)}</div>
          <div class="field compact-field"><label for="pl-deal-dest">To</label>
            <div class="select-wrap"><select class="select" id="pl-deal-dest">
              ${teams.map((t) => `<option value="${t.team_id}" ${t.team_id === deal.dest ? "selected" : ""}>${escapeHtml(t.team_name)}</option>`).join("")}
            </select></div></div>
          <p class="trade-hint text-muted">Coming back to ${escapeHtml(home.abbreviation)} (optional):</p>
          <ul class="trade-roster deal-roster">
            ${dest.players.slice(0, 14).map((p) => `<li class="${deal.back[p.person_id] ? "is-picked" : ""}"><label>
              <input type="checkbox" data-back="${p.person_id}" ${deal.back[p.person_id] ? "checked" : ""}>
              <span class="trade-name">${escapeHtml(p.name)}</span>
              <span class="trade-meta">${fixed(p.mpg)} mpg · ${signed(p.box_impact, 1)}</span></label></li>`).join("")}
          </ul>`
        : `
          <div class="section-title">Sign ${name}</div>
          <p class="trade-hint text-muted">Not on a current roster, so pick one of their seasons; it is translated into
            ${escapeHtml(rosters.host_season_label)} terms (same standing relative to the league).</p>
          <div class="grid-2">
            <div class="field compact-field"><label for="pl-deal-season">Which season</label>
              <div class="select-wrap"><select class="select" id="pl-deal-season">
                ${[...eligible].reverse().map((s) => `<option value="${s}" ${s === deal.season ? "selected" : ""}>${seasonLabel(s)}</option>`).join("")}
              </select></div></div>
            <div class="field compact-field"><label for="pl-deal-dest">With</label>
              <div class="select-wrap"><select class="select" id="pl-deal-dest">
                ${teams.map((t) => `<option value="${t.team_id}" ${t.team_id === deal.dest ? "selected" : ""}>${escapeHtml(t.team_name)}</option>`).join("")}
              </select></div></div>
          </div>`}
      </div>
      <div>
        <div class="section-title">Instant estimate</div>
        <div id="pl-deal-preview" aria-live="polite"><div class="skeleton" style="height:90px;"></div></div>
        <button class="btn btn-block mt-1" id="pl-deal-run">Simulate the ${escapeHtml(rosters.season_label)} season ▸</button>
        <button class="link-btn" id="pl-deal-sandbox">Open this in the Sandbox to add more moves ▸</button>
      </div>
    </div>
    <div id="pl-deal-result" class="mt-2"></div>`;
  const moves = () => tradeMoves(rosters, home);
  const rerender = () => {
    deal.result = null;
    renderTrade(panel, ctx);
  };
  panel.querySelector("#pl-deal-dest").addEventListener("change", (event) => {
    deal.dest = Number(event.target.value);
    deal.back = {};
    rerender();
  });
  panel.querySelector("#pl-deal-season")?.addEventListener("change", (event) => {
    deal.season = Number(event.target.value);
    rerender();
  });
  panel.querySelectorAll("[data-back]").forEach((box) => box.addEventListener("change", () => {
    if (box.checked) deal.back[box.dataset.back] = true;
    else delete deal.back[box.dataset.back];
    rerender();
  }));
  const toSandbox = () => ctx.navigate("/sandbox", { s: encodeScenario({ mode: "next", season: rosters.season, moves: moves() }) });
  panel.querySelector("#pl-open-sandbox").addEventListener("click", () => ctx.navigate("/sandbox", { player: state.personId }));
  panel.querySelector("#pl-deal-sandbox").addEventListener("click", toSandbox);
  panel.querySelector("#pl-deal-run").addEventListener("click", () => runDeal(panel, rosters, moves()));
  if (deal.result) renderDealResult(panel.querySelector("#pl-deal-result"), deal.result);

  const stamp = JSON.stringify(moves());
  const preview = unwrap(await safe(previewScenario({ mode: "next", season: rosters.season, moves: apiMoves(moves()) })));
  const mount = panel.querySelector("#pl-deal-preview");
  if (stamp !== JSON.stringify(moves()) || !mount) return;
  mount.innerHTML = preview.data
    ? `<ul class="impact-list">${preview.data.teams.map((t) => {
        const colors = teamColor(t.team_id);
        return `<li><div class="impact-row">
          <span class="team-chip" style="--team-color:${colors.primary}; --team-ink:${readableInk(colors.primary)};">${escapeHtml(rosters.teams.find((r) => r.team_id === t.team_id)?.abbreviation || "")}</span>
          <span class="impact-figure ${t.delta_net_rating >= 0 ? "is-up" : "is-down"}">${signed(t.delta_net_rating, 1)}</span>
          <span class="impact-label">net rating / 100 · about ${signed(t.delta_wins_estimate, 1)} wins</span></div></li>`;
      }).join("")}</ul>`
    : errorBanner(preview.message);
}

async function runDeal(panel, rosters, moves) {
  const button = panel.querySelector("#pl-deal-run");
  const mount = panel.querySelector("#pl-deal-result");
  button.disabled = true;
  button.textContent = "Simulating…";
  mount.innerHTML = `<div class="skeleton" style="height:160px;"></div>`;
  const res = unwrap(await safe(simulateScenario({ mode: "next", season: rosters.season, moves: apiMoves(moves) })));
  button.disabled = false;
  button.textContent = `Simulate the ${rosters.season_label} season ▸`;
  if (!res.data) {
    mount.innerHTML = errorBanner(res.message);
    return;
  }
  state.deal.result = res.data;
  renderDealResult(mount, res.data);
}

function renderDealResult(mount, data) {
  const changed = data.teams.filter((t) => t.changed);
  const story = data.stories?.scenario?.[0];
  const delta = (value, digits, points) => `<span class="delta ${value >= 0 ? "is-up" : "is-down"}">${points ? signedPoints(value, digits) : signed(value, digits)}</span>`;
  mount.innerHTML = `
    <div class="section-title">${data.n_simulations.toLocaleString()} simulated ${escapeHtml(data.season_label)} seasons, with and without the move</div>
    <div class="table-scroll"><table class="data-table">
      <thead><tr><th scope="col">Team</th><th scope="col" class="num">Wins</th><th scope="col" class="num">Playoffs</th>
        <th scope="col" class="num">Conf. finals</th><th scope="col" class="num">Title</th></tr></thead>
      <tbody>${changed.map((t) => `<tr><th scope="row">${escapeHtml(t.team_name)}</th>
        <td class="num">${fixed(t.baseline.mean_wins)} → <strong>${fixed(t.scenario.mean_wins)}</strong> ${delta(t.delta.mean_wins, 1)}</td>
        <td class="num">${pct(t.scenario.p_made_playoffs, 0)} ${delta(t.delta.p_made_playoffs, 0, true)}</td>
        <td class="num">${pct(t.scenario.p_won_conf_semifinals, 1)} ${delta(t.delta.p_won_conf_semifinals, 1, true)}</td>
        <td class="num">${pct(t.scenario.p_champion, 1)} ${delta(t.delta.p_champion, 1, true)}</td></tr>`).join("")}</tbody>
    </table></div>
    ${story ? `<p class="table-note">In one sampled season, the ${escapeHtml(story.finals.champion.team_name)} won the title.
      The Sandbox shows full brackets and the whole league table.</p>` : ""}
    <p class="text-muted" style="font-size:0.82rem;">A what-if simulation with low confidence: box scores capture offense far better than defense, and
      only about ${Math.round(data.method.realization_factor * 100)}% of a box-score-valued change showed up in real team results.</p>`;
}
