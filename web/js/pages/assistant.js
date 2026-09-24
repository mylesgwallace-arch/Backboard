// pages/assistant.js — a dedicated chat interface over the deterministic
// natural-language layer (src/assistant.py via POST /ask). This is a UI-only
// addition: it calls the exact same `ask()` function the Dashboard's
// "Advanced" panel already used, so no backend/tool-routing logic is
// duplicated. Every answer is rendered with the tool, status, and the
// model/assumptions/limitations the envelope actually returned — per the
// project's transparency requirement, nothing is summarized or softened.

import { ask } from "../api.js";

export const meta = {
  title: "Assistant",
  subtitle: "Ask a plain-language NBA question; every answer traces to a tool call.",
};

const EXAMPLE_QUESTIONS = [
  "Who is favored in Celtics vs Lakers?",
  "What are the projected playoff teams?",
  "What is Boston's projected wins and seed this season?",
  "What was OKC's record in the 2025 season?",
  "What is the head-to-head record between Boston and LA Lakers?",
];

// Module-level (not per-render) so the conversation survives navigating away
// and back, the same way a normal chat page would.
const state = {
  messages: [], // { role: "user" | "assistant", text, envelope?, tool?, status? }
  pending: false,
};

export function render(container) {
  container.innerHTML = `
    <div class="card fade-in assistant-card">
      <div class="card-header">
        <div>
          <h2>NBA Assistant</h2>
          <p class="card-subtitle">
            Questions are routed deterministically to the same analytics tools as the
            structured pages &mdash; the assistant explains results, it never invents them.
          </p>
        </div>
      </div>

      <div class="chat-log" id="chat-log" role="log" aria-live="polite"></div>

      <div class="chat-examples" id="chat-examples">
        ${EXAMPLE_QUESTIONS.map(
          (q) => `<button type="button" class="chip chip-btn" data-example="${escapeAttr(q)}">${escapeText(q)}</button>`
        ).join("")}
      </div>

      <form class="chat-composer" id="chat-form">
        <textarea
          class="input chat-input"
          id="chat-input"
          rows="1"
          placeholder="Ask about a matchup, a team's projected wins, the playoff field, a record, or a head-to-head series..."
          aria-label="Ask a question"
        ></textarea>
        <button class="btn" type="submit" id="chat-send">Ask</button>
      </form>
    </div>
  `;

  const log = container.querySelector("#chat-log");
  const form = container.querySelector("#chat-form");
  const input = container.querySelector("#chat-input");
  const sendBtn = container.querySelector("#chat-send");
  const examples = container.querySelector("#chat-examples");

  renderLog(log);

  examples.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-example]");
    if (!btn) return;
    input.value = btn.dataset.example;
    input.focus();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question || state.pending) return;

    state.messages.push({ role: "user", text: question });
    input.value = "";
    state.pending = true;
    setComposerDisabled(sendBtn, input, true);
    renderLog(log);

    try {
      const res = await ask(question);
      const data = res.data || {};
      state.messages.push({
        role: "assistant",
        text: data.answer || "No answer was returned.",
        tool: data.tool,
        status: data.status,
        envelope: data.envelope,
      });
    } catch (err) {
      state.messages.push({
        role: "assistant",
        text: `The assistant is unreachable: ${err.message}`,
        status: "error",
      });
    } finally {
      state.pending = false;
      setComposerDisabled(sendBtn, input, false);
      renderLog(log);
      input.focus();
    }
  });

  // Enter sends, Shift+Enter inserts a newline.
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
}

function setComposerDisabled(sendBtn, input, disabled) {
  sendBtn.disabled = disabled;
  input.disabled = disabled;
  sendBtn.innerHTML = disabled ? '<span class="spinner spinner-dark"></span> Asking&hellip;' : "Ask";
}

function renderLog(log) {
  if (state.messages.length === 0) {
    log.innerHTML = `
      <div class="empty-state">
        Ask a question above, or pick one of the examples to see how the assistant
        traces an answer back to a specific analytics tool.
      </div>
    `;
    scrollToBottom(log);
    return;
  }

  log.innerHTML = state.messages.map(renderMessage).join("");

  if (state.pending) {
    log.insertAdjacentHTML(
      "beforeend",
      `<div class="chat-bubble chat-bubble--assistant chat-bubble--pending">
        <span class="spinner"></span> Thinking&hellip;
      </div>`
    );
  }

  log.querySelectorAll("[data-toggle-envelope]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const panel = log.querySelector(`#${btn.dataset.toggleEnvelope}`);
      if (!panel) return;
      const open = panel.hasAttribute("hidden");
      panel.toggleAttribute("hidden", !open);
      btn.textContent = open ? "Hide details" : "How was this produced?";
    });
  });

  scrollToBottom(log);
}

function scrollToBottom(log) {
  log.scrollTop = log.scrollHeight;
}

function renderMessage(message, index) {
  if (message.role === "user") {
    return `
      <div class="chat-bubble chat-bubble--user">
        <p>${escapeText(message.text)}</p>
      </div>
    `;
  }

  const statusBadgeClass =
    message.status === "success" ? "positive" : message.status === "unavailable" ? "accent" : "";
  const statusLabel = message.status ? message.status : "error";
  const envelopeId = `envelope-${index}`;

  return `
    <div class="chat-bubble chat-bubble--assistant">
      <p>${escapeText(message.text)}</p>
      <div class="chat-bubble-meta">
        ${message.tool ? `<span class="chip"><strong>${escapeText(message.tool)}</strong></span>` : ""}
        ${message.status ? `<span class="badge ${statusBadgeClass}">${escapeText(statusLabel)}</span>` : ""}
        ${
          message.envelope
            ? `<button type="button" class="link-btn" data-toggle-envelope="${envelopeId}">How was this produced?</button>`
            : ""
        }
      </div>
      ${message.envelope ? renderEnvelopeDetails(message.envelope, envelopeId) : ""}
    </div>
  `;
}

function renderEnvelopeDetails(envelope, envelopeId) {
  const assumptions = envelope.assumptions || [];
  const limitations = envelope.limitations || [];
  return `
    <div class="chat-envelope" id="${envelopeId}" hidden>
      ${envelope.model ? `<p class="chat-envelope-row"><strong>Model:</strong> ${escapeText(envelope.model)}</p>` : ""}
      ${envelope.operation ? `<p class="chat-envelope-row"><strong>Operation:</strong> ${escapeText(envelope.operation)}</p>` : ""}
      ${
        assumptions.length
          ? `<p class="chat-envelope-row"><strong>Assumptions:</strong></p><ul>${assumptions
              .map((a) => `<li>${escapeText(a)}</li>`)
              .join("")}</ul>`
          : ""
      }
      ${
        limitations.length
          ? `<p class="chat-envelope-row"><strong>Limitations:</strong></p><ul>${limitations
              .map((l) => `<li>${escapeText(l)}</li>`)
              .join("")}</ul>`
          : ""
      }
      <pre class="output-pane mt-1">${escapeText(JSON.stringify(envelope, null, 2))}</pre>
    </div>
  `;
}

function escapeText(value) {
  return String(value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function escapeAttr(value) {
  return escapeText(value).replace(/`/g, "&#96;");
}
