import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image  # noqa: E402

from pipeline import fx, motion, sfx  # noqa: E402

RED, BLUE = Image.new("RGB", (fx.W, fx.H), (255, 0, 0)), Image.new("RGB", (fx.W, fx.H), (0, 0, 255))


class FxTests(unittest.TestCase):
    def test_transitions_start_on_old_shot_and_end_on_new(self):
        for name, fn in fx.TRANSITIONS.items():
            self.assertEqual(fn(RED, BLUE, 0.0).getpixel((fx.W // 2, fx.H // 2)), (255, 0, 0), name)
            self.assertEqual(fn(RED, BLUE, 1.0).getpixel((fx.W // 2, fx.H // 2)), (0, 0, 255), name)

    def test_whip_keeps_progress_bar_rows_still(self):
        out = fx.whip(RED, BLUE, 0.5, top=140)
        self.assertEqual(out.getpixel((10, 100)), (0, 0, 255))  # bar rows come straight from one shot

    def test_spring_settles_with_small_overshoot(self):
        vals = [fx.spring(i / 100) for i in range(100)]
        self.assertEqual(fx.spring(0), 0.0)
        self.assertLess(max(vals), 1.12)
        self.assertAlmostEqual(fx.spring(1.0), 1.0, places=2)

    def test_shake_dies_out(self):
        self.assertNotEqual(fx.shake(0.03, 10), (0, 0))
        self.assertEqual(fx.shake(0.6, 10), (0, 0))
        self.assertEqual(fx.shake(-0.1, 10), (0, 0))

    def test_counter_uses_the_real_value(self):
        self.assertEqual(fx.count_text("1.7k", 1.0), "1.7k")
        self.assertEqual(fx.count_text("1.7k", 0.5), "0.8k")
        self.assertEqual(fx.count_text("10,", 0.5), "5,")
        self.assertIsNone(fx.count_text("tool", 0.5))

    def test_partial_outline_grows_with_progress(self):
        pts = fx.outline_points((0, 0, 200, 100), 10)
        length = lambda p: sum(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 for a, b in zip(p, p[1:]))
        self.assertLess(length(fx.partial_path(pts, 0.25)), length(fx.partial_path(pts, 0.75)))
        self.assertEqual(fx.partial_path(pts, 1.0), pts)

    def test_backdrop_loops_and_moves(self):
        b = fx.Backdrop()
        self.assertEqual(b.frame(0).size, (fx.W, fx.H))
        self.assertNotEqual(b.frame(0).tobytes(), b.frame(10).tobytes())


class PlanTests(unittest.TestCase):
    def test_transitions_only_where_the_visual_changes(self):
        kinds = ["text", "scroll", "scroll", "scroll", "text"]
        pages = [None, "a", "b", "b", None]
        self.assertEqual(motion.plan_transitions(kinds, pages), [None, "whip", "panel", None, "circle"])

    def test_events_and_frames(self):
        ev = []
        gen = motion.frames([("Hello there 1.7k stars.", 1.0), ("Comment GITHUB now.", 1.0)], events=ev)
        first = next(gen)
        self.assertEqual(len(first), motion.W * motion.H * 3)
        self.assertEqual(ev[0], (0.0, "impact"))
        self.assertEqual(len(list(gen)) + 1, 2 * motion.FPS)


class SfxTests(unittest.TestCase):
    def test_track_places_sounds(self):
        import tempfile
        import wave
        dest = Path(tempfile.mkdtemp()) / "s.wav"
        sfx.track([(0.1, "whoosh"), (0.5, "impact"), (0.9, "click")], 2.0, dest)
        with wave.open(str(dest)) as w:
            self.assertEqual(w.getnframes(), 2 * sfx.SR)
            data = w.readframes(w.getnframes())
        self.assertGreater(max(abs(int.from_bytes(data[i:i + 2], "little", signed=True))
                               for i in range(0, len(data), 200)), 1000)


if __name__ == "__main__":
    unittest.main()
