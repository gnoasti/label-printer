# label-printer

A small desktop app for printing text labels on a Brother QL-700 (Linux).

- Type one or more lines; the text is sized to fit the label automatically
- Live black-and-white preview
- Standard Brother DK die-cut, round and continuous rolls
- Pictures: paste, open or drag and drop, with optional dithering
- Font, bold, italic, alignment, fixed size, border, direction, 180° rotation,
  number of copies

## Install and start

```sh
./install.sh        # adds "Label Printer" to the application menu
./label-printer     # or start it directly
```

`./install.sh --remove` removes the menu entry.

### Requirements

Python 3.10+, GTK 3 with its Python bindings, Pillow and CUPS. On Ubuntu:

```sh
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-pil cups fontconfig
```

### Printer setup

The app uses CUPS queues with the **ptouch** driver, listed as
"Brother QL-700 Foomatic/ptouch-ql" (package `printer-driver-ptouch`).
Brother's own CUPS driver uses different option names, so its queues are not
offered. If several queues qualify, the system default is listed first.

## Use

1. Choose the roll that is in the printer. The app cannot detect it.
2. Type your text and check the preview.
3. Press **Print**.

| Shortcut | Action |
|---|---|
| Ctrl+Return, Ctrl+P | Print |
| Ctrl+V | Paste text, or a picture if the clipboard holds one |
| Ctrl+O | Open a picture |
| Ctrl+Q | Quit |

On continuous tape the length follows the content, up to 300 mm. With
"Fit label" the text fills the full tape width, which can make long text use a
lot of tape on wide rolls; choose a fixed text size to avoid that. The app asks
before using more than 150 mm.

"Sent to the printer" means CUPS has handed over the job. If nothing comes out
or the printer blinks, check that the selected roll is the one inserted. If a
job is still waiting after 15 seconds, the app offers to cancel it.

Only the selected font is used, so emoji and other characters it lacks may
print as empty boxes. Paste them as a picture instead.

A job that is still waiting stays in the CUPS queue if you close the window, and
prints once the printer is available. Cancel it first if you no longer want it.

Options are remembered in `~/.config/label-printer/settings.json`. Label texts
and pictures are not saved.

## Development

```sh
python3 -m unittest
```

`tests/test_driver.py` runs every label format through the installed printer
driver and decodes the result. It prints nothing, and is skipped if there is no
suitable queue.

| File | Content |
|---|---|
| `labelprinter/layouts.py` | Label formats and their margins |
| `labelprinter/render.py` | Layout of text and pictures (no GUI code) |
| `labelprinter/printing.py` | CUPS commands |
| `labelprinter/settings.py` | Remembered options |
| `labelprinter/app.py` | GTK 3 window |

To add a format, add a line to `LAYOUTS` using the page size name and
dimensions from the queue's PPD file, then run the tests.
