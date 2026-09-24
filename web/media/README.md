# Hero background video

The landing hero plays this loop directly as its slab's visual (see
"Footage" in `web/hero.css` and `wireHero()` in `web/js/components/hero.js`).
It is the full edited clip at its original speed, frame rate and framing —
no crop, no grayscale, no halftone re-screening, no cuts. It is
re-compressed from the raw export to keep the download light.

| File | What it is |
|---|---|
| `background.mp4` | H.264 (High@3.1), 640×360, native 29.97 fps, 38.4 s, no audio (the `<video>` is `muted` anyway). CRF 20. ~5.6 MB. Offered first. |
| `background.webm` | Same picture, VP9, CRF 26. ~7.0 MB. For browsers without H.264. |
| `background-poster.jpg` | The clip's first frame (~18 KB). Shown before playback, and the only thing loaded for phones, `prefers-reduced-motion` and Save-Data. |

## Regenerate

```sh
IN=web/raw-clips/background.mp4
ffmpeg -i "$IN" -an -c:v libx264 -profile:v high -level 3.1 -crf 20 \
  -preset slow -pix_fmt yuv420p -movflags +faststart web/media/background.mp4
ffmpeg -i "$IN" -an -c:v libvpx-vp9 -b:v 0 -crf 26 \
  -row-mt 1 -deadline good -cpu-used 2 -pix_fmt yuv420p web/media/background.webm
ffmpeg -i web/media/background.mp4 -frames:v 1 -q:v 3 web/media/background-poster.jpg
```

No filters (crop/scale/setpts/fps) are applied — `-crf` is the only
quality/size knob (lower = higher quality/bigger file). CRF 20/26 here was
picked after CRF 28/36 looked too soft as a background element; push it
lower still (e.g. 16–18 for the mp4) if it ever needs to look sharper, or
higher to trade quality back for a smaller download.
If `web/raw-clips/background.mp4` is re-exported, just rerun the three
commands above.

## Serving

`src/api.py` serves `.mp4` / `.webm` / `.jpg` with real content types and
answers HTTP range requests (206 / 416), which Safari needs to play
`<video>`.
