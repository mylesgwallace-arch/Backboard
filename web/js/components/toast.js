// components/toast.js — a short-lived torn-paper note in the corner ("Link
// copied", "Saved to the gallery"). Announced politely to screen readers.

let shelf = null;

function getShelf() {
  if (!shelf) {
    shelf = document.createElement("div");
    shelf.className = "toast-shelf";
    shelf.setAttribute("role", "status");
    shelf.setAttribute("aria-live", "polite");
    document.body.appendChild(shelf);
  }
  return shelf;
}

/** Show `message` for a few seconds. tone: "ok" | "warn". */
export function toast(message, { tone = "ok", duration = 2600 } = {}) {
  const note = document.createElement("div");
  note.className = `toast toast--${tone}`;
  note.textContent = message;
  getShelf().appendChild(note);
  requestAnimationFrame(() => note.classList.add("is-in"));
  setTimeout(() => {
    note.classList.remove("is-in");
    setTimeout(() => note.remove(), 400);
  }, duration);
}

/** Copy `text` to the clipboard and say so. */
export async function copyText(text, label = "Link copied") {
  try {
    await navigator.clipboard.writeText(text);
    toast(label);
  } catch (err) {
    window.prompt("Copy this:", text);
  }
}
