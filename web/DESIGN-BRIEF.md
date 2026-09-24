# Blacktop Tabloid — design brief

Status: **rev 1 (proposal)**. This brief is written before the stylesheet
rewrite so the direction can be checked first. The implementation follows it
page by page.

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
| `--ink-red` | `#b3141a` | paper (stamps, alert headlines) | ≥ 4.5 (checked in the final pass) |

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
2. **Clash, don't blend.** When two teams on screen have near-identical
   primaries (contrast < 1.3:1, for example Bulls/Raptors/Rockets `#CE1141` or
   Knicks/76ers `#006BB6`), the away team switches to its own secondary color
   from `teamColors.js`. No colors are invented.

## 4. Type

| Role | Family (Google Fonts) | Fallback stack | Where |
|---|---|---|---|
| Headline | **Anton** | Impact, Haettenschweiler, "Arial Narrow Bold", "Franklin Gothic Heavy", "Liberation Sans Narrow", sans-serif | Mastheads, card titles, tabloid headlines |
| Stencil | **Big Shoulders Stencil Display** 800/900 | "Stencil Std", Stencil, Anton, Impact, sans-serif | Nav section label, stamps, kickers |
| Marker | **Permanent Marker** | "Marker Felt", "Segoe Print", "Bradley Hand", Anton, sans-serif | One handwritten annotation per hero, at most |
| Workhorse | **Barlow** 400–700 | Inter, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Liberation Sans", sans-serif | All body text and **all numbers** (`tabular-nums`) |
| Labels | **Barlow Condensed** 600–800 | "Arial Narrow", "Roboto Condensed", Barlow, sans-serif | Field labels, nav items, table heads, badges |

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
* **`.card` → data slab.** Square corners, flat asphalt-900, a hard offset
  "pasted" shadow and a worn chalk-paint top edge. `h2` is Anton caps.
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
