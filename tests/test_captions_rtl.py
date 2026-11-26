"""RTL embedding for rendering: only invisible controls are added, Latin-only lines untouched."""
import os, subprocess, sys, tempfile, unittest
from clipcutter import captions as cap

SRT = "1\n00:00:00,000 --> 00:00:03,000\nPython لغة برمجة سهلة.\nplain latin\n\n2\n00:00:04,000 --> 00:00:06,000\n\u200fمرحبا\n"

class Rtl(unittest.TestCase):
    def test_wraps_only_arabic_lines_and_keeps_letters(self):
        cues, _ = cap.parse(SRT)
        out = cap.rtl_embed(cues)
        c2, _ = cap.parse(out)
        self.assertEqual(c2[0].lines[0], "\u202bPython لغة برمجة سهلة.\u202c")
        self.assertEqual(c2[0].lines[1], "plain latin")
        self.assertEqual(c2[1].lines[0], "\u200fمرحبا")   # already has a control: untouched
        strip = lambda s: "".join(ch for ch in s if ch not in cap.BIDI_CONTROLS)
        self.assertEqual([strip(l) for c in c2 for l in c.lines], [strip(l) for c in cues for l in c.lines])

    def test_cli_rtl_copy_never_touches_original(self):
        d = tempfile.mkdtemp(); a = os.path.join(d, "a.srt")
        with open(a, "w", encoding="utf-8") as f: f.write(SRT)
        before = open(a, "rb").read()
        r = subprocess.run([sys.executable, "-m", "clipcutter", "captions", "rtl-copy", a, "-o", os.path.join(d, "o.srt")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(open(a, "rb").read(), before)
        self.assertIn("\u202b", open(os.path.join(d, "o.srt"), encoding="utf-8").read())
        r = subprocess.run([sys.executable, "-m", "clipcutter", "captions", "rtl-copy", a, "-o", a, "--force"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(open(a, "rb").read(), before)

if __name__ == "__main__":
    unittest.main()


class ReadableStyleTests(unittest.TestCase):
    def _vf(self, **kw):
        from clipcutter import core
        return core.burn_subs_cmd("a.mp4", "a.srt", "o.mp4", video_size=(1280, 720), **kw)[8]

    def test_plain_is_unchanged_default(self):
        vf = self._vf()
        self.assertNotIn("BorderStyle", vf); self.assertNotIn("Bold", vf)

    def test_readable_uses_plate_bold_and_bigger_margin(self):
        vf = self._vf(style="readable")
        for part in ("Bold=1", "BorderStyle=3", "OutlineColour=&H8C000000"):
            self.assertIn(part, vf)
        self.assertIn("MarginV=23.20", vf)  # 8% of 720 = 58 px -> on the 288 canvas
        self.assertIn("FontSize=14.80", vf)  # 5.2% of 720 = 37 px

    def test_bad_style_rejected(self):
        from clipcutter import core
        with self.assertRaises(core.ClipError):
            self._vf(style="neon")
