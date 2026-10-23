// components/embedDialog.js — "Embed": a live preview of a Backboard widget
// and the <iframe> snippet to paste into another site (see web/js/embed.js).

import { toast } from "./toast.js";
import { escapeHtml } from "./format.js";

const HEIGHTS = { team: 210, game: 230 };

export function embedButton(id, label = "Embed") {
  return `<button type="button" class="link-btn" id="${id}" title="Put this on another site">${label}</button>`;
}

export function openEmbedDialog(kind, params, title) {
  const query = new URLSearchParams(params);
  const dialog = document.createElement("dialog");
  dialog.className = "sheet-dialog embed-dialog";
  const snippet = (theme) => {
    const q = new URLSearchParams(query);
    if (theme === "light") q.set("theme", "light");
    return `<iframe src="${location.origin}/embed/${kind}?${q}" title="${escapeHtml(title)} (Backboard)" width="360" height="${HEIGHTS[kind]}" style="border:0;max-width:100%" loading="lazy"></iframe>`;
  };
  dialog.innerHTML = `
    <form method="dialog" class="card">
      <h2>Embed: ${escapeHtml(title)}</h2>
      <p class="text-muted">A live widget for another page. It updates itself and links back here. The host page must be able to reach this server.</p>
      <div class="segmented" role="group" aria-label="Widget background">
        <button type="button" class="seg is-on" data-theme="dark" aria-pressed="true">Dark page</button>
        <button type="button" class="seg" data-theme="light" aria-pressed="false">Light page</button>
      </div>
      <div class="embed-preview" id="embed-preview"></div>
      <label class="sr-only" for="embed-code">Embed code</label>
      <textarea class="input embed-code" id="embed-code" rows="4" readonly></textarea>
      <div class="predict-actions"><button type="button" class="btn" id="embed-copy">Copy code</button><button class="btn btn-secondary" value="close">Done</button></div>
    </form>`;
  document.body.appendChild(dialog);
  const code = dialog.querySelector("#embed-code");
  const preview = dialog.querySelector("#embed-preview");
  const paint = (theme) => {
    code.value = snippet(theme);
    preview.innerHTML = snippet(theme).replace(' loading="lazy"', "");
    preview.classList.toggle("is-light", theme === "light");
    dialog.querySelectorAll("[data-theme]").forEach((b) => {
      b.classList.toggle("is-on", b.dataset.theme === theme);
      b.setAttribute("aria-pressed", String(b.dataset.theme === theme));
    });
  };
  dialog.querySelectorAll("[data-theme]").forEach((b) => b.addEventListener("click", () => paint(b.dataset.theme)));
  dialog.querySelector("#embed-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(code.value);
      toast("Embed code copied.");
    } catch (err) {
      code.select();
      toast("Select the code and copy it (Ctrl C).", { tone: "warn" });
    }
  });
  dialog.addEventListener("close", () => dialog.remove());
  paint("dark");
  dialog.showModal();
}
