"""Tests for P2 fixes: logging migration (issue 15), exception handling (issue 16),
and ACR token refresh (issue 13).

These are unit tests that verify behaviour via mocks — no Azure credentials
or live clusters required.
"""

import logging
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from ascend.utils.errors import ImageBuildError, ImageBuildTimeout


# ---------------------------------------------------------------------------
# Issue 15 — Logging migration
# ---------------------------------------------------------------------------


class TestNullHandlerSetup:
    """Verify the library installs a NullHandler on the root 'ascend' logger."""

    def test_ascend_logger_has_null_handler(self):
        root = logging.getLogger("ascend")
        handler_types = [type(h) for h in root.handlers]
        assert logging.NullHandler in handler_types


class TestLibraryModulesUseLogging:
    """Ensure key library modules declare a module-level logger."""

    @pytest.mark.parametrize(
        "module_path",
        [
            "ascend.runtime.executor",
            "ascend.runtime.streaming",
            "ascend.cloud.kubernetes.jobs",
            "ascend.cloud.azure.registry",
            "ascend.cloud.azure.storage",
            "ascend.cloud.azure.image_builder",
            "ascend.cloud.kubernetes.kaniko",
        ],
    )
    def test_module_has_logger(self, module_path):
        import importlib

        mod = importlib.import_module(module_path)
        assert hasattr(mod, "logger"), f"{module_path} missing module-level 'logger'"
        assert isinstance(mod.logger, logging.Logger)


class TestNoPrintInLibrary:
    """Verify that library modules (non-CLI) no longer use bare print()."""

    @pytest.mark.parametrize(
        "module_path",
        [
            "ascend/runtime/executor.py",
            "ascend/cloud/azure/image_builder.py",
            "ascend/cloud/kubernetes/jobs.py",
        ],
    )
    def test_no_bare_print(self, module_path):
        import pathlib
        import re

        source = (pathlib.Path(__file__).resolve().parents[1] / module_path).read_text()
        # Match "print(" at the start of a statement (possibly indented)
        # but exclude comments and strings.
        lines = source.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if re.match(r"print\(", stripped):
                pytest.fail(
                    f"{module_path}:{i} still contains a bare print() call: "
                    f"{stripped[:80]}"
                )


# ---------------------------------------------------------------------------
# Issue 16 — Exception handling
# ---------------------------------------------------------------------------


class TestAzureContainerRegistryNormalization:
    """Verify that bare ACR names are normalized to FQDNs."""

    def test_bare_name_gets_azurecr_suffix(self):
        from ascend.cloud.azure.registry import AzureContainerRegistry

        reg = AzureContainerRegistry(login_server="myacr", credential=MagicMock())
        assert reg._login_server == "myacr.azurecr.io"
        assert reg.registry_url() == "myacr.azurecr.io"

    def test_fqdn_unchanged(self):
        from ascend.cloud.azure.registry import AzureContainerRegistry

        reg = AzureContainerRegistry(login_server="myacr.azurecr.io", credential=MagicMock())
        assert reg._login_server == "myacr.azurecr.io"

    def test_custom_domain_unchanged(self):
        from ascend.cloud.azure.registry import AzureContainerRegistry

        reg = AzureContainerRegistry(login_server="registry.example.com", credential=MagicMock())
        assert reg._login_server == "registry.example.com"

    def test_strips_whitespace(self):
        from ascend.cloud.azure.registry import AzureContainerRegistry

        reg = AzureContainerRegistry(login_server="  myacr  ", credential=MagicMock())
        assert reg._login_server == "myacr.azurecr.io"


class TestRegistryExceptionHandling:
    """Verify that AzureContainerRegistry logs warnings for unexpected errors
    rather than silently swallowing them."""

    def _make_registry(self):
        from ascend.cloud.azure.registry import AzureContainerRegistry

        reg = AzureContainerRegistry.__new__(AzureContainerRegistry)
        reg._login_server = "test.azurecr.io"
        reg._credential = MagicMock()
        reg._acr_client = MagicMock()
        return reg

    def test_image_exists_logs_unexpected_error(self, caplog):
        reg = self._make_registry()
        reg._acr_client.get_manifest_properties.side_effect = RuntimeError("boom")

        with caplog.at_level(logging.WARNING, logger="ascend.cloud.azure.registry"):
            result = reg.image_exists("repo", "tag")

        assert result is False
        assert "Unexpected error checking image" in caplog.text
        assert "boom" in caplog.text

    def test_image_exists_silent_on_not_found(self, caplog):
        """ResourceNotFoundError with status_code=404 should NOT log a warning."""
        reg = self._make_registry()

        # Simulate a ResourceNotFoundError-like exception
        class FakeNotFoundError(Exception):
            pass

        FakeNotFoundError.__name__ = "ResourceNotFoundError"
        err = FakeNotFoundError("not found")
        err.status_code = 404
        reg._acr_client.get_manifest_properties.side_effect = err

        with caplog.at_level(logging.WARNING, logger="ascend.cloud.azure.registry"):
            result = reg.image_exists("repo", "tag")

        assert result is False
        assert caplog.text == ""  # No warning for genuine 404

    def test_delete_tag_logs_unexpected_error(self, caplog):
        reg = self._make_registry()
        reg._acr_client.delete_tag.side_effect = RuntimeError("auth fail")

        with caplog.at_level(logging.WARNING, logger="ascend.cloud.azure.registry"):
            result = reg.delete_tag("repo", "tag")

        assert result is False
        assert "Failed to delete tag" in caplog.text

    def test_delete_tag_silent_on_not_found(self, caplog):
        reg = self._make_registry()

        class FakeNotFoundError(Exception):
            pass

        FakeNotFoundError.__name__ = "ResourceNotFoundError"
        reg._acr_client.delete_tag.side_effect = FakeNotFoundError("gone")

        with caplog.at_level(logging.WARNING, logger="ascend.cloud.azure.registry"):
            result = reg.delete_tag("repo", "tag")

        assert result is False
        assert caplog.text == ""


class TestStorageExceptionHandling:
    """Verify ensure_container logs at DEBUG instead of silently swallowing."""

    def test_ensure_container_logs_non_file_exists_error(self, caplog):
        from ascend.cloud.azure.storage import AzureCloudStorage

        storage = AzureCloudStorage.__new__(AzureCloudStorage)
        storage._fs = MagicMock()
        storage._fs.mkdir.side_effect = PermissionError("denied")

        with caplog.at_level(logging.DEBUG, logger="ascend.cloud.azure.storage"):
            storage.ensure_container("test-container")

        assert "Container creation" in caplog.text
        assert "may already exist" in caplog.text

    def test_ensure_container_silent_on_file_exists(self, caplog):
        from ascend.cloud.azure.storage import AzureCloudStorage

        storage = AzureCloudStorage.__new__(AzureCloudStorage)
        storage._fs = MagicMock()
        storage._fs.mkdir.side_effect = FileExistsError()

        with caplog.at_level(logging.DEBUG, logger="ascend.cloud.azure.storage"):
            storage.ensure_container("test-container")

        assert caplog.text == ""


class TestKanikoExceptionHandling:
    """Verify kaniko get_job_status distinguishes ApiException from other errors."""

    def _make_manager(self):
        from ascend.cloud.kubernetes.kaniko import KanikoJobManager

        mgr = KanikoJobManager.__new__(KanikoJobManager)
        mgr.k8s = MagicMock()
        mgr.namespace = "test-builds"
        return mgr

    def test_get_job_status_handles_api_exception(self, caplog):
        from kubernetes.client.rest import ApiException

        mgr = self._make_manager()
        exc = ApiException(status=500, reason="Internal Server Error")
        mgr.k8s.read_namespaced_job.side_effect = exc

        with caplog.at_level(logging.WARNING, logger="ascend.cloud.kubernetes.kaniko"):
            status = mgr.get_job_status("test-job")

        assert status.status == "failed"
        assert "Internal Server Error" in status.error_message

    def test_get_job_status_raises_unexpected_error(self):
        mgr = self._make_manager()
        mgr.k8s.read_namespaced_job.side_effect = RuntimeError("unexpected")

        with pytest.raises(RuntimeError, match="unexpected"):
            mgr.get_job_status("test-job")

    def test_delete_job_logs_non_404_api_exception(self, caplog):
        from kubernetes.client.rest import ApiException

        mgr = self._make_manager()
        exc = ApiException(status=500, reason="Internal Server Error")
        mgr.k8s.delete_namespaced_job.side_effect = exc

        with caplog.at_level(logging.WARNING, logger="ascend.cloud.kubernetes.kaniko"):
            mgr.delete_job("test-job")

        assert "Failed to delete build job" in caplog.text

    def test_delete_job_silent_on_404(self, caplog):
        from kubernetes.client.rest import ApiException

        mgr = self._make_manager()
        exc = ApiException(status=404, reason="Not Found")
        mgr.k8s.delete_namespaced_job.side_effect = exc

        with caplog.at_level(logging.WARNING, logger="ascend.cloud.kubernetes.kaniko"):
            mgr.delete_job("test-job")

        # Should not log a warning for 404
        assert "Failed to delete" not in caplog.text


class TestImageBuildErrorUnification:
    """Verify that ImageBuildError and ImageBuildTimeout are defined in errors.py
    and re-exported from kaniko.py."""

    def test_image_build_error_from_errors(self):
        from ascend.utils.errors import ImageBuildError

        err = ImageBuildError("build failed", logs="line1\nline2")
        assert err.logs == "line1\nline2"
        assert str(err) == "build failed"

    def test_image_build_error_from_kaniko(self):
        from ascend.cloud.kubernetes.kaniko import ImageBuildError as KanikoIBE
        from ascend.utils.errors import ImageBuildError as ErrorsIBE

        assert KanikoIBE is ErrorsIBE

    def test_image_build_timeout_from_kaniko(self):
        from ascend.cloud.kubernetes.kaniko import ImageBuildTimeout as KanikoBT
        from ascend.utils.errors import ImageBuildTimeout as ErrorsBT

        assert KanikoBT is ErrorsBT

    def test_image_build_timeout_is_timeout_error(self):
        assert issubclass(ImageBuildTimeout, TimeoutError)


class TestExecutorImageBuildWarning:
    """Verify image build failure is logged as warning, not silently printed."""

    def test_image_build_failure_logged(self, caplog):
        from ascend.runtime.executor import RemoteExecutor

        executor = RemoteExecutor.__new__(RemoteExecutor)
        executor.config = MagicMock()
        executor.config.node_type = None

        mock_builder = MagicMock()
        mock_builder.get_or_build_image.side_effect = RuntimeError("build boom")
        executor.backend = MagicMock()
        executor.backend.image_builder = mock_builder

        with caplog.at_level(logging.WARNING, logger="ascend.runtime.executor"):
            result = executor._get_or_build_image(["pandas==2.0"])

        assert result is None
        assert "Image building failed" in caplog.text
        assert "build boom" in caplog.text


# ---------------------------------------------------------------------------
# Issue 13 — ACR token refresh
# ---------------------------------------------------------------------------


class TestImageBuilderTokenRefresh:
    """Verify AzureImageBuilder refreshes registry credentials before builds."""

    def _make_builder(self, credential=None, login_server=None):
        from ascend.cloud.azure.image_builder import AzureImageBuilder

        builder = AzureImageBuilder.__new__(AzureImageBuilder)
        builder._registry = MagicMock()
        builder._registry.registry_url.return_value = "test.azurecr.io"
        builder.namespace = "ascend-builds"
        builder._k8s_client = MagicMock()
        builder._kaniko_manager = MagicMock()
        builder._credential = credential
        builder._login_server = login_server
        return builder

    @patch("ascend.cloud.azure.image_builder.AzureImageBuilder._refresh_registry_credentials")
    def test_build_image_calls_refresh(self, mock_refresh):
        from ascend.dependencies.analyzer import DependencySet

        builder = self._make_builder(
            credential=MagicMock(), login_server="test.azurecr.io",
        )

        dep_set = DependencySet(
            explicit_requirements=["pandas==2.0"],
            python_version="3.11",
            use_gpu=False,
        )

        # Make Kaniko return a completed status
        from ascend.cloud.kubernetes.kaniko import ImageBuildStatus

        builder._kaniko_manager.create_build_job.return_value = "build-job-1"
        builder._kaniko_manager.get_job_status.return_value = ImageBuildStatus(
            job_id="build-job-1", status="completed",
        )

        builder.build_image(dep_set, timeout_seconds=60)

        mock_refresh.assert_called_once()

    @patch(
        "ascend.cloud.azure.infrastructure.ensure_registry_credentials_secret"
    )
    def test_refresh_calls_ensure_with_quiet(self, mock_ensure):
        builder = self._make_builder(
            credential=MagicMock(), login_server="test.azurecr.io",
        )

        builder._refresh_registry_credentials()

        mock_ensure.assert_called_once_with(
            "test.azurecr.io",
            builder._credential,
            namespace="ascend-builds",
            quiet=True,
        )

    def test_refresh_skipped_without_credential(self, caplog):
        builder = self._make_builder(credential=None, login_server=None)

        with caplog.at_level(logging.DEBUG, logger="ascend.cloud.azure.image_builder"):
            builder._refresh_registry_credentials()

        assert "Skipping registry credential refresh" in caplog.text

    @patch(
        "ascend.cloud.azure.infrastructure.ensure_registry_credentials_secret",
        side_effect=RuntimeError("k8s unavailable"),
    )
    def test_refresh_failure_logged_as_warning(self, mock_ensure, caplog):
        builder = self._make_builder(
            credential=MagicMock(), login_server="test.azurecr.io",
        )

        with caplog.at_level(logging.WARNING, logger="ascend.cloud.azure.image_builder"):
            builder._refresh_registry_credentials()

        assert "Failed to refresh registry credentials" in caplog.text
        assert "k8s unavailable" in caplog.text


class TestBackendPassesCredential:
    """Verify the AzureImageBuilder constructor accepts credential and login_server."""

    def test_image_builder_constructor_accepts_credential(self):
        from ascend.cloud.azure.image_builder import AzureImageBuilder

        cred = MagicMock()
        registry = MagicMock()
        registry.registry_url.return_value = "test.azurecr.io"

        builder = AzureImageBuilder(
            registry=registry,
            namespace="ascend-builds",
            credential=cred,
            login_server="test.azurecr.io",
        )

        assert builder._credential is cred
        assert builder._login_server == "test.azurecr.io"

    def test_image_builder_constructor_defaults_none(self):
        from ascend.cloud.azure.image_builder import AzureImageBuilder

        builder = AzureImageBuilder(
            registry=MagicMock(),
            namespace="ascend-builds",
        )

        assert builder._credential is None
        assert builder._login_server is None


class TestEnsureRegistryCredentialsQuietParam:
    """Verify the quiet parameter suppresses console output."""

    def test_quiet_parameter_accepted(self):
        """Smoke test: the function signature accepts quiet=True."""
        import inspect
        from ascend.cloud.azure.infrastructure import (
            ensure_registry_credentials_secret,
        )

        sig = inspect.signature(ensure_registry_credentials_secret)
        assert "quiet" in sig.parameters
        assert sig.parameters["quiet"].default is False
