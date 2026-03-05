"""Tests for the wait_for_completion 404 bug fix (Item 10).

Verifies that when a K8s job returns 404:
- If pods succeeded → returns True
- If pods found but none succeeded → returns False
- If no pods found → raises ExecutionError
- If pod-check raises → raises ExecutionError
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.rest import ApiException

from ascend.cloud.kubernetes.jobs import wait_for_completion
from ascend.utils.errors import ExecutionError


def _make_api_exception(status: int) -> ApiException:
    """Create a minimal ApiException."""
    exc = ApiException(status=status, reason="test")
    exc.status = status
    return exc


class TestWaitForCompletion404:
    """Tests for 404 handling in wait_for_completion."""

    def test_404_with_succeeded_pod_returns_true(self):
        """When job is 404 but a pod succeeded, return True."""
        batch_api = MagicMock()
        batch_api.read_namespaced_job.side_effect = _make_api_exception(404)

        core_api = MagicMock()
        pod = SimpleNamespace(status=SimpleNamespace(phase="Succeeded"))
        core_api.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])

        result = wait_for_completion(
            k8s_client_api=batch_api,
            namespace="test-ns",
            job_name="ascend-test-job",
            timeout_seconds=5,
            k8s_core_api=core_api,
        )
        assert result is True

    def test_404_with_failed_pod_returns_false(self):
        """When job is 404, pods exist but none succeeded, return False."""
        batch_api = MagicMock()
        batch_api.read_namespaced_job.side_effect = _make_api_exception(404)

        core_api = MagicMock()
        pod = SimpleNamespace(status=SimpleNamespace(phase="Failed"))
        core_api.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])

        result = wait_for_completion(
            k8s_client_api=batch_api,
            namespace="test-ns",
            job_name="ascend-test-job",
            timeout_seconds=5,
            k8s_core_api=core_api,
        )
        assert result is False

    def test_404_no_pods_raises_execution_error(self):
        """When job is 404 and no pods found, raise ExecutionError."""
        batch_api = MagicMock()
        batch_api.read_namespaced_job.side_effect = _make_api_exception(404)

        core_api = MagicMock()
        core_api.list_namespaced_pod.return_value = SimpleNamespace(items=[])

        with pytest.raises(ExecutionError, match="not found"):
            wait_for_completion(
                k8s_client_api=batch_api,
                namespace="test-ns",
                job_name="ascend-test-job",
                timeout_seconds=5,
                k8s_core_api=core_api,
            )

    def test_404_no_core_api_raises_execution_error(self):
        """When job is 404 and no core_api provided, raise ExecutionError."""
        batch_api = MagicMock()
        batch_api.read_namespaced_job.side_effect = _make_api_exception(404)

        with pytest.raises(ExecutionError, match="not found"):
            wait_for_completion(
                k8s_client_api=batch_api,
                namespace="test-ns",
                job_name="ascend-test-job",
                timeout_seconds=5,
                k8s_core_api=None,
            )

    def test_404_pod_check_error_raises_execution_error(self):
        """When pod check raises an unexpected exception, raise ExecutionError."""
        batch_api = MagicMock()
        batch_api.read_namespaced_job.side_effect = _make_api_exception(404)

        core_api = MagicMock()
        core_api.list_namespaced_pod.side_effect = RuntimeError("connection lost")

        with pytest.raises(ExecutionError, match="pod status check failed"):
            wait_for_completion(
                k8s_client_api=batch_api,
                namespace="test-ns",
                job_name="ascend-test-job",
                timeout_seconds=5,
                k8s_core_api=core_api,
            )

    def test_non_404_api_exception_propagates(self):
        """Non-404 ApiExceptions should propagate unchanged."""
        batch_api = MagicMock()
        batch_api.read_namespaced_job.side_effect = _make_api_exception(403)

        with pytest.raises(ApiException):
            wait_for_completion(
                k8s_client_api=batch_api,
                namespace="test-ns",
                job_name="ascend-test-job",
                timeout_seconds=5,
            )

    def test_successful_job_returns_true(self):
        """Normal success path: job.status.succeeded is truthy."""
        batch_api = MagicMock()
        job = SimpleNamespace(
            status=SimpleNamespace(succeeded=1, failed=None)
        )
        batch_api.read_namespaced_job.return_value = job

        result = wait_for_completion(
            k8s_client_api=batch_api,
            namespace="test-ns",
            job_name="ascend-test-job",
            timeout_seconds=5,
        )
        assert result is True

    def test_failed_job_returns_false(self):
        """Normal failure path: job.status.failed is truthy."""
        batch_api = MagicMock()
        job = SimpleNamespace(
            status=SimpleNamespace(succeeded=None, failed=1)
        )
        batch_api.read_namespaced_job.return_value = job

        result = wait_for_completion(
            k8s_client_api=batch_api,
            namespace="test-ns",
            job_name="ascend-test-job",
            timeout_seconds=5,
        )
        assert result is False
