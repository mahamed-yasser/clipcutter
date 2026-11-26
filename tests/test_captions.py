import io, json, os, random, subprocess, sys, tempfile, unittest, contextlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from clipcutter import captions as cap, cli

AR = "مرحبا بكم في هذا الفيديو"
SRT = f"1\n00:00:00,000 --> 00:00:03,000\n{AR}\n\n2\n00:00:03,000 --> 00:00:06,000\nسطر ثاني\n"

def cues(s):
    c, p = cap.parse(s); assert not p, p; return c

class Parse(unittest.TestCase):
    def test_srt_basic_and_arabic(self):
        c = cues(SRT); self.assertEqual(len(c), 2); self.assertEqual(c[0].lines, [AR]); self.assertEqual(c[1].start, 3000)
    def test_crlf_bom_and_vtt(self):
        c = cues("\ufeff" + SRT.replace("\n", "\r\n")); self.assertEqual(len(c), 2)
        v = cues("WEBVTT\n\nNOTE x\n\n00:01.000 --> 00:02.500 align:start\nمرحبا\n\n1:02:03.004 --> 1:02:04.000\nBye\n")
        self.assertEqual((v[0].start, v[0].end, v[1].start), (1000, 2500, 3723004))
    def test_bad_blocks_reported_not_raised(self):
        c, p = cap.parse("1\nnot a time\ntext\n\n2\n00:00:61,000 --> 00:00:02,000\nx\n\n3\n00:00:01,000 --> 00:00:02,000\nok\n")
        self.assertEqual(len(c), 1); self.assertEqual(len(p), 2); self.assertTrue(all(x["code"] == "PARSE" for x in p))
    def test_fuzz_never_crashes(self):
        rnd = random.Random(1)
        chars = list("0123456789:,.->\n \rابتث<>{}\\i/WEBVTT") + ["\u200f", "\u202b"]
        for _ in range(400):
            s = "".join(rnd.choice(chars) for _ in range(rnd.randint(0, 200)))
            c, p = cap.parse(s); cap.check(c); cap.fix(c)
    def test_decode(self):
        self.assertEqual(cap.decode_bytes(b"\xef\xbb\xbfabc")[0], "abc")
        t, note = cap.decode_bytes("مرحبا".encode("cp1256")); self.assertEqual(t, "مرحبا"); self.assertIn("GUESS", note)
        self.assertEqual(cap.decode_bytes("مرحبا".encode("utf-16"))[1], "utf-16")
        with self.assertRaises(cap.CaptionError): cap.decode_bytes(b"x" * 5_000_001)

class Check(unittest.TestCase):
    def codes(self, s, profile="netflix-ar"): return [x["code"] for x in cap.check(cues(s), profile)]
    def test_clean_file_has_no_findings(self): self.assertEqual(self.codes(SRT), [])
    def test_timing_rules(self):
        s = "1\n00:00:00,000 --> 00:00:02,000\nأ\n\n2\n00:00:01,500 --> 00:00:01,900\nب\n\n3\n00:00:05,000 --> 00:00:04,000\nج\n\n4\n00:00:06,000 --> 00:00:14,000\nد\n"
        c = self.codes(s)
        for k in ("OVERLAP", "TOO_SHORT", "END_BEFORE_START", "TOO_LONG"): self.assertIn(k, c)
    def test_out_of_order(self):
        self.assertIn("OUT_OF_ORDER", self.codes("1\n00:00:05,000 --> 00:00:06,000\nأ\n\n2\n00:00:01,000 --> 00:00:03,000\nب\n"))
    def test_line_length_counts_visible_letters_only(self):
        ok = "ا" * 42; bad = "ا" * 43
        self.assertNotIn("LINE_TOO_LONG", self.codes(f"1\n00:00:00,000 --> 00:00:05,000\n{ok}\n"))
        self.assertIn("LINE_TOO_LONG", self.codes(f"1\n00:00:00,000 --> 00:00:05,000\n{bad}\n"))
        marked = "ا" * 42 + "\u200f" * 5 + "\u064e" * 5 + "<i></i>"     # marks, diacritics and tags do not count
        self.assertNotIn("LINE_TOO_LONG", self.codes(f"1\n00:00:00,000 --> 00:00:05,000\n{marked}\n"))
    def test_reading_speed_and_profile_none(self):
        s = "1\n00:00:00,000 --> 00:00:01,000\n" + "ا" * 40 + "\n"
        self.assertIn("READING_SPEED", self.codes(s)); self.assertEqual(self.codes(s, "none"), [])
    def test_bidi_marks_alone_are_info_not_error(self):
        r = cap.check(cues("1\n00:00:00,000 --> 00:00:03,000\n\u200fمرحبا\u200f\n"))
        self.assertEqual([(x["code"], x["severity"]) for x in r], [("BIDI_CONTROLS", "info")])
    def test_heuristics_are_review_severity(self):
        r = cap.check(cues("1\n00:00:00,000 --> 00:00:05,000\nأنا أستخدم ClipCutter.\n\n2\n00:00:06,000 --> 00:00:10,000\nسنة ٢٠٢٤ و 2025\n"))
        d = {x["code"]: x["severity"] for x in r}
        self.assertEqual(d["LATIN_RUN_BEFORE_PUNCT"], "review"); self.assertEqual(d["MIXED_DIGITS"], "review")
    def test_latin_only_line_not_flagged_as_arabic_problem(self):
        self.assertNotIn("LATIN_RUN_BEFORE_PUNCT", self.codes("1\n00:00:00,000 --> 00:00:05,000\nHello ClipCutter.\n"))

class Fix(unittest.TestCase):
    def test_rewrap_keeps_letters_and_lines_fit(self):
        long = " ".join(["كلمة"] * 14)
        new, ch = cap.fix(cues(f"1\n00:00:00,000 --> 00:00:09,000\n{long}\n"))
        self.assertEqual(len(new[0].lines), 2); self.assertTrue(all(cap.visible_len(x) <= 42 for x in new[0].lines))
        self.assertTrue(len(new[0].lines[0]) <= len(new[0].lines[1]))
        self.assertEqual("".join(new[0].text().split()), "".join(long.split()))
    def test_unfixable_long_word_reported_not_changed(self):
        w = "ا" * 60
        new, ch = cap.fix(cues(f"1\n00:00:00,000 --> 00:00:09,000\n{w}\n"))
        self.assertEqual(new[0].lines, [w]); self.assertTrue(any("NOT changed" in x for x in ch))
    def test_small_overlap_fixed_big_overlap_left(self):
        s = "1\n00:00:00,000 --> 00:00:03,200\nأ\n\n2\n00:00:03,000 --> 00:00:06,000\nب\n\n3\n00:00:04,000 --> 00:00:08,000\nج\n"
        new, ch = cap.fix(cues(s))
        self.assertEqual(new[0].end, 3000); self.assertEqual(new[1].end, 6000)   # cue 2 overlaps cue 3 by 2000ms: untouched
    def test_never_shifts_start_never_drops_cues_idempotent(self):
        rnd = random.Random(7); parts = []
        for i in range(60):
            st = i * 2000 + rnd.randint(-300, 300); en = st + rnd.randint(300, 3000)
            txt = " ".join(rnd.choice(["كلمة", "ClipCutter", "٣", "3", "هذا"]) for _ in range(rnd.randint(1, 20)))
            f = lambda ms: cap.fmt_time(max(0, ms))
            parts.append(f"{i+1}\n{f(st)} --> {f(en)}\n{txt}\n")
        c = cues("\n".join(parts)); new, _ = cap.fix(c)
        self.assertEqual(len(new), len(c)); self.assertEqual([x.start for x in new], [x.start for x in c])
        new2, ch2 = cap.fix(cap.parse(cap.to_srt(new))[0]); self.assertEqual(cap.to_srt(new2), cap.to_srt(new))
        self.assertFalse([x for x in ch2 if "re-wrapped" in x or "trimmed" in x])
    def test_trim_spaces(self):
        new, ch = cap.fix(cues("1\n00:00:00,000 --> 00:00:03,000\n  مرحبا  \n"))
        self.assertEqual(new[0].lines, ["مرحبا"])

class Files(unittest.TestCase):
    def setUp(self): self.d = tempfile.mkdtemp(); self.src = os.path.join(self.d, "a.srt"); open(self.src, "w", encoding="utf-8").write(SRT)
    def test_original_untouched_and_no_overwrite(self):
        before = open(self.src, "rb").read(); out = os.path.join(self.d, "b.srt")
        cap.write_fixed(self.src, out)
        self.assertEqual(open(self.src, "rb").read(), before); self.assertTrue(os.path.exists(out + ".changes.txt"))
        with self.assertRaises(cap.CaptionError): cap.write_fixed(self.src, out)
        cap.write_fixed(self.src, out, force=True)
    def test_same_file_refused(self):
        with self.assertRaises(cap.CaptionError): cap.write_fixed(self.src, self.src, force=True)
    def test_refuses_when_blocks_unreadable(self):
        bad = os.path.join(self.d, "bad.srt"); open(bad, "w").write("1\nxx\ntext\n")
        with self.assertRaises(cap.CaptionError): cap.write_fixed(bad, os.path.join(self.d, "o.srt"))
    def test_cli_exit_codes_and_json(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): rc = cli.main(["captions", "check", self.src, "--json"])
        self.assertEqual(rc, 0); self.assertEqual(json.loads(buf.getvalue())["cues"], 2)
        bad = os.path.join(self.d, "e.srt"); open(bad, "w").write("1\n00:00:05,000 --> 00:00:01,000\nx\n")
        with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(cli.main(["captions", "check", bad]), 1)
        with contextlib.redirect_stderr(io.StringIO()): self.assertEqual(cli.main(["captions", "check", "/nope.srt"]), 2)

@unittest.skipUnless(subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0, "needs ffmpeg")
class Preview(unittest.TestCase):
    def test_preview_writes_nonblank_png_and_bounds_checked(self):
        d = tempfile.mkdtemp(); src = os.path.join(d, "a.srt"); open(src, "w", encoding="utf-8").write(SRT)
        out = cap.preview_frames(src, os.path.join(d, "f"), [1])
        data = open(out[0], "rb").read(); self.assertTrue(data.startswith(b"\x89PNG"))
        # a frame with text differs from the same size plain frame: compare against a render of an empty-text cue
        plain = os.path.join(d, "p.srt"); open(plain, "w").write("1\n00:00:00,000 --> 00:00:03,000\n \n")
        pf = cap.preview_frames(plain, os.path.join(d, "g"), [1])
        self.assertNotEqual(data, open(pf[0], "rb").read())
        with self.assertRaises(cap.CaptionError): cap.preview_frames(src, os.path.join(d, "f"), [9], force=True)

if __name__ == "__main__":
    unittest.main()
