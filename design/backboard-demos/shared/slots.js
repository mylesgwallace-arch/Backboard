/* slots.js: Higgsfield placeholder slots.
 *
 * Wherever a page would use a generated image or clip, it carries an element
 * like  <figure class="hf-slot" data-hf="the prompt" data-hf-size="1600x1000">
 * with a hand-built stand-in already inside it. Nothing is generated yet.
 * Append ?slots (or #slots, which also survives inside the artifact viewer) to
 * any page URL to outline every slot and print its prompt.
 * `window.HF_SLOTS()` returns them all as { id, size, prompt } for the README.
 */
(function () {
  "use strict";
  if (/[?&]slots\b/.test(location.search) || location.hash === "#slots") {
    document.documentElement.classList.add("show-slots");
  }
  /* Back to the gallery. Every page loads this file, so the button lives here. */
  const back = document.createElement("a");
  back.href = "../index.html";
  back.textContent = "All styles";
  back.setAttribute("aria-label", "Back to all styles");
  back.style.cssText = "position:fixed;z-index:2147483000;right:12px;bottom:56px;display:inline-flex;align-items:center;min-height:44px;padding:0 14px 0 12px;border-radius:999px;background:#14161a;color:#fff;font:600 13px/1 system-ui,-apple-system,'Segoe UI',sans-serif;text-decoration:none;box-shadow:0 0 0 2px rgba(255,255,255,.9),0 6px 18px rgba(0,0,0,.35)";
  back.insertAdjacentHTML("afterbegin", '<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" style="margin-right:6px"><path d="M15 5l-7 7 7 7" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="square"/></svg>');
  document.body.appendChild(back);

  const slots = Array.from(document.querySelectorAll("[data-hf]"));
  slots.forEach((el, i) => {
    el.classList.add("hf-slot");
    el.dataset.hfId = el.dataset.hfId || `slot-${i + 1}`;
    const label = document.createElement("span");
    label.className = "hf-label";
    label.textContent = `Higgsfield ${el.dataset.hfSize || ""}: ${el.dataset.hf}`;
    el.appendChild(label);
  });
  window.HF_SLOTS = () => slots.map((el) => ({
    id: el.dataset.hfId, size: el.dataset.hfSize || "", prompt: el.dataset.hf,
  }));
})();
