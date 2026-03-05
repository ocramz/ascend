"""Admin CLI implementation - user provisioning and infrastructure bootstrap on AKS."""

from rich.console import Console
from rich.panel import Panel

from ..utils.errors import AscendError, AuthenticationError

console = Console()


def provision_user(
    username: str | None,
    cluster_name: str,
    resource_group: str,
) -> None:
    """
    Provision a user on AKS with namespace, ServiceAccount, and RBAC.

    When *username* is ``None`` it is derived from the current Azure
    credential using the same logic that ``ascend user init`` uses,
    ensuring admin and user always agree on the name.

    Args:
        username: Username to provision (derived from credential if None).
        cluster_name: AKS cluster name.
        resource_group: Azure resource group containing the cluster.
    """
    # ---- 1. Authenticate -----------------------------------------------
    console.print("\n[bold]1/3[/bold] Verifying credentials...")
    try:
        from ..cloud.registry import detect_backend_name

        backend_name = detect_backend_name()

        if backend_name == "azure":
            from ..cloud.azure.auth import get_azure_credential
            credential = get_azure_credential()
        else:
            raise AscendError(f"Admin setup not implemented for backend: {backend_name}")

        console.print("  [green]✓[/green] Credentials valid")
    except (AuthenticationError, ImportError) as exc:
        console.print(f"  [red]✗[/red] {exc}")
        raise SystemExit(1)

    # Derive username from credential if not explicitly provided
    if username is None:
        from ..utils.naming import derive_username_from_credential

        username = derive_username_from_credential(credential)
        console.print(f"  [green]✓[/green] Derived username: {username}")

    namespace = f"ascend-users-{username}"

    console.print(
        Panel(
            f"Provisioning user [bold]{username}[/bold]\n"
            f"Cluster: {cluster_name}\n"
            f"Resource Group: {resource_group}\n"
            f"Namespace: {namespace}",
            title="Ascend Admin Setup",
        )
    )

    # ---- 2. Load kubeconfig & create namespace + RBAC -------------------
    console.print("[bold]2/3[/bold] Creating namespace, ServiceAccount, and RBAC...")
    try:
        from kubernetes import config as k8s_config

        k8s_config.load_kube_config()

        from ..cloud.kubernetes.namespace import ensure_namespace

        result = ensure_namespace(username)
        if result.created:
            console.print(f"  [green]✓[/green] Namespace {namespace} created")
        else:
            console.print(f"  [yellow]→[/yellow] Namespace {namespace} already exists")
        console.print(f"  [green]✓[/green] ServiceAccount {result.service_account} and RBAC configured")
    except Exception as exc:
        console.print(f"  [red]✗[/red] Failed to provision namespace: {exc}")
        raise SystemExit(1)

    # ---- 3. Verify storage & registry access ----------------------------
    console.print("[bold]3/3[/bold] Verifying storage and registry access...")
    try:
        if backend_name == "azure":
            from ..cloud.azure.cli import verify_azure_storage_and_acr
            verify_azure_storage_and_acr(credential, resource_group)
    except Exception as exc:
        console.print(f"  [yellow]![/yellow] Could not verify storage/registry access: {exc}")

    console.print(
        f"\n[bold green]Done![/bold green] User [bold]{username}[/bold] is ready to use Ascend."
    )


def bootstrap_infrastructure(
    resource_group: str,
    location: str | None = None,
    storage_account: str | None = None,
    container_registry: str | None = None,
    cluster_name: str | None = None,
) -> None:
    """Idempotently create Azure infrastructure (storage account, blob container, ACR).

    If *location* is not supplied it is auto-detected from the resource group.
    When *cluster_name* is provided the ACR is attached to the AKS cluster
    (AcrPull + AcrPush) and the base runtime image is built if absent.

    Args:
        resource_group: Existing Azure resource group.
        location: Azure region override (e.g. ``eastus``).
        storage_account: Optional explicit storage account name.
        container_registry: Optional explicit container registry name.
        cluster_name: Optional AKS cluster name for ACR attachment and
            runtime-image provisioning.
    """
    console.print(
        Panel(
            f"Resource Group: {resource_group}\n"
            f"Location: {location or '(auto-detect from resource group)'}\n"
            f"Storage Account: {storage_account or '(generated)'}\n"
            f"Container Registry: {container_registry or '(generated)'}",
            title="Ascend Infrastructure Bootstrap",
        )
    )

    # ---- 1. Authenticate -----------------------------------------------
    console.print("\n[bold]Authenticating …[/bold]")
    try:
        from ..cloud.registry import detect_backend_name

        backend_name = detect_backend_name()
        if backend_name != "azure":
            raise AscendError(
                f"Infrastructure bootstrap is only supported for Azure (detected: {backend_name})"
            )

        from ..cloud.azure.auth import get_azure_credential

        credential = get_azure_credential()
        console.print("  [green]✓[/green] Credentials valid")
    except (AuthenticationError, ImportError) as exc:
        console.print(f"  [red]✗[/red] {exc}")
        raise SystemExit(1)

    # ---- 2. Resolve subscription & location ----------------------------
    from ..cloud.azure.cli import get_resource_group_location, get_subscription_id

    subscription_id = get_subscription_id(credential)
    console.print(f"  [green]✓[/green] Subscription: {subscription_id}")

    if location is None:
        location = get_resource_group_location(credential, subscription_id, resource_group)
        console.print(f"  [green]✓[/green] Location (from resource group): {location}")

    # ---- 3. Provision infrastructure -----------------------------------
    from ..cloud.azure.infrastructure import ensure_all_infrastructure

    result = ensure_all_infrastructure(
        credential=credential,
        subscription_id=subscription_id,
        resource_group=resource_group,
        location=location,
        cluster_name=cluster_name,
        storage_account_name=storage_account,
        registry_name=container_registry,
    )

    console.print(
        Panel(
            f"Storage Account:    {result.storage_account_name}\n"
            f"Blob Container:     {result.blob_container_name}\n"
            f"Container Registry: {result.container_registry_name}\n"
            f"Registry Login:     {result.container_registry_login_server}\n"
            f"Location:           {result.location}",
            title="[bold green]Bootstrap Complete[/bold green]",
        )
    )


def push_gpu_image(
    image: str | None,
    torch_version: str | None,
    resource_group: str,
    timeout: int = 600,
) -> None:
    """Pre-warm ACR with a GPU base image from Docker Hub.

    Either *image* or *torch_version* must be provided.  When
    *torch_version* is given, the matching official PyTorch Docker Hub
    image URI is resolved via :data:`PYTORCH_CUDA_COMPAT`.

    Args:
        image: Docker Hub image URI.
        torch_version: PyTorch version (e.g. ``"2.5.1"``).
        resource_group: Azure resource group.
        timeout: Build timeout in seconds.
    """
    if not image and not torch_version:
        console.print("[red]✗[/red] Provide either --image or --torch-version")
        raise SystemExit(1)

    # Resolve image from torch version
    if torch_version and not image:
        from ..dependencies.analyzer import PYTORCH_CUDA_COMPAT

        minor = ".".join(torch_version.split(".")[:2])
        if minor not in PYTORCH_CUDA_COMPAT:
            console.print(
                f"[red]✗[/red] Unknown PyTorch version {torch_version}. "
                f"Known: {', '.join(sorted(PYTORCH_CUDA_COMPAT))}"
            )
            raise SystemExit(1)
        cuda, cudnn = PYTORCH_CUDA_COMPAT[minor]
        image = f"pytorch/pytorch:{torch_version}-cuda{cuda}-cudnn{cudnn}-runtime"

    console.print(
        Panel(
            f"Image: {image}\nResource Group: {resource_group}",
            title="Ascend GPU Image Import",
        )
    )

    # Authenticate
    console.print("\n[bold]1/3[/bold] Authenticating …")
    try:
        from ..cloud.azure.auth import get_azure_credential

        credential = get_azure_credential()
        console.print("  [green]✓[/green] Credentials valid")
    except Exception as exc:
        console.print(f"  [red]✗[/red] {exc}")
        raise SystemExit(1)

    # Resolve registry
    console.print("[bold]2/3[/bold] Resolving container registry …")
    from ..cloud.azure.cli import get_subscription_id
    from ..utils.naming import generate_resource_names

    subscription_id = get_subscription_id(credential)
    defaults = generate_resource_names(resource_group)
    cr_name = defaults["container_registry"]

    from azure.mgmt.containerregistry import ContainerRegistryManagementClient

    cr_client = ContainerRegistryManagementClient(credential, subscription_id)
    try:
        registry = cr_client.registries.get(resource_group, cr_name)
        login_server = registry.login_server
    except Exception as exc:
        console.print(f"  [red]✗[/red] Registry '{cr_name}' not found: {exc}")
        raise SystemExit(1)
    console.print(f"  [green]✓[/green] Registry: {login_server}")

    # Import image
    console.print(f"[bold]3/3[/bold] Importing {image} into ACR …")
    from ..cloud.azure.registry import AzureContainerRegistry
    from ..cloud.azure.image_builder import AzureImageBuilder
    from ..cloud.azure.infrastructure import ensure_registry_credentials_secret

    ensure_registry_credentials_secret(login_server, credential)

    acr = AzureContainerRegistry(login_server, credential)
    builder = AzureImageBuilder(
        registry=acr,
        credential=credential,
        login_server=login_server,
    )

    acr_uri = builder._ensure_gpu_base_image(image, timeout)
    console.print(
        f"\n[bold green]Done![/bold green] GPU base image cached: {acr_uri}"
    )
