// tests/e2e/smoke.cjs — load every page of a running Backboard server in
// Chromium (desktop and phone widths) and fail on a script error or an
// error banner. CI runs it against the generated test league:
//
//   python src/api.py &   # then, once /health answers:
//   node tests/e2e/smoke.cjs http://127.0.0.1:8000
//
// Needs the `playwright` package (npm install --no-save playwright) and a
// Chromium it can launch. Requests to other hosts (fonts, headshots, ESPN)
// are blocked so the run does not depend on the network.

const { chromium } = require("playwright");

const BASE = (process.argv[2] || "http://127.0.0.1:8000").replace(/\/$/, "");
const EXTRA = [
  "/players?id=__first_player__", "/teams?team=1610612738", "/head-to-head?a=1610612738&b=1610612752",
  "/matchups?home=1610612738&away=1610612752", "/embed/team?team=1610612738",
  "/embed/game?home=1610612738&away=1610612752",
];
const WIDTHS = [[1366, 900], [390, 844]];
const SETTLE_MS = Number(process.env.SMOKE_WAIT || 3500);

async function pages() {
  const xml = await (await fetch(`${BASE}/sitemap.xml`)).text();
  const paths = [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => new URL(m[1]).pathname);
  return [...new Set(paths), ...EXTRA];
}

async function firstPlayer() {
  const res = await fetch(`${BASE}/tools/player_cards`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ parameters: {} }),
  });
  return (await res.json()).data?.cards?.[0]?.person_id;
}

(async () => {
  const player = await firstPlayer();
  const routes = (await pages()).map((path) => path.replace("__first_player__", String(player)));
  const browser = await chromium.launch();
  const failures = [];
  for (const [width, height] of WIDTHS) {
    const context = await browser.newContext({ viewport: { width, height }, serviceWorkers: "block" });
    await context.route((url) => !url.href.startsWith(BASE), (route) => route.abort());
    for (const path of routes) {
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("console", (message) => {
        if (message.type() === "error" && !/Failed to load resource|net::ERR_/.test(message.text())) errors.push(message.text());
      });
      await page.goto(BASE + path, { waitUntil: "load" });
      await page.waitForTimeout(SETTLE_MS);
      // The stale-data notice is expected on the test league (it never ran a live refresh).
      const banners = (await page.$$eval(".error-banner", (els) => els.map((el) => el.textContent.trim().slice(0, 200))))
        .filter((text) => !/^Data may be out of date/.test(text));
      const problems = [...errors, ...banners.map((b) => `banner: ${b}`)];
      console.log(`${problems.length ? "FAIL" : "ok  "} ${width}px ${path}${problems.length ? `\n     ${problems.join("\n     ")}` : ""}`);
      if (problems.length) failures.push(path);
      await page.close();
    }
    await context.close();
  }
  await browser.close();
  if (failures.length) {
    console.error(`${failures.length} page load(s) failed.`);
    process.exit(1);
  }
  console.log(`All ${routes.length * WIDTHS.length} page loads clean.`);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
