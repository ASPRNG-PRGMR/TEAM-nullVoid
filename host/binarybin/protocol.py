"""Host half of the framed USB-UART protocol.

Mirrors firmware/binary-bin-s3cam/src/link_uart.cpp. See docs/PROTOCOL.md.

  byte 0      0xA5
  byte 1      0x5A
  byte 2      type
  bytes 3..6  payload length, u32 little endian
  bytes 7..   payload
  last 4      CRC32 (IEEE, zlib) over the payload, little endian

The sync word plus the CRC mean the reader can start mid-stream and recover:
boot-ROM chatter and panic dumps are simply bytes that never form a valid
frame.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Iterator, Protocol

SYNC = b"\xA5\x5A"

TYPE_IMAGE = 0x01
TYPE_VERDICT = 0x02
TYPE_PREVIEW = 0x03
TYPE_LOG = 0x10
TYPE_TELEMETRY = 0x11
TYPE_HELLO = 0x20

TYPE_NAMES = {
    TYPE_IMAGE: "IMAGE",
    TYPE_VERDICT: "VERDICT",
    TYPE_PREVIEW: "PREVIEW",
    TYPE_LOG: "LOG",
    TYPE_TELEMETRY: "TELEMETRY",
    TYPE_HELLO: "HELLO",
}

MAX_PAYLOAD = 512 * 1024


@dataclass
class Frame:
    type: int
    payload: bytes

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.type, f"0x{self.type:02X}")


def encode(frame_type: int, payload: bytes) -> bytes:
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    return (
        SYNC
        + bytes([frame_type])
        + struct.pack("<I", len(payload))
        + payload
        + struct.pack("<I", crc)
    )


class ByteSource(Protocol):
    def read(self, n: int) -> bytes: ...


class FrameReader:
    """Incremental de-framer. Feed it bytes, get whole frames out.

    Deliberately not a generator over a blocking read: the serial bridge
    wants to poll, and the test harness wants to push a buffer in one go.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.resyncs = 0
        self.crc_errors = 0

    def feed(self, data: bytes) -> Iterator[Frame]:
        self._buf.extend(data)
        while True:
            frame = self._take()
            if frame is None:
                return
            yield frame

    def _take(self) -> Frame | None:
        start = self._buf.find(SYNC)
        if start < 0:
            # Keep one byte in case the sync word straddles two reads.
            if len(self._buf) > 1:
                dropped = len(self._buf) - 1
                if dropped:
                    self.resyncs += 1
                del self._buf[:-1]
            return None
        if start > 0:
            self.resyncs += 1
            del self._buf[:start]

        if len(self._buf) < 7:
            return None
        frame_type = self._buf[2]
        (length,) = struct.unpack_from("<I", self._buf, 3)
        if length > MAX_PAYLOAD:
            # Not a real header. Step over this sync word and keep hunting.
            del self._buf[:2]
            self.resyncs += 1
            return None

        total = 7 + length + 4
        if len(self._buf) < total:
            return None

        payload = bytes(self._buf[7 : 7 + length])
        (want,) = struct.unpack_from("<I", self._buf, 7 + length)
        got = zlib.crc32(payload) & 0xFFFFFFFF
        if got != want:
            self.crc_errors += 1
            del self._buf[:2]
            return None

        del self._buf[:total]
        return Frame(type=frame_type, payload=payload)


def split_image_payload(payload: bytes) -> tuple[int, bytes]:
    """IMAGE frames carry a u32 sequence number before the JPEG."""
    if len(payload) < 4:
        return 0, b""
    (seq,) = struct.unpack_from("<I", payload, 0)
    return seq, payload[4:]
