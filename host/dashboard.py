#!/usr/bin/env python3
"""Web dashboard: live viewfinder, plus scored captures from the browser.

Aiming a camera you cannot see through is guesswork, so the firmware can
stream QQVGA frames over the same serial line it sends everything else on.
This serves them as MJPEG and puts the labelling buttons next to the picture.

    python dashboard.py                 # http://127.0.0.1:8080
    python dashboard.py --port-http 9000
    python dashboard.py --host 0.0.0.0  # reachable from the LAN - see below

Only one process can hold the serial port, so this *replaces*
`tools/bench_test.py` and `serve_uart.py` while it runs; it does the same
scoring and writes to the same `data/dataset/<truth>/` layout.

The viewfinder is QQVGA (160x120) and only runs while the machine is idle -
a classification frame is SVGA, and the sensor cannot be both at once.

Binding: defaults to localhost. This page can move the mechanism, so only
pass --host 0.0.0.0 on a network you trust.
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import serial
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from binarybin import protocol as proto  # noqa: E402
from binarybin.classifier import Classifier  # noqa: E402
from binarybin.config import (  # noqa: E402
    BIN_NON_RECYCLABLE,
    BIN_RECYCLABLE,
    REPO_ROOT,
    SETTINGS,
)

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

    @property
    def correct(self) -> bool | None:
        if self.truth is None:
            return None
        return self.bin == BIN_OF[self.truth]


@dataclass
class State:
    preview: bytes | None = None
    preview_at: float = 0.0
    shots: list[Shot] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    connected: bool = False

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

    def toggle_preview(self) -> bool:
        self.want_preview = not self.want_preview
        self._cmds.put(b"v")
        return self.want_preview

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
                    _save(jpeg, truth, seq, result.label, result.confidence)
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


def _save(data: bytes, truth: str, seq: int, pred: str, conf: float) -> Path:
    out = REPO_ROOT / "data" / "dataset" / truth
    out.mkdir(parents=True, exist_ok=True)
    name = f"{time.strftime('%H%M%S')}_{seq:06d}_pred-{pred}-{int(conf * 100):03d}.jpg"
    path = out / name
    path.write_bytes(data)
    return path


# ------------------------------------------------------------------ page --
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Binary Bin - viewfinder</title>
<style>
 :root { --ink:#1a1a1a; --paper:#FFFDF8; --teal:#0F766E; --orange:#C95F16;
         --dry:#2F6FAE; --wet:#4A7C45; --muted:#6B6B6B; }
 * { box-sizing:border-box; }
 body { margin:0; font:15px/1.5 ui-sans-serif,system-ui,sans-serif;
        background:var(--paper); color:var(--ink); }
 header { padding:14px 20px; border-bottom:1px solid #e7e2d8;
          display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; }
 h1 { font-size:17px; margin:0; letter-spacing:.02em; }
 .sub { color:var(--muted); font-size:13px; }
 .dot { width:9px; height:9px; border-radius:50%; display:inline-block;
        margin-right:5px; background:#c33; }
 .dot.on { background:var(--teal); }
 main { display:grid; grid-template-columns:minmax(320px,1fr) 1fr;
        gap:22px; padding:20px; align-items:start; }
 @media (max-width:860px){ main{grid-template-columns:1fr;} }
 .stage { background:#111; border-radius:10px; overflow:hidden;
          display:flex; align-items:center; justify-content:center;
          position:relative; }
 .stage img { display:block; image-rendering:pixelated; }
 .hint { position:absolute; bottom:8px; left:10px; color:#fff; opacity:.75;
         font-size:12px; text-shadow:0 1px 3px #000; }
 button { font:inherit; padding:11px 16px; border-radius:8px; cursor:pointer;
          border:1px solid #d8d2c6; background:#fff; }
 button:hover { border-color:var(--teal); }
 button:disabled { opacity:.45; cursor:wait; }
 .b1 { border-color:var(--dry); color:var(--dry); font-weight:600; }
 .b2 { border-color:var(--wet); color:var(--wet); font-weight:600; }
 .row { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:14px; }
 table { border-collapse:collapse; width:100%; font-size:13px; }
 th,td { text-align:left; padding:5px 8px; border-bottom:1px solid #eee7da; }
 th { color:var(--muted); font-weight:500; }
 .ok { color:var(--teal); } .miss { color:#b4452c; font-weight:600; }
 .score { font-size:26px; font-weight:600; }
 .detail { color:var(--muted); font-size:12px; }
 pre { background:#f6f2e9; padding:10px; border-radius:8px; font-size:12px;
       max-height:150px; overflow:auto; margin:0; }
</style></head><body>
<header>
  <h1>Binary Bin &middot; viewfinder</h1>
  <span class="sub"><span id="dot" class="dot"></span><span id="conn">…</span></span>
  <span class="sub" id="meta"></span>
</header>
<main>
 <div>
  <div class="stage" id="stage">
    <img id="feed" src="/stream.mjpg" alt="camera feed">
    <div class="hint">QQVGA &middot; idle only</div>
  </div>
  <div class="row" style="margin-top:14px">
    <button onclick="togglePreview()" id="pv">pause feed</button>
    <button onclick="shoot(null)">capture, don't score</button>
  </div>
 </div>
 <div>
  <h3 style="margin:0 0 4px">Score an object</h3>
  <p class="detail" style="margin:0 0 10px">
    Hold the item under the lens, then say what it <em>actually</em> is.
    The frame is saved into <code>data/dataset/&lt;truth&gt;/</code>.</p>
  <div class="row">
    <button class="b1" onclick="shoot('recyclable')">recyclable &rarr; bin 1</button>
    <button class="b2" onclick="shoot('non_recyclable')">non-recyclable &rarr; bin 2</button>
  </div>
  <div class="score" id="score">0/0</div>
  <div class="detail" id="scoresub">nothing scored yet</div>
  <table id="tbl"><thead><tr><th>#</th><th>truth</th><th>model</th>
    <th>conf</th><th>bin</th><th></th></tr></thead><tbody></tbody></table>
  <h3 style="margin:18px 0 6px">Device log</h3>
  <pre id="log"></pre>
 </div>
</main>
<script>
const ROT = __ROT__;           // degrees, matching BB_ROTATE
function layout() {
  const img = document.getElementById('feed');
  const st = document.getElementById('stage');
  const w = 160, h = 120;
  // After a quarter turn the frame's on-screen footprint has its axes
  // swapped, so fit against those, and cap the height or the controls get
  // pushed off the bottom of the window.
  const shownW = (ROT % 180) ? h : w, shownH = (ROT % 180) ? w : h;
  const maxH = Math.max(240, window.innerHeight - 260);
  const scale = Math.max(1, Math.floor(Math.min(
      (st.clientWidth - 8) / shownW, maxH / shownH)));
  img.style.width = (w * scale) + 'px';
  img.style.height = (h * scale) + 'px';
  img.style.transform = 'rotate(' + (-ROT) + 'deg)';
  st.style.height = (shownH * scale) + 'px';
}
addEventListener('resize', layout); addEventListener('load', layout);

async function shoot(truth) {
  const bs = document.querySelectorAll('button');
  bs.forEach(b => b.disabled = true);
  try {
    await fetch('/api/capture', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({truth})});
  } finally { bs.forEach(b => b.disabled = false); refresh(); }
}
async function togglePreview() {
  const r = await (await fetch('/api/preview', {method:'POST'})).json();
  document.getElementById('pv').textContent = r.on ? 'pause feed' : 'resume feed';
}
async function refresh() {
  const s = await (await fetch('/api/state')).json();
  document.getElementById('dot').className = 'dot' + (s.connected ? ' on' : '');
  document.getElementById('conn').textContent = s.connected ? 'board connected' : 'no board';
  document.getElementById('meta').textContent =
    s.backend + ' · rotation ' + s.rotate + '° · ' + s.fps.toFixed(1) + ' fps';
  if (s.scored) {
    document.getElementById('score').textContent =
      s.hits + '/' + s.scored + '  ' + Math.round(100*s.hits/s.scored) + '%';
    document.getElementById('scoresub').textContent = 'routed to the right bin';
  }
  const tb = document.querySelector('#tbl tbody');
  tb.innerHTML = s.shots.slice().reverse().map(x => `<tr>
    <td>${x.seq}</td><td>${x.truth ?? '—'}</td>
    <td>${x.label}<div class="detail">${x.detail ?? ''}</div></td>
    <td>${x.confidence.toFixed(2)}</td><td>${x.bin}</td>
    <td class="${x.correct===null?'':(x.correct?'ok':'miss')}">
      ${x.correct===null?'':(x.correct?'OK':'MISS')}</td></tr>`).join('');
  document.getElementById('log').textContent = s.log.join('\\n');
}
setInterval(refresh, 1500); refresh();
</script></body></html>
"""


def build_app(worker: Worker) -> FastAPI:
    app = FastAPI(title="Binary Bin dashboard")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE.replace("__ROT__", str(SETTINGS.rotate_deg % 360))

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

    @app.post("/api/capture")
    async def capture(body: dict | None = None) -> JSONResponse:
        truth = (body or {}).get("truth")
        if truth is not None and truth not in BIN_OF:
            return JSONResponse({"error": f"bad truth {truth!r}"}, status_code=400)
        shot = worker.capture(truth)
        if shot is None:
            return JSONResponse({"error": "timed out"}, status_code=504)
        return JSONResponse(shot.__dict__ | {"correct": shot.correct})

    @app.post("/api/preview")
    def preview() -> dict:
        return {"on": worker.toggle_preview()}

    @app.get("/api/state")
    def state() -> dict:
        with worker.lock:
            s = worker.state
            fresh = time.time() - s.preview_at < PREVIEW_STALE_S
            return {
                "connected": s.connected,
                "streaming": fresh,
                "fps": worker.fps,
                "backend": worker.clf.backend.name,
                "rotate": SETTINGS.rotate_deg % 360,
                "scored": len(s.scored),
                "hits": s.hits,
                "shots": [x.__dict__ | {"correct": x.correct} for x in s.shots[-15:]],
                "log": s.log[-20:],
            }

    return app


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default=SETTINGS.serial_port, help="serial device")
    ap.add_argument("--baud", type=int, default=SETTINGS.serial_baud)
    ap.add_argument("--host", default="127.0.0.1", help="HTTP bind address")
    ap.add_argument("--port-http", type=int, default=8080)
    args = ap.parse_args()

    clf = Classifier()
    worker = Worker(args.port, args.baud, clf)
    worker.start()

    print(f"\n  dashboard:  http://{args.host}:{args.port_http}\n")
    uvicorn.run(
        build_app(worker), host=args.host, port=args.port_http, log_level="warning"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
