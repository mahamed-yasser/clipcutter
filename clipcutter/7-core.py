"""ClipCutter: build ffmpeg commands (pure functions, easy to test) and run them."""
import contextlib, json, os, shutil, subprocess, tempfile
from typing import List, Optional


class ClipError(Exception):
    pass


def need_ffmpeg():
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise ClipError(f"{tool} not found. Install ffmpeg and make sure it is on PATH.")


def parse_time(v) -> float:
    """Seconds as number, '90', '1:30', '00:01:30.5'."""
    if isinstance(v, (int, float)):
        return float(v)
    parts = str(v).strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ClipError(f"bad time: {v!r}")
    try:
        secs = 0.0
        for p in parts:
            secs = secs * 60 + float(p)
    except ValueError:
        raise ClipError(f"bad time: {v!r}")
    if secs < 0:
        raise ClipError(f"negative time: {v!r}")
    return secs


def enc(crf=22):
    if not 0 <= int(crf) <= 51:
        raise ClipError("crf must be between 0 (best) and 51 (smallest)")
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(int(crf)), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"]


ENC = enc()


def trim_cmd(src, dst, start, end=None, duration=None, fast=False, crf=22) -> List[str]:
    s = parse_time(start)
    if end is not None and duration is not None:
        raise ClipError("give end or duration, not both")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{s:.3f}", "-i", src]
    if end is not None:
        e = parse_time(end)
        if e <= s:
            raise ClipError("end must be after start")
        cmd += ["-t", f"{e - s:.3f}"]
    elif duration is not None:
        d = parse_time(duration)
        if d <= 0:
            raise ClipError("duration must be positive")
        cmd += ["-t", f"{d:.3f}"]
    if fast:  # stream copy: instant, but starts at the nearest keyframe before 'start'
        return cmd + ["-c", "copy", "-avoid_negative_ts", "make_zero", dst]
    return cmd + enc(crf) + [dst]


def vertical_filter(width=1080, height=1920, mode="crop") -> str:
    """9:16 output. 'crop' fills the frame, 'blur' keeps the whole shot over a blurred copy."""
    if mode == "crop":
        return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
    if mode == "blur":
        return (f"split[a][b];[a]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},boxblur=30:5[bg];[b]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
    raise ClipError("mode must be crop or blur")


def vertical_cmd(src, dst, mode="crop", width=1080, height=1920, crf=22) -> List[str]:
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
            "-vf", vertical_filter(width, height, mode)] + enc(crf) + [dst]


def _esc_filter_path(p: str) -> str:
    return p.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


ASS_PLAY_RES = 288  # height of the canvas libass uses for SRT files
POSITIONS = {"bottom": 2, "top": 6, "middle": 10}  # force_style uses the legacy SSA numbering, not \\an numbers


def burn_subs_cmd(src, srt, dst, font_size=None, font="Noto Sans", position="bottom", margin=None, crf=22,
                  video_size=None) -> List[str]:
    """font_size and margin are in video pixels. Left empty they scale with the video height
    (4% font, 5% margin), so the same call looks right on 720p and on a 1080x1920 reel."""
    if position not in POSITIONS:
        raise ClipError("position must be bottom, middle or top")
    if "," in font or "'" in font or "=" in font:
        raise ClipError("font name has characters that are not allowed")
    h = video_size[1] if video_size else ASS_PLAY_RES
    fs = int(font_size) if font_size else max(10, round(h * 0.04))
    mg = int(margin) if margin is not None else round(h * 0.05)
    if fs < 6 or fs > 400:
        raise ClipError("font size must be between 6 and 400 pixels")
    # ffmpeg renders SRT on a virtual 288-pixel-high canvas, so convert real pixels to that scale.
    k = ASS_PLAY_RES / h
    style = (f"FontName={font},FontSize={fs * k:.2f},Outline={max(1.0, fs / 12) * k:.2f},Shadow=0,"
             f"Alignment={POSITIONS[position]},MarginV={mg * k:.2f}")
    vf = f"subtitles='{_esc_filter_path(srt)}':force_style='{style}'"
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-vf", vf] + enc(crf) + [dst]


def thumbs_cmd(src, out_pattern, every=None, count=None, duration=None, width=640) -> List[str]:
    if (every is None) == (count is None):
        raise ClipError("give either every (seconds) or count")
    if every is not None:
        if every <= 0:
            raise ClipError("every must be positive")
        rate = f"1/{every}"
    else:
        if count < 1:
            raise ClipError("count must be at least 1")
        if not duration:
            raise ClipError("count needs the video duration")
        rate = f"{count}/{duration:.3f}"
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
            "-vf", f"fps={rate},scale={width}:-2", "-q:v", "3", out_pattern]


def probe(path) -> dict:
    need_ffmpeg()
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                        "-of", "json", path], capture_output=True, text=True)
    if r.returncode != 0:
        raise ClipError(f"cannot read {path}: {r.stderr.strip()[:200]}")
    d = json.loads(r.stdout)
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    return {"duration": float(d["format"].get("duration", 0)), "width": v.get("width"), "height": v.get("height"),
            "has_audio": any(s.get("codec_type") == "audio" for s in d.get("streams", []))}


def run(cmd: List[str]):
    need_ffmpeg()
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise ClipError(f"ffmpeg failed: {r.stderr.strip()[-400:]}")


def _check_input(p):
    if not os.path.isfile(p):
        raise ClipError(f"file not found: {p}")


def _check_output(dst, force=False, inputs=()):
    if any(os.path.abspath(dst) == os.path.abspath(i) for i in inputs):
        raise ClipError("output is the same file as an input")
    if os.path.exists(dst) and not force:
        raise ClipError(f"{dst} already exists (use --force to overwrite)")
    d = os.path.dirname(os.path.abspath(dst))
    if not os.path.isdir(d):
        raise ClipError(f"output folder does not exist: {d}")


@contextlib.contextmanager
def _atomic(dst):
    """Yield a temporary path next to dst; move it into place only if the work succeeded.
    A failed or interrupted run therefore never leaves a half-written file under the real name."""
    d, name = os.path.split(os.path.abspath(dst))
    tmp = os.path.join(d, f".{name}.partial{os.path.splitext(name)[1]}")
    try:
        yield tmp
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _verify(path):
    """ffmpeg can exit 0 and still write an empty file (for example a cut that starts after the end)."""
    info = probe(path)
    if not info["width"] or info["duration"] <= 0:
        raise ClipError("ffmpeg produced an empty video (check start/end times against the input length)")


def trim(src, dst, start, end=None, duration=None, fast=False, crf=22, force=False):
    _check_input(src); _check_output(dst, force, [src])
    need_ffmpeg()
    if parse_time(start) >= probe(src)["duration"]:
        raise ClipError(f"start ({start}) is at or after the end of {src}")
    cmd_for = lambda out: trim_cmd(src, out, start, end, duration, fast, crf)
    cmd_for("x")  # validate arguments before touching the disk
    with _atomic(dst) as tmp:
        run(cmd_for(tmp))
        _verify(tmp)


def vertical(src, dst, mode="crop", crf=22, force=False):
    _check_input(src); _check_output(dst, force, [src])
    with _atomic(dst) as tmp:
        run(vertical_cmd(src, tmp, mode, crf=crf))
        _verify(tmp)


def burn_subs(src, srt, dst, font_size=None, font="Noto Sans", position="bottom", margin=None, crf=22, force=False):
    _check_input(src); _check_input(srt); _check_output(dst, force, [src, srt])
    info = probe(src)
    if not info["width"]:
        raise ClipError(f"{src} has no video stream")
    with _atomic(dst) as tmp:
        run(burn_subs_cmd(src, srt, tmp, font_size, font, position, margin, crf, (info["width"], info["height"])))
        _verify(tmp)


def thumbs(src, out_dir, every=None, count=None, force=False):
    _check_input(src)
    os.makedirs(out_dir, exist_ok=True)
    old = [f for f in os.listdir(out_dir) if f.startswith("thumb_")]
    if old and not force:
        raise ClipError(f"{out_dir} already has thumbnails (use --force to overwrite)")
    for f in old:
        os.remove(os.path.join(out_dir, f))
    info = probe(src)
    pattern = os.path.join(out_dir, "thumb_%03d.jpg")
    run(thumbs_cmd(src, pattern, every, count, info["duration"]))
    return sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.startswith("thumb_"))


def concat(srcs: List[str], dst, width=1280, height=720, fps=30, crf=22, force=False):
    """Join clips. Every clip is first normalised to the same size, fps and audio, so mixed sources work."""
    if len(srcs) < 2:
        raise ClipError("concat needs at least two clips")
    if width < 16 or height < 16 or width % 2 or height % 2:
        raise ClipError("width and height must be even and at least 16")
    if not 1 <= fps <= 120:
        raise ClipError("fps must be between 1 and 120")
    for s in srcs:
        _check_input(s)
    _check_output(dst, force, srcs)
    need_ffmpeg()
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for i, s in enumerate(srcs):
            out = os.path.join(tmp, f"part{i}.mp4")
            vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                  f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}")
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", s]
            if not probe(s)["has_audio"]:
                cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-shortest"]
            cmd += ["-vf", vf, "-ar", "44100", "-ac", "2"] + enc(crf) + [out]
            run(cmd)
            parts.append(out)
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w") as f:
            for p in parts:
                f.write("file '" + p.replace("'", "'\\''") + "'\n")
        with _atomic(dst) as out_tmp:
            run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
                 "-c", "copy", "-movflags", "+faststart", out_tmp])
            _verify(out_tmp)


EDIT_KEYS = {"output", "clips", "vertical", "subtitles", "subtitle_style", "crf", "size", "fps"}
CLIP_KEYS = {"file", "start", "end", "duration"}


def run_edit_list(path, force=False):
    """Run a JSON edit list. Keys: output, clips[{file,start,end|duration}], vertical (crop|blur),
    subtitles (srt), subtitle_style {font,font_size,position,margin}, crf, size "WxH", fps.
    Paths are relative to the JSON file. Unknown keys are rejected so typos do not pass silently."""
    base = os.path.dirname(os.path.abspath(path))
    try:
        with open(path, encoding="utf-8") as f:
            spec = json.load(f)
    except json.JSONDecodeError as e:
        raise ClipError(f"edit list is not valid JSON: {e}")
    if not isinstance(spec, dict):
        raise ClipError("edit list must be a JSON object")
    unknown = set(spec) - EDIT_KEYS
    if unknown:
        raise ClipError(f"unknown key(s) in edit list: {', '.join(sorted(unknown))}")
    for key in ("output", "clips"):
        if key not in spec:
            raise ClipError(f"edit list missing '{key}'")
    if not isinstance(spec["clips"], list) or not spec["clips"]:
        raise ClipError("edit list has no clips")
    if spec.get("vertical") not in (None, "crop", "blur"):
        raise ClipError("'vertical' must be crop or blur")
    rel = lambda p: p if os.path.isabs(p) else os.path.join(base, p)
    width, height = 1280, 720
    if "size" in spec:
        try:
            width, height = (int(x) for x in str(spec["size"]).lower().split("x"))
        except ValueError:
            raise ClipError("'size' must look like 1280x720")
    fps, crf = spec.get("fps", 30), spec.get("crf", 22)
    style = spec.get("subtitle_style", {})
    out = rel(spec["output"])
    _check_output(out, force)
    for i, c in enumerate(spec["clips"], 1):
        if not isinstance(c, dict) or "file" not in c:
            raise ClipError(f"clip {i} has no 'file'")
        if set(c) - CLIP_KEYS:
            raise ClipError(f"clip {i}: unknown key(s) {', '.join(sorted(set(c) - CLIP_KEYS))}")
        _check_input(rel(c["file"]))
        trim_cmd("x", "y", c.get("start", 0), c.get("end"), c.get("duration"))  # validate times up front
    if spec.get("subtitles"):
        _check_input(rel(spec["subtitles"]))
    with tempfile.TemporaryDirectory() as tmp:
        cur = []
        for i, c in enumerate(spec["clips"]):
            t = os.path.join(tmp, f"c{i}.mp4")
            trim(rel(c["file"]), t, c.get("start", 0), c.get("end"), c.get("duration"), crf=crf)
            cur.append(t)
        stage = os.path.join(tmp, "joined.mp4")
        if len(cur) > 1:
            concat(cur, stage, width, height, fps, crf)
        else:
            shutil.copy(cur[0], stage)
        if spec.get("vertical"):
            v = os.path.join(tmp, "vert.mp4")
            vertical(stage, v, spec["vertical"], crf)
            stage = v
        if spec.get("subtitles"):
            s2 = os.path.join(tmp, "subs.mp4")
            burn_subs(stage, rel(spec["subtitles"]), s2, style.get("font_size"), style.get("font", "Noto Sans"),
                      style.get("position", "bottom"), style.get("margin"), crf)
            stage = s2
        shutil.copy(stage, out)
    return out


VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}


def batch(kind, in_dir, out_dir, mode="crop", force=False, crf=22, **subs_opts):
    """Process every video directly inside in_dir (no sub-folders) into out_dir, one at a time.
    kind: 'vertical' or 'subs' (uses NAME.srt next to NAME.mp4; videos without one are skipped).
    One bad file never stops the rest. Existing outputs are skipped unless force.
    Returns {'done': [...], 'skipped': [(name, reason)], 'failed': [(name, reason)]}."""
    if kind not in ("vertical", "subs"):
        raise ClipError("batch kind must be vertical or subs")
    if not os.path.isdir(in_dir):
        raise ClipError(f"input folder not found: {in_dir}")
    if os.path.abspath(in_dir) == os.path.abspath(out_dir):
        raise ClipError("output folder must be different from the input folder")
    os.makedirs(out_dir, exist_ok=True)
    names = sorted(n for n in os.listdir(in_dir)
                   if os.path.splitext(n)[1].lower() in VIDEO_EXT and os.path.isfile(os.path.join(in_dir, n)))
    if not names:
        raise ClipError(f"no video files ({', '.join(sorted(VIDEO_EXT))}) in {in_dir}")
    stems = [os.path.splitext(n)[0] for n in names]
    dupes = {x for x in stems if stems.count(x) > 1}
    if dupes:
        raise ClipError(f"files share a name and would collide in the output: {', '.join(sorted(dupes))}")
    result = {"done": [], "skipped": [], "failed": []}
    for n in names:
        src = os.path.join(in_dir, n)
        stem = os.path.splitext(n)[0]
        dst = os.path.join(out_dir, stem + ".mp4")
        if os.path.exists(dst) and not force:
            result["skipped"].append((n, "output exists (use --force)"))
            continue
        try:
            if kind == "vertical":
                vertical(src, dst, mode, crf=crf, force=True)
            else:
                srt = os.path.join(in_dir, stem + ".srt")
                if not os.path.isfile(srt):
                    result["skipped"].append((n, "no matching .srt"))
                    continue
                burn_subs(src, srt, dst, crf=crf, force=True, **subs_opts)
            result["done"].append(n)
        except ClipError as e:
            result["failed"].append((n, str(e)))
    return result
