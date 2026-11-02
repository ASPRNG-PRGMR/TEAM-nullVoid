#!/usr/bin/env python3
"""Fine-tune MobileNetV3-Small on frames the bin actually took.

The whole point of archiving every capture is that this becomes easy. Sort
the files from data/captures/ into two folders and run this:

    data/dataset/
      recyclable/      *.jpg
      non_recyclable/  *.jpg

    python tools/train_finetune.py --data ../data/dataset --epochs 12
    BB_CHECKPOINT=models/binbin.pt python serve_wifi.py

Photographs of our own bin, under our own LED, at our own working distance
beat any amount of clever prompt engineering against ImageNet labels. A
couple of hundred images per class is enough to pass the pitch deck's 90%
target; a couple of thousand is enough to trust it.

The saved checkpoint is also the input to v2: export it to INT8 and it runs
on the S3 itself.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from torch import nn
from torch.utils.data import DataLoader, random_split
from torchvision import transforms
from torchvision.datasets import ImageFolder
from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

TRAIN_TF = transforms.Compose(
    [
        transforms.Resize(256),
        transforms.RandomResizedCrop(224, scale=(0.6, 1.0)),
        transforms.RandomHorizontalFlip(),
        transforms.RandomVerticalFlip(),   # items land in the box any way up
        transforms.RandomRotation(25),
        # The LED is fixed but ambient light through the flap is not, so push
        # the model to ignore overall colour temperature.
        transforms.ColorJitter(brightness=0.35, contrast=0.3, saturation=0.3, hue=0.04),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ]
)

EVAL_TF = transforms.Compose(
    [
        transforms.Resize(232),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ]
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, required=True, help="ImageFolder root")
    ap.add_argument("--out", type=Path, default=Path("models/binbin.pt"))
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--val-split", type=float, default=0.2)
    ap.add_argument("--freeze-backbone", action="store_true",
                    help="train only the head - use this under ~100 images/class")
    args = ap.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"device: {device}")

    full = ImageFolder(args.data, transform=TRAIN_TF)
    if len(full.classes) != 2:
        raise SystemExit(f"expected 2 class folders, found {full.classes}")
    print(f"classes: {full.classes}  images: {len(full)}")

    n_val = max(1, int(len(full) * args.val_split))
    n_train = len(full) - n_val
    train_set, val_set = random_split(
        full, [n_train, n_val], generator=torch.Generator().manual_seed(42)
    )
    # random_split shares the underlying dataset, so the val half would get
    # the training augmentations. Give it its own view.
    val_set.dataset = ImageFolder(args.data, transform=EVAL_TF)

    train_loader = DataLoader(train_set, batch_size=args.batch, shuffle=True, num_workers=2)
    val_loader = DataLoader(val_set, batch_size=args.batch, num_workers=2)

    model = mobilenet_v3_small(weights=MobileNet_V3_Small_Weights.IMAGENET1K_V1)
    if args.freeze_backbone:
        for p in model.features.parameters():
            p.requires_grad = False
    in_features = model.classifier[-1].in_features
    model.classifier[-1] = nn.Linear(in_features, len(full.classes))
    model = model.to(device)

    criterion = nn.CrossEntropyLoss(label_smoothing=0.05)
    params = [p for p in model.parameters() if p.requires_grad]
    optimiser = torch.optim.AdamW(params, lr=args.lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimiser, T_max=args.epochs)

    best = 0.0
    args.out.parent.mkdir(parents=True, exist_ok=True)

    for epoch in range(1, args.epochs + 1):
        model.train()
        running = 0.0
        for x, y in train_loader:
            x, y = x.to(device), y.to(device)
            optimiser.zero_grad()
            loss = criterion(model(x), y)
            loss.backward()
            optimiser.step()
            running += loss.item() * x.size(0)
        scheduler.step()

        model.eval()
        correct = total = 0
        confusion = [[0, 0], [0, 0]]
        with torch.no_grad():
            for x, y in val_loader:
                x, y = x.to(device), y.to(device)
                pred = model(x).argmax(1)
                correct += int((pred == y).sum())
                total += y.numel()
                for t, p in zip(y.tolist(), pred.tolist()):
                    confusion[t][p] += 1

        acc = correct / max(total, 1)
        print(
            f"epoch {epoch:3d}  loss {running / max(n_train, 1):.4f}  val acc {acc:.3f}"
        )

        if acc >= best:
            best = acc
            torch.save(
                {"state_dict": model.state_dict(), "classes": full.classes, "acc": acc},
                args.out,
            )
            meta = args.out.with_suffix(".json")
            meta.write_text(
                json.dumps(
                    {
                        "classes": full.classes,
                        "val_accuracy": acc,
                        "epoch": epoch,
                        "images": len(full),
                        "confusion_rows_true_cols_pred": confusion,
                    },
                    indent=2,
                )
            )

    print(f"\nbest val accuracy {best:.3f} -> {args.out}")
    print(f"run the host with:  BB_CHECKPOINT={args.out} python serve_wifi.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
