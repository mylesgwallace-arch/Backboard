# Street Zine — design brief

The visual system for **Backboard**, ported from the style lab
(`design/backboard-demos/street-zine`, the owner's pick) into the live app.
It replaces "Blacktop Tabloid" (rev 2), which stays in git history.

## 1. The idea in one line

A photocopied street zine: a black ground, torn white paper sheets with ink
text, tape, red and yellow scraps, Anton headlines with one word on a torn
red strip, typewriter tables, and the Limelight nav bar across the top.

## 2. Files

* `web/styles.css` is the whole system (one stylesheet; `src/api.py` and the
  tests serve and check `/styles.css`).
* `web/index.html` holds the four torn-edge SVG filters (`#tear-a`, `#tear-b`,
  `#tear-c`, `#tear-edge`), the brand + nav header and the footer.
* `web/js/main.js` builds the Limelight nav (bar pages + a "More" slip) and
  the page head; `web/js/pages/dashboard.js` renders the cover.

## 3. Tokens

| Token | Value | Job |
|---|---|---|
| `--ground` | `#141414` | The page |
| `--paper` | `#f3f1ea` | Torn rims, tape, the white Home band |
| `--sheet` | `#fbfaf6` | The face of a content sheet (`.card`) |
| `--ink` | `#101010` | Text on paper |
| `--red` | `#ee3a1f` | Buttons, the title strip, the nav light, active tabs |
| `--red-ink` | `#c4241a` | Red that reads as text on paper (5.6:1) |
| `--yellow` | `#f3e32a` | Headline clippings, hover, focus ring on black, highlighter |

**Scoped tokens.** Components never use raw colors; they read `--fg`,
`--fg-2`, `--fg-3`, `--line`, `--line-strong`, `--surface`, `--surface-2`,
`--field-bg`, `--pos`, `--neg` and the `--chart-*` tokens. `:root` defines
them for the black ground; `.card` / `.paper` re-declare them for paper
(ink text, ink lines). The legacy names (`--text-primary`, `--accent`,
`--positive`, `--border`, …) are re-declared in both scopes because a few
page modules use them inline. A team scrap (`.team-hero`, `.result-team`)
sets `--fg` to the team's computed ink (`colorInk.js`).

## 4. Type

| Role | Family | Where |
|---|---|---|
| Headline | Anton | Page titles, sheet titles, big numbers, buttons |
| Body | Archivo 400/600/800 | Text, field labels (800 caps) |
| Typewriter | Courier Prime 400/700 | Tables, kickers, tags, chips, tabs, tooltips |
| Pen | Reenie Beanie | The scrawled word in a headline ("win", "over"); never numbers |
| Ransom | Abril Fatface | Occasional letter on the cover masthead |

## 5. Torn paper (how it is built)

* A sheet (`.card`, `.scrap`) is two pseudo-elements: a paper rim
  (`::before`, `filter: url(#tear-a)`) under a face (`::after`, `#tear-b`),
  both displaced by turbulence. Sheets rotate filters by position so edges
  differ. Content and focus rings sit on the element itself and are never
  displaced.
* The title strip (`.hl`), buttons (`.btn`), errors (red) and notes /
  callouts (yellow) use the same pseudo-element recipe.
* Chips, tabs and segmented toggles are torn labels (`clip-path` polygons);
  the selected tab is red, the selected toggle is ink.
* Home bands tear into each other with a `#tear-edge` paper strip; the
  player headshot is torn with seeded polygons (`tornPhoto.js`).

## 6. Rules

1. **Paper for data, black for the frame.** Every data section is a paper
   sheet; the ground carries only the nav, page head and footer.
2. **One torn red word per title** (the page head wraps the last word of the
   page title in `.hl`).
3. **Stamps say what the data said** (HIGH-CONFIDENCE PICK, TOSS-UP, HOT
   STREAK, NEW TITLE FAVORITE, a player's role tag). At most one tilted stamp
   per hero.
4. **Numbers stay upright.** Big numbers are Anton, table numbers Courier
   with tabular figures; nothing numeric is rotated or set in the pen face.
5. **Team colors are scraps**, with computed ink and a stripe in the team's
   second color; inside data they are bar fills with an ink outline.
6. **Charts take colors from tokens** (`.c-grid`, `.c-base`, `.c-accent`,
   `.c-pos`, `.c-neg`): gray vs red for two series, green vs red for
   polarity, validated for color-vision deficiency on both paper and black.
   Many series (30 teams' odds) are never 30 colors: every line is a faint
   gray and the followed one is red, with end labels (`emphasisLineChart`).
7. **Motion is a thunk**: stamps thunk in once, the nav light slides, numbers
   count up once (`data-count`), sheets below the fold drop onto the page as
   they scroll in, pages turn with a short slide (View Transitions), and a
   result the page wants to land (the matchup verdict, the report card, the
   VS splat, an upset) **uncrumples**: a wad of paper pulls open into the
   sheet while flat-shaded fold facets fade (`components/motion.js`;
   transform, clip-path and opacity only). All of it is off under
   `prefers-reduced-motion`, and the cover footage never starts itself there
   or under Save-Data.
8. **Focus is always visible**: yellow on black, ink on paper and scraps.
   Tap targets are 44px or more.

## 7. Navigation

The Limelight bar shows Home, Tonight, Matchups, Teams, Players, Sandbox,
Playoffs and Ask. Everything else lives under "More" (a torn paper slip),
grouped (League, Players, Teams, Sandbox) with one line on what each page is
for, so neighbours are told apart (the What-if Lab is the one-swap version of
the Sandbox; Head-to-Head is the season-by-season version of the Teams tab).
The light sits on the active page, or on More for pages listed there. Below
600px the bar shows icons only (each has a title and the page head names the
page). Search (`/` or Ctrl/⌘-K) and the edition switch sit at the right of the
bar; on phones search is a red button in the bottom corner and the edition
switch is in the More slip.

## 8. The arena (character select, VS)

The two full-bleed black screens (`.cs-arena`, `.vs-arena`) are the one place
the zine turns into a game: the arena takes the hovered player's team colors
(`--sel` / `--sel-alt`, registered with `@property` so they glide), speed
lines and scanlines sit behind slanted portrait tiles, the cursor (red P1,
yellow P2) slides between tiles, and the preview's name slams in with its
ratings bars filling. The type, stamps, torn photos and ink rules are the
zine's own; ratings are always labeled as percentiles.

## 9. Editions and print

The **night edition** (default) is the black ground. The **day edition**
(`:root[data-theme="light"]`) is newsprint (`--ground: #e9e5da`) with ink
frame text and a soft shadow under each torn sheet; the masthead bar, the
home cover and the arena stay black. **Print** is ink on white: no chrome,
sheets become plain bordered boxes, nothing animates.
