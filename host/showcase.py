#!/usr/bin/env python3
"""Showcase screen: the one pointed at the judges.

Same machine, same serial link, same classifier as `dashboard.py` - this is
only a different face on it, built to be read from three metres away rather
than worked at. The bench dashboard is for us; this is for the table.

    python showcase.py                   # http://127.0.0.1:8081
    python showcase.py --port-http 9001
    python showcase.py --host 0.0.0.0    # see the note in dashboard.py

Driving it, with nothing on screen to announce the keys:

    4        this item is recyclable      -> classify it, score it
    6        this item is non-recyclable  -> classify it, score it
    space    classify without scoring, for something you cannot label
    0        clear the tally between groups of judges
    esc      dismiss the verdict card early

What 4 and 6 do is tell the *scoreboard* what the object really was. The
verdict on screen is always the machine's own: the big word is read off the
bin the classifier chose, so when it gets one wrong the screen says MISS
rather than quietly agreeing with the key you pressed. A demo that applauds
itself is worth nothing, and judges ask.

By default a key classifies the viewfinder frame the host is already
holding, which answers in about as long as the model takes - the screen is
up in a second. `--full-capture` goes the long way instead: trigger the
board, swap the sensor to SVGA, shoot, push ~18 kB up the wire. That frame
is a better look at the object, and it is the one the scoring runs in
`dashboard.py` use, but it costs most of a second you do not have in front
of an audience. Either way the verdict shown is the model's, and the card
says which frame it read.

Only one process can hold the serial port, so run this *or* dashboard.py.

The look is borrowed from BhuKavach (../bhukavach): a drafting sheet rather
than a dashboard - cream ground, hairline rules, IBM Plex, numbers set in
mono so they line up. Fonts are self-hosted under host/static; the hall
Wi-Fi is not to be trusted with the demo.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent))

from binarybin.bench import Worker, mount_api  # noqa: E402
from binarybin.classifier import Classifier  # noqa: E402
from binarybin.config import SETTINGS  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Binary Bin</title>
<style>
/* IBM Plex, self-hosted: a judge may pull the network cable, and a demo
   that falls back to Arial has already lost the first impression. */
@font-face { font-family:"Plex Cond"; src:url("/static/fonts/plex-cond-400.woff2") format("woff2");
             font-weight:400; font-display:block; }
@font-face { font-family:"Plex Cond"; src:url("/static/fonts/plex-cond-600.woff2") format("woff2");
             font-weight:600; font-display:block; }
@font-face { font-family:"Plex Mono"; src:url("/static/fonts/plex-mono-400.woff2") format("woff2");
             font-weight:400; font-display:block; }
@font-face { font-family:"Plex Mono"; src:url("/static/fonts/plex-mono-500.woff2") format("woff2");
             font-weight:500; font-display:block; }

:root {
  /* Drafting sheet, not glass: flat cream ground, hairline rules, no
     gradients and no shadows except the one the verdict card needs to lift
     off the page. */
  --sheet:#f4f1ea; --raised:#fbfaf5; --sunken:#e8e2d0;
  --rule:#cfc6a9; --rule-strong:#ada07d;
  --ink:#14181d; --ink-2:#5d6570; --ink-3:#6b7380;
  /* Colour means BIN here - blue is dry, green is wet - and nothing else on
     the screen may use those two. Amber and red are kept for state: amber
     is a miss, red is a dead link. */
  --dry:#3f7ea6; --wet:#1f6e52; --amber:#b3790e; --red:#a52e22;
  --sans:"Plex Cond",system-ui,sans-serif;
  --mono:"Plex Mono",ui-monospace,monospace;
}
* { box-sizing:border-box; }
html,body { margin:0; height:100%; background:var(--sheet); color:var(--ink);
            font-family:var(--sans); font-size:14px; line-height:1.35;
            -webkit-font-smoothing:antialiased; overflow:hidden; }
.anno { font-size:10px; letter-spacing:.14em; text-transform:uppercase;
        color:var(--ink-3); }
.num { font-family:var(--mono); font-variant-numeric:tabular-nums; }

.app { display:grid; grid-template-rows:42px minmax(0,1fr) 28px; height:100vh; }

/* ------------------------------------------------------------ chrome -- */
.bar { display:flex; align-items:stretch; background:var(--raised);
       border-bottom:1px solid var(--rule-strong); font-size:11px;
       letter-spacing:.05em; overflow:hidden; }
.bar > * { display:flex; align-items:center; padding:0 14px;
           border-right:1px solid var(--rule); white-space:nowrap; }
.bar .mark { font-weight:600; letter-spacing:.2em; font-size:13px; }
.bar .spacer { flex:1; border-right:0; }
.bar .k { color:var(--ink-3); margin-right:7px; }
.bar .v { font-family:var(--mono); }
.foot { border-top:1px solid var(--rule-strong); border-bottom:0;
        color:var(--ink-3); font-size:10px; letter-spacing:.1em; }
.dot { width:7px; height:7px; border-radius:50%; background:var(--red);
       margin-right:7px; }
.dot.on { background:var(--wet); animation:pulse 2s ease-in-out infinite; }
@keyframes pulse { 50% { opacity:.35; } }

.body { display:grid; grid-template-columns:minmax(0,1fr) 400px; min-height:0; }

/* -------------------------------------------------------------- stage -- */
.stagewrap { display:flex; align-items:center; justify-content:center;
             border-right:1px solid var(--rule-strong); min-width:0;
             background:
               repeating-linear-gradient(0deg,transparent 0 23px,#e2dbc7 23px 24px),
               repeating-linear-gradient(90deg,transparent 0 23px,#e2dbc7 23px 24px),
               var(--sheet); }
.stage { position:relative; background:#0b0f14; border:1px solid var(--rule-strong);
         display:flex; align-items:center; justify-content:center; overflow:hidden; }
.stage img { position:absolute; display:block; image-rendering:pixelated;
             transition:opacity .12s; }
#frozen { opacity:0; image-rendering:auto; }
.stage.froze #frozen { opacity:1; }
/* crop marks, the way a plate is trimmed */
.tick { position:absolute; width:16px; height:16px; border:1px solid #6b7380;
        pointer-events:none; }
.tick.tl { top:7px; left:7px; border-right:0; border-bottom:0; }
.tick.tr { top:7px; right:7px; border-left:0; border-bottom:0; }
.tick.bl { bottom:7px; left:7px; border-right:0; border-top:0; }
.tick.br { bottom:7px; right:7px; border-left:0; border-top:0; }
.ov { position:absolute; z-index:3; font-size:10px; letter-spacing:.14em;
      text-transform:uppercase; color:#c9d2dd; pointer-events:none; }
.ov.tl { top:12px; left:14px; } .ov.tr { top:12px; right:14px; }
.ov.bl { bottom:12px; left:14px; } .ov.br { bottom:12px; right:14px; }

/* --------------------------------------------------------------- rail -- */
.rail { display:flex; flex-direction:column; min-height:0; background:var(--raised); }
.rail > section { border-bottom:1px solid var(--rule); padding:14px 16px; }
.rail > section:last-child { border-bottom:0; }
.rail > section.pipe { margin-top:auto; border-top:1px solid var(--rule); }

.verdict .word { font-size:30px; font-weight:600; letter-spacing:.01em;
                 line-height:1.1; margin-top:8px; }
.verdict .bin { margin-top:6px; font-family:var(--mono); font-size:12px;
                color:var(--ink-2); }
.verdict.b1 .word { color:var(--dry); } .verdict.b2 .word { color:var(--wet); }
.verdict .mark-miss { display:inline-block; margin-top:9px; padding:2px 7px;
    border:1px solid var(--amber); color:var(--amber); font-size:10px;
    letter-spacing:.14em; text-transform:uppercase; }

.stats { display:grid; grid-template-columns:1fr 1fr; gap:0; padding:0 !important; }
.stat { padding:13px 16px; border-right:1px solid var(--rule);
        border-bottom:1px solid var(--rule); }
.stat:nth-child(2n) { border-right:0; }
.stat:nth-child(n+3) { border-bottom:0; }
.stat .v { display:block; margin-top:6px; font-family:var(--mono);
           font-variant-numeric:tabular-nums; font-size:28px; line-height:1;
           font-weight:500; }
.stat .v .u { font-size:.4em; letter-spacing:.06em; margin-left:4px;
              color:var(--ink-3); }

table { border-collapse:collapse; width:100%; font-size:12px; margin-top:9px; }
td { padding:4px 0; border-bottom:1px solid var(--sunken); }
td.r { text-align:right; font-family:var(--mono); }
.chip { font-family:var(--mono); font-size:10px; padding:1px 5px;
        border:1px solid var(--rule-strong); }
.chip.b1 { color:var(--dry); border-color:var(--dry); }
.chip.b2 { color:var(--wet); border-color:var(--wet); }
.miss { color:var(--amber); }
.feed-note { color:var(--ink-3); font-size:11px; margin-top:8px; }

/* ------------------------------------------------------------ verdict -- */
/* Full-bleed because this is the one thing the room has to read, and the
   room is looking at the bin, not the laptop. */
.curtain { position:fixed; inset:0; z-index:40; display:flex;
           align-items:center; justify-content:center;
           background:color-mix(in srgb,var(--sheet) 88%,transparent);
           opacity:0; pointer-events:none; transition:opacity .13s; }
.curtain.show { opacity:1; }
.card { background:var(--raised); border:1px solid var(--rule-strong);
        border-top:6px solid var(--ink-3); padding:34px 54px 38px;
        min-width:min(760px,86vw); text-align:center;
        box-shadow:0 24px 70px rgba(20,24,29,.16); }
.card.b1 { border-top-color:var(--dry); } .card.b2 { border-top-color:var(--wet); }
.card .big { font-size:clamp(44px,8vw,104px); font-weight:600; line-height:1;
             letter-spacing:-.01em; margin:14px 0 0; }
.card.b1 .big { color:var(--dry); } .card.b2 .big { color:var(--wet); }
.card .rule { height:1px; background:var(--rule); margin:22px 0 16px; }
.card .meta { font-family:var(--mono); font-size:15px; color:var(--ink-2);
              letter-spacing:.02em; }
.card .tag { display:inline-block; margin-top:16px; padding:4px 11px;
             font-size:11px; letter-spacing:.16em; text-transform:uppercase; }
.card .tag.miss { border:1px solid var(--amber); color:var(--amber); }
.card .tag.hit  { border:1px solid var(--rule-strong); color:var(--ink-3); }
.card .wait { color:var(--ink-3); }

/* The wait. A capture is roughly a second and a half of real work - the
   flap holds, the sensor swaps back to SVGA and throws away two frames,
   then ~18 kB crosses the wire - and an empty screen for that long reads
   as a hang. So: a focus reticle, the way a camera says it is working.
   The ring sweeps for 1 s and then, if the frame still has not landed,
   hands over to an indeterminate spin rather than sitting parked at full
   and lying about it. */
.loader { position:relative; width:208px; height:208px; margin:16px auto 4px; }
.loader svg { width:100%; height:100%; transform:rotate(-90deg); }
.loader circle { fill:none; stroke-width:1.5; }
.loader .track { stroke:var(--rule); }
.loader .arc { stroke:var(--ink); stroke-dasharray:339.3; stroke-dashoffset:339.3;
               animation:fill 1s cubic-bezier(.4,0,.3,1) forwards; }
@keyframes fill { to { stroke-dashoffset:38; } }
.loader.over .arc { stroke-dasharray:74 340; stroke-dashoffset:0;
                    transform-origin:60px 60px; animation:spin 1.05s linear infinite; }
@keyframes spin { to { transform:rotate(360deg); } }

/* the autofocus box, breathing */
.ret { position:absolute; inset:58px; animation:breathe 1s ease-in-out infinite; }
.ret i { position:absolute; width:18px; height:18px; border:1.5px solid var(--ink-2); }
.ret i:nth-child(1){ top:0; left:0; border-right:0; border-bottom:0; }
.ret i:nth-child(2){ top:0; right:0; border-left:0; border-bottom:0; }
.ret i:nth-child(3){ bottom:0; left:0; border-right:0; border-top:0; }
.ret i:nth-child(4){ bottom:0; right:0; border-left:0; border-top:0; }
@keyframes breathe { 50% { transform:scale(.82); opacity:.75; } }

/* and a scan line across it, because that is what the thing is doing */
.scanbox { position:absolute; inset:58px; overflow:hidden; }
.scanbox b { position:absolute; left:0; right:0; height:1px; background:var(--dry);
             box-shadow:0 0 7px var(--dry); animation:sweep .75s ease-in-out infinite; }
@keyframes sweep { 0%,100% { top:4%; } 50% { top:96%; } }

.steps { margin-top:20px; display:flex; gap:22px; justify-content:center;
         font-family:var(--mono); font-size:11px; letter-spacing:.17em;
         text-transform:uppercase; color:var(--ink-3); }
.steps span { opacity:.32; animation:stepon 1.05s linear infinite; }
.steps span:nth-child(2) { animation-delay:.35s; }
.steps span:nth-child(3) { animation-delay:.7s; }
@keyframes stepon { 0%{opacity:.32} 6%{opacity:1;color:var(--ink)}
                    30%{opacity:1;color:var(--ink)} 40%{opacity:.32} 100%{opacity:.32} }

/* the reveal itself: the word should land, not fade in */
@keyframes land { from { opacity:0; transform:scale(.93); }
                  to   { opacity:1; transform:scale(1); } }
.card.revealed .big { animation:land .26s cubic-bezier(.2,.9,.3,1.3) both; }
@media (prefers-reduced-motion:reduce) {
  .loader .arc, .loader.over .arc, .ret, .scanbox b, .steps span,
  .card.revealed .big { animation:none; }
  .loader .arc { stroke-dashoffset:120; }
}
</style></head><body>
<div class="app">
 <div class="bar">
   <div class="mark">BINARY&nbsp;BIN</div>
   <div class="anno" style="letter-spacing:.16em">TEAM NULLVOID &middot; BUILDATHON 2026</div>
   <div><span class="dot" id="dot"></span><span class="v" id="link">—</span></div>
   <div><span class="k">MODEL</span><span class="v" id="model">—</span></div>
   <div><span class="k">READS</span><span class="v" id="src">—</span></div>
   <div><span class="k">VIEWFINDER</span><span class="v" id="fps">—</span></div>
   <div class="spacer"></div>
   <div><span class="k">UP</span><span class="v" id="up">—</span></div>
 </div>

 <div class="body">
  <div class="stagewrap">
   <div class="stage" id="stage">
     <img id="live" src="/stream.mjpg" alt="">
     <img id="frozen" alt="">
     <span class="tick tl"></span><span class="tick tr"></span>
     <span class="tick bl"></span><span class="tick br"></span>
     <div class="ov tl" id="ovmode">LOAD BAY &middot; LIVE</div>
     <div class="ov tr" id="ovres">QQVGA 160&times;120</div>
     <div class="ov bl">ESP32-S3 &middot; OV3660</div>
     <div class="ov br" id="ovseq"></div>
   </div>
  </div>

  <aside class="rail">
   <section class="verdict" id="vcard">
     <div class="anno">Last verdict</div>
     <div class="word" id="vword">standing by</div>
     <div class="bin" id="vbin">no item sorted yet</div>
     <div id="vmiss"></div>
   </section>

   <section class="stats">
     <div class="stat"><span class="anno">Items sorted</span>
       <span class="v" id="sTotal">0</span></div>
     <div class="stat"><span class="anno">Agreement</span>
       <span class="v" id="sAcc">—</span></div>
     <div class="stat"><span class="anno">Inference</span>
       <span class="v" id="sLat">—<span class="u">ms</span></span></div>
     <div class="stat"><span class="anno">Frame</span>
       <span class="v" id="sKb">—<span class="u">kB</span></span></div>
   </section>

   <section>
     <div class="anno">Recent</div>
     <table id="recent"><tbody></tbody></table>
   </section>

   <section class="pipe">
     <div class="anno">Pipeline</div>
     <div class="feed-note">
       SVGA JPEG on the board &rarr; framed over UART at 921&nbsp;600 &rarr;
       MobileNetV3 on the host &rarr; verdict back to the board, which drives
       the shuttle. The viewfinder above is a QQVGA side-channel for aiming;
       the frame that gets classified is the full-resolution one.
     </div>
   </section>
  </aside>
 </div>

 <div class="bar foot">
   <div id="fnote">ready</div>
   <div class="spacer"></div>
   <div>v1 &middot; host inference &middot; on-device INT8 is v2</div>
 </div>
</div>

<div class="curtain" id="curtain">
  <div class="card" id="card">
    <div class="anno" id="cTop">Verdict</div>
    <div id="waitBox" hidden>
      <div class="loader" id="loader">
        <svg viewBox="0 0 120 120"><circle class="track" cx="60" cy="60" r="54"/>
          <circle class="arc" cx="60" cy="60" r="54"/></svg>
        <div class="ret"><i></i><i></i><i></i><i></i></div>
        <div class="scanbox"><b></b></div>
      </div>
      <div class="steps"><span>capture</span><span>link</span><span>classify</span></div>
    </div>
    <div id="resultBox">
      <div class="big" id="cWord">—</div>
      <div class="rule"></div>
      <div class="meta" id="cMeta"></div>
      <div id="cTagWrap"></div>
    </div>
  </div>
</div>

<script>
const ROT = __ROT__;
// true  = classify the viewfinder frame already in hand (fast, QQVGA)
// false = trigger a full SVGA capture on the board (slower, better frame)
const FAST = __FAST__;
const AR = 4/3;                       // both QQVGA and SVGA are 4:3
const turned = (ROT % 180) !== 0;

function layout() {
  const wrap = document.querySelector('.stagewrap');
  const st = document.getElementById('stage');
  let h = Math.max(240, wrap.clientHeight - 36);
  let w = turned ? h / AR : h * AR;
  const maxW = wrap.clientWidth - 36;
  if (w > maxW) { w = maxW; h = turned ? w * AR : w / AR; }
  st.style.width = w + 'px'; st.style.height = h + 'px';
  // A quarter-turned image occupies its own box with the axes swapped, so
  // the element is sized to the swap of what we want it to cover.
  for (const img of st.querySelectorAll('img')) {
    img.style.width  = (turned ? h : w) + 'px';
    img.style.height = (turned ? w : h) + 'px';
    img.style.transform = 'rotate(' + (-ROT) + 'deg)';
  }
}
addEventListener('resize', layout); addEventListener('load', layout);

const CLASS_OF = {1:'RECYCLABLE', 2:'NON-RECYCLABLE'};
const BIN_NOTE = {1:'BIN 1 \\u00b7 DRY', 2:'BIN 2 \\u00b7 WET'};
const SAID = {recyclable:'recyclable', non_recyclable:'non-recyclable'};

// How long the loader is on screen at minimum. The classify itself is
// milliseconds on the viewfinder path, so this is pacing, not progress: a
// verdict that appeared the instant a key went down would look like the key
// printed it. One second is long enough for the room to follow what
// happened and short enough that nobody is waiting.
const MIN_WAIT_MS = 1000;

let curtainTimer = null, freezeTimer = null, overTimer = null;
function curtain(state, shot) {
  const el = document.getElementById('curtain'), card = document.getElementById('card');
  const waitBox = document.getElementById('waitBox');
  const resultBox = document.getElementById('resultBox');
  const loader = document.getElementById('loader');
  clearTimeout(curtainTimer);
  if (state === 'hide') { el.className = 'curtain'; clearTimeout(overTimer); return; }
  if (state === 'wait') {
    card.className = 'card';
    document.getElementById('cTop').textContent = 'Capturing';
    waitBox.hidden = false;
    resultBox.hidden = true;
    loader.className = 'loader';   // restart the ring from empty every time
    void loader.offsetWidth;
    clearTimeout(overTimer);
    overTimer = setTimeout(() => loader.classList.add('over'), MIN_WAIT_MS);
    el.className = 'curtain show';
    return;
  }
  clearTimeout(overTimer);
  waitBox.hidden = true;
  resultBox.hidden = false;
  card.className = 'card revealed b' + shot.bin;
  document.getElementById('cTop').textContent = 'Verdict';
  document.getElementById('cWord').textContent = CLASS_OF[shot.bin];
  document.getElementById('cMeta').textContent =
    BIN_NOTE[shot.bin] + '  \\u00b7  ' + shot.label + ' ' + shot.confidence.toFixed(2)
    + '  \\u00b7  ' + shot.latency_ms + ' ms  \\u00b7  '
    + (shot.source === 'preview' ? 'viewfinder frame' : 'full capture');
  // The key only says what the object WAS. If the machine disagreed, the
  // screen says so - the alternative is a demo that cannot be wrong.
  let tag = '';
  if (shot.correct === true) tag = '<span class="tag hit">matches ground truth</span>';
  if (shot.correct === false) tag = '<span class="tag miss">miss \\u00b7 logged as '
      + SAID[shot.truth] + '</span>';
  document.getElementById('cTagWrap').innerHTML = tag;
  el.className = 'curtain show';
  curtainTimer = setTimeout(() => { el.className = 'curtain'; },
                            shot.correct === false ? 6500 : 4200);
}

function freeze(seq, dims) {
  const st = document.getElementById('stage');
  document.getElementById('frozen').src = '/api/last.jpg?s=' + seq;
  st.classList.add('froze');
  document.getElementById('ovmode').textContent = 'AS CLASSIFIED';
  document.getElementById('ovres').textContent = dims === '160x120'
    ? 'QQVGA 160\\u00d7120' : 'SVGA 800\\u00d7600';
  document.getElementById('ovseq').textContent = '#' + seq;
  clearTimeout(freezeTimer);
  freezeTimer = setTimeout(() => {
    st.classList.remove('froze');
    document.getElementById('ovmode').textContent = 'LOAD BAY \\u00b7 LIVE';
    document.getElementById('ovres').textContent = 'QQVGA 160\\u00d7120';
    document.getElementById('ovseq').textContent = '';
  }, 5000);
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

let busy = false;
async function shoot(truth) {
  if (busy) return;
  busy = true;
  const t0 = performance.now();
  curtain('wait');
  document.getElementById('fnote').textContent = 'capturing\\u2026';
  try {
    const r = await fetch('/api/capture', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({truth, fast: FAST})});
    const d = await r.json();
    if (!r.ok) {
      curtain('hide');
      document.getElementById('fnote').textContent = 'capture failed: ' + (d.error || r.status);
    } else {
      const left = MIN_WAIT_MS - (performance.now() - t0);
      if (left > 0) await sleep(left);
      curtain('verdict', d);
      freeze(d.seq, d.dims);
      document.getElementById('fnote').textContent =
        'frame #' + d.seq + ' \\u00b7 ' + d.kb + ' kB \\u00b7 ' + d.detail;
    }
  } catch (e) {
    curtain('hide');
    document.getElementById('fnote').textContent = 'capture failed: ' + e;
  } finally { busy = false; refresh(); }
}

// Nothing on screen advertises these: the demo should look like a machine
// deciding, not a person operating a console. 4 and 6 sit left and right on
// the numpad exactly as bin 1 and bin 2 sit left and right in the cabinet.
const KEYS = {Numpad4:'recyclable', Digit4:'recyclable',
              Numpad6:'non_recyclable', Digit6:'non_recyclable'};
addEventListener('keydown', e => {
  if (e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
  const tag = (e.target && e.target.tagName) || '';
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
  if (KEYS[e.code]) { e.preventDefault(); shoot(KEYS[e.code]); return; }
  if (e.code === 'Space') { e.preventDefault(); shoot(null); return; }
  if (e.code === 'Escape') { curtain('hide'); return; }
  if (e.code === 'Numpad0' || e.code === 'Digit0') {
    e.preventDefault();
    fetch('/api/reset', {method:'POST'}).then(refresh);
    document.getElementById('fnote').textContent = 'tally cleared';
  }
});

function hms(s) {
  const m = Math.floor(s / 60);
  return m + ':' + String(s % 60).padStart(2, '0');
}
async function refresh() {
  let s;
  try { s = await (await fetch('/api/state')).json(); } catch (e) { return; }
  document.getElementById('dot').className = 'dot' + (s.connected ? ' on' : '');
  document.getElementById('link').textContent =
    s.connected ? 'LINK 921600 8N1' : 'NO BOARD';
  document.getElementById('model').textContent = s.backend;
  document.getElementById('src').textContent =
    FAST ? 'viewfinder frame' : 'full capture';
  document.getElementById('fps').textContent = s.fps.toFixed(1) + ' fps';
  document.getElementById('up').textContent = hms(s.uptime);
  document.getElementById('sTotal').textContent = s.total;
  document.getElementById('sAcc').innerHTML = s.scored
    ? Math.round(100 * s.hits / s.scored) + '<span class="u">% of ' + s.scored + '</span>'
    : '—';
  document.getElementById('sLat').innerHTML =
    (s.latency_ms || '—') + '<span class="u">ms</span>';
  const last = s.last;
  document.getElementById('sKb').innerHTML =
    (last ? last.kb : '—') + '<span class="u">kB</span>';

  const vc = document.getElementById('vcard');
  if (last) {
    vc.className = 'verdict b' + last.bin;
    document.getElementById('vword').textContent = CLASS_OF[last.bin];
    document.getElementById('vbin').textContent =
      BIN_NOTE[last.bin] + '  ' + last.label + ' ' + last.confidence.toFixed(2);
    document.getElementById('vmiss').innerHTML = last.correct === false
      ? '<span class="mark-miss">miss \\u00b7 was ' + SAID[last.truth] + '</span>' : '';
  }
  const tb = document.querySelector('#recent tbody');
  if (!s.shots.length) {
    tb.innerHTML = '<tr><td colspan="4" class="feed-note">nothing sorted yet</td></tr>';
  } else tb.innerHTML = s.shots.slice(-6).reverse()
    .map(x => `<tr><td class="num">#${x.seq}</td>
      <td>${x.label}</td>
      <td class="r"><span class="chip b${x.bin}">BIN ${x.bin}</span></td>
      <td class="r ${x.correct === false ? 'miss' : ''}">${
        x.correct === null ? '—' : (x.correct ? 'ok' : 'miss')}</td></tr>`).join('');
}
setInterval(refresh, 1200); refresh();
</script></body></html>
"""


def build_app(worker: Worker, full_capture: bool = False) -> FastAPI:
    app = FastAPI(title="Binary Bin showcase")
    mount_api(app, worker)
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (PAGE.replace("__ROT__", str(SETTINGS.rotate_deg % 360))
                    .replace("__FAST__", "false" if full_capture else "true"))

    return app


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default=SETTINGS.serial_port, help="serial device")
    ap.add_argument("--baud", type=int, default=SETTINGS.serial_baud)
    ap.add_argument("--host", default="127.0.0.1", help="HTTP bind address")
    ap.add_argument("--port-http", type=int, default=8081)
    ap.add_argument(
        "--full-capture",
        action="store_true",
        help="trigger a real SVGA capture per item instead of reading the "
             "viewfinder frame: a better frame, about a second slower",
    )
    args = ap.parse_args()

    clf = Classifier()
    worker = Worker(args.port, args.baud, clf)
    worker.start()

    print(f"\n  showcase:  http://{args.host}:{args.port_http}")
    print("  reads:", "full SVGA capture" if args.full_capture
          else "viewfinder frame (--full-capture for the slow, better one)")
    print("  keys: 4 recyclable | 6 non-recyclable | space unscored |"
          " 0 reset | esc dismiss\n")
    uvicorn.run(
        build_app(worker, args.full_capture),
        host=args.host, port=args.port_http, log_level="warning",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
