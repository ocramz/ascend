"""Tests for Ctrl+C job cancellation in RemoteExecutor (Item 9).

Verifies that when KeyboardInterrupt occurs during job execution:
- The K8s job is deleted
- Metadata is updated to 'cancelled'
- KeyboardInterrupt is re-raised
"""

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest


@dataclass
class _FakeConfig:
    """Minimal stand-in for AscendConfig."""
    cpu: str = "1"
    memory: str = "2Gi"
    timeout: int = 300
    node_type: str | None = None
    stream_logs: bool = False
    project: str | None = None


def _make_executor(*, wait_side_effect=None):
    """Build a RemoteExecutor with fully mocked backend."""
    from ascend.runtime.executor import RemoteExecutor

    config = _FakeConfig()

    # Build mock backend
    storage = MagicMock()
    storage.upload_package.return_value = "az://ascend-data/pkg"
    storage.exists.return_value = False
    storage.write.return_value = "az://ascend-data/meta"

    registry = MagicMock()
    registry.registry_url.return_value = "myacr.azurecr.io"

    compute = MagicMock()
    compute.create_job.return_value = "ascend-test-job-id"
    if wait_side_effect is not None:
        compute.wait_for_completion.side_effect = wait_side_effect
    else:
        compute.wait_for_completion.return_value = True

    backend = SimpleNamespace(
        name="mock",
        storage=storage,
        registry=registry,
        image_builder=None,
        compute=compute,
    )

    # Patch load_config so the constructor doesn't hit disk
    with patch("ascend.config.load_config", return_value={
        "username": "testuser",
        "namespace": "ascend-users-testuser",
        "storage_account": "teststorage",
        "container_registry": "testacr.azurecr.io",
    }):
        executor = RemoteExecutor(ascend_config=config, backend=backend)

    return executor, backend


def _make_package(job_id: str = "20260303-120000-testuser-default-00000000-aabbccdd"):
    return {
        "job_id": job_id,
        "project": "default",
        "function_name": "my_func",
        "requirements": [],
        "dep_hash": "00000000",
    }


class TestCtrlCCancellation:
    """Verify Ctrl+C deletes job and updates metadata."""

    def test_keyboard_interrupt_deletes_job(self):
        """KeyboardInterrupt during wait_for_completion should delete the K8s job."""
        executor, backend = _make_executor(
            wait_side_effect=KeyboardInterrupt,
        )

        with pytest.raises(KeyboardInterrupt):
            executor.execute(_make_package())

        backend.compute.delete_job.assert_called_once_with(
            job_name="ascend-test-job-id",
            namespace="ascend-users-testuser",
        )

    def test_keyboard_interrupt_updates_metadata(self):
        """KeyboardInterrupt should update metadata to 'cancelled'."""
        executor, backend = _make_executor(
            wait_side_effect=KeyboardInterrupt,
        )

        with pytest.raises(KeyboardInterrupt):
            executor.execute(_make_package())

        # Check that storage.write was called with cancelled status
        # The last write should contain "cancelled"
        write_calls = backend.storage.write.call_args_list
        last_write_data = write_calls[-1][0][1]  # second positional arg is data
        assert b'"cancelled"' in last_write_data

    def test_keyboard_interrupt_reraises(self):
        """KeyboardInterrupt must propagate after cleanup."""
        executor, backend = _make_executor(
            wait_side_effect=KeyboardInterrupt,
        )

        with pytest.raises(KeyboardInterrupt):
            executor.execute(_make_package())

    def test_delete_failure_still_reraises(self):
        """Even if delete_job fails, KeyboardInterrupt should propagate."""
        executor, backend = _make_executor(
            wait_side_effect=KeyboardInterrupt,
        )
        backend.compute.delete_job.side_effect = RuntimeError("delete failed")

        with pytest.raises(KeyboardInterrupt):
            executor.execute(_make_package())

    def test_normal_success_does_not_delete(self):
        """On normal success, delete_job should not be called."""
        executor, backend = _make_executor()
        # Mock download_result to return a value
        backend.storage.download_result.return_value = 42

        result = executor.execute(_make_package())
        assert result == 42
        backend.compute.delete_job.assert_not_called()

    def test_running_status_set_after_job_creation(self):
        """Metadata should be updated to 'running' after create_job."""
        executor, backend = _make_executor()
        backend.storage.download_result.return_value = "ok"

        executor.execute(_make_package())

        # Collect all write calls and check that 'running' appears
        write_calls = backend.storage.write.call_args_list
        any_running = any(
            b'"running"' in call_args[0][1]
            for call_args in write_calls
        )
        assert any_running, "Expected metadata to be updated to 'running'"
