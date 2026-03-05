"""Integration test fixtures for Ascend.

All integration tests run against real Azure/AKS infrastructure.
Authentication is handled by DefaultAzureCredential, which resolves
automatically based on the runtime environment:

- **Local with .env**: Copy ``.env.example`` to ``.env`` and fill in values.
  The session-scoped ``_load_dotenv_credentials`` fixture (in ``tests/conftest.py``)
  loads these automatically before any test runs.
- **CI**: via ``AZURE_CLIENT_ID`` / ``AZURE_TENANT_ID`` / ``AZURE_CLIENT_SECRET``
  env vars injected by the GitHub Actions workflow.
- **Local fallback**: via Azure CLI credentials (``az login``).
"""

import logging
import os
from pathlib import Path

import pytest
import yaml

# Configure logging for integration tests
logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def real_aks_cluster():
    """
    Configure real AKS cluster access for integration tests.

    Authentication uses DefaultAzureCredential, which automatically picks up:
    - Environment variables (AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_CLIENT_SECRET) in CI
    - Azure CLI credentials (az login) for local development

    Requires environment variables:
    - AZURE_SUBSCRIPTION_ID
    - AZURE_RESOURCE_GROUP
    - AZURE_AKS_CLUSTER_NAME
    """
    required_vars = [
        "AZURE_SUBSCRIPTION_ID",
        "AZURE_RESOURCE_GROUP",
        "AZURE_AKS_CLUSTER_NAME",
    ]

    missing = [v for v in required_vars if not os.getenv(v)]
    if missing:
        pytest.skip(f"Missing required environment variables: {missing}")

    subscription_id = os.environ["AZURE_SUBSCRIPTION_ID"]
    resource_group = os.environ["AZURE_RESOURCE_GROUP"]
    cluster_name = os.environ["AZURE_AKS_CLUSTER_NAME"]

    from azure.identity import DefaultAzureCredential
    from azure.mgmt.containerservice import ContainerServiceClient

    credential = DefaultAzureCredential()
    client = ContainerServiceClient(credential, subscription_id)

    try:
        cluster = client.managed_clusters.get(resource_group, cluster_name)
        assert cluster.provisioning_state == "Succeeded"
    except Exception as e:
        pytest.skip(f"AKS cluster not accessible: {e}")

    # Extract kubelet managed identity client_id for pod auth
    kubelet_identity = (getattr(cluster, "identity_profile", None) or {}).get("kubeletidentity")
    kubelet_client_id = getattr(kubelet_identity, "client_id", None) if kubelet_identity else None

    yield {
        "subscription_id": subscription_id,
        "resource_group": resource_group,
        "cluster_name": cluster_name,
        "managed_identity_client_id": kubelet_client_id,
        "credential": credential,
        "aks_client": client,
    }


@pytest.fixture(scope="session")
def ensure_kubeconfig(real_aks_cluster):
    """Programmatically fetch AKS credentials and write ``~/.kube/config``.

    Uses the Azure SDK (``managed_clusters.list_cluster_user_credentials``)
    so that a manual ``az aks get-credentials`` step is never required.

    Writes directly to ``~/.kube/config`` because the ``kubernetes`` Python
    client resolves ``KUBE_CONFIG_DEFAULT_LOCATION`` at **import time**.
    Setting the ``KUBECONFIG`` env var in a fixture (after import) has no
    effect on bare ``load_kube_config()`` calls in production code.

    Any pre-existing ``~/.kube/config`` is backed up and restored on teardown.

    Fails the entire test session if the credentials cannot be obtained.
    """
    client = real_aks_cluster["aks_client"]
    resource_group = real_aks_cluster["resource_group"]
    cluster_name = real_aks_cluster["cluster_name"]

    try:
        cred_result = client.managed_clusters.list_cluster_user_credentials(
            resource_group, cluster_name
        )
        kubeconfig_bytes = cred_result.kubeconfigs[0].value
    except Exception as e:
        pytest.fail(
            f"Failed to fetch AKS kubeconfig for cluster "
            f"'{cluster_name}' in resource group '{resource_group}': {e}\n"
            f"Ensure the service principal has 'Azure Kubernetes Service "
            f"Cluster User Role' on the cluster."
        )

    kube_dir = Path.home() / ".kube"
    kube_dir.mkdir(parents=True, exist_ok=True)
    kubeconfig_path = kube_dir / "config"

    # Back up any existing kubeconfig
    backup_path = kube_dir / "config.ascend-test-backup"
    had_existing = kubeconfig_path.exists()
    if had_existing:
        kubeconfig_path.rename(backup_path)
        logger.info("Backed up existing kubeconfig to %s", backup_path)

    kubeconfig_path.write_bytes(kubeconfig_bytes)
    logger.info("Wrote kubeconfig to %s", kubeconfig_path)

    yield str(kubeconfig_path)

    # Restore original kubeconfig on teardown
    if had_existing and backup_path.exists():
        backup_path.rename(kubeconfig_path)
        logger.info("Restored original kubeconfig from backup")
    else:
        kubeconfig_path.unlink(missing_ok=True)
        logger.info("Removed test kubeconfig")


@pytest.fixture(scope="session")
def ensure_infrastructure(real_aks_cluster, ensure_kubeconfig):
    """Idempotently provision Azure infrastructure for integration tests.

    Creates (or verifies) the storage account, blob container, and container
    registry required by the test suite.  Explicit names can be supplied via
    ``AZURE_STORAGE_ACCOUNT`` / ``AZURE_CONTAINER_REGISTRY`` environment
    variables; otherwise deterministic names are generated from the resource
    group.

    The service principal **must** have Contributor (or Owner) role on the
    resource group.  If credentials are insufficient, the fixture raises
    an exception and the tests fail.

    The infrastructure is **not** torn down after the session — it is
    long-lived and shared across CI runs.
    """
    from azure.identity import DefaultAzureCredential

    from ascend.cloud.azure.cli import get_resource_group_location
    from ascend.cloud.azure.infrastructure import ensure_all_infrastructure

    subscription_id = real_aks_cluster["subscription_id"]
    resource_group = real_aks_cluster["resource_group"]

    credential = DefaultAzureCredential()
    location = get_resource_group_location(
        credential, subscription_id, resource_group
    )

    result = ensure_all_infrastructure(
        credential=credential,
        subscription_id=subscription_id,
        resource_group=resource_group,
        location=location,
        cluster_name=real_aks_cluster["cluster_name"],
        storage_account_name=os.getenv("AZURE_STORAGE_ACCOUNT") or None,
        registry_name=os.getenv("AZURE_CONTAINER_REGISTRY") or None,
    )

    yield result


@pytest.fixture(scope="session")
def ensure_namespace(real_aks_cluster, ensure_kubeconfig):
    """Ensure the Kubernetes namespace and RBAC for the test user exist.

    Calls ``provision_user()`` which is idempotent (handles 409 Conflict).
    Kubeconfig is set up automatically by the ``ensure_kubeconfig`` fixture.
    """
    from ascend.cli.admin import provision_user

    username = os.getenv("AZURE_USERNAME", "integration-test")
    provision_user(
        username=username,
        cluster_name=real_aks_cluster["cluster_name"],
        resource_group=real_aks_cluster["resource_group"],
    )

    yield username


@pytest.fixture(scope="session", autouse=True)
def _ascend_yaml_from_env(real_aks_cluster, ensure_infrastructure, ensure_namespace, ensure_kubeconfig, tmp_path_factory):
    """Synthesize a temporary ``.ascend.yaml`` from environment variables.

    The ``@ascend`` decorator calls ``load_config()`` which searches upward
    from cwd for ``.ascend.yaml``.  In CI and local-dev there is no
    checked-in config file, so we generate one into a temp directory and
    ``chdir`` there for the duration of the session.

    Storage account and container registry names are taken from the
    ``ensure_infrastructure`` fixture (which creates them if needed).
    Explicit env-var overrides (``AZURE_STORAGE_ACCOUNT``,
    ``AZURE_CONTAINER_REGISTRY``) are respected by that fixture.

    The fixture restores the original working directory on teardown.
    """
    username = os.getenv("AZURE_USERNAME", "integration-test")
    resource_group = real_aks_cluster["resource_group"]
    cluster_name = real_aks_cluster["cluster_name"]
    storage_account = ensure_infrastructure.storage_account_name
    container_registry = ensure_infrastructure.container_registry_login_server
    managed_identity_client_id = (
        real_aks_cluster.get("managed_identity_client_id")
        or getattr(ensure_infrastructure, "managed_identity_client_id", None)
    )

    config = {
        "username": username,
        "cluster_name": cluster_name,
        "resource_group": resource_group,
        "namespace": f"ascend-users-{username}",
        "storage_account": storage_account,
        "container_registry": container_registry,
        "cloud_provider": "azure",
        "auto_build_images": True,
    }
    if managed_identity_client_id:
        config["managed_identity_client_id"] = managed_identity_client_id

    config_dir = tmp_path_factory.mktemp("ascend_config")
    config_path = config_dir / ".ascend.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))

    # Create an empty requirements.txt so the decorator's auto-detection
    # succeeds (the temp dir has no pyproject.toml or other project files).
    (config_dir / "requirements.txt").write_text("")

    original_cwd = Path.cwd()
    os.chdir(config_dir)
    logger.info("Created temporary .ascend.yaml at %s", config_path)

    yield config

    os.chdir(original_cwd)


@pytest.fixture(scope="session")
def fresh_runtime_image(request, ensure_infrastructure, real_aks_cluster):
    """Force-rebuild the runtime image, busting both ACR and Kaniko caches.

    Returns the full image URI of the freshly built image.

    When ``--rebuild-images`` is passed on the command line:
    1. Deletes the existing image tag from ACR (if present).
    2. Submits a Kaniko build job with ``--cache=false``.
    3. Waits for the build to complete (up to 10 minutes).
    4. Yields the resulting image URI.

    Without the flag, the normal cache-aware path is used.
    """
    from azure.identity import DefaultAzureCredential

    from ascend.cloud.azure.image_builder import AzureImageBuilder
    from ascend.cloud.azure.registry import AzureContainerRegistry
    from ascend.dependencies.analyzer import create_dependency_set

    force = request.config.getoption("--rebuild-images", default=False)

    credential = DefaultAzureCredential()
    login_server = ensure_infrastructure.container_registry_login_server

    registry = AzureContainerRegistry(login_server, credential)
    builder = AzureImageBuilder(registry=registry, namespace="ascend-builds")

    # Build a minimal dependency set matching what the integration tests need.
    # Use the current interpreter's Python version so the image matches.
    import sys as _sys
    current_py = f"{_sys.version_info.major}.{_sys.version_info.minor}"
    dep_set = create_dependency_set(requirements=[], use_gpu=False,
                                     python_version=current_py)

    if force:
        image_uri = builder.get_or_build_image(
            dep_set, timeout_seconds=600, force_rebuild=True,
        )
    else:
        image_uri = builder.get_or_build_image(
            dep_set, timeout_seconds=600,
        )

    yield image_uri


@pytest.fixture
def debug_mode(request):
    """Enable debug mode for verbose output."""
    debug = request.config.getoption("--integration-debug", default=False)

    if debug:
        import logging

        logging.basicConfig(level=logging.DEBUG)

    return debug


@pytest.fixture
def artifact_collector(tmp_path, request):
    """Collect artifacts from failed tests."""

    artifacts = {"logs": [], "manifests": [], "errors": []}

    yield artifacts

    # On failure, save artifacts
    if hasattr(request.node, "rep_call") and request.node.rep_call.failed:
        artifact_dir = tmp_path / "artifacts" / request.node.name
        artifact_dir.mkdir(parents=True, exist_ok=True)

        for i, log in enumerate(artifacts["logs"]):
            (artifact_dir / f"log_{i}.txt").write_text(log)

        for i, manifest in enumerate(artifacts["manifests"]):
            (artifact_dir / f"manifest_{i}.yaml").write_text(manifest)
