// components/seedProbabilities.js — renders a team's projected-seed
// probability breakdown (p_seed_1..N + out_of_playoffs_probability) as a
// list of labeled horizontal bars. Shared between Team Explorer's
// Projections tab and the Season Simulator's team drill-down so the same
// visual pattern isn't duplicated across pages.

export function renderSeedProbabilityBars(projectionRow, { barColor = "var(--accent)" } = {}) {
  const seedRows = Object.keys(projectionRow)
    .filter((key) => key.startsWith("p_seed_"))
    .sort()
    .map((key) => ({
      label: `Seed ${key.replace("p_seed_", "")}`,
      value: projectionRow[key],
      modifier: "",
    }));
  if (projectionRow.out_of_playoffs_probability != null) {
    seedRows.push({
      label: "Out of playoffs",
      value: projectionRow.out_of_playoffs_probability,
      modifier: " compare-row--out",
    });
  }

  return seedRows
    .map(
      (row) => `
        <div class="compare-row seed-row${row.modifier}">
          <div class="compare-row-head">
            <span class="metric-name">${row.label}</span>
            <span>${(row.value * 100).toFixed(1)}%</span>
          </div>
          <div class="compare-track">
            <div class="compare-fill home" style="width:${row.value * 100}%; background:${barColor};"></div>
          </div>
        </div>
      `
    )
    .join("");
}
