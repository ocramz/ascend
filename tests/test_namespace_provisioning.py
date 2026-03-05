"""Tests for Kubernetes namespace provisioning and job-creation error handling."""

from unittest.mock import MagicMock, patch, call

import pytest
from kubernetes.client.rest import ApiException


# ---------------------------------------------------------------------------
# ensure_namespace
# ---------------------------------------------------------------------------

class TestEnsureNamespace:
    """Tests for the shared ensure_namespace utility."""

    def _make_api_exc(self, status: int) -> ApiException:
        resp = MagicMock()
        resp.status = status
        resp.reason = {404: "Not Found", 409: "Conflict", 403: "Forbidden"}[status]
        resp.data = b""
        return ApiException(http_resp=resp)

    def test_creates_all_resources(self):
        """When nothing exists, all four K8s resources are created."""
        from ascend.cloud.kubernetes.namespace import ensure_namespace

        core_v1 = MagicMock()
        rbac_v1 = MagicMock()

        result = ensure_namespace("alice", core_v1=core_v1, rbac_v1=rbac_v1)

        assert result.namespace == "ascend-users-alice"
        assert result.service_account == "ascend-user-alice"
        assert result.created is True

        core_v1.create_namespace.assert_called_once()
        core_v1.create_namespaced_service_account.assert_called_once()
        rbac_v1.create_namespaced_role.assert_called_once()
        rbac_v1.create_namespaced_role_binding.assert_called_once()

    def test_idempotent_when_all_exist(self):
        """When all resources already exist (409), no error is raised."""
        from ascend.cloud.kubernetes.namespace import ensure_namespace

        conflict = self._make_api_exc(409)
        core_v1 = MagicMock()
        rbac_v1 = MagicMock()
        core_v1.create_namespace.side_effect = conflict
        core_v1.create_namespaced_service_account.side_effect = conflict
        rbac_v1.create_namespaced_role.side_effect = conflict
        rbac_v1.create_namespaced_role_binding.side_effect = conflict

        result = ensure_namespace("bob", core_v1=core_v1, rbac_v1=rbac_v1)

        assert result.namespace == "ascend-users-bob"
        assert result.created is False

    def test_propagates_forbidden(self):
        """A 403 from the API is not swallowed."""
        from ascend.cloud.kubernetes.namespace import ensure_namespace

        forbidden = self._make_api_exc(403)
        core_v1 = MagicMock()
        rbac_v1 = MagicMock()
        core_v1.create_namespace.side_effect = forbidden

        with pytest.raises(ApiException) as exc_info:
            ensure_namespace("carol", core_v1=core_v1, rbac_v1=rbac_v1)
        assert exc_info.value.status == 403

    def test_partial_existence(self):
        """Namespace exists (409) but SA does not — no error."""
        from ascend.cloud.kubernetes.namespace import ensure_namespace

        conflict = self._make_api_exc(409)
        core_v1 = MagicMock()
        rbac_v1 = MagicMock()
        core_v1.create_namespace.side_effect = conflict
        # SA and RBAC succeed (fresh create)

        result = ensure_namespace("dave", core_v1=core_v1, rbac_v1=rbac_v1)

        assert result.namespace == "ascend-users-dave"
        # Namespace already existed => created is False
        assert result.created is False
        core_v1.create_namespaced_service_account.assert_called_once()
        rbac_v1.create_namespaced_role.assert_called_once()
        rbac_v1.create_namespaced_role_binding.assert_called_once()


# ---------------------------------------------------------------------------
# namespace_exists
# ---------------------------------------------------------------------------

class TestNamespaceExists:
    """Tests for the namespace_exists helper."""

    def test_returns_true_when_present(self):
        from ascend.cloud.kubernetes.namespace import namespace_exists

        core_v1 = MagicMock()
        assert namespace_exists("ascend-users-alice", core_v1=core_v1) is True
        core_v1.read_namespace.assert_called_once_with(name="ascend-users-alice")

    def test_returns_false_on_404(self):
        from ascend.cloud.kubernetes.namespace import namespace_exists

        resp = MagicMock()
        resp.status = 404
        resp.reason = "Not Found"
        resp.data = b""
        core_v1 = MagicMock()
        core_v1.read_namespace.side_effect = ApiException(http_resp=resp)

        assert namespace_exists("ascend-users-nope", core_v1=core_v1) is False


# ---------------------------------------------------------------------------
# create_job — 404 error handling
# ---------------------------------------------------------------------------

class TestCreateJobNamespaceNotFound:
    """Verify that create_job raises ExecutionError on 404 (namespace missing)."""

    def _make_api_exc(self, status: int, body: str = "") -> ApiException:
        resp = MagicMock()
        resp.status = status
        resp.reason = {404: "Not Found", 409: "Conflict"}[status]
        resp.data = body.encode()
        return ApiException(http_resp=resp)

    def test_raises_execution_error_on_404(self):
        from ascend.cloud.kubernetes.jobs import create_job
        from ascend.utils.errors import ExecutionError

        mock_api = MagicMock()
        mock_api.create_namespaced_job.side_effect = self._make_api_exc(
            404,
            '{"message":"namespaces \\"ascend-users-test\\" not found"}',
        )

        config = MagicMock()
        config.get = lambda k, d=None: {"cpu": "1", "memory": "2Gi", "node_type": None}.get(k, d)
        config.node_type = None
        config.timeout = None

        with pytest.raises(ExecutionError, match="does not exist"):
            create_job(
                k8s_client_api=mock_api,
                namespace="ascend-users-test",
                job_id="abc123",
                package_url="https://blob/pkg",
                config=config,
                registry="test.azurecr.io",
            )

    def test_409_still_tolerated(self):
        """A 409 (AlreadyExists) should be silently handled, not raise."""
        from ascend.cloud.kubernetes.jobs import create_job

        mock_api = MagicMock()
        mock_api.create_namespaced_job.side_effect = self._make_api_exc(409)

        config = MagicMock()
        config.get = lambda k, d=None: {"cpu": "1", "memory": "2Gi", "node_type": None}.get(k, d)
        config.node_type = None
        config.timeout = None

        # Should not raise
        job_name = create_job(
            k8s_client_api=mock_api,
            namespace="ascend-users-test",
            job_id="abc123",
            package_url="https://blob/pkg",
            config=config,
            registry="test.azurecr.io",
        )
        assert job_name == "ascend-abc123"
