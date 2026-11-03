"""The live bench session: one process, one serial port, two front ends.

`dashboard.py` (the working viewfinder) and `showcase.py` (the demo screen)
are two faces of the same thing, so the part that matters - owning the link,
scoring a capture, archiving it - lives here and neither page owns a copy.
Only one of them can run at a time: the serial port has one holder.

A page module supplies HTML; everything below the HTTP boundary is this.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import serial
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import protocol as proto
from .classifier import Classifier
from .config import BIN_NON_RECYCLABLE, BIN_RECYCLABLE, REPO_ROOT, SETTINGS

BIN_OF = {"recyclable": BIN_RECYCLABLE, "non_recyclable": BIN_NON_RECYCLABLE}
PREVIEW_STALE_S = 2.5


@dataclass
class Shot:
    seq: int
    truth: str | None
    label: str
    confidence: float
    bin: int
    detail: str
    latency_ms: int
    kb: int
    saved: str | None = None
    # Which frame the verdict came from: "capture" is the SVGA frame the
    # board shoots on a full cycle, "preview" is the QQVGA viewfinder frame
    # the host already had in hand. The screen says which; they are not the
    # same evidence and should never be presented as if they were.
    source: str = "capture"
    dims: str = "800x600"

    @property
    def correct(self) -> bool | None:
        if self.truth is None:
            return None
        return self.bin == BIN_OF[self.truth]

    def as_dict(self) -> dict:
        return self.__dict__ | {"correct": self.correct}


@dataclass
class State:
    preview: bytes | None = None
    preview_at: float = 0.0
    # The full-resolution frame the model was actually given, kept so a page
    # can show it. One frame, not a history - the archive on disk is that.
    last_jpeg: bytes | None = None
    last_jpeg_seq: int = -1
    shots: list[Shot] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    connected: bool = False
    started: float = field(default_factory=time.time)

    @property
    def scored(self) -> list[Shot]:
        return [s for s in self.shots if s.truth is not None]

    @property
    def hits(self) -> int:
        return sum(1 for s in self.scored if s.correct)


class Worker(threading.Thread):
    """Owns the serial port. Everything else talks to it through queues."""

    daemon = True

    def __init__(self, port: str, baud: float, clf: Classifier) -> None:
        super().__init__(name="serial")
        self.port, self.baud, self.clf = port, baud, clf
        self.state = State()
        self.lock = threading.Lock()
        self._cmds: queue.Queue[bytes] = queue.Queue()
        self._pending_truth: str | None = None
        self._done = threading.Event()
        self._last_shot: Shot | None = None
        self.want_preview = True
        self._stop = threading.Event()
        self._frame_times: deque[float] = deque(maxlen=20)
        self._fast_seq = 0
        # Both paths can reach the model - the serial thread on a full cycle,
        # an HTTP thread on a viewfinder classify. One at a time.
        self._clf_lock = threading.Lock()

    @property
    def fps(self) -> float:
        ts = list(self._frame_times)
        if len(ts) < 2:
            return 0.0
        span = ts[-1] - ts[0]
        return (len(ts) - 1) / span if span > 0 else 0.0

    # ---------------------------------------------------------- public --
    def capture(self, truth: str | None, timeout: float = 20.0) -> Shot | None:
        with self.lock:
            self._pending_truth = truth
        self._done.clear()
        self._cmds.put(b"g")
        if not self._done.wait(timeout):
            return None
        return self._last_shot

    def classify_preview(self, truth: str | None) -> Shot | None:
        """Classify the viewfinder frame already in hand. No cycle, no wait.

        The full path is: trigger the board, let it swap the sensor to SVGA,
        shoot, push ~18 kB up the wire, classify. That is most of a second
        even with the dead waits gone. For a demo the host is already
        holding a fresh frame of the same scene, so this skips the round
        trip entirely and answers in about as long as the model takes.

        The frame is smaller - 160x120 upscaled to the model's 224 - so this
        is a weaker look at the object than a real capture. It is a demo
        path, not the scoring path, and the screen labels which one ran.
        """
        with self.lock:
            jpeg, at = self.state.preview, self.state.preview_at
            last_seq = self.state.shots[-1].seq if self.state.shots else 0
        if jpeg is None or time.time() - at > PREVIEW_STALE_S:
            return None
        self._fast_seq = max(self._fast_seq, last_seq) + 1
        seq = self._fast_seq
        with self._clf_lock:
            result = self.clf.classify_jpeg(jpeg, seq=seq, device="viewfinder")
        saved = None
        if truth:
            # A separate tree: 160x120 frames must not quietly land in the
            # set train_finetune.py reads, which is all full captures.
            saved = str(
                save(jpeg, truth, seq, result.label, result.confidence,
                     root="dataset_qqvga").relative_to(REPO_ROOT)
            )
        shot = Shot(
            seq=seq, truth=truth, label=result.label,
            confidence=result.confidence, bin=result.bin, detail=result.detail,
            latency_ms=result.latency_ms, kb=max(1, len(jpeg) // 1024),
            saved=saved, source="preview", dims="160x120",
        )
        with self.lock:
            self.state.shots.append(shot)
            del self.state.shots[:-50]
            self.state.last_jpeg = jpeg
            self.state.last_jpeg_seq = seq
        return shot

    def toggle_preview(self) -> bool:
        self.want_preview = not self.want_preview
        self._cmds.put(b"v")
        return self.want_preview

    def reset(self) -> None:
        """Clear the tally between demo runs. The archive on disk is kept."""
        with self.lock:
            self.state.shots.clear()
        self.note("tally reset")

    def note(self, line: str) -> None:
        with self.lock:
            self.state.log.append(f"{time.strftime('%H:%M:%S')}  {line}")
            del self.state.log[:-40]

    # ----------------------------------------------------------- loop ---
    def run(self) -> None:
        while not self._stop.is_set():
            try:
                with serial.Serial(self.port, self.baud, timeout=0.05) as ser:
                    with self.lock:
                        self.state.connected = True
                    self.note(f"open {self.port} @ {self.baud}")
                    ser.reset_input_buffer()
                    self._pump(ser)
            except serial.SerialException as exc:
                with self.lock:
                    self.state.connected = False
                self.note(f"serial: {exc} - retrying")
                time.sleep(2)

    def _pump(self, ser: serial.Serial) -> None:
        reader = proto.FrameReader()
        # Preview is a toggle on the firmware side, so we cannot simply
        # assert it. Ask, then check whether frames actually turn up, and ask
        # again if they did not - that converges whatever state it was in.
        next_nudge = time.time() + 0.5
        while not self._stop.is_set():
            try:
                cmd = self._cmds.get_nowait()
            except queue.Empty:
                cmd = None
            if cmd:
                ser.write(cmd)
                ser.flush()

            chunk = ser.read(8192)
            if chunk:
                for frame in reader.feed(chunk):
                    self._on_frame(frame, ser)

            now = time.time()
            if now >= next_nudge:
                next_nudge = now + PREVIEW_STALE_S
                stale = now - self.state.preview_at > PREVIEW_STALE_S
                if self.want_preview and stale and self._pending_truth is None:
                    ser.write(b"v")
                    ser.flush()

    def _on_frame(self, frame: proto.Frame, ser: serial.Serial) -> None:
        if frame.type == proto.TYPE_PREVIEW:
            now = time.time()
            self._frame_times.append(now)
            with self.lock:
                self.state.preview = frame.payload
                self.state.preview_at = now
            return

        if frame.type == proto.TYPE_IMAGE:
            seq, jpeg = proto.split_image_payload(frame.payload)
            with self._clf_lock:
                result = self.clf.classify_jpeg(jpeg, seq=seq, device="dashboard")
            reply = result.as_json()
            reply["seq"] = seq
            ser.write(proto.encode(proto.TYPE_VERDICT, json.dumps(reply).encode()))
            ser.flush()

            with self.lock:
                truth = self._pending_truth
                self._pending_truth = None
            saved = None
            if truth:
                saved = str(
                    save(jpeg, truth, seq, result.label, result.confidence)
                    .relative_to(REPO_ROOT)
                )
            shot = Shot(
                seq=seq,
                truth=truth,
                label=result.label,
                confidence=result.confidence,
                bin=result.bin,
                detail=result.detail,
                latency_ms=result.latency_ms,
                kb=len(jpeg) // 1024,
                saved=saved,
            )
            with self.lock:
                self.state.shots.append(shot)
                del self.state.shots[:-50]
                self.state.last_jpeg = jpeg
                self.state.last_jpeg_seq = seq
            self._last_shot = shot
            self._done.set()
            return

        if frame.type == proto.TYPE_LOG:
            self.note("[device] " + frame.payload.decode("utf-8", "replace").rstrip())
        elif frame.type == proto.TYPE_TELEMETRY:
            try:
                rec = json.loads(frame.payload)
                self.note(f"cycle #{rec.get('seq')} -> bin {rec.get('bin')}")
            except ValueError:
                pass


def save(data: bytes, truth: str, seq: int, pred: str, conf: float,
         root: str = "dataset") -> Path:
    out = REPO_ROOT / "data" / root / truth
    out.mkdir(parents=True, exist_ok=True)
    name = f"{time.strftime('%H%M%S')}_{seq:06d}_pred-{pred}-{int(conf * 100):03d}.jpg"
    path = out / name
    path.write_bytes(data)
    return path


# ------------------------------------------------------------------- api --
def mount_api(app: FastAPI, worker: Worker) -> None:
    """The endpoints both pages share. Pages add their own "/"."""

    @app.get("/stream.mjpg")
    def stream() -> StreamingResponse:
        def gen():
            last = 0.0
            blank = 0
            while True:
                with worker.lock:
                    buf, at = worker.state.preview, worker.state.preview_at
                if buf and at != last:
                    last = at
                    blank = 0
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(buf)).encode() + b"\r\n\r\n"
                        + buf + b"\r\n"
                    )
                else:
                    blank += 1
                    if blank > 600:  # ~60 s with nothing: let the client retry
                        return
                time.sleep(0.05)

        return StreamingResponse(
            gen(), media_type="multipart/x-mixed-replace; boundary=frame"
        )

    @app.get("/api/last.jpg")
    def last_jpg() -> Response:
        with worker.lock:
            buf = worker.state.last_jpeg
        if buf is None:
            return Response(status_code=404)
        return Response(buf, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})

    @app.post("/api/capture")
    async def capture(body: dict | None = None) -> JSONResponse:
        body = body or {}
        truth = body.get("truth")
        if truth is not None and truth not in BIN_OF:
            return JSONResponse({"error": f"bad truth {truth!r}"}, status_code=400)
        if body.get("fast"):
            shot = worker.classify_preview(truth)
            if shot is None:
                return JSONResponse({"error": "no live frame"}, status_code=503)
            return JSONResponse(shot.as_dict())
        shot = worker.capture(truth)
        if shot is None:
            return JSONResponse({"error": "timed out"}, status_code=504)
        return JSONResponse(shot.as_dict())

    @app.post("/api/preview")
    def preview() -> dict:
        return {"on": worker.toggle_preview()}

    @app.post("/api/reset")
    def reset() -> dict:
        worker.reset()
        return {"ok": True}

    @app.get("/api/state")
    def state() -> dict:
        with worker.lock:
            s = worker.state
            fresh = time.time() - s.preview_at < PREVIEW_STALE_S
            scored = s.scored
            lat = [x.latency_ms for x in s.shots]
            return {
                "connected": s.connected,
                "streaming": fresh,
                "fps": worker.fps,
                "backend": worker.clf.backend.name,
                "rotate": SETTINGS.rotate_deg % 360,
                "uptime": int(time.time() - s.started),
                "total": len(s.shots),
                "scored": len(scored),
                "hits": s.hits,
                "latency_ms": round(sum(lat) / len(lat)) if lat else 0,
                "last": s.shots[-1].as_dict() if s.shots else None,
                "shots": [x.as_dict() for x in s.shots[-15:]],
                "log": s.log[-20:],
            }
