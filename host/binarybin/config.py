"""Host settings. Every one of these can be overridden by an env var."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

BIN_RECYCLABLE = 1
BIN_NON_RECYCLABLE = 2


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else default


@dataclass(frozen=True)
class Settings:
    host: str = os.environ.get("BB_HOST", "0.0.0.0")
    port: int = int(os.environ.get("BB_PORT", "8000"))

    serial_port: str = os.environ.get("BB_SERIAL", "/dev/ttyACM0")
    serial_baud: int = int(os.environ.get("BB_BAUD", "921600"))

    # Degrees counter-clockwise to rotate a frame before inference, when the
    # camera module ends up mounted on its side in the load bay. Only the copy
    # fed to the model is rotated; the archived JPEG stays as the sensor sent
    # it. One of 0/90/180/270.
    # 270 is what this rig needs now: the module images a quarter turn
    # clockwise of upright (that was the original 90) and is mounted
    # inverted in the bay, so the correction is 90 + 180. Override without
    # editing anything with BB_ROTATE=90 if the module goes back the other
    # way up, or BB_ROTATE=0 if you remount it square.
    rotate_deg: int = int(os.environ.get("BB_ROTATE", "270"))

    # "auto" picks mobilenet if torch is importable, else heuristic.
    backend: str = os.environ.get("BB_BACKEND", "auto")
    checkpoint: Path | None = (
        Path(os.environ["BB_CHECKPOINT"]).expanduser()
        if os.environ.get("BB_CHECKPOINT")
        else None
    )

    # Below this the firmware ignores us and uses its fail-safe bin anyway;
    # we send the number so the decision is auditable from one side.
    min_confidence: float = _env_float("BB_MIN_CONFIDENCE", 0.55)

    # Every frame the bin ever sends gets written here. This is the dataset.
    capture_dir: Path = _env_path("BB_CAPTURE_DIR", REPO_ROOT / "data" / "captures")
    save_captures: bool = os.environ.get("BB_SAVE_CAPTURES", "1") != "0"

    telemetry_log: Path = _env_path(
        "BB_TELEMETRY_LOG", REPO_ROOT / "data" / "telemetry.jsonl"
    )


SETTINGS = Settings()
