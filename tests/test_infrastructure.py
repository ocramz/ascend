"""Unit tests for idempotent Azure infrastructure provisioning."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

_PY_TAG = f"python{sys.version_info.major}.{sys.version_info.minor}"

from ascend.cloud.azure.infrastructure import (
    InfrastructureResult,
    _ACR_PULL,
    _ACR_PUSH,
    _STORAGE_BLOB_DATA_CONTRIBUTOR,
    _get_aks_kubelet_identity,
    _get_current_principal_id,
    ensure_acr_role_assignment,
    ensure_all_infrastructure,
    ensure_blob_container,
    ensure_container_registry,
    ensure_runtime_image,
    ensure_storage_account,
    ensure_storage_data_role,
)
from ascend.utils.errors import AscendError
from ascend.utils.naming import generate_resource_names

# Patch targets — the Azure SDK classes are imported *inside* function bodies
# so we patch the modules from which they are imported.
_PATCH_STORAGE_CLIENT = "azure.mgmt.storage.StorageManagementClient"
_PATCH_ACR_CLIENT = "azure.mgmt.containerregistry.ContainerRegistryManagementClient"
_PATCH_AUTH_CLIENT = "azure.mgmt.authorization.AuthorizationManagementClient"
_PATCH_AKS_CLIENT = "azure.mgmt.containerservice.ContainerServiceClient"


def _resource_not_found():
    """Create an Azure ResourceNotFoundError for test mocking."""
    from azure.core.exceptions import ResourceNotFoundError

    return ResourceNotFoundError("Resource not found")


# ---------------------------------------------------------------------------
# generate_resource_names
# ---------------------------------------------------------------------------


class TestGenerateResourceNames:
    def test_deterministic(self):
        """Same resource group always produces the same names."""
        a = generate_resource_names("my-rg")
        b = generate_resource_names("my-rg")
        assert a == b

    def test_different_rg_different_names(self):
        a = generate_resource_names("rg-alpha")
        b = generate_resource_names("rg-beta")
        assert a["storage_account"] != b["storage_account"]
        assert a["container_registry"] != b["container_registry"]

    def test_name_format(self):
        names = generate_resource_names("test-rg")
        assert names["storage_account"].startswith("ascend")
        assert names["container_registry"].endswith("acr")
        # Storage account names: 3-24 chars, lowercase alphanumeric only
        assert names["storage_account"].isalnum()
        assert names["container_registry"].isalnum()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_credential():
    return MagicMock()


# ---------------------------------------------------------------------------
# ensure_storage_account
# ---------------------------------------------------------------------------


class TestEnsureStorageAccount:
    @patch("ascend.cloud.azure.infrastructure.console")
    def test_already_exists(self, mock_console):
        """No-op when the storage account is already present."""
        cred = _fake_credential()
        mock_sa = MagicMock()
        mock_sa.name = "ascendabc123"

        with patch(_PATCH_STORAGE_CLIENT) as MockClient:
            client = MockClient.return_value
            client.storage_accounts.get_properties.return_value = mock_sa

            result = ensure_storage_account(
                cred, "sub-1", "my-rg", "eastus", "ascendabc123"
            )

        assert result == "ascendabc123"
        client.storage_accounts.begin_create.assert_not_called()

    @patch("ascend.cloud.azure.infrastructure.console")
    def test_creates_when_missing(self, mock_console):
        """Creates storage account when it does not exist."""
        cred = _fake_credential()

        with patch(_PATCH_STORAGE_CLIENT) as MockClient:
            client = MockClient.return_value
            client.storage_accounts.get_properties.side_effect = _resource_not_found()

            avail = MagicMock()
            avail.name_available = True
            client.storage_accounts.check_name_availability.return_value = avail

            created = MagicMock()
            created.name = "ascendabc123"
            client.storage_accounts.begin_create.return_value.result.return_value = (
                created
            )

            result = ensure_storage_account(
                cred, "sub-1", "my-rg", "eastus", "ascendabc123"
            )

        assert result == "ascendabc123"
        client.storage_accounts.begin_create.assert_called_once()

    @patch("ascend.cloud.azure.infrastructure.console")
    def test_raises_when_name_taken(self, mock_console):
        """Raises AscendError when the name is globally unavailable."""
        cred = _fake_credential()

        with patch(_PATCH_STORAGE_CLIENT) as MockClient:
            client = MockClient.return_value
            client.storage_accounts.get_properties.side_effect = _resource_not_found()

            avail = MagicMock()
            avail.name_available = False
            avail.reason = "AlreadyExists"
            avail.message = "The storage account name is already taken."
            client.storage_accounts.check_name_availability.return_value = avail

            with pytest.raises(AscendError, match="not available"):
                ensure_storage_account(
                    cred, "sub-1", "my-rg", "eastus", "ascendabc123"
                )


# ---------------------------------------------------------------------------
# ensure_blob_container
# ---------------------------------------------------------------------------


class TestEnsureBlobContainer:
    @patch("ascend.cloud.azure.infrastructure.console")
    def test_already_exists(self, mock_console):
        cred = _fake_credential()
        mock_container = MagicMock()
        mock_container.name = "ascend-data"

        with patch(_PATCH_STORAGE_CLIENT) as MockClient:
            client = MockClient.return_value
            client.blob_containers.get.return_value = mock_container

            result = ensure_blob_container(
                cred, "sub-1", "my-rg", "ascendabc123"
            )

        assert result == "ascend-data"
        client.blob_containers.create.assert_not_called()

    @patch("ascend.cloud.azure.infrastructure.console")
    def test_creates_when_missing(self, mock_console):
        cred = _fake_credential()

        with patch(_PATCH_STORAGE_CLIENT) as MockClient:
            client = MockClient.return_value
            client.blob_containers.get.side_effect = _resource_not_found()

            created = MagicMock()
            created.name = "ascend-data"
            client.blob_containers.create.return_value = created

            result = ensure_blob_container(
                cred, "sub-1", "my-rg", "ascendabc123"
            )

        assert result == "ascend-data"
        client.blob_containers.create.assert_called_once()


# ---------------------------------------------------------------------------
# ensure_container_registry
# ---------------------------------------------------------------------------


class TestEnsureContainerRegistry:
    @patch("ascend.cloud.azure.infrastructure.console")
    def test_already_exists(self, mock_console):
        cred = _fake_credential()
        mock_reg = MagicMock()
        mock_reg.name = "ascendabc123acr"
        mock_reg.login_server = "ascendabc123acr.azurecr.io"

        with patch(_PATCH_ACR_CLIENT) as MockClient:
            client = MockClient.return_value
            client.registries.get.return_value = mock_reg

            name, server = ensure_container_registry(
                cred, "sub-1", "my-rg", "eastus", "ascendabc123acr"
            )

        assert name == "ascendabc123acr"
        assert server == "ascendabc123acr.azurecr.io"
        client.registries.begin_create.assert_not_called()

    @patch("ascend.cloud.azure.infrastructure.console")
    def test_creates_when_missing(self, mock_console):
        cred = _fake_credential()

        with patch(_PATCH_ACR_CLIENT) as MockClient:
            client = MockClient.return_value
            client.registries.get.side_effect = _resource_not_found()

            avail = MagicMock()
            avail.name_available = True
            client.registries.check_name_availability.return_value = avail

            created = MagicMock()
            created.name = "ascendabc123acr"
            created.login_server = "ascendabc123acr.azurecr.io"
            client.registries.begin_create.return_value.result.return_value = created

            name, server = ensure_container_registry(
                cred, "sub-1", "my-rg", "eastus", "ascendabc123acr"
            )

        assert name == "ascendabc123acr"
        assert server == "ascendabc123acr.azurecr.io"
        client.registries.begin_create.assert_called_once()

    @patch("ascend.cloud.azure.infrastructure.console")
    def test_raises_when_name_taken(self, mock_console):
        cred = _fake_credential()

        with patch(_PATCH_ACR_CLIENT) as MockClient:
            client = MockClient.return_value
            client.registries.get.side_effect = _resource_not_found()

            avail = MagicMock()
            avail.name_available = False
            avail.message = "The registry name is already taken."
            client.registries.check_name_availability.return_value = avail

            with pytest.raises(AscendError, match="not available"):
                ensure_container_registry(
                    cred, "sub-1", "my-rg", "eastus", "ascendabc123acr"
                )


# ---------------------------------------------------------------------------
# ensure_all_infrastructure
# ---------------------------------------------------------------------------


class TestEnsureStorageDataRole:
    @patch(_PATCH_AUTH_CLIENT)
    @patch("ascend.cloud.azure.infrastructure.console")
    def test_already_assigned(self, mock_console, mock_auth_cls):
        """No-op when the role is already assigned."""
        existing_assignment = MagicMock()
        existing_assignment.role_definition_id = (
            f"/subscriptions/sub-1/providers/Microsoft.Authorization"
            f"/roleDefinitions/{_STORAGE_BLOB_DATA_CONTRIBUTOR}"
        )
        mock_auth_cls.return_value.role_assignments.list_for_scope.return_value = [
            existing_assignment
        ]

        ensure_storage_data_role(
            _fake_credential(), "sub-1", "my-rg", "mysa",
            principal_id="oid-123", principal_type="ServicePrincipal",
        )

        mock_auth_cls.return_value.role_assignments.create.assert_not_called()

    @patch(_PATCH_AUTH_CLIENT)
    @patch("ascend.cloud.azure.infrastructure.console")
    def test_assigns_when_missing(self, mock_console, mock_auth_cls):
        """Creates the role assignment when it does not exist."""
        mock_auth_cls.return_value.role_assignments.list_for_scope.return_value = []

        ensure_storage_data_role(
            _fake_credential(), "sub-1", "my-rg", "mysa",
            principal_id="oid-123", principal_type="ServicePrincipal",
        )

        mock_auth_cls.return_value.role_assignments.create.assert_called_once()

    @patch(_PATCH_AUTH_CLIENT)
    @patch("ascend.cloud.azure.infrastructure.console")
    def test_warns_on_permission_error(self, mock_console, mock_auth_cls):
        """Logs a warning (does not raise) when the SP lacks role assignment permissions."""
        from azure.core.exceptions import HttpResponseError

        mock_auth_cls.return_value.role_assignments.list_for_scope.return_value = []
        mock_auth_cls.return_value.role_assignments.create.side_effect = (
            HttpResponseError(message="AuthorizationFailed")
        )

        # Should NOT raise — best-effort
        ensure_storage_data_role(
            _fake_credential(), "sub-1", "my-rg", "mysa",
            principal_id="oid-123",
        )

        # Verify a warning was printed
        printed = " ".join(
            str(call) for call in mock_console.print.call_args_list
        )
        assert "Could not assign" in printed or "already assigned" in printed


class TestGetCurrentPrincipalId:
    def test_extracts_sp_principal(self):
        """Extracts oid and detects ServicePrincipal from JWT."""
        import base64
        import json
        claims = {"oid": "abc-123", "appid": "client-id"}
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        fake_jwt = f"header.{payload}.signature"
        cred = MagicMock()
        cred.get_token.return_value = MagicMock(token=fake_jwt)
        oid, ptype = _get_current_principal_id(cred)
        assert oid == "abc-123"
        assert ptype == "ServicePrincipal"

    def test_extracts_user_principal(self):
        """Extracts oid and detects User when appid is absent."""
        import base64
        import json
        claims = {"oid": "user-456"}
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        fake_jwt = f"header.{payload}.signature"
        cred = MagicMock()
        cred.get_token.return_value = MagicMock(token=fake_jwt)
        oid, ptype = _get_current_principal_id(cred)
        assert oid == "user-456"
        assert ptype == "User"


# ---------------------------------------------------------------------------
# ensure_all_infrastructure
# ---------------------------------------------------------------------------


class TestEnsureAllInfrastructure:
    @patch("ascend.cloud.azure.infrastructure.ensure_storage_data_role")
    @patch("ascend.cloud.azure.infrastructure._get_current_principal_id", return_value=("oid-1", "ServicePrincipal"))
    @patch("ascend.cloud.azure.infrastructure.ensure_container_registry")
    @patch("ascend.cloud.azure.infrastructure.ensure_blob_container")
    @patch("ascend.cloud.azure.infrastructure.ensure_storage_account")
    @patch("ascend.cloud.azure.infrastructure.console")
    def test_orchestrates_four_steps_without_cluster(self,
        mock_console, mock_sa, mock_blob, mock_cr, mock_pid, mock_role
    ):
        """Without cluster_name only the first 4 steps run."""
        mock_sa.return_value = "ascendabc123"
        mock_blob.return_value = "ascend-data"
        mock_cr.return_value = ("ascendabc123acr", "ascendabc123acr.azurecr.io")

        result = ensure_all_infrastructure(
            credential=_fake_credential(),
            subscription_id="sub-1",
            resource_group="my-rg",
            location="eastus",
        )

        assert isinstance(result, InfrastructureResult)
        assert result.storage_account_name is not None
        assert result.container_registry_login_server == "ascendabc123acr.azurecr.io"
        assert result.blob_container_name == "ascend-data"
        assert result.location == "eastus"
        assert result.runtime_image_uri == ""

        mock_sa.assert_called_once()
        mock_blob.assert_called_once()
        mock_cr.assert_called_once()
        mock_role.assert_called_once()

    @patch("ascend.cloud.azure.infrastructure.ensure_runtime_image")
    @patch("ascend.cloud.azure.infrastructure.ensure_acr_role_assignment")
    @patch("ascend.cloud.azure.infrastructure._get_aks_kubelet_identity", return_value={"object_id": "kubelet-oid", "client_id": "kubelet-cid"})
    @patch("ascend.cloud.azure.infrastructure.ensure_storage_data_role")
    @patch("ascend.cloud.azure.infrastructure._get_current_principal_id", return_value=("oid-1", "ServicePrincipal"))
    @patch("ascend.cloud.azure.infrastructure.ensure_container_registry")
    @patch("ascend.cloud.azure.infrastructure.ensure_blob_container")
    @patch("ascend.cloud.azure.infrastructure.ensure_storage_account")
    @patch("ascend.cloud.azure.infrastructure.console")
    def test_orchestrates_six_steps_with_cluster(
        self, mock_console, mock_sa, mock_blob, mock_cr, mock_pid,
        mock_role, mock_kubelet, mock_acr_attach, mock_runtime_image
    ):
        """With cluster_name all six steps run."""
        mock_sa.return_value = "ascendabc123"
        mock_blob.return_value = "ascend-data"
        mock_cr.return_value = ("ascendabc123acr", "ascendabc123acr.azurecr.io")
        mock_runtime_image.return_value = f"ascendabc123acr.azurecr.io/ascend-runtime:{_PY_TAG}"

        result = ensure_all_infrastructure(
            credential=_fake_credential(),
            subscription_id="sub-1",
            resource_group="my-rg",
            location="eastus",
            cluster_name="my-cluster",
        )

        assert isinstance(result, InfrastructureResult)
        assert result.runtime_image_uri == f"ascendabc123acr.azurecr.io/ascend-runtime:{_PY_TAG}"
        assert result.managed_identity_client_id == "kubelet-cid"

        mock_sa.assert_called_once()
        mock_blob.assert_called_once()
        mock_cr.assert_called_once()
        # Called twice: once for user principal, once for kubelet identity
        assert mock_role.call_count == 2
        mock_acr_attach.assert_called_once()
        mock_runtime_image.assert_called_once()

    @patch("ascend.cloud.azure.infrastructure.ensure_storage_data_role")
    @patch("ascend.cloud.azure.infrastructure._get_current_principal_id", return_value=("oid-1", "ServicePrincipal"))
    @patch("ascend.cloud.azure.infrastructure.ensure_container_registry")
    @patch("ascend.cloud.azure.infrastructure.ensure_blob_container")
    @patch("ascend.cloud.azure.infrastructure.ensure_storage_account")
    @patch("ascend.cloud.azure.infrastructure.console")
    def test_uses_generated_names_by_default(
        self, mock_console, mock_sa, mock_blob, mock_cr, mock_pid, mock_role
    ):
        """When no explicit names are given, uses generate_resource_names()."""
        defaults = generate_resource_names("my-rg")

        mock_sa.return_value = defaults["storage_account"]
        mock_blob.return_value = "ascend-data"
        mock_cr.return_value = (defaults["container_registry"], f"{defaults['container_registry']}.azurecr.io")

        result = ensure_all_infrastructure(
            credential=_fake_credential(),
            subscription_id="sub-1",
            resource_group="my-rg",
            location="eastus",
        )

        assert result.storage_account_name == defaults["storage_account"]
        assert result.container_registry_name == defaults["container_registry"]

    @patch("ascend.cloud.azure.infrastructure.ensure_storage_data_role")
    @patch("ascend.cloud.azure.infrastructure._get_current_principal_id", return_value=("oid-1", "ServicePrincipal"))
    @patch("ascend.cloud.azure.infrastructure.ensure_container_registry")
    @patch("ascend.cloud.azure.infrastructure.ensure_blob_container")
    @patch("ascend.cloud.azure.infrastructure.ensure_storage_account")
    @patch("ascend.cloud.azure.infrastructure.console")
    def test_accepts_explicit_overrides(
        self, mock_console, mock_sa, mock_blob, mock_cr, mock_pid, mock_role
    ):
        """Explicit names override generated defaults."""
        mock_sa.return_value = "mystorage"
        mock_blob.return_value = "ascend-data"
        mock_cr.return_value = ("myregistry", "myregistry.azurecr.io")

        result = ensure_all_infrastructure(
            credential=_fake_credential(),
            subscription_id="sub-1",
            resource_group="my-rg",
            location="eastus",
            storage_account_name="mystorage",
            registry_name="myregistry",
        )

        assert result.storage_account_name == "mystorage"
        assert result.container_registry_name == "myregistry"


# ---------------------------------------------------------------------------
# ensure_acr_role_assignment
# ---------------------------------------------------------------------------


class TestEnsureAcrRoleAssignment:
    @patch("ascend.cloud.azure.infrastructure._get_aks_kubelet_identity", return_value={"object_id": "kubelet-oid", "client_id": "kubelet-cid"})
    @patch(_PATCH_AUTH_CLIENT)
    @patch("ascend.cloud.azure.infrastructure.console")
    def test_already_assigned(self, mock_console, mock_auth_cls, mock_kubelet):
        """No-op when both AcrPull and AcrPush are already assigned."""
        pull_assignment = MagicMock()
        pull_assignment.role_definition_id = f"/subscriptions/sub-1/providers/Microsoft.Authorization/roleDefinitions/{_ACR_PULL}"
        push_assignment = MagicMock()
        push_assignment.role_definition_id = f"/subscriptions/sub-1/providers/Microsoft.Authorization/roleDefinitions/{_ACR_PUSH}"

        mock_auth_cls.return_value.role_assignments.list_for_scope.return_value = [
            pull_assignment, push_assignment
        ]

        ensure_acr_role_assignment(
            _fake_credential(), "sub-1", "my-rg", "myacr", "my-cluster"
        )

        mock_auth_cls.return_value.role_assignments.create.assert_not_called()

    @patch("ascend.cloud.azure.infrastructure._get_aks_kubelet_identity", return_value={"object_id": "kubelet-oid", "client_id": "kubelet-cid"})
    @patch(_PATCH_AUTH_CLIENT)
    @patch("ascend.cloud.azure.infrastructure.console")
    def test_assigns_when_missing(self, mock_console, mock_auth_cls, mock_kubelet):
        """Creates assignments when roles are not yet assigned."""
        mock_auth_cls.return_value.role_assignments.list_for_scope.return_value = []

        ensure_acr_role_assignment(
            _fake_credential(), "sub-1", "my-rg", "myacr", "my-cluster"
        )

        # Should be called twice: once for AcrPull, once for AcrPush
        assert mock_auth_cls.return_value.role_assignments.create.call_count == 2

    @patch("ascend.cloud.azure.infrastructure._get_aks_kubelet_identity", return_value={"object_id": "kubelet-oid", "client_id": "kubelet-cid"})
    @patch(_PATCH_AUTH_CLIENT)
    @patch("ascend.cloud.azure.infrastructure.console")
    def test_warns_on_permission_error(self, mock_console, mock_auth_cls, mock_kubelet):
        """Logs a warning when role assignment fails due to permissions."""
        from azure.core.exceptions import HttpResponseError

        mock_auth_cls.return_value.role_assignments.list_for_scope.return_value = []
        mock_auth_cls.return_value.role_assignments.create.side_effect = (
            HttpResponseError(message="AuthorizationFailed")
        )

        # Should NOT raise
        ensure_acr_role_assignment(
            _fake_credential(), "sub-1", "my-rg", "myacr", "my-cluster"
        )

        printed = " ".join(
            str(call) for call in mock_console.print.call_args_list
        )
        assert "Could not assign" in printed


# ---------------------------------------------------------------------------
# _get_aks_kubelet_identity
# ---------------------------------------------------------------------------


class TestGetAksKubeletIdentity:
    @patch(_PATCH_AKS_CLIENT)
    def test_returns_kubelet_object_id(self, mock_aks_cls):
        """Returns the kubelet identity object ID."""
        kubelet_identity = MagicMock()
        kubelet_identity.object_id = "kubelet-oid-123"
        kubelet_identity.client_id = "kubelet-cid-456"
        cluster = MagicMock()
        cluster.identity_profile = {"kubeletidentity": kubelet_identity}
        mock_aks_cls.return_value.managed_clusters.get.return_value = cluster

        result = _get_aks_kubelet_identity(
            _fake_credential(), "sub-1", "my-rg", "my-cluster"
        )
        assert result == {"object_id": "kubelet-oid-123", "client_id": "kubelet-cid-456"}

    @patch(_PATCH_AKS_CLIENT)
    def test_raises_when_no_kubelet_identity(self, mock_aks_cls):
        """Raises AscendError when cluster has no kubelet identity."""
        cluster = MagicMock()
        cluster.identity_profile = {}
        mock_aks_cls.return_value.managed_clusters.get.return_value = cluster

        with pytest.raises(AscendError, match="kubelet managed identity"):
            _get_aks_kubelet_identity(
                _fake_credential(), "sub-1", "my-rg", "my-cluster"
            )


# ---------------------------------------------------------------------------
# ensure_runtime_image
# ---------------------------------------------------------------------------


class TestEnsureRuntimeImage:
    @patch("ascend.cloud.azure.registry.AzureContainerRegistry")
    @patch("ascend.cloud.azure.infrastructure.console")
    def test_noop_when_image_exists(self, mock_console, mock_acr_cls):
        """Returns URI immediately when the image already exists."""
        mock_acr_cls.return_value.image_exists.return_value = True

        result = ensure_runtime_image("myacr.azurecr.io", _fake_credential())

        assert result == f"myacr.azurecr.io/ascend-runtime:{_PY_TAG}"
        mock_acr_cls.return_value.image_exists.assert_called_once_with(
            "ascend-runtime", _PY_TAG
        )

    @patch("ascend.cloud.azure.infrastructure.ensure_registry_credentials_secret")
    @patch("ascend.cloud.azure.infrastructure._get_runner_script", return_value="# runner")
    @patch("ascend.cloud.azure.infrastructure._get_runtime_dockerfile", return_value="FROM python:3.11-slim")
    @patch("ascend.cloud.kubernetes.kaniko.KanikoJobManager")
    @patch("kubernetes.client.BatchV1Api")
    @patch("kubernetes.config.load_kube_config")
    @patch("ascend.cloud.azure.registry.AzureContainerRegistry")
    @patch("ascend.cloud.azure.infrastructure.console")
    def test_builds_when_missing(self, mock_console, mock_acr_cls,
        mock_kube_config, mock_batch_api, mock_kaniko_cls,
        mock_dockerfile, mock_runner, mock_cred_secret
    ):
        """Submits a Kaniko build job when the image does not exist."""
        from ascend.cloud.kubernetes.kaniko import ImageBuildStatus

        mock_acr_cls.return_value.image_exists.return_value = False

        # The KanikoJobManager is created inside ensure_runtime_image with the
        # real class; we need to patch the module-level import instead.
        mock_kaniko = mock_kaniko_cls.return_value
        mock_kaniko._generate_job_manifest.return_value = {
            "metadata": {"name": f"ascend-build-{_PY_TAG}"},
            "spec": {"template": {"spec": {
                "initContainers": [{"args": ["original"]}],
            }}},
        }
        mock_kaniko.get_job_status.return_value = ImageBuildStatus(
            job_id=f"ascend-build-{_PY_TAG}",
            status="completed",
            progress="Build completed successfully",
        )

        # Make batch_v1.delete_namespaced_job raise 404 (no previous job)
        from kubernetes.client.exceptions import ApiException
        mock_batch_api.return_value.delete_namespaced_job.side_effect = (
            ApiException(status=404)
        )

        result = ensure_runtime_image(
            "myacr.azurecr.io", _fake_credential(), timeout_seconds=10
        )

        assert result == f"myacr.azurecr.io/ascend-runtime:{_PY_TAG}"
        mock_cred_secret.assert_called_once()
        mock_batch_api.return_value.create_namespaced_job.assert_called_once()
