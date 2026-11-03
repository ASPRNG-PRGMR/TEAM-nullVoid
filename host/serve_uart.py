#!/usr/bin/env python3
"""USB-UART inference host.

Same classifier as serve_wifi.py, same verdict, different pipe. Use this when
the venue's Wi-Fi is hostile - which, at a hackathon, it will be.

    python serve_uart.py                      # /dev/ttyACM0 at 921600
    BB_SERIAL=/dev/ttyUSB0 python serve_uart.py
    python serve_uart.py --list               # show candidate ports
    python serve_uart.py --trigger 8          # bench: start a cycle every 8 s
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports

from binarybin import protocol as proto
from binarybin.classifier import Classifier
from binarybin.config import SETTINGS

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
)
log = logging.getLogger("binarybin.uart")


def show_ports() -> None:
    found = list(list_ports.comports())
    if not found:
        print("no serial ports found")
        return
    for p in found:
        print(f"{p.device:20s} {p.description}  [{p.hwid}]")


def handle(frame: proto.Frame, ser: serial.Serial, clf: Classifier) -> None:
    if frame.type == proto.TYPE_IMAGE:
        seq, jpeg = proto.split_image_payload(frame.payload)
        result = clf.classify_jpeg(
            jpeg, seq=seq, device=Path(SETTINGS.serial_port).name
        )
        reply = result.as_json()
        reply["seq"] = seq
        ser.write(proto.encode(proto.TYPE_VERDICT, json.dumps(reply).encode()))
        ser.flush()
        log.info(
            "#%-6d %-16s conf %.2f -> bin %d  (%d kB, %d ms)  %s",
            seq,
            result.label,
            result.confidence,
            result.bin,
            len(jpeg) // 1024,
            result.latency_ms,
            result.detail,
        )

    elif frame.type == proto.TYPE_TELEMETRY:
        try:
            record = json.loads(frame.payload)
        except ValueError:
            log.warning("unparseable telemetry")
            return
        record["received_at"] = time.time()
        SETTINGS.telemetry_log.parent.mkdir(parents=True, exist_ok=True)
        with SETTINGS.telemetry_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        log.info("cycle #%s -> bin %s", record.get("seq"), record.get("bin"))

    elif frame.type == proto.TYPE_LOG:
        log.info("[device] %s", frame.payload.decode("utf-8", "replace").rstrip())

    elif frame.type == proto.TYPE_HELLO:
        log.info("[device] hello %s", frame.payload.decode("utf-8", "replace"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default=SETTINGS.serial_port)
    ap.add_argument("--baud", type=int, default=SETTINGS.serial_baud)
    ap.add_argument("--list", action="store_true", help="list serial ports and exit")
    ap.add_argument(
        "--trigger",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="bench mode: send a 'g' this often to start a cycle, for when the "
        "presence sensor and break-beam are not wired yet (0 = off)",
    )
    args = ap.parse_args()

    if args.list:
        show_ports()
        return 0

    clf = Classifier()
    reader = proto.FrameReader()

    while True:
        try:
            with serial.Serial(args.port, args.baud, timeout=0.05) as ser:
                log.info("open %s @ %d, backend %s", args.port, args.baud, clf.backend.name)
                ser.reset_input_buffer()
                next_trigger = time.time() + args.trigger if args.trigger else None
                while True:
                    if next_trigger is not None and time.time() >= next_trigger:
                        # A bare 'g' is the firmware's console trigger. Link
                        # frames open with 0xA5, so this cannot be mistaken
                        # for one in either direction.
                        ser.write(b"g")
                        ser.flush()
                        log.info("trigger -> g")
                        next_trigger = time.time() + args.trigger
                    chunk = ser.read(8192)
                    if chunk:
                        for frame in reader.feed(chunk):
                            handle(frame, ser, clf)
                    elif reader.crc_errors:
                        # Only worth mentioning when the line goes quiet.
                        log.debug(
                            "resyncs=%d crc_errors=%d", reader.resyncs, reader.crc_errors
                        )
        except serial.SerialException as exc:
            log.warning("%s - retrying in 2 s", exc)
            time.sleep(2)
        except KeyboardInterrupt:
            log.info("bye (resyncs=%d crc_errors=%d)", reader.resyncs, reader.crc_errors)
            return 0


if __name__ == "__main__":
    sys.exit(main())
