# Hero background video

The landing hero plays this loop directly as its slab's visual (see
"Footage" in `web/hero.css` and `wireHero()` in `web/js/components/hero.js`).
It is the full edited clip, shown plainly — no crop, no grayscale, no
halftone re-screening, no speed or frame-rate changes.

| File | What it is |
|---|---|
| `background.mp4` | `web/raw-clips/background.mp4`, byte-identical. H.264 (Main@3.1), 640×360, native 29.97 fps, 38.4 s, with its original (inaudible — the `<video>` is `muted`) audio track. ~12.2 MB. Offered first. |
| `background.webm` | Same picture, transcoded to VP9 (no filters — just a codec/container change) so browsers without H.264 support still get it. ~4.4 MB. |
| `background-poster.jpg` | The clip's first frame (~21 KB). Shown before playback, and the only thing loaded for phones, `prefers-reduced-motion` and Save-Data. |

## Regenerate

```sh
cp web/raw-clips/background.mp4 web/media/background.mp4
ffmpeg -i web/media/background.mp4 -an -c:v libvpx-vp9 -b:v 0 -crf 32 \
  -row-mt 1 -deadline good -cpu-used 2 -pix_fmt yuv420p web/media/background.webm
ffmpeg -i web/media/background.mp4 -frames:v 1 -q:v 3 web/media/background-poster.jpg
```

If `web/raw-clips/background.mp4` is re-exported, just rerun the three
commands above — there's no crop/timing/color-grade metadata to re-derive.

## Serving

`src/api.py` serves `.mp4` / `.webm` / `.jpg` with real content types and
answers HTTP range requests (206 / 416), which Safari needs to play
`<video>`.
