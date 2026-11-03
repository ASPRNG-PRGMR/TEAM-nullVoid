#!/usr/bin/env python3
"""Put real objects under the lens and score the classifier against them.

The capture archive names frames by what the model *guessed*, which is the
wrong way round for testing: to know whether the bin is right you have to
record what the object actually was. This tool asks you first, then triggers
a capture, then compares.

    python tools/bench_test.py

For each item: hold it under the lens, type what it really is, press Enter.

    r / 1   recyclable      (dry: bottle, can, carton, paper, clean plastic)
    n / 2   non-recyclable  (wet: food waste, tissue, wrappers, anything soiled)
    s       skip - retake, do not score
    u       undo the last item
    q       quit and print the scoreboard

Every frame lands in data/dataset/<truth>/ , which is exactly the layout
tools/train_finetune.py wants, so a scoring session doubles as dataset
collection. The filename keeps the prediction too, so the misses are easy to
find:  HHMMSS_000007_pred-recyclable-073.jpg

Options:
    --port /dev/ttyACM0   --baud 921600
    --settle 0.0          extra seconds to wait after triggering
    --dry                 score without a board, re-feeding saved captures
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from binarybin import protocol as proto  # noqa: E402
from binarybin.classifier import Classifier  # noqa: E402
from binarybin.config import (  # noqa: E402
    BIN_NON_RECYCLABLE,
    BIN_RECYCLABLE,
    REPO_ROOT,
    SETTINGS,
)

TRUTHS = {
    "r": "recyclable",
    "1": "recyclable",
    "n": "non_recyclable",
    "2": "non_recyclable",
}
BIN_OF = {"recyclable": BIN_RECYCLABLE, "non_recyclable": BIN_NON_RECYCLABLE}

PROMPT = "truth? [r]ecyclable / [n]on-recyclable / [s]kip / [u]ndo / [q]uit > "


class Scoreboard:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, float, int]] = []

    def add(self, truth: str, pred: str, conf: float, got_bin: int) -> None:
        self.rows.append((truth, pred, conf, got_bin))

    def undo(self) -> bool:
        if not self.rows:
            return False
        self.rows.pop()
        return True

    @property
    def hits(self) -> int:
        return sum(1 for t, _, _, b in self.rows if b == BIN_OF[t])

    def report(self) -> str:
        if not self.rows:
            return "nothing scored."
        n = len(self.rows)
        acc = self.hits / n
        out = [
            "",
            "=" * 58,
            f"  {n} items   {self.hits} routed correctly   {acc:.0%} accuracy",
            "=" * 58,
        ]

        # Confusion by bin, which is the number that actually matters: a wrong
        # label that still lands in the right bin is not a sorting failure.
        cm: Counter[tuple[str, int]] = Counter()
        for truth, _, _, got in self.rows:
            cm[(truth, got)] += 1
        out.append("")
        out.append("  truth \\ routed to      bin 1 (dry)   bin 2 (wet)")
        for truth in ("recyclable", "non_recyclable"):
            b1 = cm[(truth, BIN_RECYCLABLE)]
            b2 = cm[(truth, BIN_NON_RECYCLABLE)]
            flag = "" if BIN_OF[truth] == BIN_RECYCLABLE else ""
            out.append(f"  {truth:<22}{b1:^13}{b2:^13}{flag}")

        misses = [r for r in self.rows if r[3] != BIN_OF[r[0]]]
        if misses:
            out.append("")
            out.append(f"  {len(misses)} misrouted:")
            for truth, pred, conf, got in misses:
                out.append(
                    f"    was {truth:<15} model said {pred:<16}"
                    f" {conf:.2f}  -> bin {got}"
                )

        unknowns = sum(1 for _, p, _, _ in self.rows if p == "unknown")
        if unknowns:
            out.append("")
            out.append(
                f"  {unknowns}/{n} came back 'unknown' and fell through to bin 2."
            )
            out.append(
                "  That is the ImageNet baseline's coverage gap, not a bug - it "
                "is\n  the argument for fine-tuning on what you just collected."
            )
        out.append("")
        return "\n".join(out)


def save(data: bytes, truth: str, seq: int, pred: str, conf: float) -> Path:
    out = REPO_ROOT / "data" / "dataset" / truth
    out.mkdir(parents=True, exist_ok=True)
    name = (
        f"{time.strftime('%H%M%S')}_{seq:06d}_pred-{pred}-{int(conf * 100):03d}.jpg"
    )
    path = out / name
    path.write_bytes(data)
    return path


def ask(prompt: str) -> str | None:
    try:
        line = input(prompt)
    except EOFError:
        return None
    return line.strip().lower()


def capture_one(
    ser: serial.Serial, reader: proto.FrameReader, clf: Classifier, settle: float
) -> tuple[bytes, int, object] | None:
    """Trigger one cycle; return (jpeg, seq, result) once the frame arrives."""
    ser.reset_input_buffer()
    ser.write(b"g")
    ser.flush()
    if settle:
        time.sleep(settle)

    deadline = time.time() + 15.0
    while time.time() < deadline:
        chunk = ser.read(8192)
        if not chunk:
            continue
        for frame in reader.feed(chunk):
            if frame.type == proto.TYPE_IMAGE:
                seq, jpeg = proto.split_image_payload(frame.payload)
                result = clf.classify_jpeg(jpeg, seq=seq, device="bench")
                reply = result.as_json()
                reply["seq"] = seq
                ser.write(proto.encode(proto.TYPE_VERDICT, json.dumps(reply).encode()))
                ser.flush()
                return jpeg, seq, result
            if frame.type == proto.TYPE_LOG:
                print(f"    [device] {frame.payload.decode('utf-8', 'replace').rstrip()}")
    print("    timed out waiting for a frame - is the firmware running?")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default=SETTINGS.serial_port)
    ap.add_argument("--baud", type=int, default=SETTINGS.serial_baud)
    ap.add_argument(
        "--settle",
        type=float,
        default=0.0,
        help="extra seconds between the trigger and reading the frame",
    )
    ap.add_argument(
        "--dry",
        action="store_true",
        help="no board: score saved captures from data/captures instead",
    )
    args = ap.parse_args()

    clf = Classifier()
    board = Scoreboard()

    print(__doc__.split("Options:")[0].rstrip())
    print(f"\nbackend: {clf.backend.name}   rotation: {SETTINGS.rotate_deg}deg\n")

    if args.dry:
        pool = sorted((SETTINGS.capture_dir).rglob("*.jpg"))
        if not pool:
            print("no saved captures to replay", file=sys.stderr)
            return 1
        print(f"dry run: replaying {len(pool)} saved captures\n")

    ser = None
    reader = proto.FrameReader()
    if not args.dry:
        try:
            ser = serial.Serial(args.port, args.baud, timeout=0.2)
        except serial.SerialException as exc:
            print(f"cannot open {args.port}: {exc}", file=sys.stderr)
            print("(try --dry to score saved captures without the board)")
            return 1
        print(f"open {args.port} @ {args.baud}\n")

    n = 0
    try:
        while True:
            answer = ask(PROMPT)
            if answer is None or answer.startswith("q"):
                break
            if answer == "u":
                print("  undone" if board.undo() else "  nothing to undo")
                continue
            if answer == "s" or answer == "":
                continue
            truth = TRUTHS.get(answer)
            if truth is None:
                print("  ? use r, n, s, u or q")
                continue

            if args.dry:
                src = pool[n % len(pool)]
                jpeg = src.read_bytes()
                result = clf.classify_jpeg(jpeg, seq=n, device="dry")
                seq = n
            else:
                got = capture_one(ser, reader, clf, args.settle)
                if got is None:
                    continue
                jpeg, seq, result = got

            n += 1
            path = save(jpeg, truth, seq, result.label, result.confidence)
            correct = result.bin == BIN_OF[truth]
            mark = "OK  " if correct else "MISS"
            print(
                f"  {mark} was {truth:<15} model {result.label:<16}"
                f" {result.confidence:.2f} -> bin {result.bin}"
                f"   ({len(jpeg) // 1024} kB, {result.latency_ms} ms)"
            )
            print(f"       {result.detail}")
            print(f"       saved {path.relative_to(REPO_ROOT)}")
            running = board.hits + (1 if correct else 0)
            print(f"       running: {running}/{n}")
            board.add(truth, result.label, result.confidence, result.bin)
    except KeyboardInterrupt:
        pass
    finally:
        if ser is not None:
            ser.close()

    print(board.report())
    if board.rows:
        print("  next: python tools/train_finetune.py --data ../data/dataset")
        print("  (a couple of hundred per class is enough to beat the baseline)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
