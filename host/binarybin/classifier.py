"""The v1 decision: a JPEG goes in, a bin number comes out.

Three backends, same interface:

  HeuristicBackend  no dependencies beyond Pillow/numpy. Colour and texture
                    statistics. Good enough to prove the mechanism end to end
                    before anyone has installed torch.
  ImageNetBackend   MobileNetV3-Small with ImageNet weights, folded down to
                    two classes by binarybin/labels.py. Zero training, works
                    on day one.
  FineTunedBackend  the same architecture with a two-class head, loaded from
                    a checkpoint produced by tools/train_finetune.py. This is
                    the one that should end up on the chip in v2.
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .config import BIN_NON_RECYCLABLE, BIN_RECYCLABLE, SETTINGS
from .labels import NON_RECYCLABLE, RECYCLABLE, UNKNOWN, bin_for, category_for

log = logging.getLogger("binarybin.classifier")


@dataclass
class Result:
    label: str
    bin: int
    confidence: float
    latency_ms: int
    backend: str
    detail: str = ""

    def as_json(self) -> dict:
        return {
            "label": self.label,
            "bin": self.bin,
            "confidence": round(self.confidence, 4),
            "latency_ms": self.latency_ms,
            "backend": self.backend,
            "detail": self.detail,
        }


class Backend:
    name = "base"

    def predict(self, img: Image.Image) -> tuple[str, float, str]:
        raise NotImplementedError


# ---------------------------------------------------------------- heuristic
class HeuristicBackend(Backend):
    """No model. Colour saturation and edge density.

    Organic waste under a white LED is saturated, warm and smooth-edged; a
    bottle, a wrapper or a sheet of card is desaturated, bright and full of
    straight specular edges. This is not a serious classifier - it is a way
    to shake out the wiring, the framing and the state machine while the
    dataset is still a folder of twelve photos.
    """

    name = "heuristic"

    def predict(self, img: Image.Image) -> tuple[str, float, str]:
        small = img.convert("RGB").resize((160, 120))
        arr = np.asarray(small, dtype=np.float32) / 255.0

        mx = arr.max(axis=2)
        mn = arr.min(axis=2)
        saturation = float(np.mean((mx - mn) / (mx + 1e-6)))
        brightness = float(np.mean(mx))

        grey = arr.mean(axis=2)
        gx = np.abs(np.diff(grey, axis=1)).mean()
        gy = np.abs(np.diff(grey, axis=0)).mean()
        edges = float(gx + gy)

        # Warm hue mass: organics skew red/yellow.
        warm = float(np.mean(arr[:, :, 0] - arr[:, :, 2]))

        # Clamped at zero: these are evidence counters, not logits, and a
        # negative one would otherwise push the normalised score past 1.
        organic_score = max(0.0, 2.2 * saturation + 3.0 * max(warm, 0.0) - 1.4 * edges)
        dry_score = max(0.0, 1.6 * edges + 1.1 * (1.0 - saturation) + 0.6 * brightness)

        total = organic_score + dry_score
        if total <= 1e-6:
            return UNKNOWN, 0.0, "flat frame"

        p_organic = min(1.0, max(0.0, organic_score / total))
        detail = (
            f"sat={saturation:.2f} warm={warm:+.2f} edges={edges:.3f} "
            f"bright={brightness:.2f}"
        )
        if p_organic >= 0.5:
            return NON_RECYCLABLE, p_organic, detail
        return RECYCLABLE, 1.0 - p_organic, detail


# ------------------------------------------------------------------- torch
class _TorchBase(Backend):
    def __init__(self) -> None:
        import torch  # noqa: PLC0415  - optional dependency
        import torchvision  # noqa: PLC0415

        self._torch = torch
        self._tv = torchvision
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        torch.set_grad_enabled(False)
        torch.set_num_threads(max(1, (torch.get_num_threads() or 4)))

    def _transform(self):
        from torchvision import transforms  # noqa: PLC0415

        return transforms.Compose(
            [
                transforms.Resize(232),
                transforms.CenterCrop(224),
                transforms.ToTensor(),
                transforms.Normalize(
                    mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]
                ),
            ]
        )


class ImageNetBackend(_TorchBase):
    """MobileNetV3-Small, ImageNet weights, no training.

    We do not take the argmax class and look it up. We sum the probability
    mass of every class that maps to "recyclable" and every class that maps
    to "non-recyclable", and compare the two totals. That is much steadier
    than a top-1 lookup: a crushed PET bottle spreads its mass over "water
    bottle", "pop bottle" and "beer bottle", and any single one of those can
    lose to an unrelated class.
    """

    name = "imagenet-mobilenetv3"

    def __init__(self) -> None:
        super().__init__()
        from torchvision.models import (  # noqa: PLC0415
            MobileNet_V3_Small_Weights,
            mobilenet_v3_small,
        )

        weights = MobileNet_V3_Small_Weights.IMAGENET1K_V1
        self.model = mobilenet_v3_small(weights=weights).eval().to(self.device)
        self.categories: list[str] = list(weights.meta["categories"])
        self.tf = self._transform()

        # Precompute the fold so we are not string-matching 1000 names per frame.
        self.mask_dry = self._torch.zeros(len(self.categories))
        self.mask_wet = self._torch.zeros(len(self.categories))
        for i, name in enumerate(self.categories):
            cat = category_for(name)
            if cat == RECYCLABLE:
                self.mask_dry[i] = 1.0
            elif cat == NON_RECYCLABLE:
                self.mask_wet[i] = 1.0
        mapped = int((self.mask_dry + self.mask_wet).sum().item())
        log.info("label map covers %d/%d ImageNet classes", mapped, len(self.categories))

    def predict(self, img: Image.Image) -> tuple[str, float, str]:
        x = self.tf(img.convert("RGB")).unsqueeze(0).to(self.device)
        probs = self._torch.softmax(self.model(x)[0].cpu(), dim=0)

        dry = float((probs * self.mask_dry).sum())
        wet = float((probs * self.mask_wet).sum())
        covered = dry + wet

        top_p, top_i = self._torch.topk(probs, 3)
        detail = ", ".join(
            f"{self.categories[int(i)]}={float(p):.2f}" for p, i in zip(top_p, top_i)
        )

        if covered < 0.08:
            # The model is confident about something we have no opinion on.
            # Say so rather than inventing a 50/50 split.
            return UNKNOWN, 0.0, f"unmapped ({detail})"

        if dry >= wet:
            return RECYCLABLE, dry / covered, detail
        return NON_RECYCLABLE, wet / covered, detail


class FineTunedBackend(_TorchBase):
    """Two-class head trained on our own frames. See tools/train_finetune.py."""

    name = "finetuned-mobilenetv3"

    def __init__(self, checkpoint: Path) -> None:
        super().__init__()
        from torchvision.models import mobilenet_v3_small  # noqa: PLC0415

        ckpt = self._torch.load(checkpoint, map_location="cpu", weights_only=False)
        self.classes: list[str] = ckpt.get("classes", [RECYCLABLE, NON_RECYCLABLE])

        model = mobilenet_v3_small(weights=None)
        in_features = model.classifier[-1].in_features
        model.classifier[-1] = self._torch.nn.Linear(in_features, len(self.classes))
        model.load_state_dict(ckpt["state_dict"])
        self.model = model.eval().to(self.device)
        self.tf = self._transform()
        log.info("loaded %s (classes: %s)", checkpoint, self.classes)

    def predict(self, img: Image.Image) -> tuple[str, float, str]:
        x = self.tf(img.convert("RGB")).unsqueeze(0).to(self.device)
        probs = self._torch.softmax(self.model(x)[0].cpu(), dim=0)
        idx = int(self._torch.argmax(probs))
        detail = ", ".join(
            f"{c}={float(p):.2f}" for c, p in zip(self.classes, probs)
        )
        return self.classes[idx], float(probs[idx]), detail


# ------------------------------------------------------------------ driver
def build_backend(kind: str | None = None, checkpoint: Path | None = None) -> Backend:
    kind = kind or SETTINGS.backend
    checkpoint = checkpoint or SETTINGS.checkpoint

    if kind in {"auto", "finetuned"} and checkpoint and checkpoint.exists():
        try:
            return FineTunedBackend(checkpoint)
        except Exception:
            log.exception("checkpoint %s failed to load", checkpoint)
            if kind == "finetuned":
                raise

    if kind in {"auto", "imagenet"}:
        try:
            return ImageNetBackend()
        except ImportError:
            if kind == "imagenet":
                raise
            log.warning("torch not available - using the heuristic backend")
        except Exception:
            log.exception("imagenet backend failed to start")
            if kind == "imagenet":
                raise

    return HeuristicBackend()


class Classifier:
    """Wraps a backend with decoding, the confidence floor and capture archiving."""

    def __init__(self, backend: Backend | None = None) -> None:
        self.backend = backend or build_backend()
        self.seen = 0
        log.info("backend: %s", self.backend.name)

    def classify_jpeg(self, data: bytes, seq: int = 0, device: str = "?") -> Result:
        t0 = time.perf_counter()
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception as exc:
            return Result(
                label="decode-error",
                bin=BIN_NON_RECYCLABLE,
                confidence=0.0,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                backend=self.backend.name,
                detail=str(exc),
            )

        label, confidence, detail = self.backend.predict(img)

        if label == UNKNOWN or confidence < SETTINGS.min_confidence:
            # Report honestly. The firmware applies the same floor and routes
            # anything uncertain to the non-recyclable bin, so a low number
            # here is a decision, not a failure.
            target = BIN_NON_RECYCLABLE
        else:
            target = bin_for(label)

        result = Result(
            label=label,
            bin=target,
            confidence=confidence,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            backend=self.backend.name,
            detail=detail,
        )
        self.seen += 1
        if SETTINGS.save_captures:
            self._archive(data, result, seq, device)
        return result

    def _archive(self, data: bytes, result: Result, seq: int, device: str) -> None:
        """Every frame the rig has ever seen, on disk, named by its verdict.

        This is how the fine-tuning dataset gets built: demo the bin, then
        drag the mislabelled files into the right folder.
        """
        try:
            day = time.strftime("%Y-%m-%d")
            out = SETTINGS.capture_dir / day
            out.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%H%M%S")
            name = (
                f"{stamp}_{device}_{seq:06d}_{result.label}"
                f"_{int(result.confidence * 100):03d}.jpg"
            )
            (out / name).write_bytes(data)
        except OSError:
            log.exception("could not archive frame")
