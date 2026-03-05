"""Integration tests for the PyTorch Lightning MNIST example.

These tests validate that ``train_and_validate`` from
``examples/lightning_mnist.py`` executes correctly on a real AKS cluster
with a GPU node pool.

Requirements:
    - A running AKS cluster with a T4 GPU node pool
      (``Standard_NC8as_T4_v3``, pool name ``ncast4v4``).
    - Azure credentials configured (``az login`` or env vars).
    - ``AZURE_SUBSCRIPTION_ID``, ``AZURE_RESOURCE_GROUP``,
      ``AZURE_AKS_CLUSTER_NAME`` environment variables set.
"""

import pytest


@pytest.mark.integration
class TestLightningMNIST:
    """Integration tests for the Lightning MNIST GPU example."""

    def test_train_and_validate_single_call(self, real_aks_cluster):
        """Train SmallResNet on MNIST for 2 epochs on a T4 GPU pod.

        Verifies that the decorated function:
        - Executes on a T4 GPU node (``node_type="nc8as_t4_v3"``).
        - Returns a plain dict with ``val_loss`` and ``val_acc`` keys.
        - Achieves reasonable accuracy on MNIST (>0.8 after 2 epochs).
        """
        from examples.lightning_mnist import train_and_validate

        hparams = {
            "lr": 1e-3,
            "batch_size": 64,
            "max_epochs": 2,
        }

        result = train_and_validate(hparams)

        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert "val_loss" in result, f"Missing 'val_loss' in {result}"
        assert "val_acc" in result, f"Missing 'val_acc' in {result}"
        assert isinstance(result["val_loss"], float)
        assert isinstance(result["val_acc"], float)
        # 2 epochs on MNIST with a ResNet should comfortably beat 80%
        assert result["val_acc"] > 0.8, f"val_acc too low: {result['val_acc']}"
        assert result["val_loss"] < 1.0, f"val_loss too high: {result['val_loss']}"

    @pytest.mark.slow
    def test_train_five_epochs(self, real_aks_cluster):
        """Train for 5 epochs and verify higher accuracy."""
        from examples.lightning_mnist import train_and_validate

        hparams = {
            "lr": 1e-3,
            "batch_size": 64,
            "max_epochs": 5,
        }

        result = train_and_validate(hparams)

        assert isinstance(result, dict)
        assert result["val_acc"] > 0.9, f"val_acc after 5 epochs: {result['val_acc']}"
