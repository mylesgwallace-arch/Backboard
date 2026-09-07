// components/teamSelect.js — a reusable <select> populated from the
// backend's list_teams tool. Any future page (Team Explorer, Season
// Simulator, Player Impact, ...) can reuse this instead of re-fetching or
// re-rendering team options.

import { listTeams } from "../api.js";

let cachedTeamsPromise = null;

/** Fetch (and cache in-memory) the 30 current franchises from the backend. */
export function fetchTeams() {
  if (!cachedTeamsPromise) {
    cachedTeamsPromise = listTeams().then((res) => {
      if (!res.ok || res.data?.status !== "success") {
        cachedTeamsPromise = null; // allow retry on failure
        const message =
          res.data?.error?.message || "Failed to load the team list.";
        throw new Error(message);
      }
      return res.data.data.teams;
    });
  }
  return cachedTeamsPromise;
}

/**
 * Build a labeled team <select> field inside `container`.
 * Returns { root, selectEl, setTeams(teams) } so the caller can wire
 * change handlers and read/set the current value.
 */
export function createTeamSelect({ id, labelText, placeholder }) {
  const root = document.createElement("div");
  root.className = "field";
  root.innerHTML = `
    <label for="${id}">${labelText}</label>
    <div class="select-wrap">
      <select class="select" id="${id}">
        <option value="">${placeholder || "Select a team…"}</option>
      </select>
    </div>
  `;
  const selectEl = root.querySelector("select");

  function setTeams(teams) {
    const current = selectEl.value;
    selectEl.innerHTML = `<option value="">${placeholder || "Select a team…"}</option>`;
    teams
      .slice()
      .sort((a, b) => a.full_name.localeCompare(b.full_name))
      .forEach((team) => {
        const option = document.createElement("option");
        option.value = String(team.team_id);
        option.textContent = team.full_name;
        selectEl.appendChild(option);
      });
    if (current) selectEl.value = current;
  }

  return { root, selectEl, setTeams };
}
