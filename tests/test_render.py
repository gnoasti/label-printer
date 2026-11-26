import os
import tempfile
import unittest
from unittest import mock

from PIL import Image

from labelprinter import layouts, printing, settings
from labelprinter.render import (
    MAX_LENGTH_MM, MIN_LENGTH_MM, LabelSpec, mm_to_px, pt_to_px, render,
    save_pdf)


def picture():
    image = Image.new("RGBA", (300, 150), (0, 0, 0, 0))
    image.paste((0, 0, 0, 255), (20, 20, 280, 130))
    return image


class RenderTest(unittest.TestCase):
    def test_every_combination_renders_at_page_width(self):
        contents = (("Hi\nthere", None), ("", picture()), ("Hi", picture()))
        for layout in layouts.LAYOUTS:
            for horizontal in (True, False):
                for position in ("left", "right", "top", "bottom"):
                    for text, image in contents:
                        with self.subTest(layout=layout.key, pos=position,
                                          horizontal=horizontal, text=text,
                                          picture=image is not None):
                            result = render(layout, LabelSpec(
                                text=text, image=image, horizontal=horizontal,
                                image_position=position, border=True))
                            self.assertEqual(result.page.width,
                                             pt_to_px(layout.width_pt))
                            # Automatic sizing must never overflow. The one
                            # exception: a 12 mm round label (6 mm printable)
                            # has no room for a picture plus text.
                            tiny = (layout.key == "12mm-circular"
                                    and text and image is not None)
                            if not tiny:
                                self.assertNotIn("cut off",
                                                 " ".join(result.warnings))
                            self.assertLess(result.page.getextrema()[0], 128)

    def test_output_is_pure_black_and_white(self):
        result = render(layouts.by_key("29x90mm"),
                        LabelSpec(text="Hello", image=picture()))
        colours = {value for _, value in result.page.getcolors()}
        self.assertEqual(colours, {0, 255})

    def test_too_large_text_is_reported_and_clipped(self):
        layout = layouts.by_key("29x90mm")
        result = render(layout, LabelSpec(text="W" * 300, font_size_pt=100))
        left, top, right, bottom = result.page.point(
            lambda v: 255 - v).getbbox()
        side = pt_to_px(layout.margin_x_pt)
        end = pt_to_px(layout.margin_y_pt)
        self.assertGreaterEqual(left, side)
        self.assertGreaterEqual(top, end)
        self.assertLessEqual(right, result.page.width - side)
        self.assertLessEqual(bottom, result.page.height - end)
        self.assertIn("cut off", result.warnings[0])

    def test_empty_label_is_blank(self):
        for layout in layouts.LAYOUTS:
            with self.subTest(layout=layout.key):
                spec = LabelSpec(text="  \n ")
                self.assertTrue(spec.empty)
                result = render(layout, spec)
                self.assertEqual(result.page.getextrema(), (255, 255))

    def test_continuous_length_follows_text(self):
        layout = layouts.by_key("29mm")
        short = render(layout, LabelSpec(text="Hi"))
        longer = render(layout, LabelSpec(text="Hi, this is much longer"))
        self.assertGreater(longer.page.height, short.page.height)
        self.assertGreater(longer.page_pt[1], short.page_pt[1])

    def test_continuous_length_limits(self):
        layout = layouts.by_key("62mm")
        for length, expected in ((1, MIN_LENGTH_MM), (80, 80),
                                 (999, MAX_LENGTH_MM)):
            with self.subTest(length=length):
                result = render(layout, LabelSpec(text="W" * 80,
                                                  length_mm=length))
                self.assertEqual(result.page.height, mm_to_px(expected))
        # Automatic length shrinks the text to stay within the maximum.
        result = render(layout, LabelSpec(text="W" * 80))
        self.assertLessEqual(result.page.height, mm_to_px(MAX_LENGTH_MM))
        self.assertGreater(result.page.height, mm_to_px(MAX_LENGTH_MM - 10))
        self.assertEqual(result.warnings, [])

    def test_flip_turns_by_180_degrees(self):
        layout = layouts.by_key("29x90mm")
        normal = render(layout, LabelSpec(text="Flip"))
        flipped = render(layout, LabelSpec(text="Flip", flip=True))
        self.assertEqual(
            flipped.page.tobytes(),
            normal.page.transpose(Image.Transpose.ROTATE_180).tobytes())

    def test_pdf_has_exact_page_size(self):
        result = render(layouts.by_key("29x90mm"), LabelSpec(text="PDF"))
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "a.pdf")
            save_pdf(result, path)
            with open(path, "rb") as file:
                data = file.read()
        box = data.split(b"/MediaBox [")[1].split(b"]")[0].split()
        self.assertAlmostEqual(float(box[2]), 82, places=2)
        self.assertAlmostEqual(float(box[3]), 255, places=2)


class PrintingTest(unittest.TestCase):
    def test_die_cut_command(self):
        command = printing.lp_command(
            "Q", layouts.by_key("29x90mm"), (82, 255), 3, "/tmp/x.pdf")
        self.assertEqual(command[:5], ["lp", "-d", "Q", "-n", "3"])
        self.assertIn("PageSize=29x90mm", command)
        self.assertIn("MediaType=Labels", command)
        self.assertEqual(command[-2:], ["--", "/tmp/x.pdf"])

    def test_continuous_command_has_custom_length(self):
        command = printing.lp_command(
            "Q", layouts.by_key("62mm"), (176, 200), 1, "x.pdf")
        self.assertIn("PageSize=Custom.176x200", command)
        self.assertIn("MediaType=Tape", command)

    def test_job_id_is_read_from_lp(self):
        rendered = render(layouts.by_key("29x90mm"), LabelSpec(text="Job"))
        answer = "request id is QL-700-2-42 (1 file(s))\n"
        with mock.patch.object(printing, "_run", return_value=answer):
            job = printing.print_label(
                "QL-700-2", layouts.by_key("29x90mm"), rendered)
        self.assertEqual(job, "QL-700-2-42")

    def test_job_pending_matches_whole_id(self):
        listing = "Q-12   user  1024  Thu 26 Nov\n"
        with mock.patch.object(printing, "_run", return_value=listing):
            self.assertTrue(printing.job_pending("Q", "Q-12"))
            self.assertFalse(printing.job_pending("Q", "Q-1"))

    def test_queue_compatibility(self):
        self.assertTrue(printing.is_compatible(
            "PageSize/Page Size: 29x90mm *62mm\n"
            "MediaType/Media Type: Labels *Tape\n"))
        # Brother's own driver uses different option names.
        self.assertFalse(printing.is_compatible(
            "PageSize/Media Size: *29x90 62x29\n"))


class SettingsTest(unittest.TestCase):
    def test_round_trip_and_damaged_file(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": folder}):
                self.assertEqual(settings.load(), {})
                settings.save({"layout": "62mm"})
                self.assertEqual(settings.load(), {"layout": "62mm"})
                settings.settings_path().write_text("{broken")
                self.assertEqual(settings.load(), {})


if __name__ == "__main__":
    unittest.main()
