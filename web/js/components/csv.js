// components/csv.js — download what a table shows as a CSV file. The rows are
// the same values the page rendered (from the tool envelope), nothing more.

function cell(value) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv(rows) {
  return rows.map((row) => row.map(cell).join(",")).join("\r\n") + "\r\n";
}

/** A <table>'s header and body rows (visible text) as arrays. */
export function tableRows(table) {
  const rows = [];
  table.querySelectorAll("tr").forEach((tr) => {
    if (tr.closest("thead") || tr.querySelector("td, th")) {
      if (tr.classList.contains("standings-detail-row")) return;
      rows.push([...tr.querySelectorAll("th, td")].map((c) => c.dataset.csv ?? c.textContent));
    }
  });
  return rows;
}

export function downloadCsv(filename, rows) {
  const blob = new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename.endsWith(".csv") ? filename : `${filename}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Markup for a small "Download CSV" button; wire it with `wireCsv`. */
export function csvButton(id, label = "CSV") {
  return `<button type="button" class="btn btn-secondary csv-btn" id="${id}" title="Download this table as a CSV file">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12m0 0-5-5m5 5 5-5M4 19h16"/></svg>${label}</button>`;
}

/** Wire `button` to download `table` (an element or a getter) as `filename`. */
export function wireCsv(button, table, filename) {
  button?.addEventListener("click", () => {
    const element = typeof table === "function" ? table() : table;
    if (element) downloadCsv(filename, tableRows(element));
  });
}
