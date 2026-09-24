# Blacktop Tabloid — design brief

The visual system for **Backboard** (the app was branded "NBA Sports AI"
when revs 1 and 2 were written; "Blacktop Tabloid" is the name of the look,
not the product).

Status: **rev 2, implemented**. Rev 1 was committed before any CSS was
written. Section 8 records the self-critique that produced rev 2. Section 9
lists what the implementation settled, and section 10 lists the deviations.
Where a later section differs from an earlier one, the later section wins.

## 1. The idea in one line

The app is **a tabloid's back pages glued to a playground court**: the shell
(navigation, mastheads, hero moments, alerts) is loud and made of materials
(asphalt, chain-link fence, spray paint, torn newsprint, rubber stamps). The
data inside is set like a box-score page in a newspaper: a clean grid with
crisp numerals.

## 2. Stylesheet approach

* **Rewrite `web/styles.css` in place** instead of adding a swappable theme
  file. `src/api.py` and `tests/test_api.py` already serve and check
  `/styles.css`, and the page modules use inline `var(--…)` references, so one
  stylesheet with the **same custom-property names** (`--bg`, `--bg-elevated`,
  `--text-primary`, `--text-secondary`, `--text-muted`, `--accent`,
  `--positive`, `--warning`, `--border`, …) is the smallest change that can't
  break anything. The old "calm SaaS" theme stays in git history.
* New tokens are added next to the legacy names. The legacy names become
  aliases that point at the new values.
* Google Fonts load through a `<link>` in `index.html`. Every family has a
  system fallback stack, so the layout holds if the fonts never arrive.

## 3. Color tokens

All ratios were computed with a script (WCAG 2.x relative luminance). None of
them were estimated by eye.

### Ground (asphalt & concrete)

| Token | Hex | Job |
|---|---|---|
| `--asphalt-950` | `#0f1011` | Page ground (`--bg`) |
| `--asphalt-900` | `#18191b` | Panels, sidebar (`--bg-elevated`) |
| `--asphalt-800` | `#212326` | Inputs, stat tiles, table heads (`--bg-elevated-2`) |
| `--asphalt-700` | `#2b2e32` | Hover (`--bg-hover`) |
| `--concrete-600` | `#3b3f44` | Hairlines and dividers (`--border`) |
| `--concrete-400` | `#7a7f86` | Control boundaries (`--border-strong`): 3.91 on 800, 4.36 on 900 |

### Chalk & ink (text)

| Token | Hex | On | Ratio |
|---|---|---|---|
| `--chalk` (`--text-primary`) | `#f4f1ea` | asphalt 950 / 900 / 800 / 700 | 16.9 / 15.6 / 14.0 / 12.1 |
| `--chalk-dim` (`--text-secondary`) | `#c2bdb2` | 950 / 900 / 800 / 700 | 10.2 / 9.4 / 8.4 / 7.3 |
| `--chalk-faint` (`--text-muted`) | `#9d998f` | 950 / 900 / 800 / 700 | 6.7 / 6.2 / 5.5 / 4.8 |
| `--paper` | `#ebe5d5` | newsprint clipping surface | — |
| `--paper-shade` | `#dcd4c0` | second paper tone | — |
| `--ink` | `#141312` | paper / paper-shade | 14.8 / 12.6 |
| `--ink-soft` | `#4f4a42` | paper / paper-shade | 7.0 / 6.0 |
| `--ink-red` | `#b3141a` | paper / paper-shade (stamps, scrawls, teaser kickers) | 5.5 / 4.7 |

### Spot colors (layered on top of team colors, never replacing them)

| Token | Hex | Job | Contrast notes |
|---|---|---|---|
| `--spray-orange` (`--accent`) | `#ff5b14` | Primary spray paint: buttons, the active nav slab, drips | ink on it 5.97; as text on 950 / 900 / 800 6.1 / 5.7 / 5.1 (never as text on 700) |
| `--highlighter` (`--warning`) | `#ffe03d` | Tabloid yellow: headline highlight boxes, focus ring, warnings | ink on it 14.1; on 900 13.4 |
| `--chain-silver` | `#b7bdc3` | Chain-link wire, fence-post rail | 9.3 on 900 |
| `--court-green` (`--positive`) | `#4fd887` | Positive signal | 9.6 on 900; ink on it 10.2 |
| `--signal-red` (`--negative`) | `#ff4b3e` | Negative signal | 5.3 on 900 |

Orange on paper is only 2.47:1. **Orange is never a meaningful graphic or
text color on paper.** On paper, stamps use `--ink` or `--ink-red`.

### Team colors

`web/js/teamColors.js` stays the only source of team colors. Two rules apply:

1. **Text on a team color is chosen by computed contrast.** A new helper,
   `web/js/colorInk.js` `readableInk(bg)`, walks the ladder
   `--ink → --chalk → #000 → #fff` and returns the first that reaches 4.5:1.
   Across all 60 primary and secondary colors, the lowest result is **4.55:1**
   (Thunder `#007AC1` with black).
2. **Clash, don't blend.** When two teams on screen have look-alike
   primaries (perceptual distance ΔE (CIE76) < 20, for example
   Bulls/Raptors/Rockets `#CE1141` or Knicks/76ers `#006BB6`), the second
   team switches to its own secondary color from `teamColors.js`. No colors
   are invented. (Rev 1 said "contrast < 1.3:1"; see section 10.)

## 4. Type

| Role | Family (Google Fonts) | Fallback stack | Where |
|---|---|---|---|
| Headline | **Anton** | Impact, Haettenschweiler, "Arial Narrow Bold", "Franklin Gothic Heavy", "Liberation Sans Narrow", sans-serif | Mastheads, card titles, tabloid headlines |
| Stencil | **Big Shoulders Stencil Display** 800/900 | "Stencil Std", Stencil, Anton, Impact, sans-serif | Nav section label, stamps, kickers |
| Marker | **Permanent Marker** | "Marker Felt", "Segoe Print", "Bradley Hand", Anton, sans-serif | The scrawled word in every tabloid headline ("over", "on top", "alert!") and graffiti callouts. Never numbers |
| Workhorse | **Barlow** 400–700 | Inter, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Liberation Sans", sans-serif | All body text and **all numbers** (`tabular-nums`) |
| Labels | **Barlow Condensed** 700–800 | "Arial Narrow", "Roboto Condensed", Barlow, sans-serif | Field labels, nav items, table heads, badges |

The sizes clash on purpose: the mega masthead is
`clamp(2.75rem, 1.6rem + 4.2vw, 5.5rem)` and sits right next to 0.72rem
stencil kickers. Body text is 15px, up from 14px.

## 5. Texture recipes (inline CSS/SVG only, no image files)

| Texture | Recipe | Opacity cap |
|---|---|---|
| **Asphalt grain** | SVG `feTurbulence` fractal noise, alpha-thresholded into sparse light and dark aggregate specks, tiled 200px, used as a data-URI token `--tx-grain` | Page ground ≤ 0.18. Data panels 0 |
| **Halftone** | Two offset `radial-gradient` dot grids (a 45° screen), `--tx-halftone`, faded out by a `mask-image` gradient | ≤ 0.22, only in corners away from text, and **never behind numbers** |
| **Chain-link** | 28px SVG diamond tile: a wire stroke, a shadow stroke and a highlight stroke (`--tx-chainlink`) | 0.28 on the rail; nav labels sit on solid sign plates |
| **Torn paper edge** | SVG jagged-polygon **mask** (`--tx-torn-top` / `--tx-torn-bottom`) on `::before`/`::after` strips filled with the paper color, so content and focus rings are never clipped | n/a (edge only) |
| **Spray-paint drip** | SVG mask of a solid band with rounded drips and overspray specks (`--tx-drip`), on a `::after` strip below mastheads and alert frames | n/a (edge only) |
| **Chalk line / worn paint** | `repeating-linear-gradient` dashes plus a noise **wear mask** (`--tx-wear`) that erodes the stroke | Dividers only |
| **Court markings** | SVG lane, free-throw circle and 3-pt arc in chalk strokes (`--tx-court`) behind the masthead | ≤ 0.10 |
| **Caution tape** | `repeating-linear-gradient(-45deg, highlighter, ink)` | Error/alert frames only |

## 6. Loud shell, calm data (the rules)

1. **Numbers are sacred.** Numeric data is never rotated, skewed, halftoned,
   misregistered, set in marker or stencil faces, or placed over a texture
   stronger than 0.06. Numbers use Barlow with `tabular-nums`. The one
   exception is the hero win-probability stamp, which uses Anton, upright, ink
   on a flat paper plate.
2. **Frames are loud, content is calm.** Texture, tilt and misregistration
   live on frames (`::before`/`::after`, borders, header strips), never on the
   element that holds the text.
3. **Only wordy stamps tilt**, at most 4°, and never when they contain a
   number.
4. **One hero per page.** Each page gets at most one full tabloid treatment
   (headline of the day, the prediction clipping, the team slab). Everything
   else is a calm "data slab".
5. **Team colors are slabs, not hairlines.** They fill hero halves and
   headers, with computed ink on top. Inside data they appear only as bar
   fills, with a chalk inset outline so black or navy teams stay visible on
   asphalt.
6. **Motion is a thunk, not a show.** Stamps "thunk" in once. Everything,
   including the card fade-in and the skeleton shimmer, is switched off under
   `prefers-reduced-motion`.
7. **Every interactive element gets a visible `:focus-visible`**: a 3px
   highlighter outline on asphalt, and ink on paper and team slabs.
   Tap targets are ≥ 44px.

## 7. Per-component plan

* **App shell / sidebar → chain-link rail.** An asphalt rail with a chain-link
  overlay and a silver fence-post edge. The brand is a backboard-and-rim SVG
  plus the Anton wordmark. Nav items become metal "signs" zip-tied to the
  fence (solid plates, condensed caps). The **active item is a spray-orange
  stencil slab** with rough overspray edges, ink text and a stencil ▶ marker.
  Below 860px the rail turns into a top bar with a horizontally scrolling nav
  strip. Today the 252px sidebar overflows every phone view.
* **Topbar → masthead.** A stencil kicker line, a mega Anton title with an
  offset orange misregistration ghost, and the subtitle in Barlow. Faint court
  markings sit behind it, and an orange **drip edge** runs along the bottom.
* **`.card` → data slab (rev 2).** The slab reads as a *painted court panel*,
  not a dark card. It has square corners and flat asphalt-900 inside, and its
  boundary is a 2px worn chalk-paint line (a court boundary, eroded with
  `--tx-wear`). The header is a stencil title strip: an Anton title with a
  short spray-orange tag at the left edge, sitting on a faded painted
  divider. The black offset shadow from rev 1 is gone because it is
  invisible on asphalt.
* **`.clipping` (new) → tabloid clipping.** Newsprint paper, ink text, torn top
  and bottom edges, drop shadow. Used for heroes only.
* **Matchup prediction (`matchups.js`, `probabilityBar.js`).** The whole result
  becomes one clipping with a derived **tabloid headline** ("THUNDER OVER
  WIZARDS"). The two teams are **clashing full-color halves** split by a
  diagonal with a "VS" splat. The win probability is a **stamped percentage**
  (Anton, ink on a paper plate, with a tilted double-rule stamp frame behind
  it; the number itself stays upright). The favorite's stamp is oversized, the
  underdog's is small. The probability bar becomes a thick painted
  tug-of-war strip with a chalk half-court tick; the numbers sit beside it,
  not on it. Callouts:
  * **HIGH-CONFIDENCE PICK**: favorite win probability ≥ **70%** (tilted
    ink-red stamp).
  * **TOSS-UP**: favorite < **55%**.
  * **UPSET ALERT**: underdog ≥ **35%** *and* the underdog has the better
    last-10 win rate in `team_context` (a highlighter caution band).
  * **SWING**: the same home/away pair was predicted earlier in this page
    session and the home win probability moved ≥ **10 points** (for example
    after changing the as-of date).
* **Team comparison (`comparisonBar.js`, `teamSelect.js`).** Bars stay clean.
  The header row gets team-color chips with computed ink, field labels become
  condensed stencil-ish caps, and selects get chunky 2px boundaries.
* **Season simulator (`simulator.js`, `seedProbabilities.js`,
  `playoffField.js`).** No hero. Section titles get the Anton headline
  treatment. Tables become `.data-table` (high-contrast chalk on flat asphalt,
  tabular numerals, right-aligned numbers). A dashed highlighter **playoff
  cutoff line** runs under seed 6. Background texture comes only from the
  page ground (≤ 0.18).
* **League predictions (`league.js`, `leagueSummary.js`).** A **"headline of
  the day"** clipping above the results, derived from the projection already
  loaded:
  * If #1's lead over #2 in mean wins is ≥ **4.0**: "RUNAWAY".
  * If the lead is < **1.0**: "DOGFIGHT AT THE TOP".
  * Otherwise: "{TEAM} ON TOP".
  * Two teasers: **bubble watch** (the team whose direct-playoff odds are
    closest to 50%) and **the basement** (the worst projected team).
  The rest of the page stays calm.
* **Dashboard (`dashboard.js`).** A front-page clipping headline derived from
  the `/health` and `/tools` responses the page already fetches: "ENGINE
  ONLINE" with the model name and tool count, or an **"API DOWN"** alert with
  caution tape. The quick links become newsprint posters taped to the fence.
  The raw-tool panel stays calm.
* **Teams (`teams.js`).** The team header becomes a full team-color slab with
  a torn bottom edge, computed ink and an abbreviation stamp. A **HOT
  STREAK / COLD STREAK** stamp appears when the last-10 win rate is ≥ 70% /
  ≤ 30%. Metrics and tabs stay calm.
* **Placeholder (`placeholder.js`).** A chain-link "COMING SOON" sign with a
  caution-tape edge.
* **Banners.** The error banner gets a caution-tape edge and chalk text. The
  info banner becomes a "scouting report" note.

## 8. Self-critique and what changed in rev 2

I read rev 1 against the question: *does this read as Blacktop Tabloid, or as
generic dark-mode-with-stickers?* The heroes (clippings, team slabs, the
chain-link rail) passed. Four things did not:

| Rev 1 weakness | Why it reads generic | Rev 2 fix |
|---|---|---|
| Data slabs were "dark card + hard black shadow + chalk top edge" | Dark card on a dark page is plain dark mode, and a black shadow is invisible on asphalt | The slab boundary is now a **worn chalk court line** (2px, wear-masked) with a stencil title strip. It reads as paint on the blacktop, and the inside stays flat and calm |
| Orange was the only loud color outside the heroes | Dark + one orange accent is a re-skinned SaaS theme | **Color collisions are a rule:** every hero has at least two spot colors plus team colors hitting each other (the highlighter VS splat over two team halves, the ink-red stamp over newsprint, the highlighter headline bar). Orange is kept for *spray* things only: buttons, drips, the nav slab |
| The texture list had no jobs | Textures used anywhere turn into sticker soup | **One job per texture:** chain-link = navigation only. Torn paper = "this is a story" (hero clippings, and the team slab's edge). Drips = the masthead's edge and alert frames. Halftone = team-color slabs and paper corners. Chalk / worn paint = data boundaries and the playoff cutoff line. Grain = the ground. Caution tape = errors and API-down only |
| Marker was an optional annotation, and misregistration and collage appeared once | The brief asks for *marker-scrawl headline type*, off-registration and collage layering | **Every tabloid headline mixes faces:** team names in Anton, the verb scrawled in marker in ink-red ("THUNDER *over* WIZARDS", "CELTICS *on top*"). Clipping headlines get a 2px ink-red **misregistration ghost**. Each hero overlaps one layer across its own edge (a stamp or a strip of tape crossing the torn edge) for the collage feel |

Two more rules came out of the critique:

8. **Stamps must say something the data said.** No decorative stickers. A
   tilted stamp only appears for a derived state (HIGH-CONFIDENCE PICK,
   TOSS-UP, HOT/COLD STREAK, story tags). There is at most **one tilted
   element per hero**.
9. **Backboard, rim and court lines are structural.** The backboard-and-rim
   mark is the brand. Court lines sit behind the masthead, and a court line
   marks the playoff cutoff. None of them are sprinkled around as clip-art.

## 9. What the implementation settled

### Alert and story thresholds

Every callout is derived in its page module from data the page already has.
No new API calls are made and no data is invented.

| Where | State | Rule (source field) |
|---|---|---|
| Matchups | **HIGH-CONFIDENCE PICK** (tilted ink-red stamp) | favorite win probability ≥ **0.70** (`home_/away_win_probability`) |
| Matchups | **TOSS-UP** (tilted ink stamp) | favorite win probability < **0.55** |
| Matchups | **UPSET ALERT!** (highlighter caution callout) | underdog win probability ≥ **0.35** *and* the underdog's `team_context.*.win_rate_rolling_10` > the favorite's. Takes priority over the two stamps above |
| Matchups | **BIG SWING** (orange callout) | the same home/away pairing was predicted earlier in this page session and `home_win_probability` moved ≥ **0.10** (for example after setting an as-of date) |
| Matchups | headline verb | "over" normally, "edge" for a toss-up, a trailing "?" for an upset |
| League | **RUNAWAY** / **DOGFIGHT** / **ON TOP** | the gap in `mean_wins` between #1 and #2: ≥ **4.0** / < **1.0** / otherwise |
| League | teasers | **Bubble watch** = `direct_playoff_probability` closest to 0.5. **The basement** = `league_summary.worst_team` |
| Teams | **HOT STREAK** / **COLD STREAK** | `team_form.win_rate_rolling_10` ≥ **0.70** / ≤ **0.30** |
| Dashboard | **ENGINE ONLINE** / **TOOL RACK EMPTY** / **API DOWN** | `/health` ok with tools > 0 / ok with 0 tools / not ok or network failure |

### Token and recipe changes since rev 2

* The rail is `--sidebar-width: 282px` (up from 252px), so a badged nav label
  fits on one line in Barlow Condensed. Labels may wrap only in the system
  fallback face.
* The halftone recipe is **re-declared on each host** (`.clipping`,
  `.result-team`, `.team-hero`). Custom properties resolve `var()` where they
  are declared, so a single `:root` recipe would ignore each host's `--dot`.
* **Halftone dots on team slabs are drawn in the color opposite the text ink**
  (`colorInk.halftoneDotColor`), so a dot behind a letter can only *raise*
  contrast. This is checked for all 60 team colors.
* Bars (`.compare-fill`) get a chalk inset outline at 0.55 (5.2:1 against the
  track), so black or navy team fills stay visible on asphalt.
* Buttons lost their 3px inner bottom shadow, which darkened the orange to
  4.15:1 behind ink.

### Verification summary

* **Token pairs + team colors:** 111 computed checks, 0 failures. The lowest
  text-on-team-color ratio is 4.55:1 (Thunder `#007AC1` with black). Worst
  cases through texture: chalk through the 0.30 chain-link wire is 8.97:1,
  chalk-dim through a 0.16 grain speck is 6.68:1, and ink-soft over a 0.2
  halftone dot is 4.58:1.
* **axe-core 4.13** (WCAG 2 A/AA rules) on 17 scenarios × 2 widths: 0
  violations.
* **Rendered-pixel check:** axe can't decide text over gradients and
  textures, so every such element (1,027) was measured by diffing
  screenshots with its text shown and hidden, and sampling the real
  background under the glyph strokes. After fixes there are 0 failures. The
  one remaining flag was a rasterization artifact of the ±0.9° poster tilt,
  and it measures clean with the tilt disabled.

## 10. Deviations from the constraints (and why)

1. **Files touched beyond `index.html` and `pages/`.**
   * `web/js/main.js`: nav items became `<a href="#/…">` with `aria-current`,
     so they are keyboard-focusable. Plain clicks still go through
     `navigate()`, and modifier-clicks open a new tab.
   * A new helper module, `web/js/colorInk.js`: text on team colors has to be
     chosen by computed contrast at render time.
   * Component markup changed as follows. `probabilityBar.js`: a half-court
     marker and percentages in the `aria-label`. `seedProbabilities.js` and
     `playoffField.js`: modifier classes, plus `role="link" tabindex="0"` on
     clickable names. Signatures, exports, data read and existing classes
     are unchanged.
   * `comparisonBar.js`, `teamSelect.js`, `leagueSummary.js`, `router.js`,
     `api.js` and `src/*.py` are unchanged.
2. **Small interaction additions, not just markup.** Several interactive
   things were click-only `<div>`s or `<tr>`s, which can't show a
   `:focus-visible` style: nav items, dashboard posters, League team links
   and teasers, and simulator rows. They are now focusable and open on
   Enter. Simulator rows expand through a native `<button aria-expanded>` in
   the team cell; its click bubbles to the existing row handler. Data flow
   and routing are unchanged.
3. **"Big win-probability swing" needs a baseline the API doesn't provide.**
   BIG SWING therefore compares against the previous prediction of the same
   pairing *in the current page session*. It does not persist across
   reloads.
4. **The Dashboard has no sports data**, only `/health` and `/tools`. Its
   hero is therefore a platform-status headline. The sports "headline of the
   day" lives on League Predictions, whose projection holds the biggest
   story.
5. **Numbers in display faces.** The hero win-probability stamp uses Anton
   (upright, flat paper plate, no effects). Headings may contain label
   numbers in Anton, for example "League summary — 2025-26 season (1,000
   simulations)". The team-hero name's secondary-color misregistration ghost
   also lands on the digits of "76ers", which is a team name, not a data
   value. No numeric *data value* is rotated, skewed, halftoned or ghosted:
   tabloid headlines are built from team nicknames only, and the numbers go
   in the deck.
6. **Tilts.** Each hero has one tilted *stamp*. In addition, the stamp
   *frames* around the win percentages tilt, as allowed by rule 2 ("frames
   are loud"), and the marker scrawl leans −4° as part of the headline type.
   Dashboard posters tilt ±0.9°. They are navigation and hold no numbers.
7. **Texture opacity above 0.20 in the shell.** The chain-link rail is at
   0.30 and the placeholder fence at 0.40. The 10–20% cap applies to data
   pages, and all of this is shell: nav labels sit on solid sign plates and
   the placeholder copy sits on a solid paper sign. The page ground behind
   the simulator's tables is 0.16.
8. **Text-link targets** (`.link-btn`, for example "View team ▸") have a
   32px minimum height. That is above the 24px WCAG 2.2 AA target minimum
   but below 44px, so the one link inside League's "About" paragraph stays
   inline. Buttons, selects, inputs, nav items and summaries are ≥ 44px.
9. **Five font families** (Anton, Big Shoulders Stencil Display, Permanent
   Marker, Barlow, Barlow Condensed) rather than a strict two-face pairing,
   which the tabloid's clashing type calls for. Only the weights used are
   requested, and with `display=swap`. With the fonts blocked, the system
   fallbacks were checked for layout and overflow.
10. **The clash rule changed from rev 1.** "Contrast < 1.3:1" measured
    luminance only, so it wrongly treated Celtics green vs Bulls red as
    duplicates. Perceptual ΔE < 20 swaps only look-alike colors.
11. **New editorial copy.** Headline verbs and tags ("over", "edge", "run
    away with it", "game on!", "timeout!", "Scouting report", "Analytics
    desk") are voice, not data. Every fact in the copy comes from the
    envelope.
12. **A pre-existing layout bug was fixed along the way.** The fixed 252px
    sidebar overflowed every phone view. Below 860px the rail now becomes a
    top bar with a scrolling nav strip.
13. **Ambient motion in the hero** is an exception to rule 6 ("motion is a
    thunk, not a show"): the footage loop plays continuously. It is kept
    slow (half speed, crossfades, no cuts), it only runs for viewers who
    haven't asked for reduced motion and aren't on a phone or Save-Data,
    it pauses off screen, and it has a pause button.
14. **The hero stacks below 981px, not 860px.** Between 861px and 980px the
    sidebar leaves the hero under 700px wide, and the two-column collage
    put the figure on top of the copy. Rendered-pixel checks found the deck
    running onto the torn paper and the figure's arm from 861px to 1024px
    (up to 8.1% of glyph pixels below 4.5:1). That overlap came from the
    first hero pass. It is fixed, and every hero text element now passes
    at 12 widths from 360px to 1920px, with the footage both still and
    playing.
15. **The Dashboard footage prints at 0.7, above the 0.22 halftone cap.**
    The footage replaced the cutout as the hero's main element on request,
    so it has to read at a glance. The cap exists to protect legibility,
    and here no text sits on the slab: the stat badge has its own paper
    plate, and the copy stays on the dark side. Rendered-pixel checks show
    every hero text element passing at 12 widths.

## 11. Landing hero (Dashboard)

A full-viewport "gameday cover" is the app's landing screen (`#/dashboard`
is now the default route). Code: `web/js/components/hero.js` (markup) and
`web/hero.css` (styles). A page opts in by exporting `renderHero(slot, ctx)`;
`main.js` mounts it in `#app-hero` and hides the masthead, since the hero
carries the page's `<h1>`.

- **Palette:** two colors plus a neutral, all custom properties on `.hero`:
  `--hero-c1` (slab, trim, accent word), `--hero-c2` (band, jersey, one
  ransom chip), `--hero-paper`. The brand default is spray orange + court
  blue `#1f4bd1` (chalk on it 6.2:1). `heroPalette(teamId)` swaps in any team
  from `teamColors.js`. It swaps primary/secondary when the primary nearly
  vanishes on the base, picks text on each color by computed contrast, and
  picks an accent-word color that reaches 3:1 on the base.
- **Headline:** three stacked words: solid chalk, ransom-note letter chips,
  and an accent word torn along one generated jagged line (two CSS
  `clip-path` polygons sharing an edge) with a paper sliver along the tear.
- **Decorations:** SVG `clipPath` torn paper backing, halftone field on
  the slab (replaced by the footage print when there is footage), tag pill,
  stat badge, hand-drawn arrow at the CTA. The component also supports one
  optional slanted marker sticker (`sticker` prop); the Dashboard doesn't
  use it. The stacked layout keeps only the backing, and the color blocks
  stay inside the photo panel so the headline always sits on the dark base.
- **Footage (the Dashboard's main visual):** `renderHero({ footage,
  figure: false })` drops the cutout and its torn paper and plays the full
  edited clip (`web/media/background.*`, see its README) across the whole
  `--hero-c1` slab **in full color, unprocessed** — no crop, no grayscale,
  no halftone re-screening. The video fully covers the slab (`object-fit:
  cover`), so `--hero-c1` never shows through while it's playing; it is
  clipped to the slab's diagonal and never behind copy.
  * The slab carries no text, and the stat badge sits on its own paper
    plate.
  * **Diagonal:** at 1101px and up the slab runs from 50% of the hero at
    the top to 44% at the bottom, with the blue band just left of it. The
    deck is capped at `min(34ch, 29cqi)` so it never reaches the band. At
    981–1100px the slab keeps its original 61%→49% line.
  * **Dark side:** a faint chalk halftone (dots at 0.07) covers the left
    half, fading toward the slab. In the stacked layout it covers the dark
    ground below the photo panel.
  * **Motion:** a 10.7 s loop of three steady backboard/rim shots at half
    speed with 1 s crossfades, no cuts and a seamless loop point.
  * **Who gets motion:** only viewports ≥ 861px, without
    `prefers-reduced-motion` and without Save-Data. Everyone else,
    including all phones, gets the 8 KB poster printed the same way, and
    the video is never requested (sources carry `data-src` until
    `wireHero()` activates them).
  * **Loading:** the download starts after the `load` event, at idle.
    Playback pauses while the hero is off screen or the tab is hidden.
  * **Control:** a 44px pause/play button (WCAG 2.2.2) sits on the slab
    (top-right; bottom-left of the photo panel in the stacked layout). The
    print behind it can be bright or dark, so its focus ring is two-tone:
    a highlighter outline inside an ink halo.
- **Photo:** an SVG player-bust placeholder in the palette's jersey, with
  halftone key-light and shade screens and a paper cut-out outline. The
  jersey reads **BACKBOARD**, the product name (it said BLACKTOP, which
  looked like a misspelling of the brand next to the sidebar wordmark), and
  the wordmark shrinks with its length so it never hits the armhole trim.
  `renderHero({ photo: { src, alt } })` swaps in a real transparent cutout
  (`.png` under `web/`).
- **Layout:** two columns from 981px; below that (phones, and 861–980px
  windows where the sidebar still takes 282px) the hero stacks. From
  981–1100px the deck narrows to 28ch and the collage to 52% so the copy
  never meets the paper or the figure.
- **Stat:** 65.1% = `metrics.elo_boosted_ensemble.accuracy` (0.6508) over
  the 13,332-game chronological holdout in `models/baseline_metrics.json`.
  The API doesn't serve that file, so the value is a constant in
  `dashboard.js` to update if the production model changes. The number
  stays upright and flat, per §6.
