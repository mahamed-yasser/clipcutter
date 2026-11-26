"""Regressions for the independent review of the caption workflow (each case reproduced on local/caption-v1 first)."""
import os, subprocess, sys, tempfile, time, unittest
from clipcutter import captions as cap

def w(path, data, mode="wb"):
    with open(path, mode) as f:
        f.write(data)
def rd(path):
    with open(path, "rb") as f:
        return f.read()

class Review(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = lambda n: os.path.join(self.d, n)

    def test_symlink_output_cannot_destroy_input(self):
        a = self.p("a.srt"); w(a, "1\n00:00:00,000 --> 00:00:03,000\nمرحبا\n".encode())
        before = rd(a); os.symlink(a, self.p("link.srt"))
        with self.assertRaises(cap.CaptionError): cap.write_fixed(a, self.p("link.srt"), force=True)
        self.assertEqual(rd(a), before)
        # hard link to the input too
        os.link(a, self.p("hard.srt"))
        with self.assertRaises(cap.CaptionError): cap.write_fixed(a, self.p("hard.srt"), force=True)
        self.assertEqual(rd(a), before)
        # changes file as a symlink to the input
        os.symlink(a, self.p("o.srt.changes.txt"))
        with self.assertRaises(cap.CaptionError): cap.write_fixed(a, self.p("o.srt"), force=True)
        self.assertEqual(rd(a), before)

    def test_vtt_input_with_vtt_output_name_refused(self):
        a = self.p("a.vtt"); w(a, "WEBVTT\n\n00:01.000 --> 00:03.000\nمرحبا\n".encode())
        with self.assertRaises(cap.CaptionError): cap.write_fixed(a, self.p("o.vtt"))

    def test_encoding_guess_is_never_written_but_explicit_encoding_works(self):
        a = self.p("c.srt"); w(a, ("1\n00:00:00,000 --> 00:00:03,000\n" + "مرحبا يا عالم" + "\n").encode("cp1256"))
        with self.assertRaises(cap.CaptionError) as e: cap.write_fixed(a, self.p("o.srt"))
        self.assertIn("guess", str(e.exception).lower()); self.assertFalse(os.path.exists(self.p("o.srt")))
        cap.write_fixed(a, self.p("o.srt"), encoding="cp1256")
        self.assertIn("مرحبا يا عالم", rd(self.p("o.srt")).decode("utf-8"))
        with self.assertRaises(cap.CaptionError): cap.write_fixed(a, self.p("o2.srt"), encoding="utf-8")   # wrong explicit choice fails loudly

    def test_truncated_utf8_and_utf16_without_bom(self):
        full = "1\n00:00:00,000 --> 00:00:03,000\nمرحبا يا عالم\n".encode()
        cut = self.p("cut.srt"); w(cut, full[:-2])
        with self.assertRaises(cap.CaptionError): cap.write_fixed(cut, self.p("o.srt"))    # guess -> refuse
        u = self.p("u16.srt"); w(u, "1\n00:00:00,000 --> 00:00:03,000\nمرحبا\n".encode("utf-16-le"))
        with self.assertRaises(cap.CaptionError) as e: cap.load_file(u)
        self.assertIn("utf-16", str(e.exception).lower())
        cues, problems, enc = cap.load_file(u, "utf-16-le"); self.assertEqual((len(cues), problems), (1, []))
        w(self.p("nul.srt"), b"1\n00:00:00,000 --> 00:00:03,000\nab\x00cd\n")
        with self.assertRaises(cap.CaptionError): cap.load_file(self.p("nul.srt"))
        w(self.p("bom.srt"), b"\xef\xbb\xbf1\n00:00:00,000 --> 00:00:03,000\n\xff\xfe\n")
        with self.assertRaises(cap.CaptionError): cap.load_file(self.p("bom.srt"))

    def test_dialogue_multiline_and_tags_are_not_rewrapped(self):
        cues, _ = cap.parse("1\n00:00:00,000 --> 00:00:09,000\n- أين كنت؟\n- في البيت الكبير القديم عند الجبل البعيد جدا\n\n"
                            "2\n00:00:10,000 --> 00:00:19,000\n<i>هذا نص طويل جدا داخل وسم مائل يتجاوز الحد المسموح به للسطر</i>\n\n"
                            "3\n00:00:20,000 --> 00:00:29,000\nسطر قصير\nسطر ثان\nسطر ثالث\n")
        new, ch = cap.fix(cues)
        for a, b in zip(cues, new): self.assertEqual(a.lines, b.lines)
        self.assertTrue(any("dialogue" in c for c in ch)); self.assertTrue(any("tags" in c for c in ch))

    def test_plain_long_cue_is_still_rewrapped(self):
        cues, _ = cap.parse("1\n00:00:00,000 --> 00:00:09,000\nهذا نص طويل جدا لاختبار التفاف السطور في الترجمة العربية الآن\n")
        new, ch = cap.fix(cues); self.assertEqual(len(new[0].lines), 2)

    def test_overlap_fix_that_would_break_min_duration_is_skipped_and_new_warnings_reported(self):
        cues, _ = cap.parse("1\n00:00:00,000 --> 00:00:01,200\nأول\n\n2\n00:00:00,800 --> 00:00:03,000\nثاني\n")
        new, ch = cap.fix(cues)
        self.assertEqual(new[0].end, 1200)           # not shortened to 800 ms (under 833 ms)
        self.assertTrue(any("NOT fixed" in c for c in ch))
        cues, _ = cap.parse("1\n00:00:00,000 --> 00:00:02,000\nأول\n\n2\n00:00:01,800 --> 00:00:04,000\nثاني\n")
        new, ch = cap.fix(cues); self.assertEqual(new[0].end, 1800)

    def test_huge_cue_is_bounded(self):
        text = " ".join(["كلمة"] * 60000)
        cues, _ = cap.parse("1\n00:00:00,000 --> 00:00:09,000\n" + text + "\n")
        t0 = time.time(); new, ch = cap.fix(cues); iss = cap.check(cues)
        self.assertLess(time.time() - t0, 5); self.assertEqual(new[0].lines, cues[0].lines)
        self.assertTrue(any("NOT changed" in c for c in ch))

    def test_vtt_features_are_not_silently_dropped(self):
        for body in ["WEBVTT\n\nSTYLE\n::cue { color: red }\n\n00:01.000 --> 00:03.000\nمرحبا\n",
                     "WEBVTT\n\nNOTE hello\n\n00:01.000 --> 00:03.000\nمرحبا\n",
                     "WEBVTT\n\nintro\n00:01.000 --> 00:03.000\nمرحبا\n",
                     "WEBVTT\n\n00:01.000 --> 00:03.000 align:start position:10%\nمرحبا\n"]:
            a = self.p("v.vtt"); w(a, body.encode()); out = self.p("v.srt")
            if os.path.exists(out): os.unlink(out)
            with self.assertRaises(cap.CaptionError, msg=body): cap.write_fixed(a, out)
            cap.write_fixed(a, out, allow_srt_conversion=True)
            self.assertIn("DROPPED", rd(out + ".changes.txt").decode()); os.unlink(out); os.unlink(out + ".changes.txt")
        plain = self.p("p.vtt"); w(plain, "WEBVTT\n\n1\n00:01.000 --> 00:03.000\nمرحبا\n".encode())
        cap.write_fixed(plain, self.p("p.srt"))   # plain VTT converts without needing the flag

    def test_preview_path_with_quote_colon_and_space(self):
        if subprocess.run(["which", "ffmpeg"], capture_output=True).returncode: self.skipTest("no ffmpeg")
        d = os.path.join(self.d, "it's a: dir [x],y"); os.makedirs(d)
        s = os.path.join(d, "a b.srt"); w(s, "1\n00:00:00,000 --> 00:00:03,000\nمرحبا\n".encode())
        try: outs = cap.preview_frames(s, os.path.join(self.d, "o"), [1])
        except Exception as e: self.fail("preview failed on a hard path: %s" % e)
        self.assertTrue(rd(outs[0]).startswith(b"\x89PNG"))

if __name__ == "__main__":
    unittest.main()
