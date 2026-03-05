"""
PyTorch Lightning + Ascend: GPU Training on Kubernetes
======================================================

This example trains a small ResNet-style CNN on MNIST using PyTorch Lightning,
with the training step running remotely on a GPU-equipped Kubernetes pod.

Architecture
------------

    ┌─────────────────────────────┐
    │   Local machine             │
    │                             │
    │  hparams = {"lr": 1e-3, …} │
    │    │                        │
    │    └─ train_and_validate ───┼──→  K8s pod (GPU)
    │         (hparams: dict)     │       │  ▸ download MNIST
    │              ↑              │       │  ▸ build model + trainer
    │              │              │       │  ▸ trainer.fit()
    │   val_metrics (dict) ───────┼───────┘  ▸ trainer.validate()
    │                             │
    │  print(val_metrics)         │
    └─────────────────────────────┘

Design constraint: **zero code changes between local and remote execution**.
The *only* difference is the presence of the ``@ascend`` decorator. Remove it
and the script still runs locally on CPU (assuming ``torch``, ``lightning``,
``torchvision``, and ``torchmetrics`` are installed).

Key design decisions:

1. **Serialization boundary**: Only a plain ``dict`` of hyperparameters goes
   in and a plain ``dict`` of validation metrics comes out. No model objects,
   trainers, or data loaders cross the cloudpickle wire.

2. **accelerator="auto"**: PyTorch Lightning auto-detects GPU vs CPU. On your
   laptop it trains on CPU; on a ``gpu_small`` pod (1× NVIDIA V100) it
   uses the GPU — no conditional logic needed.

3. **Data downloaded inside the pod**: ``torchvision.datasets.MNIST`` with
   ``download=True`` works identically on localhost and on a K8s pod
   (dataset is ~12 MB).

4. **Model class at module level**: ``SmallResNet`` is defined at module
   scope for readability and reuse. ``cloudpickle`` captures it automatically
   when serializing ``train_and_validate`` because the function references
   the class in its body.

Prerequisites
-------------

Install PyTorch and Lightning locally (needed for both local prototyping and
remote execution — the ``requirements`` parameter ensures they are installed
on the pod as well)::

    pip install torch pytorch-lightning torchvision torchmetrics

Ensure you have a valid ``.ascend.yaml`` configuration and Azure/AKS access
with a GPU node pool.

Base image selection
~~~~~~~~~~~~~~~~~~~~

When ``@ascend`` sees ``torch`` in the requirements list *and* a GPU node
type, it auto-selects a matching ``pytorch/pytorch`` Docker Hub image as the
base (e.g. ``pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime``).  This avoids
rebuilding CUDA/PyTorch from scratch on every run.

The image is pulled from Docker Hub on first use and cached in ACR for
subsequent runs.  To pin a specific base image, pass
``base_image="pytorch/pytorch:..."`` to the decorator.

Usage
-----

::

    # Run locally on CPU (prototype)
    python examples/lightning_mnist.py

    # Or with @ascend decorator active → runs on GPU pod
    python examples/lightning_mnist.py

"""

from __future__ import annotations

from typing import Any

import pytorch_lightning as pl
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchmetrics

from ascend import ascend


# ---------------------------------------------------------------------------
# Model — defined at module level for clarity and reuse
# ---------------------------------------------------------------------------


class ResidualBlock(nn.Module):
    """A basic residual block: two 3×3 convolutions with a skip connection."""

    def __init__(self, channels: int):
        super().__init__()
        self.conv1 = nn.Conv2d(channels, channels, 3, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(channels)
        self.conv2 = nn.Conv2d(channels, channels, 3, padding=1, bias=False)
        self.bn2 = nn.BatchNorm2d(channels)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        residual = x
        out = F.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        return F.relu(out + residual)


class SmallResNet(pl.LightningModule):
    """A lightweight ResNet-style CNN for MNIST (28×28 grayscale images).

    Architecture:
        Conv2d(1→32) → ResidualBlock(32) → Conv2d(32→64, stride=2)
        → ResidualBlock(64) → AdaptiveAvgPool2d(1) → Linear(64→10)

    Args:
        lr: Learning rate for Adam optimizer.
    """

    def __init__(self, lr: float = 1e-3):
        super().__init__()
        self.save_hyperparameters()
        self.lr = lr

        self.stem = nn.Sequential(
            nn.Conv2d(1, 32, 3, padding=1, bias=False),
            nn.BatchNorm2d(32),
            nn.ReLU(),
        )
        self.block1 = ResidualBlock(32)
        self.downsample = nn.Sequential(
            nn.Conv2d(32, 64, 3, stride=2, padding=1, bias=False),
            nn.BatchNorm2d(64),
            nn.ReLU(),
        )
        self.block2 = ResidualBlock(64)
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.fc = nn.Linear(64, 10)

        self.train_acc = torchmetrics.Accuracy(task="multiclass", num_classes=10)
        self.val_acc = torchmetrics.Accuracy(task="multiclass", num_classes=10)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.stem(x)
        x = self.block1(x)
        x = self.downsample(x)
        x = self.block2(x)
        x = self.pool(x).flatten(1)
        return self.fc(x)

    def training_step(self, batch: tuple, batch_idx: int) -> torch.Tensor:
        x, y = batch
        logits = self(x)
        loss = F.cross_entropy(logits, y)
        self.train_acc(logits, y)
        self.log("train_loss", loss, prog_bar=True)
        self.log("train_acc", self.train_acc, prog_bar=True)
        return loss

    def validation_step(self, batch: tuple, batch_idx: int) -> None:
        x, y = batch
        logits = self(x)
        loss = F.cross_entropy(logits, y)
        self.val_acc(logits, y)
        self.log("val_loss", loss, prog_bar=True)
        self.log("val_acc", self.val_acc, prog_bar=True)

    def configure_optimizers(self) -> torch.optim.Optimizer:
        return torch.optim.Adam(self.parameters(), lr=self.lr)


# ---------------------------------------------------------------------------
# Remote function — runs on Kubernetes (or locally without the decorator)
# ---------------------------------------------------------------------------


@ascend(
    node_type="nc8as_t4_v3",
    timeout=3600,
    requirements=[
        "torch==2.6.0",
        "pytorch-lightning>=2.0",
        "torchvision==0.21.0",
        "torchmetrics>=1.0",
    ],
    git_check=False,
)
def train_and_validate(hparams: dict[str, Any]) -> dict[str, float]:
    """Train a SmallResNet on MNIST and return validation metrics.

    This function executes on a remote GPU pod (or locally on CPU when the
    ``@ascend`` decorator is removed). Everything — data loading, model
    construction, training, and validation — happens inside this function.
    Only the plain ``hparams`` dict crosses the cloudpickle serialization
    boundary in, and only a plain metrics dict comes out.

    Args:
        hparams: Hyperparameter dictionary with keys:
            - ``lr`` (float): Learning rate for Adam optimizer.
            - ``batch_size`` (int): Training and validation batch size.
            - ``max_epochs`` (int): Number of training epochs.

    Returns:
        Dictionary of validation metrics, e.g.
        ``{"val_loss": 0.05, "val_acc": 0.98}``.
    """
    import tempfile

    from torch.utils.data import DataLoader, random_split
    from torchvision import transforms
    from torchvision.datasets import MNIST

    # --- Data ---
    transform = transforms.Compose(
        [transforms.ToTensor(), transforms.Normalize((0.1307,), (0.3081,))]
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        dataset = MNIST(tmpdir, train=True, download=True, transform=transform)

    # 80/20 train-val split (deterministic)
    n_val = int(0.2 * len(dataset))
    n_train = len(dataset) - n_val
    train_ds, val_ds = random_split(
        dataset,
        [n_train, n_val],
        generator=torch.Generator().manual_seed(42),
    )

    batch_size = hparams.get("batch_size", 64)
    train_loader = DataLoader(
        train_ds, batch_size=batch_size, shuffle=True, num_workers=2
    )
    val_loader = DataLoader(
        val_ds, batch_size=batch_size, shuffle=False, num_workers=2
    )

    # --- Model & Trainer ---
    model = SmallResNet(lr=hparams.get("lr", 1e-3))
    trainer = pl.Trainer(
        max_epochs=hparams.get("max_epochs", 5),
        accelerator="auto",          # GPU if available, else CPU
        devices="auto",
        enable_checkpointing=False,  # no checkpoint files on the pod
        logger=False,                # no TensorBoard logs on the pod
        enable_progress_bar=True,
    )

    # --- Train + Validate ---
    trainer.fit(model, train_loader, val_loader)
    results = trainer.validate(model, val_loader)

    # results is a list of dicts; return the first (only) one
    # Convert to plain dict[str, float] for clean serialization
    return {k: float(v) for k, v in results[0].items()}


# ---------------------------------------------------------------------------
# Entry point — local launcher
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    hparams: dict[str, Any] = {
        "lr": 1e-3,
        "batch_size": 64,
        "max_epochs": 5,
    }

    print("Starting MNIST training with hparams:", hparams)
    val_metrics = train_and_validate(hparams)

    print("\nValidation results:")
    for metric, value in val_metrics.items():
        print(f"  {metric}: {value:.4f}")
