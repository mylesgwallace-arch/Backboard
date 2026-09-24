# Hero footage loop

The landing hero prints this loop into its orange slab as live halftone dots
(see "Footage" in `web/hero.css` and `wireHero()` in
`web/js/components/hero.js`). It is ambient texture, not a highlight reel.

| File | What it is |
|---|---|
| `hero-loop.mp4` | H.264 High@3.0 (`avc1.64001E`), 232×240, native 29.97 fps, 5.67 s, no audio, `faststart`. ~84 KB. Offered first. |
| `hero-loop.webm` | VP9, same picture. ~154 KB. For browsers without H.264. |
| `hero-loop-poster.jpg` | The loop's first frame (~9 KB). Shown before playback, and the only thing loaded for phones, `prefers-reduced-motion` and Save-Data. |

## How it was made from `web/raw-clips/background.mp4`

The raw edit is a 38 s, 640×360 montage with the 90s CRT look baked in,
black side bars, fast cuts every 1–3 s and an audio track. The loop keeps
only the calmest material, played at the source's own speed and frame
rate — no slow motion, no frame blending or fps resampling:

1. **Crop** `348:360:146:0`: just inside the curved-screen edges, so no
   black shows on any frame.
2. **Three steady shots**, all framed on the backboard or rim (source
   seconds): 7.70–10.20 under the backboard, 14.35–16.50 a wide backboard
   dunk, 17.30–19.50 a rim close-up. The fast second half of the edit
   (23–38 s) is not used.
3. **Native speed, native frame rate.** No `setpts` retiming and no `fps`
   filter — every frame is exactly as captured.
4. **0.4 s crossfades** between shots instead of cuts, and a crossfade from
   the last shot back into the first 0.4 s of the first, with that stretch
   trimmed off the front, so the loop point is seamless. (Shorter than the
   old 1 s crossfade because the shots themselves are shorter at native
   speed — same ~20% overlap proportion.)
5. **Grayscale, softened, levels stretched.** The CSS reprints it in the
   palette color, so color would be wasted bytes. The faded grade's lifted
   blacks (~25% grey) are stretched back to the full range so the halftone
   dots have something to work with.
6. **Audio stripped.**

## Regenerate

```sh
IN=web/raw-clips/background.mp4
PREP="crop=348:360:146:0,format=gray,gblur=sigma=0.9,scale=232:240:flags=area"
LEVELS="lutyuv=y='clip(16+219*pow(clip((val-66)/160\,0\,1)\,1/1.4)\,16\,235)':u=128:v=128"

ffmpeg -i "$IN" -filter_complex "
[0:v]$PREP,split=3[s1][s2][s3];
[s1]trim=start=7.70:end=10.20,setpts=PTS-STARTPTS,split=2[a1][a2];
[a1]trim=start=0.4,setpts=PTS-STARTPTS[atail];
[a2]trim=end=0.4,setpts=PTS-STARTPTS[ahead];
[s2]trim=start=14.35:end=16.50,setpts=PTS-STARTPTS[c];
[s3]trim=start=17.30:end=19.50,setpts=PTS-STARTPTS[d];
[atail][c]xfade=transition=fade:duration=0.4:offset=1.7[x1];
[x1][d]xfade=transition=fade:duration=0.4:offset=3.45[x2];
[x2][ahead]xfade=transition=fade:duration=0.4:offset=5.25,format=yuv420p,$LEVELS[out]
" -map "[out]" -an -c:v ffv1 master.mkv

ffmpeg -i master.mkv -an -c:v libx264 -profile:v high -level 3.0 -crf 30 \
  -preset veryslow -g 48 -pix_fmt yuv420p -movflags +faststart web/media/hero-loop.mp4
ffmpeg -i master.mkv -an -c:v libvpx-vp9 -b:v 0 -crf 40 -row-mt 1 \
  -deadline good -cpu-used 1 -g 48 -pix_fmt yuv420p web/media/hero-loop.webm
ffmpeg -i master.mkv -frames:v 1 -q:v 4 web/media/hero-loop-poster.jpg
```

The xfade offsets follow from the shot lengths at native speed (2.5 s,
2.15 s, 2.2 s, less the 0.4 s taken off the front of the first shot). If the
edit is re-exported, re-measure the crop, because the picture's position may
change, and re-check the shot boundaries before reusing these timings.

## Serving

`src/api.py` serves `.mp4` / `.webm` / `.jpg` with real content types and
answers HTTP range requests (206 / 416), which Safari needs to play
`<video>`.
