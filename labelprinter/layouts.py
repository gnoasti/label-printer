"""Standard Brother DK label formats.

Page sizes and margins (in PostScript points, 1/72 inch) are taken from the
CUPS PPD "Brother QL-700 Foomatic/ptouch-ql". For the small round labels the
PPD claims 5.76 pt margins, but the driver cuts 8.40 pt at both ends (see
tests/test_driver.py), so the larger value is used. "Width" is the direction across
the print head, "length" the direction in which the tape is fed.
"""

from dataclasses import dataclass
from typing import Optional

DIE_CUT = "die-cut"
ROUND = "round"
CONTINUOUS = "continuous"


@dataclass(frozen=True)
class Layout:
    key: str                    # PPD PageSize name
    code: str                   # Brother order code of the roll
    title: str
    kind: str
    width_pt: float
    length_pt: Optional[float]  # None for continuous tape
    margin_x_pt: float = 4.32   # unprintable border left and right
    margin_y_pt: float = 8.40   # unprintable border at both ends

    @property
    def continuous(self):
        return self.kind == CONTINUOUS

    @property
    def media_type(self):
        return "Tape" if self.continuous else "Labels"

    @property
    def label(self):
        return f"{self.code}  ·  {self.title}"


LAYOUTS = (
    Layout("29x90mm", "DK-11201", "29 × 90 mm address", DIE_CUT, 82, 255),
    Layout("38x90mm", "DK-11208", "38 × 90 mm large address", DIE_CUT, 108, 255),
    Layout("62x100mm", "DK-11202", "62 × 100 mm shipping", DIE_CUT, 176, 283),
    Layout("62x29mm", "DK-11209", "62 × 29 mm small address", DIE_CUT, 176, 82),
    Layout("17x54mm", "DK-11204", "17 × 54 mm multi-purpose", DIE_CUT, 48, 153),
    Layout("17x87mm", "DK-11203", "17 × 87 mm file folder", DIE_CUT, 48, 246),
    Layout("23x23mm", "DK-11221", "23 × 23 mm square", DIE_CUT, 65, 65),
    Layout("12mm-circular", "DK-11219", "12 mm round", ROUND, 34, 34, 8.40, 8.40),
    Layout("24mm-circular", "DK-11218", "24 mm round", ROUND, 68, 68, 8.40, 8.40),
    Layout("58mm-circular", "DK-11207", "58 mm round (CD/DVD)", ROUND,
           165, 165, 8.40, 8.40),
    Layout("12mm", "DK-22214", "12 mm continuous", CONTINUOUS, 34, None),
    Layout("29mm", "DK-22210", "29 mm continuous", CONTINUOUS, 82, None),
    Layout("38mm", "DK-22225", "38 mm continuous", CONTINUOUS, 108, None),
    Layout("50mm", "DK-22223", "50 mm continuous", CONTINUOUS, 142, None),
    Layout("54mm", "DK-N55224", "54 mm continuous", CONTINUOUS, 153, None, 5.76),
    Layout("62mm", "DK-22205", "62 mm continuous", CONTINUOUS, 176, None),
)

DEFAULT_KEY = "29x90mm"


def by_key(key):
    """Return the layout with this key, or the default layout."""
    for layout in LAYOUTS:
        if layout.key == key:
            return layout
    return by_key(DEFAULT_KEY)
