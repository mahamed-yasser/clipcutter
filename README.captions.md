# Caption workflow (local extension, not published)

New in this local branch (`local/caption-workflow`); the original clipcutter code is unchanged except the CLI hook. Baseline (main): 34 tests pass. New: `clipcutter/captions.py`, `tests/test_captions.py`.

```
clipcutter captions check   file.srt [--profile netflix-ar|none] [--json]
clipcutter captions fix     file.srt -o fixed.srt [--force]    # writes fixed.srt and fixed.srt.changes.txt (diff). Original never touched.
clipcutter captions preview file.srt frames/ --cues 1,3 [--font "Noto Sans"]   # PNGs of the real rendered result
```

What it does and does not claim
- `warn` findings are measured against a style preset (42 chars/line, 2 lines, 20 chars/s, 5/6 s to 7 s). The preset is Netflix's Arabic guide, not a universal rule; `--profile none` disables the numeric checks. Sources are in `captions.py`.
- `review` findings (mixed digits, a Latin/number run before final punctuation, one-word last line) are heuristics. They are prompts to look at the frame, never proof of an error. Bidi control characters alone are `info`.
- `fix` only trims spaces, re-breaks lines at spaces, and ends a cue early to remove an overlap of 500 ms or less. It never changes, adds or deletes a letter or a cue, and it enforces that in code. It does not fix reading speed, long duration, or linguistic line-break rules (for example not separating a verb from its subject), which need a human.
- Files not valid UTF-8 are read as cp1256 and flagged as a GUESS.
- Always look at the frames. Finding from the real renders in this environment (ffmpeg 4.4.2, fontconfig fonts here): the font `Noto Kufi Arabic` rendered every Arabic letter as an empty box, and `Noto Sans Arabic` rendered the lam-alef "لا" as an empty box, while `Noto Sans`, `Noto Naskh Arabic UI` and `DejaVu Sans` rendered correctly. The caption text was fine; the font rendering was not. See evidence/fontcompare.png. Cause not investigated (likely variable-font handling in this ffmpeg/libass build); other machines may differ.

## Exact limits of what was tested (docs-only addition after review)
- The "no words changed" check compares words split on whitespace, so a no-break space (U+00A0) may be normalized to a normal space in re-wrapped cues. Letters and word order do not change.
- In the golden sample, cue 7 is a repeated alef (U+0627) used as a deliberate unbroken-text case. The vertical strokes in its frame are that letter, not glyph loss.
- Not tested: real video end to end, Windows, non-ASCII paths on other systems, other ffmpeg/libass builds, symlink races between the check and the write.
- Baseline `clipcutter subs` still breaks on a subtitle path containing an apostrophe; this extension does not change it (only `captions preview` works around it).
- Baseline vs extension: the baseline is clipcutter main (0466843, 34 tests). The extension adds clipcutter/captions.py, the `captions` CLI hook, tests/test_captions*.py and this file (33 tests); nothing else in baseline is changed. Run: `python3 -m clipcutter captions check FILE`, then `fix FILE -o OUT.srt`, review OUT.srt.changes.txt, then `preview OUT.srt DIR --cues 1,2`; always look at the frames.

## Arabic right-to-left layout (added in v2.1)
libass lays each subtitle line out left-to-right unless told otherwise. In real renders this put the final full stop / "!" on the wrong end of lines such as `Python لغة برمجة سهلة.` and scrambled the order of `الرياضيات (2026): ... x² - 4 = 0`. A plain RLM (U+200F) did not help; wrapping the line in RLE...PDF (U+202B/U+202C) did.
- `captions preview` now does this automatically (`--no-rtl-embed` shows the raw file as written).
- `captions rtl-copy file.srt -o file.rtl.srt` writes a marked COPY (invisible controls only, no letter changes; the original is never touched). Burn that copy: `python3 -m clipcutter subs in.mp4 file.rtl.srt out.mp4 --font-size 42`.
- Baseline `clipcutter subs` itself is unchanged: burning an UNMARKED Arabic SRT still gives the old left-to-right layout.
- Checked end to end on a short generated clip (1280x720, ffmpeg 4.4.2, font Noto Sans, 5 cues incl. an English-only line). Not tested: real recorded video, other ffmpeg/libass builds, Windows.
- Credit: the CLI epilog / sample text "Made by Mahamed Yasser" is the baseline tool's credit line and is kept. It is a credit, not a statement that one person wrote every line; this extension was AI-assisted.


## Readable burn style (opt-in)
`clipcutter subs IN.mp4 captions.rtl.srt OUT.mp4 --style readable` (arguments: input video, SRT, output video) burns larger bold text (5.2% of video height) on a semi-transparent dark plate with an 8% bottom margin and 6% side margins. The default `--style plain` is unchanged. `--font-size` and `--margin` still override. Burn the `rtl-copy` output for Arabic. Tested on a generated 1280x720 clip with Noto Sans and ffmpeg 4.4.2 only; the plate is drawn by libass, other builds may differ slightly. Not tested on real footage or on 1080x1920 reels. Cue line breaks are not changed by this option: use `captions check` / `captions fix` to review them.
The JSON edit-list (`run`) has no style option; use the `subs` command.
