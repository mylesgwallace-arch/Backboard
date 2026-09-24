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
  "What is the predicted score of Celtics vs Lakers, and who wins?",
  "Who is most likely to win the 2025-26 title, and how reliable are the title odds?",
  "Compare the Knicks and Celtics: 2025 records, head-to-head, and who is favored if the Knicks host?",
  "How many points per game did Stephen Curry average in 2015-16?",
  "Who did the Philadelphia 76ers add this offseason?",
  "How good is the prediction model overall?",
];

// Module-level (not per-render) so the conversation survives navigating away
// and back, the same way a normal chat page would. `context` carries the last
// answer's teams/players so follow-ups ("that matchup") resolve.
const state = {
  messages: [], // { role, text, envelopes?, plan?, status?, grounding?, mode?, fallback? }
  pending: false,
  context: null,
};

export function render(container) {
  container.innerHTML = `
    <div class="card fade-in assistant-card">
      <div class="card-header">
        <div>
          <h2>NBA Assistant</h2>
          <p class="card-subtitle">
            Each question becomes one or more calls to the same analytics tools as the
            structured pages; the answer keeps database facts, model output and uncertainty
            apart, and every number in it is checked against the tool results.
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
      const res = await ask(question, { context: state.context });
      const data = res.data || {};
      if (data.context && (data.context.teams?.length || data.context.players?.length)) {
        state.context = data.context;
      }
      state.messages.push({
        role: "assistant",
        text: data.answer || "No answer was returned.",
        plan: data.plan || (data.tool ? [{ tool: data.tool, parameters: data.parameters }] : []),
        status: data.status,
        envelopes: data.envelopes || (data.envelope ? [data.envelope] : []),
        grounding: data.grounding,
        mode: data.mode,
        fallback: data.fallback_reason,
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
    message.status === "success" ? "positive" : message.status === "unavailable" || message.status === "partial" ? "accent" : "";
  const statusLabel = message.status ? message.status : "error";
  const envelopeId = `envelope-${index}`;
  const envelopes = message.envelopes || [];
  const grounding = message.grounding;
  const groundingBadge = grounding && grounding.numbers_checked
    ? grounding.grounded
      ? `<span class="badge positive" title="Every number in this answer was found in the tool results.">${grounding.numbers_checked} numbers traced to tool results</span>`
      : `<span class="badge" title="These numbers were not found in any tool result.">Untraced numbers: ${escapeText(grounding.unsupported.join(", "))}</span>`
    : "";

  return `
    <div class="chat-bubble chat-bubble--assistant">
      ${renderAnswerText(message.text)}
      ${message.fallback ? `<p class="text-muted" style="font-size:0.8rem;">${escapeText(message.fallback)}</p>` : ""}
      <div class="chat-bubble-meta">
        ${(message.plan || []).map((step) => `<span class="chip"><strong>${escapeText(step.tool)}</strong></span>`).join("")}
        ${message.status ? `<span class="badge ${statusBadgeClass}">${escapeText(statusLabel)}</span>` : ""}
        ${groundingBadge}
        ${
          envelopes.length
            ? `<button type="button" class="link-btn" data-toggle-envelope="${envelopeId}">How was this produced?</button>`
            : ""
        }
      </div>
      ${envelopes.length ? `<div class="chat-envelope" id="${envelopeId}" hidden>${envelopes.map(renderEnvelopeDetails).join("<hr>")}</div>` : ""}
    </div>
  `;
}

// Answers come as "Section title:\n- line\n- line" blocks; render each block
// as a heading plus a list so facts, model output and uncertainty stay apart.
function renderAnswerText(text) {
  return String(text)
    .split(/\n\s*\n/)
    .map((block) => {
      const lines = block.split("\n");
      const bullets = lines.filter((line) => line.startsWith("- "));
      if (bullets.length && lines[0].endsWith(":")) {
        return `<p><strong>${escapeText(lines[0].slice(0, -1))}</strong></p>
          <ul class="plain-list">${bullets.map((line) => `<li>${escapeText(line.slice(2))}</li>`).join("")}</ul>`;
      }
      return `<p>${escapeText(block)}</p>`;
    })
    .join("");
}

function renderEnvelopeDetails(envelope) {
  const assumptions = envelope.assumptions || [];
  const limitations = envelope.limitations || [];
  return `
    <div>
      ${envelope.tool ? `<p class="chat-envelope-row"><strong>Tool:</strong> ${escapeText(envelope.tool)} (${escapeText(envelope.status)})</p>` : ""}
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
