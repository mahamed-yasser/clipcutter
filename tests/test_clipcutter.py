import json, os, re, shutil, subprocess, sys, tempfile, unittest
from clipcutter import core
from clipcutter.core import ClipError

HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
ROOT = os.path.dirname(os.path.dirname(__file__))


def make_clip(path, seconds=3, size="640x360", audio=True, color="testsrc"):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"{color}=size={size}:rate=25:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"] + (["-c:a", "aac"] if audio else []) + [path]
    subprocess.run(cmd, check=True)


class Pure(unittest.TestCase):
    def test_parse_time(self):
        self.assertEqual(core.parse_time("1:30"), 90)
        self.assertEqual(core.parse_time("00:01:30.5"), 90.5)
        self.assertEqual(core.parse_time(12), 12)
        for bad in ("abc", "1:2:3:4", "-5"):
            with self.assertRaises(ClipError):
                core.parse_time(bad)

    def test_trim_cmd(self):
        c = core.trim_cmd("a.mp4", "b.mp4", "0:05", end="0:12")
        self.assertEqual(c[c.index("-ss") + 1], "5.000")
        self.assertEqual(c[c.index("-t") + 1], "7.000")
        with self.assertRaises(ClipError):
            core.trim_cmd("a", "b", 10, end=5)
        with self.assertRaises(ClipError):
            core.trim_cmd("a", "b", 1, end=5, duration=2)

    def test_vertical_filter(self):
        self.assertIn("crop=1080:1920", core.vertical_filter())
        self.assertIn("overlay", core.vertical_filter(mode="blur"))
        with self.assertRaises(ClipError):
            core.vertical_filter(mode="zoom")

    def test_subtitle_path_escaping(self):
        c = core.burn_subs_cmd("a.mp4", "C:\\my dir\\it's.srt", "o.mp4")
        vf = c[c.index("-vf") + 1]
        self.assertIn("C\\:/my dir/it\\'s.srt", vf)

    def test_thumbs_args(self):
        with self.assertRaises(ClipError):
            core.thumbs_cmd("a", "p")
        with self.assertRaises(ClipError):
            core.thumbs_cmd("a", "p", every=2, count=3)
        with self.assertRaises(ClipError):
            core.thumbs_cmd("a", "p", count=3)

    def test_option_validation(self):
        with self.assertRaises(ClipError):
            core.enc(99)
        with self.assertRaises(ClipError):
            core.burn_subs_cmd("a", "b.srt", "c", position="left")
        with self.assertRaises(ClipError):
            core.burn_subs_cmd("a", "b.srt", "c", font="x,Outline=9")
        with self.assertRaises(ClipError):
            core.burn_subs_cmd("a", "b.srt", "c", font_size=2)
        c = core.burn_subs_cmd("a", "b.srt", "c", position="top", margin=10)
        self.assertIn("Alignment=6", c[c.index("-vf") + 1])

    def test_subtitle_size_scales_with_video(self):
        small = core.burn_subs_cmd("a", "b.srt", "c", video_size=(1280, 720))
        tall = core.burn_subs_cmd("a", "b.srt", "c", video_size=(1080, 1920))
        vf = lambda c: c[c.index("-vf") + 1]
        size = lambda c: float(re.search(r"FontSize=([\d.]+)", vf(c)).group(1))
        self.assertAlmostEqual(size(small), size(tall), delta=0.2)  # same relative size on both videos
        self.assertAlmostEqual(size(small), 11.6, delta=0.2)
        self.assertNotIn("original_size", vf(tall))

    def test_fast_trim_copies_streams(self):
        c = core.trim_cmd("a.mp4", "b.mp4", 1, end=3, fast=True)
        self.assertIn("copy", c)
        self.assertNotIn("libx264", c)

    def test_missing_input(self):
        with self.assertRaises(ClipError):
            core.trim("/nonexistent.mp4", "/tmp/x.mp4", 0, 1)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg not installed")
class RealRuns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.a = os.path.join(cls.tmp, "a.mp4"); make_clip(cls.a, 4)
        cls.b = os.path.join(cls.tmp, "b.mp4"); make_clip(cls.b, 2, size="320x240", audio=False, color="smptebars")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def out(self, n):
        return os.path.join(self.tmp, n)

    def test_trim_duration(self):
        core.trim(self.a, self.out("t.mp4"), 1, end=3)
        self.assertAlmostEqual(core.probe(self.out("t.mp4"))["duration"], 2.0, delta=0.15)

    def test_vertical_sizes(self):
        for mode in ("crop", "blur"):
            core.vertical(self.a, self.out(f"v_{mode}.mp4"), mode)
            i = core.probe(self.out(f"v_{mode}.mp4"))
            self.assertEqual((i["width"], i["height"]), (1080, 1920))

    def test_concat_mixed_sources(self):
        core.concat([self.a, self.b], self.out("c.mp4"))
        i = core.probe(self.out("c.mp4"))
        self.assertEqual((i["width"], i["height"]), (1280, 720))
        self.assertTrue(i["has_audio"])  # silent clip got silent audio
        self.assertAlmostEqual(i["duration"], 6.0, delta=0.4)

    def test_thumbs(self):
        files = core.thumbs(self.a, self.out("th"), count=4)
        self.assertEqual(len(files), 4)
        self.assertTrue(all(os.path.getsize(f) > 1000 for f in files))

    def test_burn_subs(self):
        srt = self.out("s.srt")
        with open(srt, "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,500 --> 00:00:03,000\nمرحبا Hello\n")
        core.burn_subs(self.a, srt, self.out("s.mp4"))
        self.assertEqual(core.probe(self.out("s.mp4"))["width"], 640)

    def test_edit_list(self):
        spec = {"output": "final.mp4", "vertical": "blur",
                "clips": [{"file": "a.mp4", "start": 0, "end": 2}, {"file": "b.mp4", "start": 0, "duration": 1}]}
        with open(self.out("edit.json"), "w") as f:
            json.dump(spec, f)
        core.run_edit_list(self.out("edit.json"))
        i = core.probe(self.out("final.mp4"))
        self.assertEqual((i["width"], i["height"]), (1080, 1920))
        self.assertAlmostEqual(i["duration"], 3.0, delta=0.4)

    def test_edit_list_errors(self):
        with open(self.out("bad.json"), "w") as f:
            json.dump({"output": "x.mp4", "clips": []}, f)
        with self.assertRaises(ClipError):
            core.run_edit_list(self.out("bad.json"))

    def test_refuses_overwrite_and_same_file(self):
        core.trim(self.a, self.out("ow.mp4"), 0, end=1)
        with self.assertRaises(ClipError) as cm:
            core.trim(self.a, self.out("ow.mp4"), 0, end=1)
        self.assertIn("--force", str(cm.exception))
        core.trim(self.a, self.out("ow.mp4"), 0, end=2, force=True)
        self.assertAlmostEqual(core.probe(self.out("ow.mp4"))["duration"], 2.0, delta=0.15)
        with self.assertRaises(ClipError):
            core.trim(self.a, self.a, 0, end=1, force=True)  # would destroy the source
        with self.assertRaises(ClipError):
            core.trim(self.a, "/no/such/folder/x.mp4", 0, end=1)

    def test_thumbs_refuse_existing(self):
        core.thumbs(self.a, self.out("th2"), count=2)
        with self.assertRaises(ClipError):
            core.thumbs(self.a, self.out("th2"), count=2)
        self.assertEqual(len(core.thumbs(self.a, self.out("th2"), count=3, force=True)), 3)

    def test_fast_trim_runs(self):
        core.trim(self.a, self.out("fast.mp4"), 1, end=3, fast=True)
        self.assertGreater(core.probe(self.out("fast.mp4"))["duration"], 1.5)

    def test_concat_custom_size_and_validation(self):
        core.concat([self.a, self.b], self.out("c2.mp4"), width=640, height=360, fps=24)
        i = core.probe(self.out("c2.mp4"))
        self.assertEqual((i["width"], i["height"]), (640, 360))
        with self.assertRaises(ClipError):
            core.concat([self.a, self.b], self.out("c3.mp4"), width=641, height=360)

    def test_subs_position_runs(self):
        srt = self.out("p.srt")
        with open(srt, "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,500 --> 00:00:02,000\nTop caption\n")
        core.burn_subs(self.a, srt, self.out("top.mp4"), position="top", font_size=30)
        self.assertEqual(core.probe(self.out("top.mp4"))["height"], 360)

    def test_subtitle_position_on_screen(self):
        black = self.out("black.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=black:size=640x360:rate=25:duration=2",
                        "-pix_fmt", "yuv420p", "-c:v", "libx264", black], check=True)
        srt = self.out("pos.srt")
        with open(srt, "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> 00:00:02,000\nWWWWWWWW\n")

        def centre_row(pos):
            dst = self.out(f"pos_{pos}.mp4")
            core.burn_subs(black, srt, dst, position=pos)
            raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", "1", "-i", dst, "-frames:v", "1", "-f", "rawvideo",
                                  "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
            rows = [r for r in range(360) if max(raw[r * 640:(r + 1) * 640]) > 128]
            self.assertTrue(rows, "no subtitle pixels found")
            return sum(rows) / len(rows) / 360

        top, mid, bot = centre_row("top"), centre_row("middle"), centre_row("bottom")
        self.assertLess(top, 0.25)
        self.assertTrue(0.35 < mid < 0.65)
        self.assertGreater(bot, 0.75)

    def test_trim_start_past_end_is_an_error(self):
        with self.assertRaises(ClipError) as cm:
            core.trim(self.a, self.out("past.mp4"), 99, end=100)
        self.assertIn("after the end", str(cm.exception))
        self.assertFalse(os.path.exists(self.out("past.mp4")))

    def test_end_past_duration_is_clamped(self):
        core.trim(self.a, self.out("over.mp4"), 3, end=99)
        self.assertAlmostEqual(core.probe(self.out("over.mp4"))["duration"], 1.0, delta=0.2)

    def test_failed_run_leaves_no_partial_file(self):
        junk = self.out("junk2.mp4")
        with open(junk, "w") as f:
            f.write("not a video")
        with self.assertRaises(ClipError):
            core.vertical(junk, self.out("nope.mp4"))
        left = [n for n in os.listdir(self.tmp) if n.startswith("nope") or ".partial" in n]
        self.assertEqual(left, [])

    def test_batch_vertical_continues_past_bad_file(self):
        src = os.path.join(self.tmp, "batch_in"); os.makedirs(src, exist_ok=True)
        shutil.copy(self.a, os.path.join(src, "one.mp4"))
        shutil.copy(self.b, os.path.join(src, "two.mp4"))
        with open(os.path.join(src, "broken.mp4"), "w") as f:
            f.write("not a video")
        with open(os.path.join(src, "notes.txt"), "w") as f:
            f.write("ignored")
        out = os.path.join(self.tmp, "batch_out")
        r = core.batch("vertical", src, out, "blur")
        self.assertEqual(r["done"], ["one.mp4", "two.mp4"])
        self.assertEqual([n for n, _ in r["failed"]], ["broken.mp4"])
        self.assertEqual(sorted(os.listdir(out)), ["one.mp4", "two.mp4"])
        self.assertEqual(core.probe(os.path.join(out, "one.mp4"))["height"], 1920)
        # second run: nothing is overwritten
        r2 = core.batch("vertical", src, out)
        self.assertEqual(r2["done"], [])
        self.assertEqual(len(r2["skipped"]), 2)
        self.assertEqual(len(core.batch("vertical", src, out, force=True)["done"]), 2)

    def test_batch_subs_needs_matching_srt(self):
        src = os.path.join(self.tmp, "bs_in"); os.makedirs(src, exist_ok=True)
        shutil.copy(self.a, os.path.join(src, "x.mp4"))
        shutil.copy(self.a, os.path.join(src, "y.mp4"))
        with open(os.path.join(src, "x.srt"), "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,500 --> 00:00:02,000\nمرحبا\n")
        r = core.batch("subs", src, os.path.join(self.tmp, "bs_out"))
        self.assertEqual(r["done"], ["x.mp4"])
        self.assertEqual(r["skipped"], [("y.mp4", "no matching .srt")])

    def test_batch_refuses_same_folder_and_empty(self):
        with self.assertRaises(ClipError):
            core.batch("vertical", self.tmp, self.tmp)
        empty = os.path.join(self.tmp, "empty"); os.makedirs(empty, exist_ok=True)
        with self.assertRaises(ClipError):
            core.batch("vertical", empty, os.path.join(self.tmp, "eo"))

    def test_batch_cli_exit_codes(self):
        src = os.path.join(self.tmp, "cli_in"); os.makedirs(src, exist_ok=True)
        shutil.copy(self.b, os.path.join(src, "ok.mp4"))
        out = os.path.join(self.tmp, "cli_out")
        r = subprocess.run([sys.executable, "-m", "clipcutter", "batch", "vertical", src, out], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 done, 0 skipped, 0 failed", r.stdout)
        with open(os.path.join(src, "bad.mp4"), "w") as f:
            f.write("x")
        r = subprocess.run([sys.executable, "-m", "clipcutter", "batch", "vertical", src, out, "--force"], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAILED  bad.mp4", r.stdout)

    def test_edit_list_rejects_typos_and_bad_json(self):
        bad = self.out("typo.json")
        with open(bad, "w") as f:
            json.dump({"output": "x.mp4", "clips": [{"file": "a.mp4"}], "vertcal": "blur"}, f)
        with self.assertRaises(ClipError) as cm:
            core.run_edit_list(bad)
        self.assertIn("vertcal", str(cm.exception))
        with open(bad, "w") as f:
            f.write("{not json")
        with self.assertRaises(ClipError):
            core.run_edit_list(bad)
        with open(bad, "w") as f:
            json.dump({"output": "x.mp4", "clips": [{"file": "a.mp4", "start": 3, "end": 1}]}, f)
        with self.assertRaises(ClipError):
            core.run_edit_list(bad)
        self.assertFalse(os.path.exists(self.out("x.mp4")))  # failed before any work

    def test_edit_list_options(self):
        spec = {"output": "opt.mp4", "size": "640x360", "fps": 25, "crf": 28,
                "clips": [{"file": "a.mp4", "end": 1}, {"file": "b.mp4", "end": 1}]}
        with open(self.out("opt.json"), "w") as f:
            json.dump(spec, f)
        core.run_edit_list(self.out("opt.json"))
        self.assertEqual(core.probe(self.out("opt.mp4"))["width"], 640)

    def test_cli_info_and_force(self):
        r = subprocess.run([sys.executable, "-m", "clipcutter", "info", self.a], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0)
        self.assertIn("640x360", r.stdout)
        r = subprocess.run([sys.executable, "-m", "clipcutter", "trim", self.a, self.a, "--end", "1"],
                           capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 2)

    def test_cli_error_exit_code(self):
        r = subprocess.run([sys.executable, "-m", "clipcutter", "trim", "/nope.mp4", "/tmp/o.mp4"],
                           capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 2)
        self.assertIn("file not found", r.stderr)

    def test_not_a_video(self):
        junk = self.out("junk.mp4")
        with open(junk, "w") as f:
            f.write("not video")
        with self.assertRaises(ClipError):
            core.probe(junk)


if __name__ == "__main__":
    unittest.main()
