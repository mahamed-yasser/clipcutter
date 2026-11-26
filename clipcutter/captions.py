"""Caption checking and conservative fixing for SRT/VTT files (Arabic-aware, review-first).

Design rules:
- The original file is never modified. `fix` writes a new file and a human-readable diff.
- Fixes only change the timing of small overlaps and whitespace/line breaks. No character is added,
  removed or replaced, and no cue is deleted. This is enforced in code (see _same_letters).
- Most Arabic/bidi findings are HEURISTIC prompts for a human ("review"), never proof of an error.
  Bidi control characters on their own are reported as information, not as a problem.
- Default limits come from the Netflix Arabic Timed Text Style Guide and its General Requirements
  (42 chars per line, 2 lines, up to 20 chars/second for adult programs, 5/6 s minimum and 7 s
  maximum duration). They are a style preset, not a universal rule. Use profile "none" to turn
  the numeric checks off.
Sources:
  https://partnerhelp.netflixstudios.com/hc/en-us/articles/215517947-Arabic-Timed-Text-Style-Guide
  https://partnerhelp.netflixstudios.com/hc/en-us/articles/215758617-Timed-Text-Style-Guide-General-Requirements
"""
import difflib
import re
import unicodedata

PROFILES = {
    "netflix-ar": {"max_chars": 42, "max_lines": 2, "max_cps": 20.0, "min_dur_ms": 833, "max_dur_ms": 7000},
    "none": {"max_chars": None, "max_lines": None, "max_cps": None, "min_dur_ms": None, "max_dur_ms": None},
}
MAX_OVERLAP_FIX_MS = 500        # bigger overlaps may be intentional (two speakers); leave for a human
MAX_FILE_BYTES = 5_000_000

BIDI_CONTROLS = "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u061c"
_TAG = re.compile(r"<[^>]*>|\{\\[^}]*\}")
_ARABIC = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]")
_ARABIC_INDIC = re.compile(r"[\u0660-\u0669\u06f0-\u06f9]")
_WESTERN = re.compile(r"[0-9]")
_TIME = re.compile(r"^(?:(\d{1,3}):)?(\d{1,2}):(\d{2})[,.](\d{1,3})$")


LAST_PARSE_LOSSY = set()   # what an SRT copy of the last parsed file would lose (VTT-only features)


class CaptionError(Exception):
    pass


class Cue:
    def __init__(self, start, end, lines, index=None, source_line=0):
        self.start, self.end, self.lines, self.index, self.source_line = start, end, lines, index, source_line

    def text(self):
        return "\n".join(self.lines)


def decode_bytes(data, encoding=None):
    """Return (text, encoding_note). With an explicit encoding the decode is strict: bad bytes raise.
    Without one, UTF-8 (strict) is tried; a file that is not valid UTF-8 is read as cp1256 and the note
    contains "GUESS". `fix` refuses to work from a guess. NUL bytes without a BOM usually mean UTF-16."""
    if len(data) > MAX_FILE_BYTES:
        raise CaptionError("file is larger than 5 MB")
    if encoding:
        try:
            return data.decode(encoding).lstrip("\ufeff"), f"{encoding} (chosen by you)"
        except (UnicodeDecodeError, LookupError) as e:
            raise CaptionError(f"cannot read the file as {encoding}: {e}")
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data[3:].decode("utf-8"), "utf-8 (BOM removed)"
        except UnicodeDecodeError:
            raise CaptionError("file has a UTF-8 BOM but contains invalid UTF-8 bytes (damaged or truncated)")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            raise CaptionError("file looks like UTF-16 but is damaged")
    if b"\x00" in data:
        raise CaptionError("file contains NUL bytes and no BOM: probably UTF-16. Re-run with --encoding utf-16-le or utf-16-be")
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1256", errors="replace"), "cp1256 (GUESS: not valid UTF-8; confirm the text looks right, or pass --encoding)"


def _t(s):
    m = _TIME.match(s.strip())
    if not m:
        return None
    h, mi, se, ms = m.groups()
    if int(mi) > 59 or int(se) > 59:
        return None
    return ((int(h or 0) * 60 + int(mi)) * 60 + int(se)) * 1000 + int(ms.ljust(3, "0"))


def fmt_time(ms):
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def parse(text):
    """Parse SRT or WebVTT. Returns (cues, parse_problems). Never raises on bad content."""
    text = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    problems, cues, blocks, cur, start_line = [], [], [], [], 1
    global LAST_PARSE_LOSSY
    lossy = LAST_PARSE_LOSSY = set()
    for n, ln in enumerate(text.split("\n"), 1):
        if ln.strip() == "":
            if cur:
                blocks.append((start_line, cur))
                cur = []
        else:
            if not cur:
                start_line = n
            cur.append(ln)
    if cur:
        blocks.append((start_line, cur))
    for sl, b in blocks:
        head = b[0].strip()
        if head.upper().startswith("WEBVTT"):
            if len(head) > 6 or len(b) > 1:
                lossy.add("WEBVTT header text")
            continue
        if head.split(" ")[0] in ("NOTE", "STYLE", "REGION"):
            lossy.add(head.split(" ")[0] + " block")
            continue
        k, index = 0, None
        if "-->" not in b[0]:
            if re.fullmatch(r"\d+", head):
                index = int(head)
            else:
                lossy.add("cue identifier")
            k = 1
        if k >= len(b) or "-->" not in b[k]:
            problems.append({"cue": None, "line": sl, "code": "PARSE", "severity": "error",
                             "msg": "block has no timing line (start --> end)"})
            continue
        left, right = b[k].split("-->", 1)
        st = _t(left)
        rest = right.strip().split()
        en = _t(rest[0]) if rest else None
        if len(rest) > 1:
            lossy.add("cue settings (position/align/...)")
        if st is None or en is None:
            problems.append({"cue": None, "line": sl + k, "code": "PARSE", "severity": "error", "msg": "unreadable timestamp"})
            continue
        cues.append(Cue(st, en, [x.rstrip() for x in b[k + 1:]], index, sl))
    return cues, problems


def visible_len(line):
    s = _TAG.sub("", line)
    return sum(1 for c in s if c not in BIDI_CONTROLS and unicodedata.category(c) != "Mn")


def _issue(i, cue, code, sev, msg):
    return {"cue": i + 1, "line": cue.source_line, "code": code, "severity": sev, "msg": msg}


def check(cues, profile="netflix-ar"):
    """Return a list of issues. severity: error (cannot work), warn (breaks the preset),
    review (heuristic, a human decides), info (no action needed)."""
    p = PROFILES[profile] if isinstance(profile, str) else profile
    out = []
    for i, c in enumerate(cues):
        txt = c.text()
        dur = c.end - c.start
        if c.end <= c.start:
            out.append(_issue(i, c, "END_BEFORE_START", "error", "end time is not after start time"))
        if not "".join(c.lines).strip():
            out.append(_issue(i, c, "EMPTY", "warn", "cue has no text"))
        if i and c.start < cues[i - 1].start:
            out.append(_issue(i, c, "OUT_OF_ORDER", "warn", "cue starts before the previous cue starts"))
        elif i and c.start < cues[i - 1].end:
            out.append(_issue(i, c, "OVERLAP", "warn", f"overlaps previous cue by {cues[i - 1].end - c.start} ms"))
        if p["min_dur_ms"] and 0 < dur < p["min_dur_ms"]:
            out.append(_issue(i, c, "TOO_SHORT", "warn", f"{dur} ms is under {p['min_dur_ms']} ms"))
        if p["max_dur_ms"] and dur > p["max_dur_ms"]:
            out.append(_issue(i, c, "TOO_LONG", "warn", f"{dur} ms is over {p['max_dur_ms']} ms"))
        if p["max_lines"] and len(c.lines) > p["max_lines"]:
            out.append(_issue(i, c, "TOO_MANY_LINES", "warn", f"{len(c.lines)} lines, limit {p['max_lines']}"))
        if p["max_chars"]:
            longest = max([visible_len(ln) for ln in c.lines] or [0])
            if longest > p["max_chars"]:
                out.append(_issue(i, c, "LINE_TOO_LONG", "warn", f"line of {longest} characters, limit {p['max_chars']}"))
        if p["max_cps"] and dur > 0:
            n = sum(visible_len(x) for x in c.lines) + max(0, len(c.lines) - 1)
            cps = n / (dur / 1000.0)
            if cps > p["max_cps"]:
                out.append(_issue(i, c, "READING_SPEED", "warn", f"{cps:.1f} characters/second, limit {p['max_cps']:g}"))
        # ---- heuristics below: prompts for a human, never proof ----
        if any(ch in txt for ch in BIDI_CONTROLS):
            out.append(_issue(i, c, "BIDI_CONTROLS", "info", "contains bidi control characters (not an error by itself)"))
        if _ARABIC_INDIC.search(txt) and _WESTERN.search(txt):
            out.append(_issue(i, c, "MIXED_DIGITS", "review", "Arabic-Indic and Western digits both appear; check the project's digit style"))
        if len(c.lines) == 2 and len(c.lines[1].split()) == 1 and len(c.lines[0].split()) > 2:
            out.append(_issue(i, c, "ONE_WORD_LAST_LINE", "review", "second line is a single word (the style guide prefers avoiding this)"))
        for ln in c.lines:
            s = _TAG.sub("", ln).strip().strip(BIDI_CONTROLS)
            if _ARABIC.search(s) and re.search(r"[A-Za-z0-9]+[?!.,;:\u061f\u061b\u060c]+$", s):
                out.append(_issue(i, c, "LATIN_RUN_BEFORE_PUNCT", "review",
                                  "Arabic line ends with a Latin/number run then punctuation; punctuation can display on an unexpected side. Check the rendered frame"))
                break
    return out


def summarize(issues):
    s = {"error": 0, "warn": 0, "review": 0, "info": 0}
    for x in issues:
        s[x["severity"]] += 1
    return s


# ------------------------------------------------------------------ fixing
def _same_letters(a, b):
    """Same words in the same order (only spaces and line breaks may differ)."""
    return a.split() == b.split() and "".join(a.split()) == "".join(b.split())


MAX_REWRAP_WORDS = 120          # longer cues are never re-wrapped (keeps the work linear and the result reviewable)
_DASH = re.compile(r"^[\-\u2013\u2014]\s")


def _split_balanced(text, max_chars):
    """Split one subtitle into at most 2 lines at spaces, second line at least as long as the first,
    each line within max_chars. Returns a list of lines, or None if that needs cutting words."""
    words = text.split()
    if not words or len(words) > MAX_REWRAP_WORDS:
        return None
    if visible_len(" ".join(words)) <= max_chars:
        return [" ".join(words)]
    best = None
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        la, lb = visible_len(a), visible_len(b)
        if la <= max_chars and lb <= max_chars and la <= lb:
            score = (lb - la) + (50 if len(words[k:]) == 1 else 0)
            if best is None or score < best[0]:
                best = (score, [a, b])
    return best[1] if best else None


def _rewrap_blocker(lines):
    """Why this cue must NOT be re-wrapped automatically (None = it may be)."""
    if any(_TAG.search(x) for x in lines):
        return "contains tags (<i>, {\\an8}, ...); line breaks inside tags are not changed automatically"
    if len(lines) > 1 and any(_DASH.match(x.strip()) for x in lines):
        return "looks like dialogue (lines start with a dash); merging lines could mix two speakers"
    if len(lines) > 2:
        return "has more than 2 lines of the writer's own breaks"
    if sum(len(x.split()) for x in lines) > MAX_REWRAP_WORDS:
        return "is too long to re-wrap safely"
    return None


def _structure(cue):
    return (len(cue.lines) if False else 0, sum(1 for x in cue.lines if _DASH.match(x.strip())), len(_TAG.findall(cue.text())))


def fix(cues, profile="netflix-ar"):
    """Return (new_cues, changes). Works on copies. Changes are human-readable strings.
    After fixing, the result is re-checked; warnings that did not exist before are reported as NEW WARNING."""
    p = PROFILES[profile] if isinstance(profile, str) else profile
    new = [Cue(c.start, c.end, list(c.lines), c.index, c.source_line) for c in cues]
    changes = []
    for i, c in enumerate(new):
        trimmed = [x.strip(" \t") for x in c.lines]
        if trimmed != c.lines:
            changes.append(f"cue {i + 1}: trimmed leading/trailing spaces")
            c.lines = trimmed
        if p["max_chars"] and p["max_lines"] and p["max_lines"] >= 2:
            over = len(c.lines) > p["max_lines"] or any(visible_len(x) > p["max_chars"] for x in c.lines)
            if over:
                why = _rewrap_blocker(c.lines)
                if why:
                    changes.append(f"cue {i + 1}: NOT changed ({why}); needs a human")
                    continue
                cand = _split_balanced(" ".join(c.lines), p["max_chars"])
                if cand and len(cand) <= p["max_lines"] and _same_letters(c.text(), "\n".join(cand)):
                    changes.append(f"cue {i + 1}: re-wrapped {c.lines!r} -> {cand!r}")
                    c.lines = cand
                else:
                    changes.append(f"cue {i + 1}: NOT changed, cannot fit without cutting words (needs a human)")
    for i in range(1, len(new)):
        prev, cur = new[i - 1], new[i]
        ov = prev.end - cur.start
        if 0 < ov <= MAX_OVERLAP_FIX_MS and cur.start > prev.start:
            newdur = cur.start - prev.start
            if p["min_dur_ms"] and newdur < p["min_dur_ms"]:
                changes.append(f"cue {i}: overlap of {ov} ms NOT fixed, ending it early would leave it {newdur} ms (under {p['min_dur_ms']} ms); needs a human")
                continue
            if p["max_cps"]:
                n = sum(visible_len(x) for x in prev.lines) + max(0, len(prev.lines) - 1)
                if n / (newdur / 1000.0) > p["max_cps"] >= n / ((prev.end - prev.start) / 1000.0 or 1):
                    changes.append(f"cue {i}: overlap of {ov} ms NOT fixed, ending it early would push reading speed over {p['max_cps']:g} chars/s; needs a human")
                    continue
            changes.append(f"cue {i}: end {fmt_time(prev.end)} -> {fmt_time(cur.start)} (overlap of {ov} ms)")
            prev.end = cur.start
    assert len(new) == len(cues), "internal error: cue count changed"
    for a, b in zip(cues, new):
        assert _same_letters(a.text(), b.text()), "internal error: fix changed the text"
        assert _structure(a)[1:] == _structure(b)[1:], "internal error: fix changed dialogue dashes or tags"
    before = {(x["cue"], x["code"]) for x in check(cues, profile)}
    for x in check(new, profile):
        if (x["cue"], x["code"]) not in before and x["severity"] in ("warn", "error"):
            changes.append(f"NEW WARNING after fixing, cue {x['cue']} {x['code']}: {x['msg']}")
    return new, changes


def to_srt(cues):
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{fmt_time(c.start)} --> {fmt_time(c.end)}\n" + "\n".join(c.lines) + "\n")
    return "\n".join(out)


def diff(old_cues, new_cues):
    a, b = to_srt(old_cues).splitlines(), to_srt(new_cues).splitlines()
    return "\n".join(difflib.unified_diff(a, b, "original (renumbered)", "fixed", lineterm=""))


# ------------------------------------------------------------------ files and preview
_ARABIC = re.compile("[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")


def rtl_embed(cues):
    """Return an SRT string where every line that contains Arabic letters is wrapped in RLE ... PDF (U+202B/U+202C).
    Why: libass starts each line left-to-right. For a line like "Python <arabic>." that puts the full stop on the
    wrong end and mis-orders "<arabic> (2026): ... x = 0". Real renders showed U+200F (RLM) does NOT fix this,
    RLE ... PDF does. Only invisible control characters are added; no letter changes. Lines that already start with
    a bidi control are left alone."""
    out = []
    for c in cues:
        lines = [l if (not _ARABIC.search(l) or (l and l[0] in BIDI_CONTROLS)) else "\u202b" + l + "\u202c" for l in c.lines]
        out.append(Cue(c.start, c.end, lines, c.index, c.source_line))
    return to_srt(out)


def load_file(path, encoding=None):
    import os
    if not os.path.isfile(path):
        raise CaptionError(f"file not found: {path}")
    with open(path, "rb") as f:
        data = f.read(MAX_FILE_BYTES + 1)
    text, enc = decode_bytes(data, encoding)
    cues, problems = parse(text)
    return cues, problems, enc


def write_fixed(src, dst, profile="netflix-ar", force=False, encoding=None, allow_srt_conversion=False):
    """Write a fixed SRT copy plus DST.changes.txt. Refuses to touch the original (also through links),
    to guess an encoding, or to silently drop VTT-only features."""
    import os
    import tempfile
    if not dst.lower().endswith(".srt"):
        raise CaptionError("the fixed copy is always written as SRT: give an output name ending in .srt")
    chg = dst + ".changes.txt"
    for p in (dst, chg):
        if os.path.islink(p):
            raise CaptionError(f"{p} is a symbolic link; refusing to write through it")
        if os.path.exists(os.path.realpath(src)) and os.path.exists(p) and os.path.samefile(src, p):
            raise CaptionError("output is the same file as the input; the original is never modified")
        if os.path.exists(p) and not force:
            raise CaptionError(f"{p} already exists (use --force to overwrite)")
    if os.path.realpath(dst) == os.path.realpath(src):
        raise CaptionError("output is the same file as the input; the original is never modified")
    cues, problems, enc = load_file(src, encoding)
    if "GUESS" in enc:
        raise CaptionError("the text encoding is only a guess (" + enc + "). Fixing would write that guess to a new file. "
                           "Look at `check` output, then re-run with --encoding cp1256 (or the right one) if the text is correct")
    if problems:
        raise CaptionError(f"{len(problems)} block(s) could not be read (first: line {problems[0]['line']}); fix or remove them first")
    if LAST_PARSE_LOSSY and not allow_srt_conversion:
        raise CaptionError("an SRT copy would lose: " + ", ".join(sorted(LAST_PARSE_LOSSY)) + ". Refusing; use --allow-srt-conversion if that is acceptable")
    lost = sorted(LAST_PARSE_LOSSY)
    new, changes = fix(cues, profile)
    body = to_srt(new)
    note = (f"input: {src}\nencoding read as: {enc}\nprofile: {profile}\n"
            + (f"DROPPED by SRT conversion (you allowed it): {', '.join(lost)}\n" if lost else "")
            + "Cue numbers are renumbered from 1. REVIEW EVERY CHANGE. Only spaces, line breaks and small overlaps are touched; words are not changed.\n\n"
            + ("\n".join(changes) if changes else "no changes needed") + "\n\n" + diff(cues, new) + "\n")
    for path, data in ((dst, body), (chg, note)):   # write to a temp file in the same folder, then rename
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".cap-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    return len([c for c in changes if "NOT" not in c and not c.startswith("NEW WARNING")])


def preview_frames(srt, out_dir, cue_numbers, font="Noto Sans", size=(1280, 720), force=False, encoding=None, rtl=True):
    """Render chosen cues over a plain background to PNG so a human can look at the real shaping/RTL result.
    Uses the same subtitle styling as `clipcutter subs`. Returns the list of PNG paths."""
    import os
    import subprocess
    from . import core
    core.need_ffmpeg()
    cues, problems, _ = load_file(srt, encoding)
    if not cues:
        raise CaptionError("no cues to preview")
    os.makedirs(out_dir, exist_ok=True)
    # ffmpeg's subtitles filter mis-parses paths containing quotes, colons, commas or brackets (a hazard in
    # `clipcutter subs` too). Preview works on a copy under a plain name, so any input path is safe.
    import shutil
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="capprev-")
    plain = os.path.join(tmpdir, "preview.srt")
    if rtl:
        with open(plain, "w", encoding="utf-8", newline="\n") as f:
            f.write(rtl_embed(cues))
    else:
        shutil.copyfile(srt, plain)
    try:
        return _render_cues(plain, cues, out_dir, cue_numbers, font, size, force)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _render_cues(srt, cues, out_dir, cue_numbers, font, size, force):
    import os
    import subprocess
    from . import core
    outs = []
    for n in cue_numbers:
        if not 1 <= n <= len(cues):
            raise CaptionError(f"cue {n} does not exist (file has {len(cues)})")
        c = cues[n - 1]
        dst = os.path.join(out_dir, f"cue-{n:03d}.png")
        if os.path.exists(dst) and not force:
            raise CaptionError(f"{dst} already exists (use --force to overwrite)")
        mid = (c.start + c.end) / 2000.0
        cmd = core.burn_subs_cmd("x", os.path.abspath(srt), "x", font=font, video_size=size)
        vf = cmd[cmd.index("-vf") + 1]
        full = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", f"color=c=0x203040:s={size[0]}x{size[1]}:r=25:d={mid + 2:.3f}",
                "-vf", vf, "-ss", f"{mid:.3f}", "-frames:v", "1", dst]
        r = subprocess.run(full, capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(dst):
            raise CaptionError(f"preview failed for cue {n}: {r.stderr.strip()[-300:]}")
        outs.append(dst)
    return outs
