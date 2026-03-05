"""Tests for NodePoolValidator wiring in RemoteExecutor."""

from unittest.mock import MagicMock, patch

import pytest

from ascend.decorator import AscendConfig
from ascend.node_types import NodeType
from ascend.runtime.executor import RemoteExecutor
from ascend.utils.errors import ExecutionError


_VALID_USER_CONFIG = {
    "username": "testuser",
    "cluster_name": "ascend-test",
    "resource_group": "test-rg",
    "namespace": "ascend-users-test",
    "storage_account": "teststorage123",
    "container_registry": "testacr.azurecr.io",
}


def _make_executor(
    node_type: str | None = None,
    user_config: dict | None = None,
) -> RemoteExecutor:
    """Build a RemoteExecutor with mocked backend and config."""
    config = AscendConfig(
        cpu="1",
        memory="2Gi",
        timeout=60,
        node_type=node_type,
    )
    backend = MagicMock()
    with patch("ascend.config.load_config", return_value=user_config or _VALID_USER_CONFIG):
        executor = RemoteExecutor(config, backend)
    return executor


# --------------------------------------------------------------------------- #
#  _validate_node_pool tests
# --------------------------------------------------------------------------- #

class TestValidateNodePool:
    """Unit tests for RemoteExecutor._validate_node_pool."""

    @patch("ascend.cloud.azure.node_pool_validator.NodePoolValidator", autospec=True)
    @patch("ascend.cloud.azure.cli.get_subscription_id", return_value="sub-123")
    @patch("ascend.cloud.azure.auth.get_azure_credential", return_value=MagicMock())
    def test_passes_when_valid(self, _cred, _sub, mock_validator_cls):
        """No exception raised when the validator says the node pool is available."""
        mock_instance = mock_validator_cls.return_value
        mock_instance.validate_node_type_available.return_value = (
            True,
            "Node type 'gpu_small' is available",
        )

        executor = _make_executor(node_type="gpu_small")
        # Should not raise
        executor._validate_node_pool()

        mock_instance.validate_node_type_available.assert_called_once_with(
            node_type=NodeType.GPU_SMALL,
            resource_group="test-rg",
            cluster_name="ascend-test",
        )

    @patch("ascend.cloud.azure.node_pool_validator.NodePoolValidator", autospec=True)
    @patch("ascend.cloud.azure.cli.get_subscription_id", return_value="sub-123")
    @patch("ascend.cloud.azure.auth.get_azure_credential", return_value=MagicMock())
    def test_raises_when_invalid(self, _cred, _sub, mock_validator_cls):
        """ExecutionError raised when validation fails."""
        mock_instance = mock_validator_cls.return_value
        mock_instance.validate_node_type_available.return_value = (
            False,
            "No matching nodes found",
        )

        executor = _make_executor(node_type="gpu_small")
        with pytest.raises(ExecutionError, match="not available in cluster"):
            executor._validate_node_pool()

    @patch("ascend.cloud.azure.node_pool_validator.NodePoolValidator", autospec=True)
    @patch("ascend.cloud.azure.cli.get_subscription_id", return_value="sub-123")
    @patch("ascend.cloud.azure.auth.get_azure_credential", return_value=MagicMock())
    def test_error_message_contains_admin_hint(self, _cred, _sub, mock_validator_cls):
        """Error message should tell the user to ask admin for help."""
        mock_instance = mock_validator_cls.return_value
        mock_instance.validate_node_type_available.return_value = (
            False,
            "No GPU pool",
        )

        executor = _make_executor(node_type="gpu_large")
        with pytest.raises(ExecutionError, match="ascend admin setup --gpu"):
            executor._validate_node_pool()

    @patch("ascend.cloud.azure.node_pool_validator.NodePoolValidator", autospec=True)
    @patch("ascend.cloud.azure.auth.get_azure_credential", side_effect=Exception("no creds"))
    def test_works_without_subscription_id(self, _cred, mock_validator_cls):
        """Validator should still be called even if subscription_id cannot be obtained."""
        mock_instance = mock_validator_cls.return_value
        mock_instance.validate_node_type_available.return_value = (
            True,
            "Available via K8s check",
        )

        executor = _make_executor(node_type="gpu_small")
        executor._validate_node_pool()

        # subscription_id should be None when Azure creds fail
        mock_validator_cls.assert_called_once_with(subscription_id=None)


# --------------------------------------------------------------------------- #
#  execute() integration: validate is called at the right time
# --------------------------------------------------------------------------- #

class TestExecuteCallsValidator:
    """Verify that execute() invokes node pool validation before create_job."""

    @patch("ascend.config.load_config", return_value=_VALID_USER_CONFIG)
    def test_execute_no_node_type_skips_validation(self, _load):
        """When no node_type is set, _validate_node_pool is never called."""
        config = AscendConfig(cpu="1", memory="2Gi", timeout=60)
        backend = MagicMock()
        executor = RemoteExecutor(config, backend)

        with patch.object(executor, "_validate_node_pool") as mock_validate:
            # We'll let the rest of execute() crash (no real storage, etc.)
            # but _validate_node_pool should not have been called
            try:
                executor.execute(
                    {
                        "job_id": "test-001",
                        "function_name": "f",
                        "requirements": [],
                    }
                )
            except Exception:
                pass
            mock_validate.assert_not_called()

    @patch("ascend.config.load_config", return_value=_VALID_USER_CONFIG)
    def test_execute_with_node_type_calls_validation(self, _load):
        """When node_type is set, _validate_node_pool is called before create_job."""
        config = AscendConfig(cpu="1", memory="2Gi", timeout=60, node_type="gpu_small")
        backend = MagicMock()
        executor = RemoteExecutor(config, backend)

        call_order = []

        def record_validate():
            call_order.append("validate")

        def record_create_job(**kwargs):
            call_order.append("create_job")
            return "job-name-123"

        with patch.object(executor, "_validate_node_pool", side_effect=record_validate):
            backend.compute.create_job.side_effect = record_create_job
            try:
                executor.execute(
                    {
                        "job_id": "test-001",
                        "function_name": "f",
                        "requirements": [],
                    }
                )
            except Exception:
                pass

            assert "validate" in call_order, "Validation should have been called"
            if "create_job" in call_order:
                assert call_order.index("validate") < call_order.index(
                    "create_job"
                ), "Validation must happen before create_job"

    @patch("ascend.config.load_config", return_value=_VALID_USER_CONFIG)
    @patch("ascend.cloud.azure.node_pool_validator.NodePoolValidator", autospec=True)
    @patch("ascend.cloud.azure.auth.get_azure_credential", side_effect=Exception("no creds"))
    def test_execute_aborts_on_validation_failure(
        self, _cred, mock_validator_cls, _load
    ):
        """execute() should raise ExecutionError and never call create_job when validation fails."""
        mock_instance = mock_validator_cls.return_value
        mock_instance.validate_node_type_available.return_value = (
            False,
            "No matching pool",
        )

        config = AscendConfig(cpu="1", memory="2Gi", timeout=60, node_type="gpu_small")
        backend = MagicMock()
        executor = RemoteExecutor(config, backend)

        with pytest.raises(ExecutionError, match="not available"):
            executor.execute(
                {
                    "job_id": "test-001",
                    "function_name": "f",
                    "requirements": [],
                }
            )

        backend.compute.create_job.assert_not_called()
