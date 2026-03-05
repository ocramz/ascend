"""
Integration tests for the Kaniko image build pipeline.

Exercises the full lifecycle: build a custom image with Kaniko, push it
to ACR, verify it exists in the registry, then launch a K8s Job that
pulls and runs the custom image — proving the entire chain works end-to-end.

Components exercised
--------------------
- ``AzureImageBuilder.build_image`` (Dockerfile generation, Kaniko
  orchestration, build polling)
- ``KanikoJobManager.create_build_job`` / ``get_job_status``
- ``ensure_registry_credentials_secret`` (ACR push auth)
- ``AzureContainerRegistry.image_exists`` / ``delete_tag``
- ``AzureComputeBackend.create_job`` with a custom image URI
- ``AzureCloudStorage.upload_package`` / ``download_result``
- ``wait_for_completion`` (K8s job polling)
- ``runner.py`` execution inside the custom image
"""

from __future__ import annotations

import logging
import os
import sys
import time

import cloudpickle
import pytest

from ascend.storage.metadata import create_job_metadata, update_metadata_status
from ascend.storage.paths import get_job_base_path, get_metadata_path
from ascend.utils.job_ids import generate_job_id

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.integration, pytest.mark.slow]

# ---------------------------------------------------------------------------
# Marker package: lightweight, pure-Python, fast to install.
# Used to prove that the Kaniko-built image contains user requirements.
# ---------------------------------------------------------------------------
_MARKER_PACKAGE = "six"


# ---------------------------------------------------------------------------
# Session-scoped fixture: build a custom image once, share across tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def kaniko_test_image(ensure_infrastructure, real_aks_cluster):
    """Build a fresh custom image with a marker package via Kaniko.

    The fixture force-rebuilds so every run exercises the full Kaniko
    pipeline (Dockerfile generation → build job → ACR push).

    Yields a dict with:
    - ``image_uri``: full image URI (``<acr>/ascend-runtime:<tag>``)
    - ``image_tag``: the tag portion (``user-<hash>``)
    - ``registry``:  :class:`AzureContainerRegistry` instance
    - ``builder``:   :class:`AzureImageBuilder` instance
    - ``dep_set``:   the :class:`DependencySet` used for the build

    On teardown the ACR tag is deleted.
    """
    from azure.identity import DefaultAzureCredential

    from ascend.cloud.azure.image_builder import AzureImageBuilder
    from ascend.cloud.azure.registry import AzureContainerRegistry
    from ascend.dependencies.analyzer import create_dependency_set

    credential = DefaultAzureCredential()
    login_server = ensure_infrastructure.container_registry_login_server

    registry = AzureContainerRegistry(login_server, credential)
    builder = AzureImageBuilder(
        registry=registry,
        namespace="ascend-builds",
        credential=credential,
        login_server=login_server,
    )

    current_py = f"{sys.version_info.major}.{sys.version_info.minor}"
    dep_set = create_dependency_set(
        requirements=[_MARKER_PACKAGE],
        use_gpu=False,
        python_version=current_py,
    )
    image_tag = builder._generate_image_tag(dep_set)

    logger.info("Building Kaniko test image (tag=%s) …", image_tag)
    image_uri = builder.get_or_build_image(
        dep_set, timeout_seconds=600, force_rebuild=True,
    )
    logger.info("Kaniko build complete: %s", image_uri)

    yield {
        "image_uri": image_uri,
        "image_tag": image_tag,
        "registry": registry,
        "builder": builder,
        "dep_set": dep_set,
    }

    # ---- teardown: delete the test tag from ACR ----
    try:
        registry.delete_tag("ascend-runtime", image_tag)
        logger.info("Deleted ACR tag: ascend-runtime:%s", image_tag)
    except Exception:
        logger.warning("Failed to delete ACR test tag", exc_info=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestKanikoBuildPushPull:
    """Full Kaniko build → ACR push → K8s pull integration tests."""

    # -- 1. Verify the image lands in ACR --------------------------------

    def test_kaniko_build_pushes_to_acr(self, kaniko_test_image):
        """After a Kaniko build the image tag must exist in ACR."""
        registry = kaniko_test_image["registry"]
        tag = kaniko_test_image["image_tag"]
        uri = kaniko_test_image["image_uri"]
        login_server = registry.registry_url()

        assert registry.image_exists("ascend-runtime", tag), (
            f"Image ascend-runtime:{tag} not found in ACR after Kaniko build"
        )
        assert uri.startswith(f"{login_server}/ascend-runtime:"), (
            f"Image URI '{uri}' does not match expected format"
        )
        assert tag in uri

    # -- 2. Verify the image is pullable and functional ------------------

    def test_kaniko_built_image_is_pullable(
        self,
        kaniko_test_image,
        ensure_infrastructure,
        real_aks_cluster,
        ensure_namespace,
    ):
        """Launch a K8s Job with the Kaniko-built image.

        The job imports the marker package (``six``) and returns its
        ``__version__``.  Success proves that:
        - The image can be pulled from ACR by the kubelet.
        - The Kaniko-installed requirements are present in the image.
        - ``runner.py`` executes correctly inside the custom image.
        """
        from azure.identity import DefaultAzureCredential

        from ascend.cloud.azure.compute import AzureComputeBackend
        from ascend.cloud.azure.storage import AzureCloudStorage

        credential = DefaultAzureCredential()
        storage_account = ensure_infrastructure.storage_account_name
        managed_identity_client_id = (
            real_aks_cluster.get("managed_identity_client_id")
            or getattr(ensure_infrastructure, "managed_identity_client_id", None)
        )
        registry_url = ensure_infrastructure.container_registry_login_server
        username = os.getenv("AZURE_USERNAME", "integration-test")
        namespace = f"ascend-users-{username}"
        project = "default"
        image_uri = kaniko_test_image["image_uri"]

        # --- backends ---
        storage = AzureCloudStorage(account_name=storage_account, credential=credential)
        storage.ensure_container("ascend-data")
        compute = AzureComputeBackend(
            storage_account_name=storage_account,
            managed_identity_client_id=managed_identity_client_id,
        )

        # --- build + upload package ---
        job_id = generate_job_id(
            user=username,
            project=project,
            dep_hash=kaniko_test_image["image_tag"].replace("user-", ""),
            function_name="kaniko_pull_test",
        )
        job_name = f"ascend-{job_id}"

        def _import_marker():
            import six  # noqa: F811
            return six.__version__

        package = {
            "function": cloudpickle.dumps(_import_marker),
            "args": cloudpickle.dumps(((), {})),
            "requirements": [_MARKER_PACKAGE],
            "job_id": job_id,
            "function_name": "kaniko_pull_test",
            "project": project,
            "dep_hash": kaniko_test_image["image_tag"].replace("user-", ""),
        }

        metadata = create_job_metadata(
            job_id=job_id,
            user=username,
            project=project,
            function_name="kaniko_pull_test",
            config={"cpu": "1", "memory": "2Gi", "timeout": 300, "node_type": None},
            dep_hash=kaniko_test_image["image_tag"].replace("user-", ""),
        )
        metadata_path = get_metadata_path(project, username, job_id)
        storage.write(metadata_path, metadata.to_json().encode("utf-8"))

        package_uri = storage.upload_package(
            username=username,
            job_id=job_id,
            package=package,
            project=project,
        )

        try:
            # --- create + wait for K8s job ---
            compute.create_job(
                namespace=namespace,
                job_id=job_id,
                package_uri=package_uri,
                config={"cpu": "1", "memory": "2Gi", "timeout": 300, "node_type": None},
                registry=registry_url,
                custom_image_uri=image_uri,
            )

            metadata = update_metadata_status(metadata, status="running")
            storage.write(metadata_path, metadata.to_json().encode("utf-8"))

            success = compute.wait_for_completion(
                namespace=namespace,
                job_name=job_name,
                timeout=300,
            )
            assert success, "K8s job using Kaniko-built image did not succeed"

            # --- download + verify result ---
            result = storage.download_result(
                username=username,
                job_id=job_id,
                project=project,
            )
            assert isinstance(result, str), f"Expected str version, got {type(result)}"
            assert len(result) > 0, "six.__version__ should be a non-empty string"
            logger.info("Kaniko pull test returned six version: %s", result)

        finally:
            # --- cleanup: K8s job + blobs ---
            try:
                compute.delete_job(job_name, namespace)
            except Exception:
                pass
            try:
                fs = storage.get_filesystem()
                base_uri = storage.storage_uri(
                    get_job_base_path(project, username, job_id)
                )
                fs.rm(base_uri, recursive=True)
            except Exception:
                pass

    # -- 3. Verify force-rebuild replaces the image ----------------------

    def test_force_rebuild_replaces_image(self, kaniko_test_image):
        """A second ``force_rebuild=True`` call must succeed and leave
        the image present in ACR (delete + rebuild cycle).
        """
        builder = kaniko_test_image["builder"]
        registry = kaniko_test_image["registry"]
        dep_set = kaniko_test_image["dep_set"]
        tag = kaniko_test_image["image_tag"]

        logger.info("Force-rebuilding image (tag=%s) …", tag)
        new_uri = builder.get_or_build_image(
            dep_set, timeout_seconds=600, force_rebuild=True,
        )

        assert registry.image_exists("ascend-runtime", tag), (
            "Image should exist in ACR after force rebuild"
        )
        assert tag in new_uri

    # -- 4. Verify cache-hit fast path -----------------------------------

    def test_cached_image_skips_build(self, kaniko_test_image):
        """When the image already exists in ACR, ``get_or_build_image``
        should return immediately without submitting a new Kaniko build.
        """
        builder = kaniko_test_image["builder"]
        dep_set = kaniko_test_image["dep_set"]
        expected_uri = kaniko_test_image["image_uri"]

        start = time.monotonic()
        uri = builder.get_or_build_image(
            dep_set, timeout_seconds=600, force_rebuild=False,
        )
        elapsed = time.monotonic() - start

        assert uri == expected_uri, (
            f"Cache-hit URI mismatch: expected {expected_uri}, got {uri}"
        )
        # A real Kaniko build takes >60 s; the cache path should be <5 s.
        assert elapsed < 30, (
            f"Cache-hit path took {elapsed:.1f}s — expected <30 s (possible rebuild?)"
        )
