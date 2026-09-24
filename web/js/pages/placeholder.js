// pages/placeholder.js — a "coming soon" page factory for nav sections whose
// backend tools already exist but whose structured UI hasn't been built yet.
// Keeps the sidebar/router honest about scope while making it obvious the
// architecture already supports adding these pages without a rewrite.

export function createPlaceholderPage({ title, subtitle, description, backedByTools }) {
  return {
    meta: { title, subtitle },
    render(container) {
      container.innerHTML = `
        <div class="coming-soon fade-in">
          <div class="coming-soon-sign">
            <p class="stamp stamp--tilt coming-soon-stamp">Coming soon</p>
            <h2>${title}</h2>
            <p>${description}</p>
            ${
              backedByTools && backedByTools.length
                ? `
                  <p class="mt-1 coming-soon-note">
                    Backend tools already available for this page:
                  </p>
                  <ul>
                    ${backedByTools.map((tool) => `<li><code>${tool}</code></li>`).join("")}
                  </ul>
                `
                : ""
            }
          </div>
        </div>
      `;
    },
  };
}
