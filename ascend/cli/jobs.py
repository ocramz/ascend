"""Job lifecycle CLI commands.

Implements ``ascend jobs list|status|cancel|logs``.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from rich.console import Console
from rich.table import Table

from ..utils.errors import ConfigError, ExecutionError

logger = logging.getLogger(__name__)
console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_config() -> dict:
    from ..config import load_config

    return load_config()


def _get_backend():
    """Return the active :class:`CloudBackend`."""
    from ..cloud.registry import get_backend

    return get_backend()


def _read_metadata(storage, project: str, username: str, job_id: str):
    """Read and parse ``metadata.json`` for a job.

    Returns ``None`` when the metadata file does not exist.
    """
    from ..storage.paths import get_metadata_path
    from ..storage.metadata import JobMetadata

    path = get_metadata_path(project, username, job_id)
    if not storage.exists(path):
        return None

    raw = storage.read(path)
    return JobMetadata.from_json(raw.decode("utf-8"))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def list_jobs(
    status: Optional[str] = None,
    limit: int = 20,
    project: Optional[str] = None,
) -> None:
    """List jobs from blob storage metadata."""
    cfg = _get_config()
    backend = _get_backend()
    storage = backend.storage
    username = cfg["username"]
    proj = project or "default"

    from ..storage.paths import get_user_jobs_prefix
    from ..storage.metadata import JobMetadata

    prefix = get_user_jobs_prefix(proj, username)
    entries = storage.list(prefix)

    # Each entry is the full relative path to a job directory.
    # We need the last path segment (the job_id) to locate metadata.
    job_ids: list[str] = []
    for entry in entries:
        # entry looks like: projects/<proj>/users/<user>/jobs/<job_id>
        parts = entry.rstrip("/").split("/")
        if parts:
            job_ids.append(parts[-1])

    # Load metadata for each job
    jobs: list[JobMetadata] = []
    for jid in job_ids:
        meta = _read_metadata(storage, proj, username, jid)
        if meta is not None:
            if status and meta.status != status:
                continue
            jobs.append(meta)

    # Sort by creation time (newest first)
    jobs.sort(key=lambda m: m.created_at, reverse=True)
    jobs = jobs[:limit]

    if not jobs:
        console.print("[dim]No jobs found.[/dim]")
        return

    table = Table(title="Jobs")
    table.add_column("Job ID", style="cyan", no_wrap=True)
    table.add_column("Status", style="bold")
    table.add_column("Function", style="green")
    table.add_column("Node Type")
    table.add_column("Created")
    table.add_column("Duration")

    _STATUS_STYLES = {
        "queued": "yellow",
        "running": "blue",
        "completed": "green",
        "failed": "red",
        "cancelled": "dim",
    }

    for m in jobs:
        style = _STATUS_STYLES.get(m.status, "")
        status_text = f"[{style}]{m.status}[/{style}]" if style else m.status

        duration = ""
        if m.execution.start_time and m.execution.end_time:
            from datetime import datetime

            try:
                start = datetime.fromisoformat(m.execution.start_time)
                end = datetime.fromisoformat(m.execution.end_time)
                secs = int((end - start).total_seconds())
                duration = f"{secs}s"
            except (ValueError, TypeError):
                pass

        node_type = m.config.get("node_type", "default") or "default"
        created = m.created_at[:19].replace("T", " ")

        table.add_row(m.job_id, status_text, m.function_name, node_type, created, duration)

    console.print(table)


def job_status(job_id: str) -> None:
    """Show detailed status for a single job."""
    cfg = _get_config()
    backend = _get_backend()
    storage = backend.storage
    username = cfg["username"]

    # Try to extract project from job_id, fall back to "default"
    from ..utils.job_ids import parse_job_id

    try:
        components = parse_job_id(job_id)
        project = components.project
    except ValueError:
        project = "default"

    meta = _read_metadata(storage, project, username, job_id)
    if meta is None:
        console.print(f"[red]Job {job_id} not found.[/red]")
        raise SystemExit(1)

    from rich.panel import Panel

    lines = [
        f"[bold]Job ID:[/bold]    {meta.job_id}",
        f"[bold]Status:[/bold]    {meta.status}",
        f"[bold]Function:[/bold]  {meta.function_name}",
        f"[bold]Project:[/bold]   {meta.project}",
        f"[bold]User:[/bold]      {meta.user}",
        f"[bold]Created:[/bold]   {meta.created_at}",
        f"[bold]Updated:[/bold]   {meta.updated_at}",
    ]

    if meta.config:
        cpu = meta.config.get("cpu", "?")
        mem = meta.config.get("memory", "?")
        node = meta.config.get("node_type", "default") or "default"
        timeout = meta.config.get("timeout", "?")
        lines.append(f"[bold]Resources:[/bold] cpu={cpu}, memory={mem}, node_type={node}, timeout={timeout}")

    if meta.execution.start_time:
        lines.append(f"[bold]Started:[/bold]  {meta.execution.start_time}")
    if meta.execution.end_time:
        lines.append(f"[bold]Ended:[/bold]    {meta.execution.end_time}")

    if meta.dependencies.packages:
        pkgs = ", ".join(meta.dependencies.packages[:10])
        if len(meta.dependencies.packages) > 10:
            pkgs += f", ... ({len(meta.dependencies.packages)} total)"
        lines.append(f"[bold]Packages:[/bold]  {pkgs}")

    # If the job is running, try to get live K8s status
    if meta.status in ("queued", "running"):
        try:
            k8s_status = backend.compute.get_job_status(
                job_name=f"ascend-{job_id}",
                namespace=cfg["namespace"],
            )
            if k8s_status:
                k8s_line = (
                    f"[bold]K8s:[/bold]      active={k8s_status['active']}, "
                    f"succeeded={k8s_status['succeeded']}, "
                    f"failed={k8s_status['failed']}"
                )
                lines.append(k8s_line)
            else:
                lines.append("[bold]K8s:[/bold]      job not found in cluster")
        except Exception as e:
            logger.debug("Could not query K8s status: %s", e)

    console.print(Panel("\n".join(lines), title="Job Details"))


def cancel_job(job_id: str) -> None:
    """Cancel a running job."""
    cfg = _get_config()
    backend = _get_backend()
    storage = backend.storage
    username = cfg["username"]
    namespace = cfg["namespace"]

    from ..utils.job_ids import parse_job_id

    try:
        components = parse_job_id(job_id)
        project = components.project
    except ValueError:
        project = "default"

    job_name = f"ascend-{job_id}"

    # Delete the K8s job
    try:
        backend.compute.delete_job(job_name=job_name, namespace=namespace)
        console.print(f"[green]Deleted K8s job {job_name}[/green]")
    except Exception as e:
        console.print(f"[yellow]Warning: could not delete K8s job: {e}[/yellow]")

    # Update metadata
    meta = _read_metadata(storage, project, username, job_id)
    if meta and meta.status in ("queued", "running"):
        from ..storage.metadata import update_metadata_status
        from ..storage.paths import get_metadata_path
        from datetime import datetime, timezone

        meta = update_metadata_status(
            meta,
            status="cancelled",
            execution_data={"end_time": datetime.now(timezone.utc).isoformat()},
        )
        path = get_metadata_path(project, username, job_id)
        storage.write(path, meta.to_json().encode("utf-8"))
        console.print(f"[green]Job {job_id} marked as cancelled.[/green]")
    elif meta:
        console.print(f"[yellow]Job is already in '{meta.status}' state.[/yellow]")
    else:
        console.print("[yellow]No metadata found for this job.[/yellow]")


def job_logs(job_id: str, follow: bool = False) -> None:
    """Display logs for a job."""
    cfg = _get_config()
    backend = _get_backend()
    storage = backend.storage
    username = cfg["username"]
    namespace = cfg["namespace"]

    from ..utils.job_ids import parse_job_id

    try:
        components = parse_job_id(job_id)
        project = components.project
    except ValueError:
        project = "default"

    # If --follow requested and job may still be running, try live streaming
    if follow:
        job_name = f"ascend-{job_id}"
        try:
            console.print("[dim]Streaming live logs (Ctrl+C to stop)...[/dim]")
            backend.compute.stream_logs(namespace=namespace, job_name=job_name)
            return
        except Exception as e:
            console.print(f"[yellow]Live streaming not available: {e}[/yellow]")
            console.print("[dim]Falling back to stored logs...[/dim]")

    # Read stored logs from blob storage
    from ..storage.paths import get_log_path

    log_path = get_log_path(project, username, job_id)
    if not storage.exists(log_path):
        console.print("[dim]No stored logs found for this job.[/dim]")
        return

    raw = storage.read(log_path)
    text = raw.decode("utf-8", errors="replace")

    # Try to pretty-print JSONL log entries
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            level = entry.get("level", "INFO")
            msg = entry.get("message", line)
            ts = entry.get("timestamp", "")
            if ts:
                ts = ts[:19]
            console.print(f"[dim]{ts}[/dim] [{level}] {msg}")
        except (json.JSONDecodeError, TypeError):
            console.print(line)
