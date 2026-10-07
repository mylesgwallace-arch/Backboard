# Home cover footage

The Home cover (`renderHero` in `web/js/pages/dashboard.js`) runs a 90s dunk
montage behind the torn-letter masthead, as in the street-zine style lab
(`design/backboard-demos/street-zine`).

| File | What it is |
|---|---|
| `zine/montage.mp4` | The montage edit from the style lab (~2.7 MB, no audio needed: it plays muted). |
| `zine/still-166.jpg` | A still from it, shown before playback and as the poster. |

Playback rules: the video is `preload="none"`, starts only while the cover is
on screen, pauses when it scrolls away, and never starts by itself under
`prefers-reduced-motion` or Save-Data (the Play footage button still works).
The CSS greys and darkens it (`.cover-bg`), so it reads as texture behind the
type.

The raw source edit is kept in `web/raw-clips/`.
