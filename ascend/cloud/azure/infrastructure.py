"""Idempotent Azure infrastructure provisioning.

Provides functions that ensure Azure resources (storage account, blob container,
container registry) exist, creating them only when missing.  Every ``ensure_*``
function is a no-op when the resource is already present.

These functions are used by both the ``ascend admin bootstrap`` CLI command and
the integration-test fixtures so that the provisioning logic has a single source
of truth.
"""

from __future__ import annotations

import base64
import json
import logging
import pathlib
import uuid
from dataclasses import dataclass

from rich.console import Console

from ascend.utils.errors import AscendError
from ascend.utils.naming import generate_resource_names

logger = logging.getLogger(__name__)
console = Console()


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InfrastructureResult:
    """Summary of the provisioned (or pre-existing) Azure resources."""

    storage_account_name: str
    container_registry_name: str
    container_registry_login_server: str
    blob_container_name: str
    location: str
    runtime_image_uri: str = ""
    managed_identity_client_id: str = ""


# ---------------------------------------------------------------------------
# Individual ensure_* functions
# ---------------------------------------------------------------------------


def ensure_storage_account(
    credential,
    subscription_id: str,
    resource_group: str,
    location: str,
    account_name: str,
) -> str:
    """Ensure a storage account exists in *resource_group*, creating it if needed.

    Args:
        credential: Azure credential (``DefaultAzureCredential``).
        subscription_id: Azure subscription ID.
        resource_group: Target resource group (must exist).
        location: Azure region (e.g. ``eastus``).
        account_name: Desired storage account name.

    Returns:
        The storage account name.

    Raises:
        AscendError: If the name is globally taken by another subscription.
    """
    from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
    from azure.mgmt.storage import StorageManagementClient
    from azure.mgmt.storage.models import (
        Kind,
        Sku,
        SkuName,
        StorageAccountCreateParameters,
    )

    client = StorageManagementClient(credential, subscription_id)

    # Check if it already exists in our resource group
    try:
        existing = client.storage_accounts.get_properties(resource_group, account_name)
        console.print(
            f"  [yellow]→[/yellow] Storage account [bold]{existing.name}[/bold] "
            f"already exists in {resource_group}"
        )
        return existing.name
    except ResourceNotFoundError:
        pass  # Not found – fall through to creation

    # Check global name availability
    availability = client.storage_accounts.check_name_availability(
        {"name": account_name, "type": "Microsoft.Storage/storageAccounts"}
    )
    if not availability.name_available:
        raise AscendError(
            f"Storage account name '{account_name}' is not available: "
            f"{availability.reason} – {availability.message}"
        )

    console.print(f"  Creating storage account [bold]{account_name}[/bold] …")
    try:
        poller = client.storage_accounts.begin_create(
            resource_group,
            account_name,
            StorageAccountCreateParameters(
                sku=Sku(name=SkuName.STANDARD_LRS),
                kind=Kind.STORAGE_V2,
                location=location,
                tags={"managed-by": "ascend"},
            ),
        )
        result = poller.result()
    except HttpResponseError as exc:
        raise AscendError(
            f"Failed to create storage account '{account_name}': {exc.message}\n"
            "Ensure the service principal has Contributor (or Owner) role "
            f"on resource group '{resource_group}'."
        ) from exc
    console.print(
        f"  [green]✓[/green] Storage account [bold]{result.name}[/bold] created"
    )
    return result.name


def ensure_blob_container(
    credential,
    subscription_id: str,
    resource_group: str,
    account_name: str,
    container_name: str = "ascend-data",
) -> str:
    """Ensure a blob container exists inside *account_name*.

    Args:
        credential: Azure credential.
        subscription_id: Azure subscription ID.
        resource_group: Resource group containing the storage account.
        account_name: Storage account name.
        container_name: Blob container name (default ``ascend-data``).

    Returns:
        The container name.
    """
    from azure.core.exceptions import (
        HttpResponseError,
        ResourceExistsError,
        ResourceNotFoundError,
    )
    from azure.mgmt.storage import StorageManagementClient

    client = StorageManagementClient(credential, subscription_id)

    try:
        existing = client.blob_containers.get(
            resource_group, account_name, container_name
        )
        console.print(
            f"  [yellow]→[/yellow] Blob container [bold]{existing.name}[/bold] "
            f"already exists"
        )
        return existing.name
    except ResourceNotFoundError:
        pass  # Not found – create below

    try:
        result = client.blob_containers.create(
            resource_group,
            account_name,
            container_name,
            {},
        )
        console.print(
            f"  [green]✓[/green] Blob container [bold]{result.name}[/bold] created"
        )
        return result.name
    except ResourceExistsError:
        console.print(
            f"  [yellow]→[/yellow] Blob container [bold]{container_name}[/bold] "
            f"already exists"
        )
        return container_name
    except HttpResponseError as exc:
        raise AscendError(
            f"Failed to create blob container '{container_name}': {exc.message}\n"
            "Ensure the service principal has Contributor (or Owner) role "
            f"on resource group '{resource_group}'."
        ) from exc


def ensure_container_registry(
    credential,
    subscription_id: str,
    resource_group: str,
    location: str,
    registry_name: str,
) -> tuple[str, str]:
    """Ensure a container registry exists, creating it if needed.

    Args:
        credential: Azure credential.
        subscription_id: Azure subscription ID.
        resource_group: Target resource group.
        location: Azure region.
        registry_name: Desired registry name (alphanumeric only).

    Returns:
        ``(registry_name, login_server)`` tuple.

    Raises:
        AscendError: If the registry name is globally taken.
    """
    from azure.mgmt.containerregistry import ContainerRegistryManagementClient
    from azure.mgmt.containerregistry.models import Registry, Sku, SkuName

    from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

    client = ContainerRegistryManagementClient(credential, subscription_id)

    # Check if it already exists
    try:
        existing = client.registries.get(resource_group, registry_name)
        console.print(
            f"  [yellow]→[/yellow] Container registry [bold]{existing.name}[/bold] "
            f"already exists ({existing.login_server})"
        )
        return existing.name, existing.login_server
    except ResourceNotFoundError:
        pass  # Not found – fall through to creation

    # Check name availability
    availability = client.registries.check_name_availability(
        {"name": registry_name, "type": "Microsoft.ContainerRegistry/registries"}
    )
    if not availability.name_available:
        raise AscendError(
            f"Container registry name '{registry_name}' is not available: "
            f"{availability.message}"
        )

    console.print(f"  Creating container registry [bold]{registry_name}[/bold] …")
    try:
        poller = client.registries.begin_create(
            resource_group,
            registry_name,
            Registry(
                location=location,
                sku=Sku(name=SkuName.BASIC),
                admin_user_enabled=False,
                tags={"managed-by": "ascend"},
            ),
        )
        result = poller.result()
    except HttpResponseError as exc:
        raise AscendError(
            f"Failed to create container registry '{registry_name}': {exc.message}\n"
            "Ensure the service principal has Contributor (or Owner) role "
            f"on resource group '{resource_group}'."
        ) from exc
    console.print(
        f"  [green]✓[/green] Container registry [bold]{result.name}[/bold] "
        f"created ({result.login_server})"
    )
    return result.name, result.login_server


# ---------------------------------------------------------------------------
# Data-plane role assignment
# ---------------------------------------------------------------------------

# Well-known Azure built-in role definition ID
_STORAGE_BLOB_DATA_CONTRIBUTOR = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"


def _get_current_principal_id(credential) -> tuple[str, str]:
    """Return ``(object_id, principal_type)`` of the authenticated identity.

    Decodes the JWT access token issued for the ARM audience to extract the
    ``oid`` claim and detect whether the caller is a service principal or a
    user.
    """
    token = credential.get_token("https://management.azure.com/.default")
    # JWT is header.payload.signature – we need the payload
    payload_b64 = token.token.split(".")[1]
    # Add Base64 padding
    payload_b64 += "=" * (4 - len(payload_b64) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload_b64))
    oid = claims["oid"]
    # Service principals have an "appid" claim; users don't
    principal_type = "ServicePrincipal" if "appid" in claims else "User"
    return oid, principal_type


def ensure_storage_data_role(
    credential,
    subscription_id: str,
    resource_group: str,
    storage_account_name: str,
    principal_id: str,
    principal_type: str = "ServicePrincipal",
) -> None:
    """Assign *Storage Blob Data Contributor* role on the storage account.

    This is needed so that the caller (SP or user) can read/write blob data
    via ``DefaultAzureCredential``.  The function is idempotent: if the role
    is already assigned, it is a no-op.

    Args:
        credential: Azure credential with Owner or User Access Administrator
            role on the resource group.
        subscription_id: Azure subscription ID.
        resource_group: Resource group containing the storage account.
        storage_account_name: Name of the storage account.
        principal_id: Object ID of the identity to grant access.
        principal_type: ``"ServicePrincipal"`` or ``"User"``.

    Raises:
        AscendError: If role assignment fails (e.g. insufficient permissions).
    """
    from azure.core.exceptions import HttpResponseError
    from azure.mgmt.authorization import AuthorizationManagementClient
    from azure.mgmt.authorization.models import RoleAssignmentCreateParameters

    scope = (
        f"/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.Storage/storageAccounts/{storage_account_name}"
    )
    role_definition_id = (
        f"/subscriptions/{subscription_id}"
        f"/providers/Microsoft.Authorization/roleDefinitions"
        f"/{_STORAGE_BLOB_DATA_CONTRIBUTOR}"
    )

    auth_client = AuthorizationManagementClient(credential, subscription_id)

    # Check for an existing assignment
    for assignment in auth_client.role_assignments.list_for_scope(
        scope, filter=f"principalId eq '{principal_id}'"
    ):
        if assignment.role_definition_id.lower().endswith(
            _STORAGE_BLOB_DATA_CONTRIBUTOR
        ):
            console.print(
                "  [yellow]→[/yellow] Storage Blob Data Contributor role "
                "already assigned"
            )
            return

    # Create a deterministic assignment name so re-runs are idempotent
    assignment_name = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{scope}/{principal_id}/{_STORAGE_BLOB_DATA_CONTRIBUTOR}",
        )
    )
    console.print("  Assigning Storage Blob Data Contributor role …")
    try:
        auth_client.role_assignments.create(
            scope,
            assignment_name,
            RoleAssignmentCreateParameters(
                role_definition_id=role_definition_id,
                principal_id=principal_id,
                principal_type=principal_type,
            ),
        )
    except HttpResponseError as exc:
        if "RoleAssignmentExists" in str(exc):
            console.print(
                "  [yellow]→[/yellow] Storage Blob Data Contributor role "
                "already assigned"
            )
            return
        # Not fatal — the caller may not have Owner/UAA role, but the role
        # might have been pre-assigned by an admin.  Log a clear warning so
        # the user knows what to fix if data-plane operations fail later.
        console.print(
            "  [yellow]⚠[/yellow] Could not assign Storage Blob Data "
            "Contributor role (requires Owner or User Access Administrator).\n"
            "    If blob operations fail, assign the role manually:\n"
            f"    az role assignment create --assignee {principal_id} "
            f"--role 'Storage Blob Data Contributor' "
            f"--scope {scope}"
        )
        logger.warning(
            "Cannot assign Storage Blob Data Contributor role: %s",
            exc.message,
        )
        return
    console.print(
        "  [green]✓[/green] Storage Blob Data Contributor role assigned"
    )


# ---------------------------------------------------------------------------
# ACR ↔ AKS role assignments
# ---------------------------------------------------------------------------

# Well-known Azure built-in role definition IDs for ACR
_ACR_PULL = "7f951dda-4ed3-4680-a7ca-43fe172d538d"
_ACR_PUSH = "8311e382-0749-4cb8-b61a-304f252e45ec"


def _get_aks_kubelet_identity(
    credential, subscription_id: str, resource_group: str, cluster_name: str
) -> dict:
    """Return the kubelet managed identity details (object_id and client_id)."""
    from azure.mgmt.containerservice import ContainerServiceClient

    client = ContainerServiceClient(credential, subscription_id)
    cluster = client.managed_clusters.get(resource_group, cluster_name)
    kubelet = getattr(cluster, "identity_profile", None) or {}
    kubelet_identity = kubelet.get("kubeletidentity")
    if kubelet_identity is None:
        raise AscendError(
            f"AKS cluster '{cluster_name}' does not have a kubelet managed "
            "identity.  Ensure the cluster uses a system-assigned or "
            "user-assigned managed identity (not service principal)."
        )
    return {
        "object_id": kubelet_identity.object_id,
        "client_id": kubelet_identity.client_id,
    }


def ensure_acr_role_assignment(
    credential,
    subscription_id: str,
    resource_group: str,
    registry_name: str,
    cluster_name: str,
) -> None:
    """Grant the AKS kubelet identity *AcrPull* and *AcrPush* on the registry.

    This allows the cluster nodes to pull runtime images and Kaniko build
    jobs to push newly built images.  The function is idempotent.

    Args:
        credential: Azure credential.
        subscription_id: Azure subscription ID.
        resource_group: Resource group containing both AKS and ACR.
        registry_name: Name of the container registry.
        cluster_name: Name of the AKS cluster.
    """
    from azure.core.exceptions import HttpResponseError
    from azure.mgmt.authorization import AuthorizationManagementClient
    from azure.mgmt.authorization.models import RoleAssignmentCreateParameters

    kubelet_info = _get_aks_kubelet_identity(
        credential, subscription_id, resource_group, cluster_name
    )
    kubelet_oid = kubelet_info["object_id"]

    scope = (
        f"/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.ContainerRegistry/registries/{registry_name}"
    )

    auth_client = AuthorizationManagementClient(credential, subscription_id)

    # Collect already-assigned role definition IDs for this principal
    existing_role_ids: set[str] = set()
    for assignment in auth_client.role_assignments.list_for_scope(
        scope, filter=f"principalId eq '{kubelet_oid}'"
    ):
        existing_role_ids.add(
            assignment.role_definition_id.rsplit("/", 1)[-1].lower()
        )

    roles = [
        (_ACR_PULL, "AcrPull"),
        (_ACR_PUSH, "AcrPush"),
    ]

    for role_id, role_name in roles:
        if role_id.lower() in existing_role_ids:
            console.print(
                f"  [yellow]→[/yellow] {role_name} already assigned to "
                f"kubelet identity"
            )
            continue

        assignment_name = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{scope}/{kubelet_oid}/{role_id}",
            )
        )
        role_definition_id = (
            f"/subscriptions/{subscription_id}"
            f"/providers/Microsoft.Authorization/roleDefinitions"
            f"/{role_id}"
        )

        console.print(f"  Assigning {role_name} role …")
        try:
            auth_client.role_assignments.create(
                scope,
                assignment_name,
                RoleAssignmentCreateParameters(
                    role_definition_id=role_definition_id,
                    principal_id=kubelet_oid,
                    principal_type="ServicePrincipal",
                ),
            )
            console.print(f"  [green]✓[/green] {role_name} role assigned")
        except HttpResponseError as exc:
            if "RoleAssignmentExists" in str(exc):
                console.print(
                    f"  [yellow]→[/yellow] {role_name} already assigned"
                )
            else:
                console.print(
                    f"  [yellow]⚠[/yellow] Could not assign {role_name} role: "
                    f"{exc.message}\n"
                    f"    Assign manually:\n"
                    f"    az role assignment create --assignee {kubelet_oid} "
                    f"--role '{role_name}' --scope {scope}"
                )
                logger.warning(
                    "Cannot assign %s role: %s", role_name, exc.message
                )


# ---------------------------------------------------------------------------
# Runtime image
# ---------------------------------------------------------------------------


def _get_runtime_dockerfile() -> str:
    """Return the content of ``docker/Dockerfile.runtime``.

    Resolves the path relative to this package so it works from any cwd.
    """
    docker_dir = pathlib.Path(__file__).resolve().parents[3] / "docker"
    return (docker_dir / "Dockerfile.runtime").read_text()


def _get_runner_script() -> str:
    """Return the content of ``docker/runner.py``."""
    docker_dir = pathlib.Path(__file__).resolve().parents[3] / "docker"
    return (docker_dir / "runner.py").read_text()


def ensure_registry_credentials_secret(
    login_server: str,
    credential,
    namespace: str = "ascend-builds",
    quiet: bool = False,
) -> None:
    """Ensure a ``registry-credentials`` Docker-config secret exists.

    Creates the ``ascend-builds`` namespace and a K8s Secret of type
    ``kubernetes.io/dockerconfigjson`` containing an ACR access token
    so that Kaniko can push images.

    The function is idempotent: if the namespace or secret already exists
    it is updated in-place.

    Args:
        login_server: ACR login server (e.g. ``myacr.azurecr.io``).
        credential: Azure credential.
        namespace: Kubernetes namespace for the secret.
        quiet: If *True*, suppress ``console.print`` status messages
            (used when called automatically from the image builder).
    """
    from kubernetes import client as k8s, config as k8s_config

    k8s_config.load_kube_config()
    core_v1 = k8s.CoreV1Api()

    # 1. Ensure namespace
    ns_body = k8s.V1Namespace(
        metadata=k8s.V1ObjectMeta(
            name=namespace,
            labels={"app": "ascend", "component": "image-builder"},
        )
    )
    try:
        core_v1.create_namespace(body=ns_body)
        if not quiet:
            console.print(f"  [green]✓[/green] Namespace {namespace} created")
    except k8s.exceptions.ApiException as exc:
        if exc.status != 409:
            raise

    # 1b. Ensure the kaniko-builder ServiceAccount exists
    sa_name = "kaniko-builder"
    sa_body = k8s.V1ServiceAccount(
        metadata=k8s.V1ObjectMeta(name=sa_name, namespace=namespace),
    )
    try:
        core_v1.create_namespaced_service_account(
            namespace=namespace, body=sa_body,
        )
        if not quiet:
            console.print(
                f"  [green]✓[/green] ServiceAccount {sa_name} created in {namespace}"
            )
    except k8s.exceptions.ApiException as exc:
        if exc.status == 409:
            if not quiet:
                console.print(
                    f"  [yellow]→[/yellow] ServiceAccount {sa_name} already exists in {namespace}"
                )
        else:
            raise

    # 2. Obtain an ACR access token via the Exchange endpoint
    endpoint = login_server
    if not endpoint.startswith("https://"):
        endpoint = f"https://{endpoint}"

    # Get an ARM token, then exchange it for an ACR refresh token
    arm_token = credential.get_token("https://management.azure.com/.default").token

    import urllib.request
    import urllib.parse

    exchange_url = f"{endpoint}/oauth2/exchange"
    data = urllib.parse.urlencode({
        "grant_type": "access_token",
        "service": login_server,
        "access_token": arm_token,
    }).encode()
    req = urllib.request.Request(exchange_url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req) as resp:
        refresh_token = json.loads(resp.read())["refresh_token"]

    # 3. Build the docker config JSON
    import base64 as b64
    auth_str = b64.b64encode(
        f"00000000-0000-0000-0000-000000000000:{refresh_token}".encode()
    ).decode()
    docker_config = json.dumps({
        "auths": {
            login_server: {"auth": auth_str}
        }
    })
    docker_config_b64 = b64.b64encode(docker_config.encode()).decode()

    # 4. Create or update the secret
    secret_name = "registry-credentials"
    secret_body = k8s.V1Secret(
        metadata=k8s.V1ObjectMeta(name=secret_name, namespace=namespace),
        type="kubernetes.io/dockerconfigjson",
        data={".dockerconfigjson": docker_config_b64},
    )

    try:
        core_v1.create_namespaced_secret(namespace=namespace, body=secret_body)
        if not quiet:
            console.print(
                f"  [green]✓[/green] Secret {secret_name} created in {namespace}"
            )
    except k8s.exceptions.ApiException as exc:
        if exc.status == 409:
            # Update existing secret
            core_v1.replace_namespaced_secret(
                name=secret_name, namespace=namespace, body=secret_body
            )
            if not quiet:
                console.print(
                    f"  [yellow]→[/yellow] Secret {secret_name} updated in {namespace}"
                )
        else:
            raise


def ensure_runtime_image(
    login_server: str,
    credential,
    image_tag: str | None = None,
    timeout_seconds: int = 600,
) -> str:
    """Ensure the base runtime image exists in the registry.

    If the image ``ascend-runtime:{image_tag}`` is already present in the
    registry this is a no-op.  Otherwise a Kaniko build job is submitted
    inside the ``ascend-builds`` namespace to build and push the image.

    Args:
        login_server: ACR login server (e.g. ``myacr.azurecr.io``).
        credential: Azure credential (used to check image existence and
            create the registry-credentials secret).
        image_tag: Tag for the runtime image. Defaults to the current
            Python major.minor version (e.g. ``python3.12``).
        timeout_seconds: Maximum time to wait for the Kaniko build.

    Returns:
        Full image URI (e.g. ``myacr.azurecr.io/ascend-runtime:python3.12``).

    Raises:
        AscendError: If the build fails or times out.
    """
    import sys as _sys

    if image_tag is None:
        image_tag = f"python{_sys.version_info.major}.{_sys.version_info.minor}"
    import time as _time

    from ascend.cloud.azure.registry import AzureContainerRegistry

    image_uri = f"{login_server}/ascend-runtime:{image_tag}"
    acr = AzureContainerRegistry(login_server, credential)

    # Fast path — image already exists
    if acr.image_exists("ascend-runtime", image_tag):
        console.print(
            f"  [yellow]→[/yellow] Runtime image [bold]{image_uri}[/bold] "
            "already exists"
        )
        return image_uri

    # Ensure the registry-credentials secret is available for Kaniko
    console.print("  Preparing registry credentials for Kaniko …")
    ensure_registry_credentials_secret(login_server, credential)

    # Build the Dockerfile content with runner.py inlined via the init
    # container (Kaniko builds from /workspace).
    dockerfile_content = _get_runtime_dockerfile()
    runner_script = _get_runner_script()

    # Override the default PYTHON_VERSION ARG so the built image uses the
    # same Python version as the client, regardless of the Dockerfile default.
    target_py = f"{_sys.version_info.major}.{_sys.version_info.minor}"
    dockerfile_content = dockerfile_content.replace(
        "ARG PYTHON_VERSION=3.12",
        f"ARG PYTHON_VERSION={target_py}",
    )

    from ascend.cloud.kubernetes.kaniko import (
        ImageBuildSpec,
        KanikoJobManager,
    )
    from kubernetes import client as k8s, config as k8s_config

    k8s_config.load_kube_config()
    batch_v1 = k8s.BatchV1Api()
    kaniko = KanikoJobManager(batch_v1, namespace="ascend-builds")

    build_spec = ImageBuildSpec(
        base_image=f"python:{_sys.version_info.major}.{_sys.version_info.minor}-slim",
        requirements=[],
        system_packages=[],
        image_tag=image_tag,
        registry_url=login_server,
        dockerfile_content=dockerfile_content,
        requirements_txt_content="",
    )

    # The init container needs to also write runner.py for the COPY
    # instruction.  We override the manifest's init-container args.
    job_manifest = kaniko._generate_job_manifest(build_spec, service_account="default")

    # Patch init container to also write runner.py
    init_args = (
        "mkdir -p /workspace && "
        f"cat > /workspace/Dockerfile <<'ASCEND_EOF'\n{dockerfile_content}\nASCEND_EOF\n"
        f"cat > /workspace/runner.py <<'ASCEND_EOF'\n{runner_script}\nASCEND_EOF\n"
        "ls -la /workspace/"
    )
    job_manifest["spec"]["template"]["spec"]["initContainers"][0]["args"] = [
        init_args
    ]

    console.print(
        f"  Building runtime image [bold]{image_uri}[/bold] via Kaniko …"
    )

    # Delete any previous build job with the same name (idempotent)
    job_name = job_manifest["metadata"]["name"]
    try:
        batch_v1.delete_namespaced_job(
            name=job_name,
            namespace="ascend-builds",
            body=k8s.V1DeleteOptions(propagation_policy="Foreground"),
        )
        # Wait briefly for deletion to propagate
        _time.sleep(5)
    except k8s.exceptions.ApiException as exc:
        if exc.status != 404:
            raise

    batch_v1.create_namespaced_job(
        namespace="ascend-builds", body=job_manifest
    )

    # Poll until completion
    start = _time.time()
    poll_interval = 10
    while _time.time() - start < timeout_seconds:
        status = kaniko.get_job_status(job_name)
        if status.status == "completed":
            console.print(
                f"  [green]✓[/green] Runtime image [bold]{image_uri}[/bold] built"
            )
            return image_uri
        if status.status == "failed":
            raise AscendError(
                f"Runtime image build failed: {status.error_message}"
            )
        if status.progress:
            console.print(f"  … {status.progress}")
        _time.sleep(poll_interval)

    raise AscendError(
        f"Runtime image build timed out after {timeout_seconds}s"
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def ensure_all_infrastructure(
    credential,
    subscription_id: str,
    resource_group: str,
    location: str,
    cluster_name: str | None = None,
    storage_account_name: str | None = None,
    registry_name: str | None = None,
) -> InfrastructureResult:
    """Idempotently provision all required Azure infrastructure.

    Generates deterministic resource names from *resource_group* via
    :func:`~ascend.utils.naming.generate_resource_names` when explicit names
    are not supplied.

    Args:
        credential: Azure credential.
        subscription_id: Azure subscription ID.
        resource_group: Existing resource group.
        location: Azure region (e.g. ``eastus``).
        cluster_name: AKS cluster name (needed for ACR attachment and
            runtime image build).  When *None* steps 5 and 6 are skipped.
        storage_account_name: Override for the storage account name.
        registry_name: Override for the container registry name.

    Returns:
        An :class:`InfrastructureResult` describing the resources.
    """
    defaults = generate_resource_names(resource_group)
    sa_name = storage_account_name or defaults["storage_account"]
    cr_name = registry_name or defaults["container_registry"]

    total_steps = 6 if cluster_name else 4

    console.print(
        f"\n[bold]1/{total_steps}[/bold] Ensuring storage account [bold]{sa_name}[/bold] …"
    )
    ensure_storage_account(credential, subscription_id, resource_group, location, sa_name)

    console.print(f"\n[bold]2/{total_steps}[/bold] Ensuring blob container [bold]ascend-data[/bold] …")
    container = ensure_blob_container(
        credential, subscription_id, resource_group, sa_name
    )

    console.print(
        f"\n[bold]3/{total_steps}[/bold] Ensuring container registry [bold]{cr_name}[/bold] …"
    )
    _cr_name, login_server = ensure_container_registry(
        credential, subscription_id, resource_group, location, cr_name
    )

    console.print(
        f"\n[bold]4/{total_steps}[/bold] Ensuring Storage Blob Data Contributor role …"
    )
    principal_id, principal_type = _get_current_principal_id(credential)
    ensure_storage_data_role(
        credential,
        subscription_id,
        resource_group,
        sa_name,
        principal_id=principal_id,
        principal_type=principal_type,
    )

    runtime_image_uri = ""
    managed_identity_client_id = ""
    if cluster_name:
        console.print(
            f"\n[bold]5/{total_steps}[/bold] Attaching ACR to AKS "
            f"(AcrPull + AcrPush) …"
        )
        ensure_acr_role_assignment(
            credential, subscription_id, resource_group, _cr_name, cluster_name
        )

        # Also grant kubelet identity Storage Blob Data Contributor
        # so that runner pods can access the storage account.
        kubelet_info = _get_aks_kubelet_identity(
            credential, subscription_id, resource_group, cluster_name
        )
        managed_identity_client_id = kubelet_info["client_id"]
        console.print(
            f"\n  Ensuring kubelet identity has storage access …"
        )
        ensure_storage_data_role(
            credential,
            subscription_id,
            resource_group,
            sa_name,
            principal_id=kubelet_info["object_id"],
            principal_type="ServicePrincipal",
        )

        console.print(
            f"\n[bold]6/{total_steps}[/bold] Ensuring runtime image in ACR …"
        )
        runtime_image_uri = ensure_runtime_image(login_server, credential)

    return InfrastructureResult(
        storage_account_name=sa_name,
        container_registry_name=_cr_name,
        container_registry_login_server=login_server,
        blob_container_name=container,
        location=location,
        runtime_image_uri=runtime_image_uri,
        managed_identity_client_id=managed_identity_client_id,
    )
