#!/usr/bin/env python3
"""Wi-Fi inference host.

    python serve_wifi.py                 # 0.0.0.0:8000, backend auto-selected
    BB_BACKEND=heuristic python serve_wifi.py
    BB_CHECKPOINT=models/binbin.pt python serve_wifi.py

The ESP32-S3 POSTs a JPEG to /classify and reads back the bin number. Test
the whole path without hardware:

    curl -s --data-binary @sample.jpg -H 'Content-Type: image/jpeg' \
         http://localhost:8000/classify | python -m json.tool
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from binarybin.classifier import Classifier
from binarybin.config import SETTINGS

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
)
log = logging.getLogger("binarybin.serve")

classifier: Classifier | None = None
started = time.time()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Load the model once, before the first item ever lands in the shuttle.
    # A cold first inference is ~1.4 s and the firmware's budget is 4 s.
    global classifier
    classifier = Classifier()
    SETTINGS.telemetry_log.parent.mkdir(parents=True, exist_ok=True)
    log.info("listening on %s:%d", SETTINGS.host, SETTINGS.port)
    log.info("captures -> %s", SETTINGS.capture_dir)
    yield


app = FastAPI(title="Binary Bin inference host", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "backend": classifier.backend.name if classifier else "starting",
        "seen": classifier.seen if classifier else 0,
        "uptime_s": int(time.time() - started),
        "min_confidence": SETTINGS.min_confidence,
    }


@app.post("/classify")
async def classify(
    request: Request,
    x_seq: str = Header(default="0"),
    x_device_id: str = Header(default="unknown"),
) -> JSONResponse:
    body = await request.body()
    if not body:
        return JSONResponse({"error": "empty body"}, status_code=400)

    try:
        seq = int(x_seq)
    except ValueError:
        seq = 0

    assert classifier is not None
    result = classifier.classify_jpeg(body, seq=seq, device=x_device_id)
    log.info(
        "#%-6d %-7s %-16s conf %.2f -> bin %d  (%d kB, %d ms)  %s",
        seq,
        x_device_id,
        result.label,
        result.confidence,
        result.bin,
        len(body) // 1024,
        result.latency_ms,
        result.detail,
    )
    payload = result.as_json()
    payload["seq"] = seq
    return JSONResponse(payload)


@app.post("/telemetry")
async def telemetry(request: Request) -> PlainTextResponse:
    """One JSON line per completed cycle. This is the demo-day evidence."""
    try:
        record = await request.json()
    except Exception:
        return PlainTextResponse("bad json", status_code=400)

    record["received_at"] = time.time()
    with SETTINGS.telemetry_log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    log.info(
        "cycle #%s bin %s in %s ms (link %s ms)%s",
        record.get("seq"),
        record.get("bin"),
        record.get("cycle_ms"),
        record.get("link_ms"),
        "  [fallback]" if record.get("fallback") else "",
    )
    return PlainTextResponse("ok")


if __name__ == "__main__":
    uvicorn.run(app, host=SETTINGS.host, port=SETTINGS.port, log_level="warning")
