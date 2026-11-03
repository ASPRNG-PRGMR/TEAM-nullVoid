#!/usr/bin/env python3
"""Contact sheet of recent captures, each labelled with the verdict it got.

Camera tuning is a visual job: you need to see framing, focus and exposure
next to what the model made of them, not one JPEG at a time. The capture
archive already encodes the verdict in each filename, so this just lays them
out in a grid and writes the caption underneath.

    python tools/contact_sheet.py                    # today, newest 24
    python tools/contact_sheet.py --count 40
    python tools/contact_sheet.py --day 2026-11-03 --out /tmp/sheet.png
    python tools/contact_sheet.py --no-rotate        # ignore BB_ROTATE

Frames are shown rotated by BB_ROTATE, which is how the model sees them -
the archived JPEGs themselves are never modified.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from binarybin.config import SETTINGS  # noqa: E402

# 131540_ttyACM0_000006_recyclable_073.jpg
NAME_RE = re.compile(
    r"^(?P<stamp>\d{6})_(?P<device>.+?)_(?P<seq>\d+)_"
    r"(?P<label>.+)_(?P<conf>\d{3})\.jpg$"
)

BIN_OF = {"recyclable": 1}


def describe(path: Path) -> tuple[str, str]:
    """(caption, detail) for one capture."""
    m = NAME_RE.match(path.name)
    if not m:
        return path.name, ""
    stamp = m.group("stamp")
    clock = f"{stamp[0:2]}:{stamp[2:4]}:{stamp[4:6]}"
    label = m.group("label")
    conf = int(m.group("conf")) / 100.0
    bin_id = BIN_OF.get(label, 2)
    return f"#{int(m.group('seq'))} {label} {conf:.2f}", f"{clock}  bin {bin_id}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--day", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--count", type=int, default=24, help="newest N captures")
    ap.add_argument("--cols", type=int, default=6)
    ap.add_argument("--width", type=int, default=240, help="cell width in px")
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--no-rotate",
        action="store_true",
        help="show the raw frame instead of applying BB_ROTATE",
    )
    args = ap.parse_args()

    day_dir = SETTINGS.capture_dir / args.day
    shots = sorted(day_dir.glob("*.jpg"))[-args.count :]
    if not shots:
        print(f"no captures in {day_dir}", file=sys.stderr)
        return 1

    rotate = 0 if args.no_rotate else SETTINGS.rotate_deg % 360

    # Size one cell from the first frame so the grid matches the real aspect.
    probe = Image.open(shots[0])
    if rotate:
        probe = probe.rotate(rotate, expand=True)
    cell_w = args.width
    cell_h = round(cell_w * probe.height / probe.width)
    pad, caption_h = 8, 30

    cols = min(args.cols, len(shots))
    rows = (len(shots) + cols - 1) // cols
    sheet = Image.new(
        "RGB",
        (cols * (cell_w + pad) + pad, rows * (cell_h + caption_h + pad) + pad),
        "#FFFDF8",
    )
    draw = ImageDraw.Draw(sheet)

    for i, path in enumerate(shots):
        im = Image.open(path)
        if rotate:
            im = im.rotate(rotate, expand=True)
        mean = ImageStat.Stat(im.convert("L")).mean[0]
        im = im.resize((cell_w, cell_h))

        x = pad + (i % cols) * (cell_w + pad)
        y = pad + (i // cols) * (cell_h + caption_h + pad)
        sheet.paste(im, (x, y))

        caption, detail = describe(path)
        draw.text((x, y + cell_h + 2), caption, fill="#0F766E")
        draw.text((x, y + cell_h + 14), f"{detail}  lum {mean:.0f}", fill="#6B6B6B")

    out = Path(args.out) if args.out else day_dir / "sheet.png"
    sheet.save(out)
    print(f"{len(shots)} frames -> {out}")
    print(f"rotation applied: {rotate}deg" if rotate else "rotation: none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
