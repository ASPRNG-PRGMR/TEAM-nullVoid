#!/usr/bin/env python3
"""Exercise the host without a bin attached.

    python tools/send_test_image.py photo.jpg
    python tools/send_test_image.py photos/*.jpg --url http://localhost:8000/classify

Useful for sanity-checking the label map before you trust it on the rig, and
for timing the backend on whichever laptop is doing the demo.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import urllib.request
from pathlib import Path


def post(url: str, data: bytes, seq: int) -> dict:
    import json

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "image/jpeg",
            "X-Seq": str(seq),
            "X-Device-Id": "bench",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("images", nargs="+", type=Path)
    ap.add_argument("--url", default="http://localhost:8000/classify")
    args = ap.parse_args()

    times: list[float] = []
    for i, path in enumerate(args.images, start=1):
        if not path.is_file():
            print(f"skip {path}", file=sys.stderr)
            continue
        t0 = time.perf_counter()
        result = post(args.url, path.read_bytes(), i)
        wall = (time.perf_counter() - t0) * 1000
        times.append(wall)
        print(
            f"{path.name:40s} {result['label']:16s} "
            f"conf {result['confidence']:.2f}  bin {result['bin']}  "
            f"{wall:.0f} ms wall / {result['latency_ms']} ms model"
        )
        if result.get("detail"):
            print(f"{'':40s} {result['detail']}")

    if len(times) > 1:
        print(
            f"\n{len(times)} images: median {statistics.median(times):.0f} ms, "
            f"max {max(times):.0f} ms"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
