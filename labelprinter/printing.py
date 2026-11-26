"""Send rendered labels to a CUPS queue that uses the ptouch driver."""

import os
import re
import subprocess
import tempfile

from .render import save_pdf


class PrintError(Exception):
    pass


def _run(args):
    env = dict(os.environ, LC_ALL="C.UTF-8", LANGUAGE="C")
    try:
        result = subprocess.run(args, capture_output=True, text=True,
                                env=env, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PrintError(f"Could not run {args[0]}: {error}") from error
    if result.returncode != 0:
        raise PrintError(result.stderr.strip() or f"{args[0]} failed")
    return result.stdout


def is_compatible(lpoptions_output):
    """True if a queue offers the ptouch driver's page and media names."""
    return ("29x90mm" in lpoptions_output
            and re.search(r"^MediaType/.*\bLabels\b", lpoptions_output, re.M)
            is not None)


def find_queues():
    """Names of usable queues, the system default first."""
    try:
        names = _run(["lpstat", "-e"]).split()
    except PrintError:
        return []
    queues = []
    for name in names:
        try:
            if is_compatible(_run(["lpoptions", "-p", name, "-l"])):
                queues.append(name)
        except PrintError:
            continue
    try:
        default = _run(["lpstat", "-d"]).rsplit(":", 1)[-1].strip()
    except PrintError:
        default = ""
    return sorted(queues, key=lambda name: (name != default, name))


def page_size_option(layout, page_pt):
    if layout.continuous:
        return f"PageSize=Custom.{round(page_pt[0])}x{round(page_pt[1])}"
    return f"PageSize={layout.key}"


def lp_command(queue, layout, page_pt, copies, path):
    return [
        "lp", "-d", queue, "-n", str(int(copies)), "-t", "label",
        "-o", page_size_option(layout, page_pt),
        "-o", f"MediaType={layout.media_type}",
        "-o", "print-scaling=none",
        "--", path,
    ]


def print_label(queue, layout, rendered, copies=1):
    """Submit a print job and return its CUPS job id."""
    with tempfile.TemporaryDirectory(prefix="labelprinter-") as folder:
        path = os.path.join(folder, "label.pdf")
        save_pdf(rendered, path)
        # lp copies the file into the spool before it returns.
        output = _run(lp_command(queue, layout, rendered.page_pt, copies, path))
    match = re.search(r"request id is (\S+)", output)
    if not match:
        raise PrintError(f"Unexpected answer from lp: {output.strip()}")
    return match.group(1)


def job_pending(queue, job_id):
    output = _run(["lpstat", "-o", queue])
    return any(line.split()[:1] == [job_id] for line in output.splitlines())


def queue_status(queue):
    """Human-readable state of a queue, as reported by CUPS."""
    return " ".join(_run(["lpstat", "-p", queue]).split())


def cancel_job(job_id):
    _run(["cancel", job_id])
