# Device <-> host protocol

One question, two transports:

> here is a JPEG — which bin does it go in?

Both transports carry the same verdict object, so the firmware's state machine
cannot tell them apart. Implementations:
`firmware/binary-bin-s3cam/src/link_wifi.cpp`,
`firmware/binary-bin-s3cam/src/link_uart.cpp`,
`host/binarybin/protocol.py`.

## The verdict

```json
{
  "label": "recyclable",
  "bin": 1,
  "confidence": 0.87,
  "latency_ms": 31,
  "backend": "imagenet-mobilenetv3",
  "detail": "water bottle=0.52, pop bottle=0.21, ...",
  "seq": 42
}
```

| field | meaning |
|---|---|
| `bin` | `1` = recyclable / dry, `2` = non-recyclable / wet, `0` = no opinion |
| `confidence` | 0..1. Below `kMinConfidence` (0.55) the firmware uses bin 2 regardless |
| `seq` | echoed from the request, so a late reply can be dropped |
| `detail` | free text for the operator; the firmware ignores it |

The firmware applies the confidence floor itself (`bb::resolveBin`). The host
reports honestly rather than rounding an uncertain answer up — both ends agree
on the number, and the decision stays auditable from either side.

## Transport A — Wi-Fi (default)

```
POST http://<laptop>:8000/classify
Content-Type: image/jpeg
X-Device-Id: binary-bin-01
X-Seq: 42

<JPEG bytes>
```

Response is the verdict JSON, HTTP 200. Also:

* `GET  /health` — backend name, frames seen, uptime
* `POST /telemetry` — one JSON object per completed cycle, appended to
  `data/telemetry.jsonl`

Testable with curl, which is the main reason it is HTTP and not a raw socket:

```bash
curl -s --data-binary @sample.jpg -H 'Content-Type: image/jpeg' \
     http://localhost:8000/classify | python -m json.tool
```

## Transport B — USB-UART (fallback)

Everything — logs, telemetry, images, verdicts — goes down the one USB cable,
framed and CRC'd:

```
byte  0      0xA5                      sync
byte  1      0x5A                      sync
byte  2      type
bytes 3..6   payload length, u32 LE
bytes 7..    payload
last  4      CRC32 (IEEE / zlib) over the payload, u32 LE
```

| type | name | direction | payload |
|---|---|---|---|
| `0x01` | IMAGE | device -> host | u32 LE sequence number, then the JPEG |
| `0x02` | VERDICT | host -> device | verdict JSON |
| `0x10` | LOG | device -> host | UTF-8 text |
| `0x11` | TELEMETRY | device -> host | cycle JSON |
| `0x20` | HELLO | device -> host | `{"device":..,"fw":..,"proto":1}` on boot |

Default 921600 baud, `/dev/ttyACM0` on Linux when using the S3's native USB.

**Why framed and not line-oriented JSON:** the same port carries the ESP32's
boot-ROM banner, any panic dump, and binary JPEG data that will absolutely
contain `0x0A`. The sync word plus the CRC let the host start reading
mid-stream and recover — plaintext noise is simply bytes that never form a
valid frame. `FrameReader` counts its resyncs and CRC errors so a flaky cable
shows up as a number instead of a mystery.

A truncated frame is rejected by the CRC; the reader then steps past that sync
word and keeps hunting, so it cannot deadlock on a bad length field.

## Choosing a transport

Build with `-DBB_LINK_DEFAULT=BB_LINK_WIFI` (env `s3cam`) or
`-DBB_LINK_DEFAULT=BB_LINK_UART` (env `s3cam_uart`). The Wi-Fi build falls
back to UART automatically if it cannot associate, so the bin keeps working
on a venue network that will not let two devices talk to each other.

## v2: no transport at all

v2 moves inference onto the S3. That is a third `bb::Link` implementation
whose `classify()` runs an INT8 model in PSRAM and returns immediately. The
state machine, the interlocks and the verdict shape all stay as they are; the
network just stops being in the loop.
