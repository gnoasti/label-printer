"""Run every layout through the real CUPS driver, without printing.

The driver output is written to a file and decoded back into an image.
Skipped unless a ptouch queue and its filters are available.
"""

import os
import subprocess
import tempfile
import unittest
import urllib.request

from PIL import Image, ImageDraw

from labelprinter import layouts, printing
from labelprinter.render import LabelSpec, render, save_pdf

FILTERS = "/usr/lib/cups/filter"


def decode_raster(data):
    """Parse Brother raster commands into (media info, list of pixel rows)."""
    info, rows, pos = None, [], 0
    arguments = {b"M": 1, b"K": 1, b"A": 1, b"a": 1, b"!": 1, b"d": 2}
    while pos < len(data):
        byte = data[pos:pos + 1]
        if byte in (b"\x00", b"\x1a", b"\x0c"):
            pos += 1
        elif data[pos:pos + 2] == b"\x1b@":
            pos += 2
        elif data[pos:pos + 3] == b"\x1biz":
            info = data[pos + 3:pos + 13]
            pos += 13
        elif data[pos:pos + 2] == b"\x1bi":
            pos += 3 + arguments[data[pos + 2:pos + 3]]
        elif byte == b"M":
            if data[pos + 1] != 0:
                raise ValueError("compressed raster data is not supported")
            pos += 2
        elif byte == b"g":
            count = data[pos + 2]
            rows.append(data[pos + 3:pos + 3 + count])
            pos += 3 + count
        elif byte == b"Z":
            rows.append(b"")
            pos += 1
        else:
            raise ValueError(f"unknown command at {pos}: {data[pos:pos + 8]!r}")
    return info, rows


def raster_image(rows):
    """The printed picture; black pixels are 0. The head prints mirrored."""
    width = max(len(row) for row in rows)
    packed = b"".join(row.ljust(width, b"\0") for row in rows)
    bits = Image.frombytes("1", (width * 8, len(rows)), packed)
    inverted = bits.convert("L").point(lambda v: 255 - v)
    return inverted.transpose(Image.Transpose.FLIP_LEFT_RIGHT)


def black_box(image):
    return image.point(lambda v: 255 if v < 128 else 0).getbbox()


class DriverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        queues = printing.find_queues()
        if not queues or not os.path.exists(f"{FILTERS}/rastertoptch"):
            raise unittest.SkipTest("no ptouch printer queue on this machine")
        cls.folder = tempfile.TemporaryDirectory()
        cls.ppd = os.path.join(cls.folder.name, "queue.ppd")
        url = f"http://localhost:631/printers/{queues[0]}.ppd"
        urllib.request.urlretrieve(url, cls.ppd)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def drive(self, layout, rendered):
        pdf = os.path.join(self.folder.name, "label.pdf")
        postscript = os.path.join(self.folder.name, "label.ps")
        save_pdf(rendered, pdf)
        command = printing.lp_command("q", layout, rendered.page_pt, 1, pdf)
        options = [command[i + 1] for i, a in enumerate(command) if a == "-o"]
        flags = [part for option in options for part in ("-o", option)]
        with open(postscript, "wb") as out:
            subprocess.run(
                ["cupsfilter", "-p", self.ppd, "-m", "printer/foo", *flags,
                 pdf], stdout=out, stderr=subprocess.DEVNULL, check=True)
        env = dict(os.environ, PPD=self.ppd,
                   PATH=os.environ["PATH"] + ":" + FILTERS)
        return subprocess.run(
            [f"{FILTERS}/foomatic-rip", "1", "user", "label", "1",
             " ".join(options), postscript],
            env=env, capture_output=True, check=True).stdout

    def check(self, layout, spec):
        rendered = render(layout, spec)
        info, rows = decode_raster(self.drive(layout, rendered))
        media_kind, width_mm = info[1], info[2]
        self.assertAlmostEqual(width_mm, layout.width_pt / 72 * 25.4, delta=1.0)
        if layout.continuous:
            self.assertEqual(media_kind, 0x0A)
        else:
            self.assertEqual(media_kind, 0x0B)
            self.assertAlmostEqual(
                info[3], layout.length_pt / 72 * 25.4, delta=1.0)
        printed = raster_image(rows)
        # The driver sends only the printable length of the page.
        expected_rows = rendered.page.height - 2 * layout.margin_y_pt * 300 / 72
        self.assertAlmostEqual(printed.height, expected_rows, delta=6)
        # Content keeps its size: no scaling or cropping by the filters.
        want, got = black_box(rendered.page), black_box(printed)
        for axis in (0, 1):
            self.assertAlmostEqual(
                got[axis + 2] - got[axis], want[axis + 2] - want[axis],
                delta=5, msg=f"{layout.key}: wanted {want}, got {got}")
        self.assertAlmostEqual(
            got[1], want[1] - layout.margin_y_pt * 300 / 72, delta=5,
            msg=f"{layout.key}: wrong position along the tape")

    def test_every_layout(self):
        for layout in layouts.LAYOUTS:
            with self.subTest(layout=layout.key):
                self.check(layout, LabelSpec(text="Test 123", border=True))

    def test_continuous_lengths_and_directions(self):
        layout = layouts.by_key("29mm")
        for horizontal in (True, False):
            for length in (None, 40, 150):
                with self.subTest(horizontal=horizontal, length=length):
                    self.check(layout, LabelSpec(
                        text="Tape", border=True, horizontal=horizontal,
                        length_mm=length))

    def test_picture_is_not_mirrored(self):
        layout = layouts.by_key("62x100mm")
        corner = Image.new("L", (200, 200), 255)
        ImageDraw.Draw(corner).polygon([(0, 0), (199, 0), (0, 199)], fill=0)
        rendered = render(layout, LabelSpec(image=corner, horizontal=False))
        _, rows = decode_raster(self.drive(layout, rendered))
        printed = raster_image(rows)
        shape = printed.crop(black_box(printed))
        w, h = shape.size
        self.assertLess(shape.getpixel((w // 5, h // 5)), 128)
        self.assertGreater(shape.getpixel((w * 4 // 5, h * 4 // 5)), 128)


if __name__ == "__main__":
    unittest.main()
