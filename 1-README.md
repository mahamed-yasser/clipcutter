# ClipCutter

**Made by Mahamed Yasser**

Command-line tool for the usual short-video jobs: cut a section, join clips of different sizes, make a vertical 9:16 version, burn in subtitles (Arabic works), and export thumbnails. Everything runs locally through ffmpeg. Python 3.8+, no pip packages.

## Requirements

[ffmpeg](https://ffmpeg.org/) with libx264 and libass, and ffprobe, on your PATH.

## Install (optional)

```
pip install .        # gives you a `clipcutter` command; or just use `python -m clipcutter`
```

## Commands

```
clipcutter trim     in.mp4 out.mp4 --start 0:05 --end 0:12      # or --duration 7; add --fast for an instant stream-copy cut
clipcutter concat   a.mp4 b.mp4 c.mp4 -o joined.mp4 [--size 1920x1080 --fps 25]   # mixed sizes and silent clips are normalised
clipcutter vertical in.mp4 out.mp4 --mode blur                  # crop (fill) or blur (whole shot over blurred background)
clipcutter subs     in.mp4 captions.srt out.mp4 [--position top|middle|bottom --font-size 48 --margin 60 --font "Noto Sans"]
clipcutter thumbs   in.mp4 thumbs/ --count 6                    # or --every 10
clipcutter batch    vertical clips/ out/ [--mode blur]       # every video in a folder (not sub-folders)
clipcutter batch    subs clips/ out/                         # uses NAME.srt next to NAME.mp4
clipcutter info     in.mp4
clipcutter run      examples/edit.json                          # a whole edit from one file
```

Quality: `--crf 0-51` on trim, concat, vertical, subs (default 22, lower is better and bigger).

Batch: one bad file never stops the rest. It prints `ok`, `skipped` or `FAILED` per file and a summary, and exits 1 if anything failed. Existing outputs are skipped unless `--force`. The output folder must differ from the input folder. Videos without a matching `.srt` are skipped by `batch subs`. Outputs are always `NAME.mp4`.

Safety: output is written under a temporary name and moved into place only when ffmpeg succeeded and the result has video and a length, so a failed or interrupted run never leaves a half-written file. It never overwrites a file unless you pass `--force`, never writes over its own input, and checks the output folder exists before doing any work.

Subtitle size and margin are in video pixels. If you leave them out they scale with the video height (4% and 5%), so the same command looks right on a 720p clip and on a 1080x1920 reel.

## Edit list

```json
{
  "output": "reel.mp4",
  "vertical": "blur",
  "subtitles": "captions.srt",
  "subtitle_style": {"position": "bottom"},
  "crf": 22,
  "clips": [
    {"file": "scene1.mp4", "start": "0:01", "end": "0:04"},
    {"file": "scene2.mp4", "start": 0, "duration": 3}
  ]
}
```

Optional keys: `size` ("1280x720", used when joining), `fps`, `crf`, `subtitle_style` (`font`, `font_size`, `position`, `margin`).
Clips are trimmed, joined, reframed, then subtitled. Paths are relative to the JSON file. The whole list is checked first (unknown or misspelled keys, missing files, bad times, existing output) so it fails before any slow encoding starts.

Try it: `sh examples/make_samples.sh && clipcutter run examples/edit.json`

## Tests

```
python -m unittest discover -s tests
```

Unit tests check the commands and option validation. The real-run tests generate clips with ffmpeg and check output sizes, durations, audio, thumbnail counts, overwrite protection and where subtitles land on screen (top, middle, bottom). They skip if ffmpeg is missing.

## Things to know

- `trim --start` at or after the end of the input is an error (ffmpeg alone would "succeed" with an empty file). An `--end` past the end is not an error: the clip simply stops at the end of the input.
- `--fast` cuts at a keyframe, so the result can start earlier and run longer than you asked.
- `concat` letterboxes (adds bars) instead of stretching when sizes or shapes differ.
- Batch only looks at files directly inside the folder, and two files with the same name but different extensions (a.mp4, a.mov) are refused because both would become a.mp4.

## Limits

- Normal mode re-encodes to H.264/AAC, so cuts are frame-accurate but not instant. `--fast` is instant but snaps to the keyframe before `--start`, so the clip can start earlier (and run longer) than asked.
- Subtitle look is fixed apart from font, size, position and margin: white text, black outline. Long lines wrap by themselves and a very long cue can take 4 or more lines, so shorten or re-flow them first (SRT Toolkit's `wrap` helps, but video subtitles are not limited to two lines). If the font is missing, ffmpeg picks another one. Arabic needs a font with Arabic glyphs.
- No GUI and no progress bar. Batch covers `vertical` and `subs` only and runs one file at a time.
- Tested with ffmpeg 4.4 on Linux only.

## Changelog

- 0.2.0: folder `batch` (vertical, subs), atomic output (no half-written files), start-past-end check, CI workflow, overwrite protection, `--fast`, `--crf`, concat size/fps, subtitle font/size/position/margin (scales with video height), stricter edit-list checks, `pip install` support, `info` prints a readable line.
- 0.1.0: first version.

---
Made by Mahamed Yasser
