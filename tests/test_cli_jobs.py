"""Tests for the ``ascend jobs`` CLI commands (Item 8).

Uses click.testing.CliRunner so no real infrastructure is needed.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from ascend.cli.main import cli
from ascend.storage.metadata import JobMetadata, DependencyMetadata, ExecutionMetadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_metadata(
    job_id: str = "20260303-120000-alice-default-00000000-aabbccdd",
    status: str = "completed",
    function_name: str = "train_model",
    project: str = "default",
    user: str = "alice",
    created_at: str | None = None,
) -> JobMetadata:
    now = created_at or datetime.now(timezone.utc).isoformat()
    return JobMetadata(
        job_id=job_id,
        created_at=now,
        updated_at=now,
        status=status,
        user=user,
        project=project,
        function_name=function_name,
        config={"cpu": "1", "memory": "2Gi", "timeout": 300, "node_type": None},
        dependencies=DependencyMetadata(hash="00000000", python_version="3.12"),
        execution=ExecutionMetadata(),
    )


def _patch_backend_and_config():
    """Return a context-manager stack that patches config loading and backend creation."""
    storage = MagicMock()
    compute = MagicMock()
    registry = MagicMock()

    backend = SimpleNamespace(
        name="mock",
        storage=storage,
        registry=registry,
        image_builder=None,
        compute=compute,
    )

    config = {
        "username": "alice",
        "namespace": "ascend-users-alice",
        "storage_account": "teststorage",
        "container_registry": "testacr.azurecr.io",
        "cluster_name": "testcluster",
        "resource_group": "testrg",
    }

    patches = [
        patch("ascend.cli.jobs._get_config", return_value=config),
        patch("ascend.cli.jobs._get_backend", return_value=backend),
    ]

    return patches, backend, config


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestJobsListCommand:
    def test_list_no_jobs(self):
        patches, backend, _ = _patch_backend_and_config()
        backend.storage.list.return_value = []

        runner = CliRunner()
        with patches[0], patches[1]:
            result = runner.invoke(cli, ["jobs", "list"])

        assert result.exit_code == 0
        assert "No jobs found" in result.output

    def test_list_with_jobs(self):
        patches, backend, _ = _patch_backend_and_config()
        job_id = "20260303-120000-alice-default-00000000-aabbccdd"
        backend.storage.list.return_value = [
            f"projects/default/users/alice/jobs/{job_id}",
        ]
        meta = _fake_metadata(job_id=job_id)
        backend.storage.exists.return_value = True
        backend.storage.read.return_value = meta.to_json().encode("utf-8")

        runner = CliRunner()
        with patches[0], patches[1]:
            result = runner.invoke(cli, ["jobs", "list"])

        assert result.exit_code == 0
        assert job_id in result.output
        # Rich may truncate column values in narrow terminals; check prefix
        assert "tr" in result.output  # train_model truncated

    def test_list_filter_by_status(self):
        patches, backend, _ = _patch_backend_and_config()
        job_id_completed = "20260303-120000-alice-default-00000000-aabbccdd"
        job_id_failed = "20260303-120100-alice-default-00000000-bbccddee"
        backend.storage.list.return_value = [
            f"projects/default/users/alice/jobs/{job_id_completed}",
            f"projects/default/users/alice/jobs/{job_id_failed}",
        ]
        meta_completed = _fake_metadata(job_id=job_id_completed, status="completed")
        meta_failed = _fake_metadata(job_id=job_id_failed, status="failed")

        def fake_exists(path):
            return True

        def fake_read(path):
            if job_id_completed in path:
                return meta_completed.to_json().encode("utf-8")
            return meta_failed.to_json().encode("utf-8")

        backend.storage.exists.side_effect = fake_exists
        backend.storage.read.side_effect = fake_read

        runner = CliRunner()
        with patches[0], patches[1]:
            result = runner.invoke(cli, ["jobs", "list", "--status", "failed"])

        assert result.exit_code == 0
        assert job_id_failed in result.output
        assert job_id_completed not in result.output

    def test_list_with_limit(self):
        patches, backend, _ = _patch_backend_and_config()
        # Create 5 jobs, limit to 2
        job_ids = [f"20260303-1200{i:02d}-alice-default-00000000-aabb{i:04d}" for i in range(5)]
        backend.storage.list.return_value = [
            f"projects/default/users/alice/jobs/{jid}" for jid in job_ids
        ]

        def fake_exists(path):
            return True

        def fake_read(path):
            for jid in job_ids:
                if jid in path:
                    return _fake_metadata(job_id=jid).to_json().encode("utf-8")
            return b"{}"

        backend.storage.exists.side_effect = fake_exists
        backend.storage.read.side_effect = fake_read

        runner = CliRunner()
        with patches[0], patches[1]:
            result = runner.invoke(cli, ["jobs", "list", "--limit", "2"])

        assert result.exit_code == 0


class TestJobsStatusCommand:
    def test_status_existing_job(self):
        patches, backend, _ = _patch_backend_and_config()
        job_id = "20260303-120000-alice-default-00000000-aabbccdd"
        meta = _fake_metadata(job_id=job_id, status="completed")
        backend.storage.exists.return_value = True
        backend.storage.read.return_value = meta.to_json().encode("utf-8")

        runner = CliRunner()
        with patches[0], patches[1]:
            result = runner.invoke(cli, ["jobs", "status", job_id])

        assert result.exit_code == 0
        assert "completed" in result.output
        assert "train_model" in result.output

    def test_status_not_found(self):
        patches, backend, _ = _patch_backend_and_config()
        backend.storage.exists.return_value = False

        runner = CliRunner()
        with patches[0], patches[1]:
            result = runner.invoke(cli, ["jobs", "status", "20260303-120000-alice-default-00000000-aabbccdd"])

        assert result.exit_code == 1
        assert "not found" in result.output

    def test_status_running_queries_k8s(self):
        """Running job should attempt to query K8s for live status."""
        patches, backend, _ = _patch_backend_and_config()
        job_id = "20260303-120000-alice-default-00000000-aabbccdd"
        meta = _fake_metadata(job_id=job_id, status="running")
        backend.storage.exists.return_value = True
        backend.storage.read.return_value = meta.to_json().encode("utf-8")
        backend.compute.get_job_status.return_value = {
            "active": 1, "succeeded": 0, "failed": 0,
            "start_time": None, "completion_time": None,
        }

        runner = CliRunner()
        with patches[0], patches[1]:
            result = runner.invoke(cli, ["jobs", "status", job_id])

        assert result.exit_code == 0
        assert "active=1" in result.output
        backend.compute.get_job_status.assert_called_once()


class TestJobsCancelCommand:
    def test_cancel_running_job(self):
        patches, backend, _ = _patch_backend_and_config()
        job_id = "20260303-120000-alice-default-00000000-aabbccdd"
        meta = _fake_metadata(job_id=job_id, status="running")
        backend.storage.exists.return_value = True
        backend.storage.read.return_value = meta.to_json().encode("utf-8")

        runner = CliRunner()
        with patches[0], patches[1]:
            result = runner.invoke(cli, ["jobs", "cancel", job_id])

        assert result.exit_code == 0
        assert "cancelled" in result.output
        backend.compute.delete_job.assert_called_once_with(
            job_name=f"ascend-{job_id}",
            namespace="ascend-users-alice",
        )

    def test_cancel_already_completed(self):
        patches, backend, _ = _patch_backend_and_config()
        job_id = "20260303-120000-alice-default-00000000-aabbccdd"
        meta = _fake_metadata(job_id=job_id, status="completed")
        backend.storage.exists.return_value = True
        backend.storage.read.return_value = meta.to_json().encode("utf-8")

        runner = CliRunner()
        with patches[0], patches[1]:
            result = runner.invoke(cli, ["jobs", "cancel", job_id])

        assert result.exit_code == 0
        assert "already" in result.output


class TestJobsLogsCommand:
    def test_logs_stored(self):
        patches, backend, _ = _patch_backend_and_config()
        job_id = "20260303-120000-alice-default-00000000-aabbccdd"
        log_content = '{"timestamp":"2026-03-03T12:00:05","level":"INFO","message":"Starting training"}\n'
        backend.storage.exists.return_value = True
        backend.storage.read.return_value = log_content.encode("utf-8")

        runner = CliRunner()
        with patches[0], patches[1]:
            result = runner.invoke(cli, ["jobs", "logs", job_id])

        assert result.exit_code == 0
        assert "Starting training" in result.output

    def test_logs_not_found(self):
        patches, backend, _ = _patch_backend_and_config()
        job_id = "20260303-120000-alice-default-00000000-aabbccdd"
        backend.storage.exists.return_value = False

        runner = CliRunner()
        with patches[0], patches[1]:
            result = runner.invoke(cli, ["jobs", "logs", job_id])

        assert result.exit_code == 0
        assert "No stored logs" in result.output

    def test_logs_follow_attempts_streaming(self):
        patches, backend, _ = _patch_backend_and_config()
        job_id = "20260303-120000-alice-default-00000000-aabbccdd"
        # stream_logs succeeds
        backend.compute.stream_logs.return_value = None

        runner = CliRunner()
        with patches[0], patches[1]:
            result = runner.invoke(cli, ["jobs", "logs", "--follow", job_id])

        assert result.exit_code == 0
        backend.compute.stream_logs.assert_called_once()


class TestJobsHelpText:
    def test_jobs_group_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["jobs", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "status" in result.output
        assert "cancel" in result.output
        assert "logs" in result.output
