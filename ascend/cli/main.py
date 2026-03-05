"""Ascend CLI - admin and user setup commands

Usage:
    ascend admin setup   - Provision a user on AKS
    ascend user init     - User self-setup (verify access, write config)
"""

import click
from rich.console import Console

console = Console()


@click.group()
def cli():
    """Ascend – serverless cloud execution for Python."""


# ---------------------------------------------------------------------------
# Admin commands
# ---------------------------------------------------------------------------

@cli.group()
def admin():
    """Administrator commands for cluster provisioning."""


@admin.command("setup")
@click.option(
    "--username",
    default=None,
    help="Username to provision (derived from Azure identity if omitted)",
)
@click.option("--cluster", required=True, help="AKS cluster name")
@click.option("--resource-group", required=True, help="Azure resource group")
def admin_setup(username: str | None, cluster: str, resource_group: str):
    """Provision a user namespace, ServiceAccount, and RBAC on AKS.

    Creates:
      - Namespace ascend-users-{username}
      - ServiceAccount ascend-user-{username}
      - Role + RoleBinding for job management
      - Verifies blob storage and ACR access

    When --username is omitted the name is derived from the current
    Azure credential, matching the behaviour of 'ascend user init'.
    """
    from .admin import provision_user

    provision_user(
        username=username,
        cluster_name=cluster,
        resource_group=resource_group,
    )


@admin.command("bootstrap")
@click.option("--resource-group", required=True, help="Azure resource group (must exist)")
@click.option("--location", default=None, help="Azure region (auto-detected from resource group if omitted)")
@click.option("--storage-account", default=None, help="Override storage account name (default: generated from resource group)")
@click.option("--container-registry", default=None, help="Override container registry name (default: generated from resource group)")
@click.option("--cluster-name", default=None, help="AKS cluster name (enables ACR attachment and runtime-image build)")
def admin_bootstrap(
    resource_group: str,
    location: str | None,
    storage_account: str | None,
    container_registry: str | None,
    cluster_name: str | None,
):
    """Idempotently create Azure infrastructure for Ascend.

    Creates (if not already present):
      - Storage account (Standard_LRS, StorageV2)
      - Blob container 'ascend-data'
      - Azure Container Registry (Basic SKU)

    When --cluster-name is provided, also:
      - Attaches ACR to the AKS cluster (AcrPull + AcrPush)
      - Builds and pushes the base runtime image via Kaniko

    Resource names are deterministically generated from the resource group
    name unless explicit overrides are provided.

    Requires Contributor (or Owner) RBAC on the resource group.
    """
    from .admin import bootstrap_infrastructure

    bootstrap_infrastructure(
        resource_group=resource_group,
        location=location,
        storage_account=storage_account,
        container_registry=container_registry,
        cluster_name=cluster_name,
    )


@admin.command("push-gpu-image")
@click.option("--image", default=None, help="Docker Hub image URI (e.g. pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime)")
@click.option("--torch-version", default=None, help="PyTorch version (e.g. 2.5.1) — auto-selects CUDA/cuDNN")
@click.option("--resource-group", required=True, help="Azure resource group")
@click.option("--timeout", default=600, help="Build timeout in seconds (default 600)")
def admin_push_gpu_image(
    image: str | None,
    torch_version: str | None,
    resource_group: str,
    timeout: int,
):
    """Pre-warm ACR with a GPU base image from Docker Hub.

    Either --image or --torch-version must be provided.  When
    --torch-version is given, the matching official PyTorch image is
    resolved automatically.

    Example::

        ascend admin push-gpu-image --torch-version 2.5.1 --resource-group myRG
        ascend admin push-gpu-image --image pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime --resource-group myRG
    """
    from .admin import push_gpu_image

    push_gpu_image(
        image=image,
        torch_version=torch_version,
        resource_group=resource_group,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# User commands
# ---------------------------------------------------------------------------

@cli.group()
def user():
    """User self-setup commands."""


@user.command("init")
@click.option("--cluster", required=True, help="AKS cluster name")
@click.option("--resource-group", required=True, help="Azure resource group")
@click.option(
    "--username",
    default=None,
    help="Override auto-derived username (must match an existing namespace)",
)
def user_init(cluster: str, resource_group: str, username: str | None):
    """Verify Azure credentials, AKS access, and write .ascend.yaml."""
    from .user import init_user

    init_user(
        cluster_name=cluster,
        resource_group=resource_group,
        username_override=username,
    )


# ---------------------------------------------------------------------------
# Job lifecycle commands
# ---------------------------------------------------------------------------

@cli.group()
def jobs():
    """Job lifecycle commands — list, inspect, cancel, and view logs."""


@jobs.command("list")
@click.option(
    "--status",
    type=click.Choice(["queued", "running", "completed", "failed", "cancelled"]),
    default=None,
    help="Filter by job status",
)
@click.option("--limit", default=20, show_default=True, help="Maximum number of jobs to show")
@click.option("--project", default=None, help="Project name (default: 'default')")
def jobs_list(status: str | None, limit: int, project: str | None):
    """List recent jobs."""
    from .jobs import list_jobs

    list_jobs(status=status, limit=limit, project=project)


@jobs.command("status")
@click.argument("job_id")
def jobs_status(job_id: str):
    """Show detailed status for a job."""
    from .jobs import job_status

    job_status(job_id=job_id)


@jobs.command("cancel")
@click.argument("job_id")
def jobs_cancel(job_id: str):
    """Cancel a running job."""
    from .jobs import cancel_job

    cancel_job(job_id=job_id)


@jobs.command("logs")
@click.argument("job_id")
@click.option("--follow", "-f", is_flag=True, help="Stream live logs")
def jobs_logs(job_id: str, follow: bool):
    """View logs for a job."""
    from .jobs import job_logs

    job_logs(job_id=job_id, follow=follow)


# Allow running as ``python -m ascend.cli``
if __name__ == "__main__":
    cli()
