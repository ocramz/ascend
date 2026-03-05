"""Unit tests for the Lightning MNIST example.

These tests run locally on CPU without AKS credentials. They verify
that ``train_and_validate`` produces valid metrics when executed
directly (bypassing the ``@ascend`` decorator's remote dispatch).
"""

import torch
import pytest


@pytest.mark.slow
def test_train_and_validate_local_cpu():
    """Run a 1-epoch MNIST training locally on CPU and check returned metrics."""
    from examples.lightning_mnist import train_and_validate

    hparams = {
        "lr": 1e-3,
        "batch_size": 256,   # large batch for speed
        "max_epochs": 1,
    }

    # Access the unwrapped function to bypass remote dispatch
    unwrapped = getattr(train_and_validate, "__wrapped__", train_and_validate)
    result = unwrapped(hparams)

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert "val_loss" in result, f"Missing 'val_loss' key in {result}"
    assert "val_acc" in result, f"Missing 'val_acc' key in {result}"
    assert isinstance(result["val_loss"], float)
    assert isinstance(result["val_acc"], float)
    # Even 1 epoch on MNIST should beat random (10%)
    assert result["val_acc"] > 0.5, f"val_acc too low: {result['val_acc']}"
    assert result["val_loss"] >= 0.0, f"val_loss negative: {result['val_loss']}"


def test_small_resnet_forward_pass():
    """Verify SmallResNet produces correct output shape."""
    from examples.lightning_mnist import SmallResNet

    model = SmallResNet(lr=1e-3)
    # MNIST: 1 channel, 28×28
    x = torch.randn(4, 1, 28, 28)
    logits = model(x)
    assert logits.shape == (4, 10), f"Expected (4, 10), got {logits.shape}"


def test_small_resnet_is_lightning_module():
    """Verify SmallResNet is a proper LightningModule."""
    import pytorch_lightning as pl

    from examples.lightning_mnist import SmallResNet

    model = SmallResNet(lr=0.01)
    assert isinstance(model, pl.LightningModule)
    # configure_optimizers should return an optimizer
    optimizer = model.configure_optimizers()
    assert isinstance(optimizer, torch.optim.Optimizer)


def test_gpu_base_image_auto_detected():
    """Verify torch==2.5.1 in requirements triggers correct base image.

    When the client Python version matches the PyTorch Docker Hub image
    (3.11), the PyTorch image is returned.  When it doesn't (e.g. 3.13),
    the system falls back to ``python:{version}-slim`` because PyTorch
    pip wheels bundle their own CUDA runtime.
    """
    from ascend.dependencies.analyzer import (
        PYTORCH_DOCKER_PYTHON_VERSION,
        create_dependency_set,
    )
    import sys

    reqs = [
        "torch==2.5.1",
        "pytorch-lightning>=2.0",
        "torchvision>=0.16",
        "torchmetrics>=1.0",
    ]

    dep_set = create_dependency_set(requirements=reqs, use_gpu=True)
    base_image = dep_set.get_base_image()

    current_py = f"{sys.version_info.major}.{sys.version_info.minor}"
    if current_py == PYTORCH_DOCKER_PYTHON_VERSION:
        assert base_image == "pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime"
    else:
        # Falls back to python:slim when Python versions don't match
        assert base_image == f"python:{current_py}-slim"

    # Also verify that pinning python_version=3.11 picks the PyTorch image
    dep_set_311 = create_dependency_set(
        requirements=reqs, use_gpu=True, python_version="3.11",
    )
    assert dep_set_311.get_base_image() == "pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime"


def test_gpu_base_image_included_in_hash():
    """Different base images produce different dependency hashes."""
    from ascend.dependencies.analyzer import create_dependency_set

    dep_auto = create_dependency_set(
        requirements=["torch==2.5.1"],
        use_gpu=True,
    )
    dep_custom = create_dependency_set(
        requirements=["torch==2.5.1"],
        use_gpu=True,
        base_image="custom/my-image:latest",
    )

    assert dep_auto.calculate_hash() != dep_custom.calculate_hash()
