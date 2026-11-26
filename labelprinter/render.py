"""Turn text and an optional picture into a black-and-white label image.

The label is composed on a "canvas" in reading orientation and then rotated
into the "page" orientation expected by the printer. Everything is 300 dpi.
"""

import functools
import math
import subprocess
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .layouts import ROUND, Layout

DPI = 300
PADDING_MM = 0.8          # breathing room inside the printable area
GAP_MM = 1.5              # between picture and text
MIN_LENGTH_MM = 13        # shortest piece of continuous tape
MAX_LENGTH_MM = 300
MIN_FONT_PX = 6
FONT_FAMILIES = (
    "DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono",
    "Liberation Sans", "Liberation Sans Narrow", "Liberation Serif",
    "Ubuntu", "Noto Sans",
)


def mm_to_px(mm):
    return round(mm / 25.4 * DPI)


def pt_to_px(pt):
    return round(pt / 72 * DPI)


def px_to_mm(px):
    return px / DPI * 25.4


@dataclass
class LabelSpec:
    text: str = ""
    font_family: str = "DejaVu Sans"
    bold: bool = True
    italic: bool = False
    align: str = "center"                 # left | center | right
    font_size_pt: Optional[float] = None  # None: as large as fits
    horizontal: bool = True               # see canvas_is_rotated()
    flip: bool = False                    # rotate by 180 degrees
    border: bool = False
    length_mm: Optional[float] = None     # continuous tape only; None: automatic
    image: Optional[Image.Image] = None
    image_position: str = "left"          # left | right | top | bottom
    dither: bool = True                   # False: hard threshold

    @property
    def empty(self):
        return not self.text.strip() and self.image is None


@dataclass
class Rendered:
    canvas: Image.Image     # reading orientation, for the preview
    page: Image.Image       # printer orientation
    page_pt: tuple          # (width, length) in points
    warnings: list = field(default_factory=list)


@functools.lru_cache(maxsize=None)
def font_file(family, bold, italic):
    """Ask fontconfig for the file of a font; returns (path, face index)."""
    pattern = family
    if bold:
        pattern += ":bold"
    if italic:
        pattern += ":italic"
    out = subprocess.run(
        ["fc-match", "--format=%{file}\n%{index}", pattern],
        capture_output=True, text=True, check=True,
    ).stdout.split("\n")
    return out[0], int(out[1] or 0)


@functools.lru_cache(maxsize=512)
def _font(path, index, size):
    return ImageFont.truetype(path, size, index=index)


class _Text:
    """Measures and draws a block of text in one font."""

    def __init__(self, spec):
        self.text = spec.text.strip("\n").expandtabs(4)
        self.align = spec.align
        self.file = (font_file(spec.font_family, spec.bold, spec.italic)
                     if self.text.strip() else None)
        self._probe = ImageDraw.Draw(Image.new("L", (1, 1)))

    def __bool__(self):
        return self.file is not None

    def _options(self, size):
        return dict(font=_font(*self.file, size), align=self.align,
                    spacing=round(size * 0.18))

    def bbox(self, size):
        return self._probe.multiline_textbbox((0, 0), self.text,
                                              **self._options(size))

    def size(self, size):
        left, top, right, bottom = self.bbox(size)
        return math.ceil(right - left), math.ceil(bottom - top)

    def largest(self, fits, upper=2400):
        """Largest font size for which fits(width, height) is true."""
        low, high = MIN_FONT_PX, upper
        while low < high:
            middle = (low + high + 1) // 2
            if fits(*self.size(middle)):
                low = middle
            else:
                high = middle - 1
        return low

    def draw(self, target, box, size):
        """Draw into box=(x0, y0, x1, y1), centred vertically."""
        x0, y0, x1, y1 = box
        width, height = self.size(size)
        left, top = self.bbox(size)[:2]
        if self.align == "left":
            x = x0
        elif self.align == "right":
            x = x1 - width
        else:
            x = (x0 + x1 - width) / 2
        y = (y0 + y1 - height) / 2
        ImageDraw.Draw(target).multiline_text(
            (x - left, y - top), self.text, fill=0, **self._options(size))


def flatten(image):
    """Greyscale copy of a picture, with transparency turned into white."""
    image = ImageOps.exif_transpose(image).convert("RGBA")
    white = Image.new("RGBA", image.size, "white")
    return Image.alpha_composite(white, image).convert("L")


def _to_black_and_white(image, dither):
    if dither:
        return image.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L")
    return image.point(lambda v: 255 if v >= 128 else 0)


def _fit_size(source, max_width, max_height):
    scale = min(max_width / source.width, max_height / source.height)
    return (max(1, int(source.width * scale)),
            max(1, int(source.height * scale)))


def _paste_image(target, picture, box, dither):
    x0, y0, x1, y1 = box
    width, height = _fit_size(picture, max(1, x1 - x0), max(1, y1 - y0))
    scaled = picture.resize((width, height), Image.Resampling.LANCZOS)
    target.paste(_to_black_and_white(scaled, dither),
                 ((x0 + x1 - width) // 2, (y0 + y1 - height) // 2))


def canvas_is_rotated(layout: Layout, horizontal: bool):
    """Whether the canvas is turned by 90 degrees relative to the page.

    For die-cut labels "horizontal" means the text runs along the longer side.
    For continuous tape it means the text runs along the tape.
    """
    if layout.continuous:
        return horizontal
    if layout.length_pt == layout.width_pt:
        return not horizontal
    page_is_wide = layout.width_pt > layout.length_pt
    return horizontal != page_is_wide


def _picture_size(picture, spec, has_text, box_w, box_h):
    """Room reserved for the picture inside a label of known size."""
    if not has_text:
        return box_w, box_h
    if spec.image_position in ("top", "bottom"):
        return _fit_size(picture, box_w, int(box_h * 0.5))
    return _fit_size(picture, int(box_w * 0.4), box_h)


def _picture_size_auto(picture, spec, has_text, along, fixed_side, free_side):
    """Room for the picture on tape whose length is not fixed yet.

    along: the length is the canvas width (else it is the canvas height).
    fixed_side is the printable tape width, free_side the longest length.
    """
    stacked = spec.image_position in ("top", "bottom")
    if along:
        share = 0.55 if has_text and stacked else 1
        return _fit_size(picture, free_side, int(fixed_side * share))
    share = 0.4 if has_text and not stacked else 1
    return _fit_size(picture, int(fixed_side * share), free_side)


def _split(box, position, image_w, image_h, gap):
    """Divide a box into (picture box, text box)."""
    x0, y0, x1, y1 = box
    if position == "left":
        return (x0, y0, x0 + image_w, y1), (x0 + image_w + gap, y0, x1, y1)
    if position == "right":
        return (x1 - image_w, y0, x1, y1), (x0, y0, x1 - image_w - gap, y1)
    if position == "top":
        return (x0, y0, x1, y0 + image_h), (x0, y0 + image_h + gap, x1, y1)
    return (x0, y1 - image_h, x1, y1), (x0, y0, x1, y1 - image_h - gap)


def render(layout: Layout, spec: LabelSpec) -> Rendered:
    warnings = []
    rotated = canvas_is_rotated(layout, spec.horizontal)
    across = pt_to_px(layout.width_pt)
    margin_side = pt_to_px(layout.margin_x_pt)
    margin_end = pt_to_px(layout.margin_y_pt)
    # Margins in canvas coordinates.
    mx, my = ((margin_end, margin_side) if rotated
              else (margin_side, margin_end))
    pad = mm_to_px(PADDING_MM) * (2 if spec.border else 1)
    inset_x, inset_y = mx + pad, my + pad

    def canvas_size(length):
        return (length, across) if rotated else (across, length)

    text = _Text(spec)
    picture = flatten(spec.image) if spec.image is not None else None
    stacked = spec.image_position in ("top", "bottom")
    gap = mm_to_px(GAP_MM)

    auto_length = layout.continuous and spec.length_mm is None
    if layout.continuous:
        mm = MAX_LENGTH_MM if auto_length else spec.length_mm
        length = mm_to_px(min(MAX_LENGTH_MM, max(MIN_LENGTH_MM, mm)))
    else:
        length = pt_to_px(layout.length_pt)

    canvas_w, canvas_h = canvas_size(length)
    box_w = max(1, canvas_w - 2 * inset_x)
    box_h = max(1, canvas_h - 2 * inset_y)

    is_round = layout.kind == ROUND
    diameter = min(box_w, box_h)
    if is_round:
        # A centred square inside the printable circle.
        box_w = box_h = int(diameter / math.sqrt(2))

    image_w = image_h = 0
    if picture is not None:
        if auto_length:
            # "rotated" means that the length runs along the canvas width.
            fixed_side, free_side = ((box_h, box_w) if rotated
                                     else (box_w, box_h))
            image_w, image_h = _picture_size_auto(
                picture, spec, bool(text), rotated, fixed_side, free_side)
        else:
            image_w, image_h = _picture_size(
                picture, spec, bool(text), box_w, box_h)

    size = None
    text_w = text_h = 0
    if text:
        area_w, area_h = box_w, box_h
        if picture is not None:
            if stacked:
                area_h = max(1, box_h - image_h - gap)
            else:
                area_w = max(1, box_w - image_w - gap)
        if is_round and picture is None:
            # Text may use the circle, not just the square inside it.
            box_w = box_h = diameter

            def fits(w, h):
                return w * w + h * h <= diameter * diameter
        else:
            def fits(w, h):
                return w <= area_w and h <= area_h
        if spec.font_size_pt:
            size = max(MIN_FONT_PX, pt_to_px(spec.font_size_pt))
        else:
            size = text.largest(fits)
        text_w, text_h = text.size(size)
        if not fits(text_w, text_h):
            warnings.append("The text does not fit and will be cut off.")
        elif size < pt_to_px(5):
            warnings.append("The text is very small and may be hard to read.")

    if auto_length:
        picture_extent = image_w if rotated else image_h
        text_extent = text_w if rotated else text_h
        picture_follows_text = stacked != rotated  # same axis as the length
        if picture is None:
            content = text_extent
        elif not text:
            content = picture_extent
        elif picture_follows_text:
            content = picture_extent + gap + text_extent
        else:
            content = max(picture_extent, text_extent)
        inset = inset_x if rotated else inset_y
        length = min(mm_to_px(MAX_LENGTH_MM),
                     max(mm_to_px(MIN_LENGTH_MM), content + 2 * inset))
        canvas_w, canvas_h = canvas_size(length)
        box_w = max(1, canvas_w - 2 * inset_x)
        box_h = max(1, canvas_h - 2 * inset_y)

    x0 = (canvas_w - box_w) // 2
    y0 = (canvas_h - box_h) // 2
    image_box = text_box = (x0, y0, x0 + box_w, y0 + box_h)
    if picture is not None and text:
        image_box, text_box = _split(image_box, spec.image_position,
                                     image_w, image_h, gap)

    canvas = Image.new("L", (canvas_w, canvas_h), 255)
    if text:
        text.draw(canvas, text_box, size)
    if picture is not None:
        _paste_image(canvas, picture, image_box, spec.dither)

    printable = (mx, my, canvas_w - mx - 1, canvas_h - my - 1)
    shape_name = "ellipse" if is_round else "rectangle"
    if spec.border:
        inner = (printable[0] + 3, printable[1] + 3,
                 printable[2] - 3, printable[3] - 3)
        getattr(ImageDraw.Draw(canvas), shape_name)(inner, outline=0, width=4)

    # Nothing outside the printable area reaches the paper.
    mask = Image.new("L", canvas.size, 0)
    getattr(ImageDraw.Draw(mask), shape_name)(printable, fill=255)
    clipped = Image.new("L", canvas.size, 255)
    clipped.paste(canvas, (0, 0), mask)
    canvas = clipped.point(lambda v: 255 if v >= 128 else 0)

    page = canvas.transpose(Image.Transpose.ROTATE_90) if rotated else canvas
    if spec.flip:
        canvas = canvas.transpose(Image.Transpose.ROTATE_180)
        page = page.transpose(Image.Transpose.ROTATE_180)

    if layout.continuous:
        page_pt = (layout.width_pt, round(page.height * 72 / DPI))
    else:
        page_pt = (layout.width_pt, layout.length_pt)
    return Rendered(canvas, page, page_pt, warnings)


def save_pdf(rendered: Rendered, path):
    """Write the page as a lossless 1-bit PDF at its physical size."""
    width_pt, length_pt = rendered.page_pt
    page = rendered.page.convert("1", dither=Image.Dither.NONE)
    page.save(path, "PDF",
              dpi=(page.width * 72 / width_pt, page.height * 72 / length_pt))
