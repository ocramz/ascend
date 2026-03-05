"""
Integration tests for job lifecycle CLI commands.

Tests the ``ascend jobs list|status|cancel|logs`` commands against real
Azure infrastructure and AKS.

Backend-level tests (``TestJobListIntegration``, ``TestJobCancelIntegration``,
``TestJobStatusIntegration``) exercise the storage and compute backends
directly.

CLI-level tests (``TestJobCancelRunningIntegration``, ``TestJobLogsIntegration``)
call the functions from ``ascend.cli.jobs`` to validate the full stack:
config loading → backend wiring → K8s/storage operations → output formatting.
"""

from __future__ import annotations

import json
import os
import time
from io import StringIO

import pytest
from rich.console import Console

from ascend.storage.metadata import JobMetadata, create_job_metadata, update_metadata_status
from ascend.storage.paths import (
    get_job_base_path,
    get_log_path,
    get_metadata_path,
    get_package_path,
)
from ascend.utils.job_ids import generate_job_id


@pytest.fixture
def storage_backend(ensure_infrastructure, real_aks_cluster):
    """Return a configured AzureCloudStorage ready for use."""
    from azure.identity import DefaultAzureCredential

    from ascend.cloud.azure.storage import AzureCloudStorage

    storage_account = ensure_infrastructure.storage_account_name
    credential = DefaultAzureCredential()
    storage = AzureCloudStorage(account_name=storage_account, credential=credential)
    storage.ensure_container("ascend-data")
    return storage


@pytest.fixture
def compute_backend(real_aks_cluster, ensure_infrastructure):
    """Return a configured AzureComputeBackend."""
    from ascend.cloud.azure.compute import AzureComputeBackend

    managed_identity_client_id = real_aks_cluster.get("managed_identity_client_id")
    return AzureComputeBackend(
        storage_account_name=ensure_infrastructure.storage_account_name,
        managed_identity_client_id=managed_identity_client_id,
    )


@pytest.fixture
def test_username():
    return os.getenv("AZURE_USERNAME", "integration-test")


@pytest.fixture
def test_namespace(test_username):
    return f"ascend-users-{test_username}"


@pytest.fixture
def sample_job_metadata(storage_backend, test_username):
    """Create sample job metadata in blob storage and return its ID."""
    job_id = generate_job_id(
        user=test_username,
        project="default",
        dep_hash="00000000",
        function_name="integration_test_func",
    )

    metadata = create_job_metadata(
        job_id=job_id,
        user=test_username,
        project="default",
        function_name="integration_test_func",
        config={"cpu": "1", "memory": "2Gi", "timeout": 300, "node_type": None},
        dep_hash="00000000",
    )

    metadata_path = get_metadata_path("default", test_username, job_id)
    storage_backend.write(metadata_path, metadata.to_json().encode("utf-8"))

    yield job_id

    # Cleanup: delete the metadata
    try:
        fs = storage_backend.get_filesystem()
        uri = storage_backend.storage_uri(metadata_path)
        fs.rm(uri)
    except Exception:
        pass


class TestJobListIntegration:
    """Integration tests for listing jobs from blob storage."""

    @pytest.mark.integration
    def test_list_finds_uploaded_metadata(self, storage_backend, test_username, sample_job_metadata):
        """Verify that a job with uploaded metadata appears in list results."""
        from ascend.storage.paths import get_user_jobs_prefix
        from ascend.storage.metadata import JobMetadata

        prefix = get_user_jobs_prefix("default", test_username)
        entries = storage_backend.list(prefix)
        assert len(entries) > 0, "Expected at least one job entry"

        # Extract job_ids from entries
        job_ids = [e.rstrip("/").split("/")[-1] for e in entries]
        assert sample_job_metadata in job_ids, (
            f"Expected {sample_job_metadata} in {job_ids}"
        )

    @pytest.mark.integration
    def test_metadata_round_trip(self, storage_backend, test_username, sample_job_metadata):
        """Verify metadata can be read back after writing."""
        from ascend.storage.paths import get_metadata_path
        from ascend.storage.metadata import JobMetadata

        path = get_metadata_path("default", test_username, sample_job_metadata)
        assert storage_backend.exists(path), "Metadata should exist"

        raw = storage_backend.read(path)
        meta = JobMetadata.from_json(raw.decode("utf-8"))

        assert meta.job_id == sample_job_metadata
        assert meta.status == "queued"
        assert meta.function_name == "integration_test_func"
        assert meta.user == test_username

    @pytest.mark.integration
    def test_status_update_round_trip(self, storage_backend, test_username, sample_job_metadata):
        """Verify status can be updated and re-read."""
        from ascend.storage.paths import get_metadata_path
        from ascend.storage.metadata import JobMetadata

        path = get_metadata_path("default", test_username, sample_job_metadata)
        raw = storage_backend.read(path)
        meta = JobMetadata.from_json(raw.decode("utf-8"))

        # Update to running
        meta = update_metadata_status(meta, status="running")
        storage_backend.write(path, meta.to_json().encode("utf-8"))

        # Re-read
        raw2 = storage_backend.read(path)
        meta2 = JobMetadata.from_json(raw2.decode("utf-8"))
        assert meta2.status == "running"

        # Update to completed
        meta2 = update_metadata_status(meta2, status="completed")
        storage_backend.write(path, meta2.to_json().encode("utf-8"))

        raw3 = storage_backend.read(path)
        meta3 = JobMetadata.from_json(raw3.decode("utf-8"))
        assert meta3.status == "completed"


class TestJobCancelIntegration:
    """Integration tests for job cancellation."""

    @pytest.mark.integration
    def test_cancel_nonexistent_k8s_job_succeeds(self, compute_backend, test_namespace):
        """Calling delete_job on a non-existent job should not raise."""
        # This should succeed silently (404 is swallowed)
        compute_backend.delete_job(
            job_name="ascend-nonexistent-job-12345678",
            namespace=test_namespace,
        )

    @pytest.mark.integration
    def test_cancel_updates_metadata(
        self,
        storage_backend,
        compute_backend,
        test_username,
        test_namespace,
        sample_job_metadata,
    ):
        """Cancel should delete K8s job and update metadata to cancelled."""
        from ascend.storage.paths import get_metadata_path
        from ascend.storage.metadata import JobMetadata

        job_id = sample_job_metadata
        job_name = f"ascend-{job_id}"
        path = get_metadata_path("default", test_username, job_id)

        # Set to running first
        raw = storage_backend.read(path)
        meta = JobMetadata.from_json(raw.decode("utf-8"))
        meta = update_metadata_status(meta, status="running")
        storage_backend.write(path, meta.to_json().encode("utf-8"))

        # Delete K8s job (may not exist — that's fine)
        compute_backend.delete_job(job_name=job_name, namespace=test_namespace)

        # Update metadata
        meta = update_metadata_status(meta, status="cancelled")
        storage_backend.write(path, meta.to_json().encode("utf-8"))

        # Verify
        raw2 = storage_backend.read(path)
        meta2 = JobMetadata.from_json(raw2.decode("utf-8"))
        assert meta2.status == "cancelled"


class TestJobStatusIntegration:
    """Integration tests for getting K8s job status."""

    @pytest.mark.integration
    def test_get_status_nonexistent_returns_none(self, compute_backend, test_namespace):
        """Getting status of a non-existent job returns None."""
        result = compute_backend.get_job_status(
            job_name="ascend-nonexistent-job-12345678",
            namespace=test_namespace,
        )
        assert result is None

    @pytest.mark.integration
    def test_list_jobs_returns_list(self, compute_backend, test_namespace):
        """list_jobs should return a list (possibly empty)."""
        result = compute_backend.list_jobs(namespace=test_namespace)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Helpers for CLI-level tests
# ---------------------------------------------------------------------------


def _capture_console() -> tuple[Console, StringIO]:
    """Create a Rich Console that writes to a StringIO for output capture.

    Returns ``(console, buffer)`` — after calling the CLI function with the
    patched console, read ``buffer.getvalue()`` for the rendered output.
    """
    buf = StringIO()
    return Console(file=buf, force_terminal=False, width=200), buf


# ---------------------------------------------------------------------------
# Fixtures for CLI-level tests
# ---------------------------------------------------------------------------


@pytest.fixture
def running_job(
    storage_backend,
    compute_backend,
    test_username,
    test_namespace,
    ensure_infrastructure,
    real_aks_cluster,
    fresh_runtime_image,
):
    """Submit a real K8s job that sleeps long enough to be cancelled.

    Yields a dict with ``job_id``, ``job_name``, ``project``, ``namespace``.

    On teardown the K8s job is deleted (best-effort) and all blobs under
    the job base path are removed.
    """
    import cloudpickle

    project = "default"
    job_id = generate_job_id(
        user=test_username,
        project=project,
        dep_hash="00000000",
        function_name="sleep_for_cancel",
    )
    job_name = f"ascend-{job_id}"

    # --- 1. Upload metadata (status = queued → running) -----------------
    metadata = create_job_metadata(
        job_id=job_id,
        user=test_username,
        project=project,
        function_name="sleep_for_cancel",
        config={"cpu": "1", "memory": "2Gi", "timeout": 600, "node_type": None},
        dep_hash="00000000",
    )
    metadata_path = get_metadata_path(project, test_username, job_id)
    storage_backend.write(metadata_path, metadata.to_json().encode("utf-8"))

    # --- 2. Upload a package blob (the runner unpickles it) -------------

    def _sleep_for_cancel():
        import time
        time.sleep(300)
        return "should-not-reach"

    package = {
        "function": cloudpickle.dumps(_sleep_for_cancel),
        "args": cloudpickle.dumps(((), {})),
        "requirements": [],
        "job_id": job_id,
        "function_name": "sleep_for_cancel",
        "project": project,
        "dep_hash": "00000000",
    }

    package_uri = storage_backend.upload_package(
        username=test_username,
        job_id=job_id,
        package=package,
        project=project,
    )

    # --- 3. Create the K8s job ------------------------------------------
    registry_url = ensure_infrastructure.container_registry_login_server
    config = {"cpu": "1", "memory": "2Gi", "timeout": 600, "node_type": None}
    compute_backend.create_job(
        namespace=test_namespace,
        job_id=job_id,
        package_uri=package_uri,
        config=config,
        registry=registry_url,
    )

    # Mark metadata as running
    metadata = update_metadata_status(metadata, status="running")
    storage_backend.write(metadata_path, metadata.to_json().encode("utf-8"))

    # Wait for the K8s job to become active (pod scheduled and running)
    deadline = time.monotonic() + 120  # up to 2 minutes for pod pull/start
    while time.monotonic() < deadline:
        status = compute_backend.get_job_status(job_name, test_namespace)
        if status and status.get("active", 0) > 0:
            break
        time.sleep(5)
    else:
        # Best-effort cleanup before failing
        compute_backend.delete_job(job_name, test_namespace)
        pytest.fail("K8s job did not become active within 120 seconds")

    yield {
        "job_id": job_id,
        "job_name": job_name,
        "project": project,
        "namespace": test_namespace,
    }

    # --- teardown -------------------------------------------------------
    # Delete K8s job (best-effort)
    try:
        compute_backend.delete_job(job_name, test_namespace)
    except Exception:
        pass

    # Delete all blobs under the job base path
    try:
        fs = storage_backend.get_filesystem()
        base_uri = storage_backend.storage_uri(
            get_job_base_path(project, test_username, job_id)
        )
        fs.rm(base_uri, recursive=True)
    except Exception:
        pass


@pytest.fixture
def completed_job_with_logs(storage_backend, test_username):
    """Create a completed job with stored JSONL log entries.

    Yields the ``job_id``.  On teardown all blobs for the job are removed.
    """
    project = "default"
    job_id = generate_job_id(
        user=test_username,
        project=project,
        dep_hash="00000000",
        function_name="logged_func",
    )

    # --- metadata (completed) -------------------------------------------
    metadata = create_job_metadata(
        job_id=job_id,
        user=test_username,
        project=project,
        function_name="logged_func",
        config={"cpu": "1", "memory": "2Gi", "timeout": 300, "node_type": None},
        dep_hash="00000000",
    )
    metadata = update_metadata_status(metadata, status="completed")
    metadata_path = get_metadata_path(project, test_username, job_id)
    storage_backend.write(metadata_path, metadata.to_json().encode("utf-8"))

    # --- JSONL log file -------------------------------------------------
    log_entries = [
        {"level": "INFO", "message": "Job started", "timestamp": "2026-03-03T10:00:00Z"},
        {"level": "INFO", "message": "Loading package from blob storage", "timestamp": "2026-03-03T10:00:01Z"},
        {"level": "INFO", "message": "Executing function logged_func", "timestamp": "2026-03-03T10:00:02Z"},
        {"level": "WARNING", "message": "Slow network detected", "timestamp": "2026-03-03T10:00:05Z"},
        {"level": "INFO", "message": "Job completed successfully", "timestamp": "2026-03-03T10:00:10Z"},
    ]
    log_text = "\n".join(json.dumps(entry) for entry in log_entries) + "\n"
    log_path = get_log_path(project, test_username, job_id)
    storage_backend.write(log_path, log_text.encode("utf-8"))

    yield job_id

    # --- teardown -------------------------------------------------------
    try:
        fs = storage_backend.get_filesystem()
        base_uri = storage_backend.storage_uri(
            get_job_base_path(project, test_username, job_id)
        )
        fs.rm(base_uri, recursive=True)
    except Exception:
        pass


@pytest.fixture
def metadata_only_job(storage_backend, test_username):
    """Create a job with metadata but **no** stored logs.

    Simulates a job whose K8s resources (and log blob) have been deleted
    but whose metadata still lingers in blob storage.

    Yields the ``job_id``.  On teardown all blobs for the job are removed.
    """
    project = "default"
    job_id = generate_job_id(
        user=test_username,
        project=project,
        dep_hash="00000000",
        function_name="deleted_func",
    )

    metadata = create_job_metadata(
        job_id=job_id,
        user=test_username,
        project=project,
        function_name="deleted_func",
        config={"cpu": "1", "memory": "2Gi", "timeout": 300, "node_type": None},
        dep_hash="00000000",
    )
    metadata = update_metadata_status(metadata, status="completed")
    metadata_path = get_metadata_path(project, test_username, job_id)
    storage_backend.write(metadata_path, metadata.to_json().encode("utf-8"))

    yield job_id

    try:
        fs = storage_backend.get_filesystem()
        base_uri = storage_backend.storage_uri(
            get_job_base_path(project, test_username, job_id)
        )
        fs.rm(base_uri, recursive=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CLI-level: Cancel a real running job
# ---------------------------------------------------------------------------


class TestJobCancelRunningIntegration:
    """Integration tests that cancel a *real* running K8s job via the CLI
    function ``cancel_job()``.

    These tests submit a long-sleeping job, wait for it to become active,
    cancel it through the CLI layer, and verify both K8s and blob-side
    effects.
    """

    @pytest.mark.integration
    def test_cancel_running_job_deletes_k8s_job(
        self, running_job, compute_backend
    ):
        """After ``cancel_job()`` the K8s job should no longer exist."""
        import ascend.cli.jobs as cli_jobs

        job_id = running_job["job_id"]
        job_name = running_job["job_name"]
        namespace = running_job["namespace"]

        # Patch the module-level Rich console so we can capture output
        orig_console = cli_jobs.console
        capture_console, buf = _capture_console()
        cli_jobs.console = capture_console
        try:
            cli_jobs.cancel_job(job_id)
        finally:
            cli_jobs.console = orig_console

        output = buf.getvalue()

        # CLI should report the deletion
        assert "Deleted" in output or "cancelled" in output.lower()

        # K8s job should be gone (or at least not active)
        # Allow a brief propagation delay for the background delete
        gone = False
        for _ in range(6):
            status = compute_backend.get_job_status(job_name, namespace)
            if status is None:
                gone = True
                break
            time.sleep(5)
        assert gone, f"K8s job {job_name} still exists after cancel"

    @pytest.mark.integration
    def test_cancel_running_job_updates_metadata(
        self, running_job, storage_backend, test_username
    ):
        """After ``cancel_job()`` the metadata blob should have
        ``status == 'cancelled'`` and a populated ``end_time``.
        """
        import ascend.cli.jobs as cli_jobs

        job_id = running_job["job_id"]
        project = running_job["project"]

        orig_console = cli_jobs.console
        cli_jobs.console, _ = _capture_console()
        try:
            cli_jobs.cancel_job(job_id)
        finally:
            cli_jobs.console = orig_console

        # Re-read metadata from blob storage
        path = get_metadata_path(project, test_username, job_id)
        raw = storage_backend.read(path)
        meta = JobMetadata.from_json(raw.decode("utf-8"))

        assert meta.status == "cancelled"
        assert meta.execution.end_time is not None, (
            "end_time should be set after cancellation"
        )

    @pytest.mark.integration
    def test_cancel_preserves_storage_artifacts(
        self, running_job, storage_backend, test_username
    ):
        """Cancellation deletes the K8s job but preserves blob artifacts
        (metadata, package) for auditability.
        """
        import ascend.cli.jobs as cli_jobs

        job_id = running_job["job_id"]
        project = running_job["project"]

        orig_console = cli_jobs.console
        cli_jobs.console, _ = _capture_console()
        try:
            cli_jobs.cancel_job(job_id)
        finally:
            cli_jobs.console = orig_console

        # Metadata blob should still exist
        metadata_path = get_metadata_path(project, test_username, job_id)
        assert storage_backend.exists(metadata_path), (
            "Metadata should be preserved after cancel"
        )

        # Package blob should still exist
        package_path = get_package_path(project, test_username, job_id)
        assert storage_backend.exists(package_path), (
            "Package blob should be preserved after cancel"
        )


# ---------------------------------------------------------------------------
# CLI-level: List / read logs
# ---------------------------------------------------------------------------


class TestJobLogsIntegration:
    """Integration tests for ``job_logs()`` against real blob storage."""

    @pytest.mark.integration
    def test_logs_lists_previous_run(
        self, completed_job_with_logs, storage_backend
    ):
        """Stored JSONL logs from a completed job should be pretty-printed."""
        import ascend.cli.jobs as cli_jobs

        job_id = completed_job_with_logs

        orig_console = cli_jobs.console
        capture_console, buf = _capture_console()
        cli_jobs.console = capture_console
        try:
            cli_jobs.job_logs(job_id)
        finally:
            cli_jobs.console = orig_console

        output = buf.getvalue()

        # Each log message written by the fixture should appear
        assert "Job started" in output
        assert "Loading package from blob storage" in output
        assert "Executing function logged_func" in output
        assert "Slow network detected" in output
        assert "Job completed successfully" in output

    @pytest.mark.integration
    def test_logs_shows_no_logs_for_deleted_job(
        self, metadata_only_job, storage_backend
    ):
        """When the log blob is missing, ``job_logs()`` should report
        'No stored logs found'.
        """
        import ascend.cli.jobs as cli_jobs

        job_id = metadata_only_job

        # Verify precondition: log blob really does not exist
        from ascend.storage.paths import get_log_path

        log_path = get_log_path(
            "default",
            os.getenv("AZURE_USERNAME", "integration-test"),
            job_id,
        )
        assert not storage_backend.exists(log_path), (
            "Precondition failed — log blob should not exist for this fixture"
        )

        orig_console = cli_jobs.console
        capture_console, buf = _capture_console()
        cli_jobs.console = capture_console
        try:
            cli_jobs.job_logs(job_id)
        finally:
            cli_jobs.console = orig_console

        output = buf.getvalue()
        assert "No stored logs found" in output
