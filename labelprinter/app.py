"""GTK 3 user interface."""

import math
import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, GdkPixbuf, Gtk  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402

from . import layouts, printing, settings  # noqa: E402
from .render import (  # noqa: E402
    FONT_FAMILIES, MAX_LENGTH_MM, MIN_LENGTH_MM, LabelSpec, px_to_mm, render)

APP_ID = "io.github.gnoasti.LabelPrinter"
MAX_IMAGE_SIDE = 3000   # larger pictures only slow down the preview
STUCK_AFTER_S = 15
LONG_TAPE_MM = 150      # ask before printing more continuous tape than this


def load_picture(path):
    with Image.open(path) as image:
        return shrink(ImageOps.exif_transpose(image).convert("RGBA"))


def shrink(image):
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    return image


def pixbuf_to_pil(pixbuf):
    mode = "RGBA" if pixbuf.get_has_alpha() else "RGB"
    width, height = pixbuf.get_width(), pixbuf.get_height()
    stride = pixbuf.get_rowstride()
    # The last row of a pixbuf may be shorter than the row stride.
    data = pixbuf.get_pixels().ljust(stride * height, b"\0")
    image = Image.frombytes(mode, (width, height), data, "raw", mode, stride)
    return shrink(image.convert("RGBA"))


def pil_to_pixbuf(image):
    rgb = image.convert("RGB")
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(rgb.tobytes()), GdkPixbuf.Colorspace.RGB, False, 8,
        rgb.width, rgb.height, rgb.width * 3)


def combo(entries, active):
    widget = Gtk.ComboBoxText()
    for key, title in entries:
        widget.append(key, title)
    if not widget.set_active_id(active):
        widget.set_active(0)
    return widget


class LabelWindow(Gtk.ApplicationWindow):
    def __init__(self, application=None):
        super().__init__(application=application, title="Label Printer")
        self.set_default_size(1000, 640)
        self.set_icon_name("printer")
        self.saved = settings.load()
        self.picture = None
        self.rendered = None
        self.job = None            # (queue, job id, seconds waited)
        self.submitting = False
        self._redraw_source = None

        self._build()
        self._connect()
        self.refresh_queues()
        self._layout_changed()
        self.update_preview()
        self.text_view.grab_focus()

    # ----- construction -------------------------------------------------

    def _build(self):
        saved = self.saved
        header = Gtk.HeaderBar(title="Label Printer", show_close_button=True)
        self.set_titlebar(header)
        self.print_button = Gtk.Button(label="Print")
        self.print_button.get_style_context().add_class("suggested-action")
        self.print_button.set_tooltip_text("Ctrl+Return or Ctrl+P")
        header.pack_end(self.print_button)
        self.copies = Gtk.SpinButton.new_with_range(1, 50, 1)
        header.pack_end(self.copies)
        header.pack_end(Gtk.Label(label="Copies"))

        self.content = Gtk.Box(spacing=12, margin=12)
        self.add(self.content)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        sidebar.set_size_request(380, -1)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.add(sidebar)
        self.content.pack_start(scroller, False, False, 0)

        self.layout_combo = combo(
            [(item.key, item.label) for item in layouts.LAYOUTS],
            saved.get("layout", layouts.DEFAULT_KEY))
        sidebar.pack_start(self._section("Label roll"), False, False, 0)
        sidebar.pack_start(self.layout_combo, False, False, 0)

        sidebar.pack_start(self._section("Text"), False, False, 0)
        self.text_view = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.NONE, accepts_tab=False,
            left_margin=8, right_margin=8, top_margin=8, bottom_margin=8)
        text_scroller = Gtk.ScrolledWindow(shadow_type=Gtk.ShadowType.IN)
        text_scroller.set_size_request(-1, 110)
        text_scroller.add(self.text_view)
        sidebar.pack_start(text_scroller, False, False, 0)

        grid = Gtk.Grid(row_spacing=6, column_spacing=10)
        sidebar.pack_start(grid, False, False, 0)
        self.font_combo = combo([(f, f) for f in FONT_FAMILIES],
                                saved.get("font", FONT_FAMILIES[0]))
        self._row(grid, 0, "Font", self.font_combo)

        styles = Gtk.Box(spacing=12)
        self.bold = Gtk.CheckButton(label="Bold",
                                    active=saved.get("bold", True))
        self.italic = Gtk.CheckButton(label="Italic",
                                      active=saved.get("italic", False))
        self.border = Gtk.CheckButton(label="Border",
                                      active=saved.get("border", False))
        for widget in (self.bold, self.italic, self.border):
            styles.pack_start(widget, False, False, 0)
        self._row(grid, 1, "Style", styles)

        self.align = combo(
            [("left", "Left"), ("center", "Centre"), ("right", "Right")],
            saved.get("align", "center"))
        self._row(grid, 2, "Alignment", self.align)

        size_box = Gtk.Box(spacing=8)
        self.size_auto = Gtk.CheckButton(label="Fit label",
                                         active=saved.get("size_auto", True))
        self.size_spin = Gtk.SpinButton.new_with_range(4, 300, 1)
        self.size_spin.set_value(saved.get("size_pt", 14))
        size_box.pack_start(self.size_auto, False, False, 0)
        size_box.pack_start(self.size_spin, True, True, 0)
        size_box.pack_start(Gtk.Label(label="pt"), False, False, 0)
        self._row(grid, 3, "Text size", size_box)

        self.orientation = Gtk.ComboBoxText()
        self._row(grid, 4, "Direction", self.orientation)
        self.flip = Gtk.CheckButton(label="Rotate by 180°",
                                    active=saved.get("flip", False))
        self._row(grid, 5, "", self.flip)

        length_box = Gtk.Box(spacing=8)
        self.length_auto = Gtk.CheckButton(
            label="Automatic", active=saved.get("length_auto", True))
        self.length_spin = Gtk.SpinButton.new_with_range(
            MIN_LENGTH_MM, MAX_LENGTH_MM, 1)
        self.length_spin.set_value(saved.get("length_mm", 60))
        length_box.pack_start(self.length_auto, False, False, 0)
        length_box.pack_start(self.length_spin, True, True, 0)
        length_box.pack_start(Gtk.Label(label="mm"), False, False, 0)
        self.length_widgets = (self._row(grid, 6, "Length", length_box),
                               length_box)

        sidebar.pack_start(self._section("Picture"), False, False, 0)
        buttons = Gtk.Box(spacing=6, homogeneous=True)
        self.paste_button = Gtk.Button(label="Paste")
        self.paste_button.set_tooltip_text("Ctrl+V")
        self.open_button = Gtk.Button(label="Open…")
        self.open_button.set_tooltip_text("Ctrl+O, or drop a file on the preview")
        self.remove_button = Gtk.Button(label="Remove")
        for widget in (self.paste_button, self.open_button,
                       self.remove_button):
            buttons.pack_start(widget, True, True, 0)
        sidebar.pack_start(buttons, False, False, 0)

        image_grid = Gtk.Grid(row_spacing=6, column_spacing=10)
        sidebar.pack_start(image_grid, False, False, 0)
        self.image_position = combo(
            [("left", "Left of text"), ("right", "Right of text"),
             ("top", "Above text"), ("bottom", "Below text")],
            saved.get("image_position", "left"))
        self._row(image_grid, 0, "Position", self.image_position)
        self.dither = Gtk.CheckButton(
            label="Dither (for photos)", active=saved.get("dither", True))
        self._row(image_grid, 1, "", self.dither)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.content.pack_start(right, True, True, 0)
        self.preview = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.preview.set_size_request(420, 300)
        frame = Gtk.Frame()
        frame.add(self.preview)
        right.pack_start(frame, True, True, 0)

        self.info = Gtk.Label(xalign=0, wrap=True)
        right.pack_start(self.info, False, False, 0)

        status_box = Gtk.Box(spacing=8)
        self.status = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.status.set_can_focus(False)
        status_box.pack_start(self.status, True, True, 0)
        self.cancel_button = Gtk.Button(label="Cancel job", no_show_all=True)
        status_box.pack_end(self.cancel_button, False, False, 0)
        right.pack_start(status_box, False, False, 0)

        printer_box = Gtk.Box(spacing=8)
        printer_box.pack_start(Gtk.Label(label="Printer"), False, False, 0)
        self.queue_combo = Gtk.ComboBoxText()
        printer_box.pack_start(self.queue_combo, True, True, 0)
        self.refresh_button = Gtk.Button.new_from_icon_name(
            "view-refresh-symbolic", Gtk.IconSize.BUTTON)
        self.refresh_button.set_tooltip_text("Search for printers again")
        printer_box.pack_start(self.refresh_button, False, False, 0)
        right.pack_start(printer_box, False, False, 0)

        header.show_all()
        self.content.show_all()

    @staticmethod
    def _section(title):
        label = Gtk.Label(xalign=0)
        label.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
        return label

    @staticmethod
    def _row(grid, row, title, widget):
        label = Gtk.Label(label=title, xalign=0)
        grid.attach(label, 0, row, 1, 1)
        widget.set_hexpand(True)
        grid.attach(widget, 1, row, 1, 1)
        return label

    def _connect(self):
        self.layout_combo.connect("changed", lambda *_: self._layout_changed())
        for widget in (self.font_combo, self.align, self.orientation,
                       self.image_position):
            widget.connect("changed", lambda *_: self.schedule_update())
        for widget in (self.bold, self.italic, self.border, self.flip,
                       self.dither, self.size_auto, self.length_auto):
            widget.connect("toggled", lambda *_: self.schedule_update())
        for widget in (self.size_spin, self.length_spin):
            widget.connect("value-changed", lambda *_: self.schedule_update())
        self.text_view.get_buffer().connect(
            "changed", lambda *_: self.schedule_update())
        self.print_button.connect("clicked", lambda *_: self.print_current())
        self.cancel_button.connect("clicked", lambda *_: self.cancel_job())
        self.paste_button.connect("clicked", lambda *_: self.paste_picture())
        self.open_button.connect("clicked", lambda *_: self.choose_picture())
        self.remove_button.connect("clicked",
                                   lambda *_: self.set_picture(None))
        self.refresh_button.connect("clicked",
                                    lambda *_: self.refresh_queues())
        self.preview.connect("draw", self._draw_preview)
        self.preview.drag_dest_set(Gtk.DestDefaults.ALL, [],
                                   Gdk.DragAction.COPY)
        self.preview.drag_dest_add_uri_targets()
        self.preview.connect("drag-data-received", self._dropped)
        self.connect("key-press-event", self._key_pressed)
        self.connect("delete-event", lambda *_: self.save_settings())

    # ----- state --------------------------------------------------------

    @property
    def layout(self):
        return layouts.by_key(self.layout_combo.get_active_id())

    def text(self):
        buffer = self.text_view.get_buffer()
        return buffer.get_text(*buffer.get_bounds(), False)

    def spec(self):
        return LabelSpec(
            text=self.text(),
            font_family=self.font_combo.get_active_id(),
            bold=self.bold.get_active(),
            italic=self.italic.get_active(),
            align=self.align.get_active_id(),
            font_size_pt=(None if self.size_auto.get_active()
                          else self.size_spin.get_value()),
            horizontal=self.orientation.get_active_id() != "vertical",
            flip=self.flip.get_active(),
            border=self.border.get_active(),
            length_mm=(None if self.length_auto.get_active()
                       else self.length_spin.get_value()),
            image=self.picture,
            image_position=self.image_position.get_active_id(),
            dither=self.dither.get_active())

    def save_settings(self):
        direction = self.orientation.get_active_id()
        settings.save({
            "layout": self.layout.key,
            "queue": self.queue_combo.get_active_id(),
            "font": self.font_combo.get_active_id(),
            "bold": self.bold.get_active(),
            "italic": self.italic.get_active(),
            "border": self.border.get_active(),
            "align": self.align.get_active_id(),
            "size_auto": self.size_auto.get_active(),
            "size_pt": self.size_spin.get_value(),
            "direction": direction,
            "flip": self.flip.get_active(),
            "length_auto": self.length_auto.get_active(),
            "length_mm": self.length_spin.get_value(),
            "image_position": self.image_position.get_active_id(),
            "dither": self.dither.get_active(),
        })
        return False

    def _layout_changed(self):
        layout = self.layout
        if layout.continuous:
            names = ("Along the tape", "Across the tape")
        elif layout.width_pt == layout.length_pt:
            names = ("Upright", "Turned by 90°")
        else:
            names = ("Landscape", "Portrait")
        current = (self.orientation.get_active_id()
                   or self.saved.get("direction", "horizontal"))
        self.orientation.remove_all()
        self.orientation.append("horizontal", names[0])
        self.orientation.append("vertical", names[1])
        self.orientation.set_active_id(current)
        for widget in self.length_widgets:
            widget.set_visible(layout.continuous)
        self.schedule_update()

    # ----- preview ------------------------------------------------------

    def schedule_update(self):
        self.size_spin.set_sensitive(not self.size_auto.get_active())
        self.length_spin.set_sensitive(not self.length_auto.get_active())
        if self._redraw_source is None:
            self._redraw_source = GLib.timeout_add(60, self.update_preview)

    def update_preview(self):
        if self._redraw_source is not None:
            GLib.source_remove(self._redraw_source)
            self._redraw_source = None
        has_picture = self.picture is not None
        for widget in (self.remove_button, self.image_position, self.dither):
            widget.set_sensitive(has_picture)
        layout = self.layout
        try:
            self.rendered = render(layout, self.spec())
        except Exception as error:  # keep the window usable, show the reason
            self.rendered = None
            self.info.set_text(f"Cannot draw this label: {error}")
        else:
            length = (px_to_mm(self.rendered.page.height)
                      if layout.continuous else None)
            lines = [f"Insert roll {layout.code} ({layout.title})."]
            if length is not None:
                lines[0] += f" This label uses {length:.0f} mm of tape."
            lines += [f"⚠ {text}" for text in self.rendered.warnings]
            self.info.set_text("\n".join(lines))
        self._update_print_button()
        self.preview.queue_draw()
        return False

    def _draw_preview(self, area, context):
        if self.rendered is None:
            return False
        canvas = self.rendered.canvas
        width = area.get_allocated_width()
        height = area.get_allocated_height()
        context.set_source_rgb(0.55, 0.56, 0.58)
        context.paint()
        # Small labels are enlarged, but at most to twice the printed pixels.
        scale = min((width - 48) / canvas.width,
                    (height - 48) / canvas.height, 2.0)
        if scale <= 0:
            return False
        w, h = canvas.width * scale, canvas.height * scale
        x, y = (width - w) / 2, (height - h) / 2
        is_round = self.layout.kind == layouts.ROUND

        def outline(dx, dy):
            if is_round:
                context.arc(x + w / 2 + dx, y + h / 2 + dy,
                            min(w, h) / 2, 0, 2 * math.pi)
            else:
                context.rectangle(x + dx, y + dy, w, h)

        context.set_source_rgba(0, 0, 0, 0.25)
        outline(4, 4)
        context.fill()
        context.set_source_rgb(1, 1, 1)
        outline(0, 0)
        context.fill()
        # Scale down with PIL: it keeps thin lines and dithering readable.
        shown = canvas.resize((max(1, round(w)), max(1, round(h))),
                              Image.Resampling.LANCZOS)
        context.save()
        outline(0, 0)
        context.clip()
        Gdk.cairo_set_source_pixbuf(context, pil_to_pixbuf(shown),
                                    round(x), round(y))
        context.paint()
        context.restore()
        return False

    # ----- pictures -----------------------------------------------------

    def set_picture(self, picture, message=""):
        self.picture = picture
        self.status.set_text(message)
        self.update_preview()

    def open_picture(self, path):
        try:
            picture = load_picture(path)
        except Exception as error:  # PIL raises many different types
            self.status.set_text(f"Could not open the picture: {error}")
            return False
        self.set_picture(picture)
        return True

    def _open_first_uri(self, uris):
        for uri in uris or []:
            path = Gio.File.new_for_uri(uri).get_path()
            if path:
                return self.open_picture(path)
        return False

    def paste_picture(self):
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        if clipboard.wait_is_uris_available():
            self._open_first_uri(clipboard.wait_for_uris())
            return
        pixbuf = clipboard.wait_for_image()
        if pixbuf is None:
            self.status.set_text("There is no picture in the clipboard.")
        else:
            self.set_picture(pixbuf_to_pil(pixbuf))

    def choose_picture(self):
        dialog = Gtk.FileChooserNative.new(
            "Open picture", self, Gtk.FileChooserAction.OPEN, None, None)
        pictures = Gtk.FileFilter()
        pictures.set_name("Pictures")
        pictures.add_pixbuf_formats()
        dialog.add_filter(pictures)
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            self.open_picture(dialog.get_filename())
        dialog.destroy()

    def _dropped(self, _widget, _context, _x, _y, data, _info, _time):
        self._open_first_uri(data.get_uris())

    def _key_pressed(self, _widget, event):
        if not event.state & Gdk.ModifierType.CONTROL_MASK:
            return False
        key = Gdk.keyval_to_lower(event.keyval)
        if key in (Gdk.KEY_p, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.print_current()
        elif key == Gdk.KEY_o:
            self.choose_picture()
        elif key == Gdk.KEY_q:
            self.save_settings()
            self.destroy()
        elif key == Gdk.KEY_v:
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            # Plain text goes to the focused text field as usual.
            if (clipboard.wait_is_text_available()
                    and not clipboard.wait_is_uris_available()):
                return False
            if not (clipboard.wait_is_image_available()
                    or clipboard.wait_is_uris_available()):
                return False
            self.paste_picture()
        else:
            return False
        return True

    # ----- printing -----------------------------------------------------

    def refresh_queues(self):
        wanted = self.queue_combo.get_active_id() or self.saved.get("queue")
        self.queue_combo.remove_all()
        queues = printing.find_queues()
        for name in queues:
            self.queue_combo.append(name, name)
        if queues and not (wanted and self.queue_combo.set_active_id(wanted)):
            self.queue_combo.set_active(0)
        self.status.set_text(
            "" if queues else
            "No usable printer queue found. See “Printer setup” in the README.")
        self._update_print_button()

    def _update_print_button(self):
        self.print_button.set_sensitive(
            self.rendered is not None
            and not self.spec().empty
            and self.queue_combo.get_active_id() is not None
            and self.job is None and not self.submitting)

    def _confirm(self, question):
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL, text=question)
        answer = dialog.run()
        dialog.destroy()
        return answer == Gtk.ResponseType.OK

    def print_current(self):
        self.copies.update()          # take over a number still being typed
        self.update_preview()         # print exactly what is entered now
        if not self.print_button.get_sensitive():
            return
        questions = list(self.rendered.warnings)
        if self.layout.continuous:
            total_mm = (px_to_mm(self.rendered.page.height)
                        * self.copies.get_value_as_int())
            if total_mm > LONG_TAPE_MM:
                questions.append(f"This will use {total_mm:.0f} mm of tape.")
        if questions and not self._confirm(
                "\n".join(questions) + "\n\nPrint anyway?"):
            return
        queue = self.queue_combo.get_active_id()
        args = (queue, self.layout, self.rendered,
                self.copies.get_value_as_int())
        self.submitting = True
        self._update_print_button()
        self.status.set_text("Sending…")
        self.save_settings()

        def work():
            try:
                job_id = printing.print_label(*args)
            except Exception as error:  # report every failure in the window
                GLib.idle_add(self._submit_failed, str(error))
            else:
                GLib.idle_add(self._submitted, queue, job_id)

        threading.Thread(target=work, daemon=True).start()

    def _submit_failed(self, message):
        self.submitting = False
        self.status.set_text(f"Printing failed: {message}")
        self._update_print_button()
        return False

    def _submitted(self, queue, job_id):
        self.submitting = False
        self.job = [queue, job_id, 0]
        self.status.set_text(f"Printing (job {job_id})…")
        self._update_print_button()
        GLib.timeout_add_seconds(1, self._watch_job, job_id)
        return False

    def _watch_job(self, job_id):
        if self.job is None or self.job[1] != job_id:
            return False
        queue = self.job[0]
        self.job[2] += 1
        try:
            pending = printing.job_pending(queue, job_id)
            if pending and self.job[2] >= STUCK_AFTER_S:
                self.status.set_text(
                    "Still waiting. Check that the printer is on and "
                    "connected, and that the correct roll is inserted.\n"
                    + printing.queue_status(queue))
                self.cancel_button.show()
        except printing.PrintError as error:
            self.status.set_text(f"Cannot check the job: {error}")
            self.cancel_button.show()
            return True
        if pending:
            return True
        self._job_finished("Sent to the printer.")
        return False

    def _job_finished(self, message):
        self.job = None
        self.cancel_button.hide()
        self.status.set_text(message)
        self._update_print_button()

    def cancel_job(self):
        if self.job is None:
            return
        try:
            printing.cancel_job(self.job[1])
        except printing.PrintError as error:
            self.status.set_text(f"Could not cancel the job: {error}")
            return
        self._job_finished("Job cancelled.")


class LabelApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        window = self.get_active_window() or LabelWindow(self)
        window.present()


def main(argv=None):
    GLib.set_prgname(APP_ID)
    GLib.set_application_name("Label Printer")
    return LabelApplication().run(argv if argv is not None else sys.argv)
