"""Azure-specific CLI logic.

Contains the Azure management-plane operations that were previously
inlined in ``ascend/cli/admin.py`` and ``ascend/cli/user.py``.
"""

from __future__ import annotations

from rich.console import Console

from ascend.utils.errors import AscendError

console = Console()


def get_subscription_id(credential) -> str:
    """Retrieve the default Azure subscription ID.

    Resolution order:
    1. ``AZURE_SUBSCRIPTION_ID`` environment variable (if set).
    2. ``az account show`` CLI output (requires ``az login``).
    """
    import json
    import os
    import subprocess

    sub_id = os.environ.get("AZURE_SUBSCRIPTION_ID", "").strip()
    if sub_id:
        return sub_id

    try:
        result = subprocess.run(
            ["az", "account", "show", "--query", "id", "-o", "tsv"],
            capture_output=True,
            text=True,
            check=True,
        )
        sub_id = result.stdout.strip()
        if sub_id:
            return sub_id
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    raise AscendError(
        "Could not determine Azure subscription ID.\n"
        "Set the AZURE_SUBSCRIPTION_ID environment variable or run 'az login'."
    )


def verify_azure_storage_and_acr(credential, resource_group: str) -> None:
    """Verify that storage accounts and container registries exist."""
    from azure.mgmt.storage import StorageManagementClient
    from azure.mgmt.containerregistry import ContainerRegistryManagementClient

    sub_id = get_subscription_id(credential)

    storage_client = StorageManagementClient(credential, sub_id)
    storage_accounts = list(
        storage_client.storage_accounts.list_by_resource_group(resource_group)
    )
    if storage_accounts:
        console.print(
            f"  [green]✓[/green] Found {len(storage_accounts)} storage account(s) "
            f"in {resource_group}"
        )
    else:
        console.print(
            f"  [yellow]![/yellow] No storage accounts found in {resource_group}"
        )

    acr_client = ContainerRegistryManagementClient(credential, sub_id)
    registries = list(acr_client.registries.list_by_resource_group(resource_group))
    if registries:
        console.print(
            f"  [green]✓[/green] Found {len(registries)} container registry(ies) "
            f"in {resource_group}"
        )
    else:
        console.print(
            f"  [yellow]![/yellow] No container registries found in {resource_group}"
        )


def get_resource_group_location(credential, subscription_id: str, resource_group: str) -> str:
    """Return the Azure region of an existing resource group.

    Args:
        credential: Azure credential.
        subscription_id: Azure subscription ID.
        resource_group: Name of the resource group.

    Returns:
        The location string (e.g. ``eastus``).

    Raises:
        AscendError: If the resource group does not exist.
    """
    from azure.mgmt.resource import ResourceManagementClient

    client = ResourceManagementClient(credential, subscription_id)
    try:
        rg = client.resource_groups.get(resource_group)
        return rg.location
    except Exception as exc:
        raise AscendError(
            f"Resource group '{resource_group}' not found or not accessible: {exc}"
        ) from exc


def discover_kubelet_identity(
    credential, resource_group: str, cluster_name: str,
) -> str:
    """Return the kubelet managed-identity client ID for an AKS cluster.

    Args:
        credential: Azure credential.
        resource_group: Resource group containing the cluster.
        cluster_name: AKS cluster name.

    Returns:
        The client ID string, or empty string on failure.
    """
    from azure.mgmt.containerservice import ContainerServiceClient

    try:
        sub_id = get_subscription_id(credential)
        aks_client = ContainerServiceClient(credential, sub_id)
        cluster = aks_client.managed_clusters.get(resource_group, cluster_name)
        kubelet = (getattr(cluster, "identity_profile", None) or {}).get(
            "kubeletidentity"
        )
        if kubelet and kubelet.client_id:
            return kubelet.client_id
    except Exception:
        pass
    return ""


def discover_azure_resources(credential, resource_group: str, cluster_name: str):
    """Discover Azure resources for user init.

    Returns:
        A tuple of ``(storage_account, container_registry, managed_identity_client_id)``.
        *managed_identity_client_id* may be an empty string if it cannot be
        determined (e.g. insufficient permissions).
    """
    from azure.mgmt.containerservice import ContainerServiceClient
    from azure.mgmt.storage import StorageManagementClient
    from azure.mgmt.containerregistry import ContainerRegistryManagementClient

    sub_id = get_subscription_id(credential)

    storage_account = ""
    container_registry = ""
    managed_identity_client_id = ""

    # AKS
    aks_client = ContainerServiceClient(credential, sub_id)
    cluster = aks_client.managed_clusters.get(resource_group, cluster_name)
    console.print(f"  [green]✓[/green] AKS cluster '{cluster_name}' accessible")

    # Kubelet managed identity (best-effort)
    try:
        kubelet = getattr(cluster, "identity_profile", None) or {}
        kubelet_identity = kubelet.get("kubeletidentity")
        if kubelet_identity and kubelet_identity.client_id:
            managed_identity_client_id = kubelet_identity.client_id
            console.print(
                f"  [green]✓[/green] Managed identity: {managed_identity_client_id}"
            )
    except Exception:
        console.print(
            "  [yellow]![/yellow] Could not detect kubelet managed identity"
        )

    # Storage
    storage_client = StorageManagementClient(credential, sub_id)
    accounts = list(
        storage_client.storage_accounts.list_by_resource_group(resource_group)
    )
    if accounts:
        storage_account = accounts[0].name
        console.print(f"  [green]✓[/green] Storage account: {storage_account}")
    else:
        raise AscendError(
            f"No storage accounts found in resource group '{resource_group}'.\n\n"
            f"Ascend requires a storage account for artifacts and results.\n"
            f"Ask your admin to run 'ascend admin bootstrap' or create one manually:\n"
            f"  az storage account create --name <account-name> "
            f"--resource-group {resource_group} --location <location>"
        )

    # ACR
    acr_client = ContainerRegistryManagementClient(credential, sub_id)
    registries = list(acr_client.registries.list_by_resource_group(resource_group))
    if registries:
        container_registry = registries[0].login_server
        console.print(f"  [green]✓[/green] Container registry: {container_registry}")
    else:
        raise AscendError(
            f"No container registries found in resource group '{resource_group}'.\n\n"
            f"Ascend requires a container registry for custom runtime images.\n"
            f"Ask your admin to run 'ascend admin bootstrap' or create one manually:\n"
            f"  az acr create --name <registry-name> "
            f"--resource-group {resource_group} --sku Basic"
        )

    return storage_account, container_registry, managed_identity_client_id
