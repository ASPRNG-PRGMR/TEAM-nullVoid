#!/usr/bin/env python3
"""Web dashboard: live viewfinder, plus scored captures from the browser.

Aiming a camera you cannot see through is guesswork, so the firmware can
stream QQVGA frames over the same serial line it sends everything else on.
This serves them as MJPEG and puts the labelling buttons next to the picture.

    python dashboard.py                 # http://127.0.0.1:8080
    python dashboard.py --port-http 9000
    python dashboard.py --host 0.0.0.0  # reachable from the LAN - see below

Numpad 4 scores the item as recyclable (bin 1) and numpad 6 as
non-recyclable (bin 2), so you can keep both hands on the objects; every
capture answers with a popup saying whether it was routed correctly.

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
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from binarybin.bench import Worker, mount_api  # noqa: E402
from binarybin.classifier import Classifier  # noqa: E402
from binarybin.config import SETTINGS  # noqa: E402


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
 kbd { font:12px/1 ui-monospace,monospace; border:1px solid #d8d2c6;
       border-bottom-width:2px; border-radius:5px; padding:3px 6px;
       background:#fff; color:var(--muted); margin-left:7px; }
 /* The popup is deliberately large and centred at the top: when you are
    standing at the bench with an object in your hand, the screen is an
    arm's length away and a line in a table is unreadable. */
 .toast { position:fixed; left:50%; bottom:10px; transform:translateX(-50%);
          min-width:360px; max-width:92vw; padding:16px 22px; border-radius:12px;
          background:#fff; border:1px solid #e0dacd; border-left:8px solid var(--muted);
          box-shadow:0 12px 34px rgba(0,0,0,.18); z-index:50;
          opacity:0; pointer-events:none; transition:opacity .14s, bottom .14s; }
 .toast.show { opacity:1; bottom:24px; }
 .toast.good { border-left-color:var(--teal); }
 .toast.bad  { border-left-color:#b4452c; }
 .toast h2 { margin:0 0 3px; font-size:21px; letter-spacing:.01em; }
 .toast p  { margin:0; color:var(--muted); font-size:13px; }
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
    The frame is saved into <code>data/dataset/&lt;truth&gt;/</code>.
    Numpad <strong>4</strong> and <strong>6</strong> do the same thing without
    the mouse &mdash; left key, left bin.</p>
  <div class="row">
    <button class="b1" onclick="shoot('recyclable')">recyclable &rarr; bin 1<kbd>4</kbd></button>
    <button class="b2" onclick="shoot('non_recyclable')">non-recyclable &rarr; bin 2<kbd>6</kbd></button>
  </div>
  <div class="score" id="score">0/0</div>
  <div class="detail" id="scoresub">nothing scored yet</div>
  <table id="tbl"><thead><tr><th>#</th><th>truth</th><th>model</th>
    <th>conf</th><th>bin</th><th></th></tr></thead><tbody></tbody></table>
  <h3 style="margin:18px 0 6px">Device log</h3>
  <pre id="log"></pre>
 </div>
</main>
<div class="toast" id="toast"><h2 id="tmsg"></h2><p id="tsub"></p></div>
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

const SAID = {recyclable:'recyclable', non_recyclable:'non-recyclable'};
let toastTimer = null;
function toast(kind, msg, sub, holdMs) {
  const el = document.getElementById('toast');
  document.getElementById('tmsg').textContent = msg;
  document.getElementById('tsub').textContent = sub || '';
  el.className = 'toast show ' + (kind || '');
  clearTimeout(toastTimer);
  // holdMs 0 means "leave it up": the capture is still in flight and the
  // next call replaces it with the verdict.
  if (holdMs) toastTimer = setTimeout(() => { el.className = 'toast ' + (kind||''); }, holdMs);
}

let busy = false;
async function shoot(truth) {
  if (busy) return;            // keys repeat, and a cycle owns the serial port
  busy = true;
  const bs = document.querySelectorAll('button');
  bs.forEach(b => b.disabled = true);
  toast('', truth ? 'capturing \u2014 ' + SAID[truth] : 'capturing\u2026',
        'hold it still, this frame is full resolution', 0);
  try {
    const r = await fetch('/api/capture', {method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify({truth})});
    const d = await r.json();
    if (!r.ok) {
      toast('bad', 'capture failed', d.error || ('HTTP ' + r.status), 5000);
    } else {
      const verdict = d.label + ' ' + d.confidence.toFixed(2) + ' \u2192 bin ' + d.bin;
      if (d.truth === null) toast('', 'captured', verdict, 4000);
      else if (d.correct)   toast('good', 'OK \u2014 bin ' + d.bin,
                                  'you said ' + SAID[d.truth] + ' \u00b7 model said ' + verdict, 3500);
      else                  toast('bad', 'MISS \u2014 went to bin ' + d.bin,
                                  'you said ' + SAID[d.truth] + ' \u00b7 model said ' + verdict, 6000);
    }
  } catch (e) {
    toast('bad', 'capture failed', String(e), 5000);
  } finally { busy = false; bs.forEach(b => b.disabled = false); refresh(); }
}

// Numpad 4 -> bin 1, numpad 6 -> bin 2, laid out like the bins themselves.
// Keyed on e.code, the physical key, so this still works with NumLock off
// where e.key reads 'ArrowLeft'; the top-row digits are accepted too.
const KEYS = {Numpad4:'recyclable',     Digit4:'recyclable',
              Numpad6:'non_recyclable', Digit6:'non_recyclable'};
addEventListener('keydown', e => {
  if (e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
  const tag = (e.target && e.target.tagName) || '';
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
  const truth = KEYS[e.code];
  if (!truth) return;
  e.preventDefault();
  shoot(truth);
});
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
    mount_api(app, worker)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE.replace("__ROT__", str(SETTINGS.rotate_deg % 360))

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
