# data/

`captures/` fills up on its own: the host writes every frame the bin sends,
named `HHMMSS_<device>_<seq>_<label>_<confidence>.jpg`. That naming is
deliberate — you can sort by verdict, spot the wrong ones, and drag them into
the right folder.

To build a training set, sort them into:

```
data/dataset/recyclable/
data/dataset/non_recyclable/
```

then run `host/tools/train_finetune.py`. Captures and datasets are gitignored;
only this file and `captures/.gitkeep` are tracked.

`telemetry.jsonl` gets one line per completed cycle (sequence, label,
confidence, bin, cycle time, link latency, running totals, whether the
fail-safe fired). That file is the demo-day evidence.
