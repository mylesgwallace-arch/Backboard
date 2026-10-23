// pages/lottery.js — Draft Lottery: every lottery team's chance at each pick
// (draft_lottery: exact 2019-rules odds over the final or projected
// standings), and a lottery machine that runs the real draw on demand:
// fourteen balls tumble, four teams come out weighted by their combinations,
// and the picks are revealed from 14 up to 1 the way the NBA does it.
//
// The odds come from the tool. The draw itself runs here, in the browser,
// with the tool's combination counts and the browser's crypto randomness;
// it is a toy, the table is the forecast.

import { getDraftLottery } from "../api.js";
import { teamColor } from "../teamColors.js";
import { readableInk } from "../colorInk.js";
import { reduceMotion } from "../components/motion.js";
import { errorBanner, escapeHtml, fixed, safe, seasonLabel, unwrap } from "../components/format.js";

export const meta = {
  title: "Draft Lottery",
  subtitle: "Who could land the first pick: the odds for every lottery team, and a machine to run the draw yourself.",
};

const PICKS_DRAWN = 4;
let tally = {};

function random() {
  const buffer = new Uint32Array(1);
  crypto.getRandomValues(buffer);
  return buffer[0] / 2 ** 32;
}

/** One draw: order is worst-first; returns the 14 teams in pick order. */
export function drawLottery(order, combinations, rand = random) {
  const left = order.map((team, slot) => ({ team, slot, combos: combinations[slot] }));
  const drawn = [];
  for (let pick = 0; pick < PICKS_DRAWN; pick += 1) {
    const total = left.reduce((sum, entry) => sum + entry.combos, 0);
    let ball = rand() * total;
    const index = left.findIndex((entry) => (ball -= entry.combos) < 0);
    drawn.push(left.splice(index === -1 ? left.length - 1 : index, 1)[0]);
  }
  return [...drawn, ...left.sort((a, b) => a.slot - b.slot)];
}

export function render(container, ctx = {}) {
  const season = Number(ctx.query?.get?.("season")) || null;
  container.innerHTML = `<div id="lt-body"><div class="card"><div class="skeleton" style="height:520px;"></div></div></div>`;
  load(container, ctx, season);
}

function lotteryOrder(data) {
  // The fourteen teams most likely to be in it, worst expected record first.
  const ranked = [...data.teams].sort((a, b) => b.p_lottery - a.p_lottery).slice(0, 14);
  const expectedSlot = (t) => t.slot_distribution.reduce((sum, p, i) => sum + p * i, 0) / Math.max(t.p_lottery, 1e-9);
  return ranked.sort((a, b) => expectedSlot(a) - expectedSlot(b));
}

async function load(container, ctx, season) {
  const res = unwrap(await safe(getDraftLottery({ season, nSimulations: 1000 })));
  if (!container.isConnected) return;
  const body = container.querySelector("#lt-body");
  if (!res.data) {
    body.innerHTML = errorBanner(res.message);
    return;
  }
  const data = res.data;
  const order = lotteryOrder(data);
  tally = {};
  const mode = data.mode === "final"
    ? `Final ${seasonLabel(data.season)} standings`
    : `Projected from the ${seasonLabel(data.season)} standings on ${escapeHtml(data.as_of)} (${data.n_simulations.toLocaleString("en-US")} simulated seasons)`;
  body.innerHTML = `
    <section class="card fade-in lt-machine-card" aria-labelledby="lt-title">
      <div class="card-header"><div><p class="kicker">${data.draft_year} draft · ${mode}</p>
        <h2 class="tabloid-headline" id="lt-title">Run the lottery</h2>
        <p class="card-subtitle">${data.mode === "final" ? "The real lottery order." : "The most likely lottery order if the season ended as projected."}
          Four picks are drawn; everyone else slots in by record.</p></div></div>
      <div class="lt-stage">
        <div class="lt-machine" id="lt-machine" aria-hidden="true">
          <div class="lt-drum">${order.map((team, i) => `<span class="lt-ball" style="--i:${i}; --x:${14 + ((i * 37) % 60)}%; --y:${48 + ((i * 23) % 32)}%; --c:${teamColor(team.teamId).primary}">${i + 1}</span>`).join("")}</div>
          <div class="lt-chute"></div>
          <div class="lt-tray" id="lt-tray"></div>
        </div>
        <ol class="lt-board" id="lt-board">${order.map((team, i) => slotCard(team, i, null)).join("")}</ol>
      </div>
      <div class="lt-actions">
        <button type="button" class="btn" id="lt-draw">Draw the lottery</button>
        <span class="text-muted" id="lt-tally" aria-live="polite"></span>
      </div>
    </section>
    ${oddsTable(data)}
    <details class="card collapsible fade-in"><summary>The odds by lottery slot</summary>
      <div class="table-scroll"><table class="data-table compact-table lt-odds"><thead><tr><th>Slot</th><th class="num">Combos</th>
        ${Array.from({ length: 14 }, (_, i) => `<th class="num">${i + 1}</th>`).join("")}</tr></thead>
        <tbody>${data.slot_pick_odds.map((row, slot) => `<tr><th>${slot + 1}</th><td class="num">${data.combinations[slot]}</td>
          ${row.map((p) => heatCell(p)).join("")}</tr>`).join("")}</tbody></table></div>
      <p class="table-note">${escapeHtml(data.rules)}</p>
    </details>`;
  body.querySelector("#lt-draw").addEventListener("click", () => runDraw(body, order, data.combinations));
}

function slotCard(team, slot, result) {
  const colors = teamColor(team.teamId);
  const moved = result ? result.slot - result.pick : 0;
  return `<li class="lt-slot ${result ? "is-revealed" : ""}" style="--team:${colors.primary}; --team-ink:${readableInk(colors.primary)}">
    <span class="lt-pick">${result ? result.pick + 1 : slot + 1}</span>
    <span class="lt-team">${escapeHtml(team.teamName)}</span>
    <span class="lt-move">${result ? (moved > 0 ? `▲ ${moved}` : moved < 0 ? `▼ ${-moved}` : "—") : `${fixed(team.p_first_pick * 100, 1)}% #1`}</span>
  </li>`;
}

async function runDraw(body, order, combinations) {
  const button = body.querySelector("#lt-draw");
  const machine = body.querySelector("#lt-machine");
  const board = body.querySelector("#lt-board");
  button.disabled = true;
  const result = drawLottery(order, combinations).map((entry, pick) => ({ ...entry, pick }));
  const fast = reduceMotion();
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, fast ? 0 : ms));
  board.innerHTML = order.map((team, i) => `<li class="lt-slot is-hidden"><span class="lt-pick">${i + 1}</span><span class="lt-team">?</span><span class="lt-move"></span></li>`).join("");
  const tray = body.querySelector("#lt-tray");
  tray.innerHTML = "";
  machine.classList.add("is-spinning");
  await wait(1300);
  // The four drawn balls pop out of the chute.
  for (let pick = 0; pick < PICKS_DRAWN; pick += 1) {
    const ball = machine.querySelectorAll(".lt-ball")[result[pick].slot];
    ball.classList.add("is-drawn");
    const copy = ball.cloneNode(true);
    copy.className = "lt-ball is-tray";
    copy.title = result[pick].team.teamName;
    tray.appendChild(copy);
    await wait(650);
  }
  machine.classList.remove("is-spinning");
  // Reveal from pick 14 up to pick 1.
  const slots = [...board.children];
  for (let pick = result.length - 1; pick >= 0; pick -= 1) {
    const entry = result[pick];
    slots[pick].outerHTML = slotCard(entry.team, pick, entry);
    await wait(pick < PICKS_DRAWN ? 700 : 140);
  }
  const winner = result[0].team;
  tally[winner.teamName] = (tally[winner.teamName] || 0) + 1;
  const runs = Object.values(tally).reduce((a, b) => a + b, 0);
  body.querySelector("#lt-tally").textContent = `${winner.teamName} win it. Your draws so far: ${Object.entries(tally)
    .sort((a, b) => b[1] - a[1]).map(([name, n]) => `${name.split(" ").slice(-1)[0]} ${n}`).join(", ")} (${runs} draw${runs === 1 ? "" : "s"}).`;
  machine.querySelectorAll(".lt-ball").forEach((ball) => ball.classList.remove("is-drawn"));
  button.textContent = "Draw again";
  button.disabled = false;
}

function heatCell(p, max = 0.45) {
  const alpha = Math.min(1, (p / max) * 0.85);
  return `<td class="num lt-heat" style="--a:${alpha.toFixed(3)}">${p >= 0.005 ? Math.round(p * 100) : p > 0 ? "·" : ""}</td>`;
}

function oddsTable(data) {
  const max = Math.max(0.01, ...data.teams.flatMap((t) => t.pick_distribution));
  const rows = data.teams.map((t) => `<tr>
    <th><i class="lt-swatch" style="background:${teamColor(t.teamId).primary}"></i>${escapeHtml(t.teamName)}</th>
    <td class="num">${data.mode === "final" ? `${t.current_wins}-${t.current_losses}` : fixed(t.projected_wins, 1)}</td>
    <td class="num">${Math.round(t.p_lottery * 100)}%</td>
    ${t.pick_distribution.map((p) => heatCell(p, max)).join("")}
    <td class="num"><b>${fixed(t.p_top_four * 100, 1)}%</b></td>
    <td class="num">${fixed(t.expected_pick, 1)}</td>
  </tr>`).join("");
  return `<section class="card fade-in" aria-labelledby="lt-odds-title">
    <div class="card-header"><div><h2 id="lt-odds-title">Every team's odds, pick by pick</h2>
      <p class="card-subtitle">Percent chance of each pick. ${data.mode === "final" ? "From the final order." : "Averaged over simulated finishes, so a team can be in the lottery in some seasons and not others."}</p></div></div>
    <div class="table-scroll"><table class="data-table compact-table lt-odds sticky-first"><thead><tr><th>Team</th><th class="num">${data.mode === "final" ? "Record" : "Proj. wins"}</th><th class="num">In lottery</th>
      ${Array.from({ length: 14 }, (_, i) => `<th class="num">${i + 1}</th>`).join("")}<th class="num">Top 4</th><th class="num">Avg pick</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="table-note">${escapeHtml(data.rules)}</p>
  </section>`;
}
