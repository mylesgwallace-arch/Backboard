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
