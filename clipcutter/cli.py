import argparse, sys
from . import core, __version__
from .captions import CaptionError as _CapErr


def _size(v):
    try:
        w, h = (int(x) for x in v.lower().split("x"))
        return w, h
    except ValueError:
        raise argparse.ArgumentTypeError("size must look like 1280x720")


def _captions(a):
    import json
    from . import captions as cap
    if a.cap_cmd == "check":
        cues, problems, enc = cap.load_file(a.file, a.encoding)
        issues = problems + cap.check(cues, a.profile)
        if a.json:
            print(json.dumps({"encoding": enc, "cues": len(cues), "summary": cap.summarize(issues), "issues": issues}, ensure_ascii=False, indent=2))
        else:
            print(f"{a.file}: {len(cues)} cue(s), read as {enc}")
            for x in issues:
                print(f"  [{x['severity']:6}] cue {x['cue']} (line {x['line']}) {x['code']}: {x['msg']}")
            sm = cap.summarize(issues)
            print(f"{sm['error']} error, {sm['warn']} warn, {sm['review']} review (heuristic), {sm['info']} info")
        return 1 if any(x["severity"] == "error" for x in issues) else 0
    if a.cap_cmd == "fix":
        n = cap.write_fixed(a.file, a.output, a.profile, a.force, a.encoding, a.allow_srt_conversion)
        print(f"{n} change(s) written to {a.output}; read {a.output}.changes.txt before using it")
        return 0
    if a.cap_cmd == "rtl-copy":
        import os
        if not a.output.lower().endswith(".srt") or os.path.islink(a.output) or (os.path.exists(a.output) and (os.path.samefile(a.output, a.file) or not a.force)):
            raise cap.CaptionError("output must be a new .srt (not a link, not the input; use --force to overwrite another file)")
        cues, problems, enc = cap.load_file(a.file, a.encoding)
        if "GUESS" in enc or problems:
            raise cap.CaptionError("unreadable blocks or guessed encoding; run `check` first")
        with open(a.output, "w", encoding="utf-8", newline="\n") as f:
            f.write(cap.rtl_embed(cues))
        print(f"wrote {a.output}")
        return 0
    if a.cap_cmd == "preview":
        try:
            nums = [int(x) for x in a.cues.split(",")]
        except ValueError:
            raise ValueError("--cues must be numbers like 1,3,7")
        for f in cap.preview_frames(a.file, a.out_dir, nums, a.font, force=a.force, encoding=a.encoding, rtl=not a.no_rtl_embed):
            print(f)
        return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="clipcutter", description="Trim, join, reframe and subtitle videos with ffmpeg.",
                                epilog="Made by Mahamed Yasser")
    p.add_argument("--version", action="version", version=f"clipcutter {__version__} - Made by Mahamed Yasser")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--force", action="store_true", help="overwrite existing output")
    quality = argparse.ArgumentParser(add_help=False)
    quality.add_argument("--crf", type=int, default=22, help="quality 0-51, lower is better and bigger (default 22)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("trim", parents=[common, quality], help="cut a section")
    s.add_argument("input"); s.add_argument("output")
    s.add_argument("--start", default="0"); g = s.add_mutually_exclusive_group()
    g.add_argument("--end"); g.add_argument("--duration")
    s.add_argument("--fast", action="store_true", help="no re-encode: instant, but the cut snaps to the keyframe before --start")

    s = sub.add_parser("concat", parents=[common, quality], help="join clips in order (sizes may differ)")
    s.add_argument("inputs", nargs="+"); s.add_argument("-o", "--output", required=True)
    s.add_argument("--size", type=_size, default=(1280, 720), help="output size, default 1280x720")
    s.add_argument("--fps", type=int, default=30)

    s = sub.add_parser("vertical", parents=[common, quality], help="make a 1080x1920 version")
    s.add_argument("input"); s.add_argument("output"); s.add_argument("--mode", choices=["crop", "blur"], default="crop")

    s = sub.add_parser("subs", parents=[common, quality], help="burn subtitles in (SRT)")
    s.add_argument("input"); s.add_argument("srt"); s.add_argument("output")
    s.add_argument("--font", default="Noto Sans"); s.add_argument("--font-size", type=int, default=None, help="pixels; default 4%% of video height")
    s.add_argument("--position", choices=["bottom", "middle", "top"], default="bottom")
    s.add_argument("--margin", type=int, default=None, help="pixels from the edge; default 5%% of video height (8%% with --style readable)")
    s.add_argument("--style", choices=["plain", "readable"], default="plain", help="plain = unchanged default; readable = larger bold text on a semi-transparent plate, safer margins")

    s = sub.add_parser("thumbs", parents=[common], help="export JPG thumbnails"); s.add_argument("input"); s.add_argument("out_dir")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--every", type=float, help="one every N seconds"); g.add_argument("--count", type=int, help="N evenly spaced")

    s = sub.add_parser("run", parents=[common], help="run a JSON edit list"); s.add_argument("edit_list")

    s = sub.add_parser("batch", parents=[common, quality], help="process every video in a folder (not sub-folders)")
    s.add_argument("kind", choices=["vertical", "subs"], help="subs uses NAME.srt next to NAME.mp4")
    s.add_argument("in_dir"); s.add_argument("out_dir")
    s.add_argument("--mode", choices=["crop", "blur"], default="crop")
    s.add_argument("--position", choices=["bottom", "middle", "top"], default="bottom")

    s = sub.add_parser("captions", help="check, fix or preview an SRT/VTT caption file (review-first)")
    cs = s.add_subparsers(dest="cap_cmd", required=True)
    c = cs.add_parser("check", help="list problems; Arabic/bidi findings are heuristics for a human to review")
    c.add_argument("file"); c.add_argument("--profile", choices=["netflix-ar", "none"], default="netflix-ar"); c.add_argument("--json", action="store_true"); c.add_argument("--encoding", help="text encoding, e.g. utf-8, cp1256, utf-16-le (default: UTF-8, else a flagged cp1256 guess)")
    c = cs.add_parser("fix", parents=[common], help="write a fixed COPY and a .changes.txt diff; the original is never changed")
    c.add_argument("file"); c.add_argument("-o", "--output", required=True); c.add_argument("--profile", choices=["netflix-ar", "none"], default="netflix-ar")
    c.add_argument("--encoding", help="required when the file is not valid UTF-8 (fix never works from a guess)")
    c.add_argument("--allow-srt-conversion", action="store_true", help="accept losing VTT-only features (settings, NOTE/STYLE, cue ids)")
    c = cs.add_parser("preview", parents=[common], help="render chosen cues to PNG so you can look at the real Arabic result")
    c.add_argument("file"); c.add_argument("out_dir"); c.add_argument("--cues", required=True, help="cue numbers, e.g. 1,3,7")
    c.add_argument("--encoding"); c.add_argument("--font", default="Noto Sans", help="same default as `subs`; always LOOK at the frames, some fonts render wrong")
    c.add_argument("--no-rtl-embed", action="store_true", help="render the file exactly as written (libass then lays Arabic lines out left-to-right)")
    c = cs.add_parser("rtl-copy", parents=[common], help="write a COPY whose Arabic lines carry invisible RTL marks, for `subs` burning (original untouched)")
    c.add_argument("file"); c.add_argument("-o", "--output", required=True); c.add_argument("--encoding")

    s = sub.add_parser("info", help="show duration and size"); s.add_argument("input")

    a = p.parse_args(argv)
    try:
        if a.cmd == "trim":
            core.trim(a.input, a.output, a.start, a.end, a.duration, a.fast, a.crf, a.force)
        elif a.cmd == "concat":
            core.concat(a.inputs, a.output, a.size[0], a.size[1], a.fps, a.crf, a.force)
        elif a.cmd == "vertical":
            core.vertical(a.input, a.output, a.mode, a.crf, a.force)
        elif a.cmd == "subs":
            core.burn_subs(a.input, a.srt, a.output, a.font_size, a.font, a.position, a.margin, a.crf, a.force, a.style)
        elif a.cmd == "thumbs":
            files = core.thumbs(a.input, a.out_dir, a.every, a.count, a.force)
            print(f"{len(files)} thumbnail(s) in {a.out_dir}")
            return 0
        elif a.cmd == "run":
            print(f"done: {core.run_edit_list(a.edit_list, a.force)}"); return 0
        elif a.cmd == "batch":
            extra = {"position": a.position} if a.kind == "subs" else {}
            r = core.batch(a.kind, a.in_dir, a.out_dir, a.mode, a.force, a.crf, **extra)
            for n in r["done"]:
                print(f"ok      {n}")
            for n, why in r["skipped"]:
                print(f"skipped {n}: {why}")
            for n, why in r["failed"]:
                print(f"FAILED  {n}: {why}")
            print(f"{len(r['done'])} done, {len(r['skipped'])} skipped, {len(r['failed'])} failed")
            return 1 if r["failed"] else 0
        elif a.cmd == "captions":
            return _captions(a)
        elif a.cmd == "info":
            i = core.probe(a.input)
            print(f"{a.input}: {i['width']}x{i['height']}, {i['duration']:.2f}s, audio: {'yes' if i['has_audio'] else 'no'}")
            return 0
        print(f"done: {a.output}")
    except (core.ClipError, OSError, ValueError, _CapErr) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
