"""User CLI implementation - self-setup and configuration."""

from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from ..utils.errors import AuthenticationError, ConfigError

console = Console()


def init_user(
    cluster_name: str,
    resource_group: str,
    username_override: str | None = None,
) -> None:
    """
    Verify credentials, cluster access, blob storage access,
    derive username, and write .ascend.yaml.

    Args:
        cluster_name: Cluster name.
        resource_group: Cloud resource group.
        username_override: Explicit username (skips automatic derivation).
    """
    console.print(
        Panel(
            f"Initialising Ascend for cluster [bold]{cluster_name}[/bold]\n"
            f"Resource Group: {resource_group}",
            title="Ascend User Init",
        )
    )

    # ---- 1. Detect backend & verify credentials ------------------------
    console.print("\n[bold]1/5[/bold] Verifying credentials...")
    try:
        from ..cloud.registry import detect_backend_name

        backend_name = detect_backend_name()

        if backend_name == "azure":
            from ..cloud.azure.auth import get_azure_credential
            credential = get_azure_credential()
        else:
            console.print(f"  [red]✗[/red] Backend '{backend_name}' not yet supported for user init")
            raise SystemExit(1)

        console.print("  [green]✓[/green] Credentials valid")
    except AuthenticationError as exc:
        console.print(f"  [red]✗[/red] {exc}")
        raise SystemExit(1)

    # ---- 2. Derive username --------------------------------------------
    console.print("[bold]2/5[/bold] Resolving username...")
    if username_override:
        username = username_override
        console.print(f"  [green]✓[/green] Using provided username: {username}")
    else:
        username = _derive_username(credential)
        console.print(f"  [green]✓[/green] Derived username: {username}")
    namespace = f"ascend-users-{username}"
    console.print(f"  [green]✓[/green] Namespace: {namespace}")

    # ---- 3. Verify cluster & storage access ----------------------------
    console.print("[bold]3/5[/bold] Verifying cluster and storage access...")
    
    try:
        if backend_name == "azure":
            from ..cloud.azure.cli import discover_azure_resources
            storage_account, container_registry, managed_identity_client_id = (
                discover_azure_resources(credential, resource_group, cluster_name)
            )
    except Exception as exc:
        console.print(f"  [red]✗[/red] Resource discovery failed")
        console.print(f"\n[red]{exc}[/red]")
        raise SystemExit(1)

    # ---- 4. Ensure Kubernetes namespace exists --------------------------
    console.print("[bold]4/5[/bold] Ensuring Kubernetes namespace exists...")
    namespace_ok = False
    try:
        from kubernetes import config as k8s_config

        k8s_config.load_kube_config()

        from ..cloud.kubernetes.namespace import (
            ensure_namespace,
            list_user_namespaces,
            namespace_exists,
        )

        if namespace_exists(namespace):
            console.print(f"  [green]✓[/green] Namespace {namespace} exists")
            namespace_ok = True
            # Still call ensure_namespace for service account / RBAC
            try:
                result = ensure_namespace(username)
                console.print(
                    f"  [green]✓[/green] ServiceAccount {result.service_account} configured"
                )
            except Exception:
                # Namespace exists but we can't manage RBAC — admin likely did it
                pass
        else:
            # Namespace does not exist — try to create it
            try:
                result = ensure_namespace(username)
                console.print(f"  [green]✓[/green] Namespace {namespace} created")
                console.print(
                    f"  [green]✓[/green] ServiceAccount {result.service_account} configured"
                )
                namespace_ok = True
            except Exception as exc:
                _exc_status = getattr(exc, "status", None)
                available = list_user_namespaces()
                if _exc_status == 403:
                    _print_namespace_not_found(
                        namespace, username, available,
                        cluster_name, resource_group,
                        reason="Insufficient permissions to create it.",
                    )
                else:
                    _print_namespace_not_found(
                        namespace, username, available,
                        cluster_name, resource_group,
                        reason=f"Could not create namespace: {exc}",
                    )
    except ImportError:
        console.print(
            "  [red]✗[/red] kubernetes package not installed. "
            "Install it with: pip install kubernetes"
        )
    except Exception as exc:
        console.print(f"  [yellow]![/yellow] Could not verify namespace: {exc}")
        console.print(
            f"      If this persists, ask an admin to run:\n"
            f"      [bold]ascend admin setup --username {username} "
            f"--cluster {cluster_name} --resource-group {resource_group}[/bold]"
        )

    if not namespace_ok:
        console.print(
            "\n[red]Aborting:[/red] cannot write .ascend.yaml "
            "with a namespace that does not exist on the cluster.\n"
            "Fix the namespace issue above and re-run "
            "[bold]ascend user init[/bold]."
        )
        raise SystemExit(1)

    # ---- 5. Write .ascend.yaml -----------------------------------------
    console.print("[bold]5/5[/bold] Writing .ascend.yaml...")
    config_path = Path.cwd() / ".ascend.yaml"

    config_dict = {
        "cloud_provider": backend_name,
        "username": username,
        "cluster_name": cluster_name,
        "resource_group": resource_group,
        "namespace": namespace,
        "storage_account": storage_account,
        "container_registry": container_registry,
    }
    if managed_identity_client_id:
        config_dict["managed_identity_client_id"] = managed_identity_client_id

    from ..config import save_config

    save_config(config_path, config_dict)
    console.print(f"  [green]✓[/green] Configuration written to {config_path}")

    console.print(
        f"\n[bold green]Done![/bold green] You are ready to use @ascend in this project."
    )


def _derive_username(credential) -> str:
    """Derive a username from the cloud identity.

    Delegates to the shared implementation in ``ascend.utils.naming``.
    Falls back to the local OS username if the token cannot be decoded.
    """
    from ..utils.naming import derive_username_from_credential

    return derive_username_from_credential(credential)


def _print_namespace_not_found(
    namespace: str,
    username: str,
    available_namespaces: list[str],
    cluster_name: str,
    resource_group: str,
    *,
    reason: str = "",
) -> None:
    """Print an actionable message when the target namespace is missing."""
    console.print(f"  [red]✗[/red] Namespace [bold]{namespace}[/bold] not found.")
    if reason:
        console.print(f"      {reason}")

    if available_namespaces:
        console.print("\n      Available user namespaces on this cluster:")
        for ns in available_namespaces:
            extracted = ns.removeprefix("ascend-users-")
            console.print(f"        • {ns}  (--username {extracted})")
        console.print(
            f"\n      To use an existing namespace, re-run with [bold]--username <name>[/bold]:\n"
            f"        ascend user init --cluster {cluster_name} "
            f"--resource-group {resource_group} --username <name>"
        )
    else:
        console.print(
            "\n      No user namespaces found on this cluster."
        )

    console.print(
        f"\n      Or ask a cluster admin to provision your namespace:\n"
        f"        ascend admin setup --username {username} "
        f"--cluster {cluster_name} --resource-group {resource_group}"
    )
