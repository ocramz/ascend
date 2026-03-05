"""
Ascend runtime script executed inside Kubernetes pods.
Downloads code package, executes function, uploads result with structured logging.

Storage I/O uses fsspec, so the runner is cloud-agnostic.  The correct
fsspec backend (adlfs, s3fs, gcsfs) must be installed in the runner image.
"""
import os
import sys
import cloudpickle
import time
import json
from datetime import datetime, timezone

import fsspec


def _close_fsspec_filesystems() -> None:
    """Best-effort cleanup of cached fsspec filesystems.

    Prevents the adlfs weakref finalizer from raising a TypeError
    when ``credential.close()`` returns ``None`` at interpreter shutdown.
    """
    try:
        from fsspec.spec import AbstractFileSystem
        AbstractFileSystem.clear_instance_cache()
    except Exception:
        pass


def _get_storage_options(uri: str) -> dict:
    """Build fsspec storage options from environment variables.

    For ``az://`` URIs the runner needs an account name and a credential.
    Workload-identity on AKS makes ``DefaultAzureCredential`` resolve
    automatically; we just need to pass it explicitly to adlfs.
    """
    if not uri.startswith("az://"):
        return {}

    opts: dict = {}
    account_name = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME")
    if account_name:
        opts["account_name"] = account_name

    try:
        from azure.identity import ManagedIdentityCredential, DefaultAzureCredential

        client_id = os.environ.get("AZURE_MANAGED_IDENTITY_CLIENT_ID")
        if client_id:
            opts["credential"] = ManagedIdentityCredential(client_id=client_id)
        else:
            opts["credential"] = DefaultAzureCredential()
    except ImportError:
        pass  # azure-identity not installed; rely on other auth

    return opts


def _derive_paths(package_uri: str, job_id: str):
    """Derive result and log URIs from the package URI.

    The package URI follows the pattern:
        <scheme>://ascend-data/projects/<project>/users/<user>/jobs/<job_id>/package.pkl
    or the legacy pattern:
        <scheme>://ascend-data/users/<user>/jobs/<job_id>/package.pkl

    We replace the final segment to get result.pkl and job.log.jsonl.
    """
    base = package_uri.rsplit("/", 1)[0]  # drop "package.pkl"
    return {
        "package": package_uri,
        "result": f"{base}/result.pkl",
        "log": f"{base}/job.log.jsonl",
        "exception": f"{base}/exception.pkl",
    }


def main():
    # Get environment variables
    job_id = os.environ["ASCEND_JOB_ID"]
    package_uri = os.environ["ASCEND_PACKAGE_URI"]

    # Derive related URIs
    paths = _derive_paths(package_uri, job_id)

    # --- simple structured logger -----------------------------------------
    log_entries: list[dict] = []

    def log(level: str, message: str, **kwargs):
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "logger": "ascend.runner",
            "job_id": job_id,
            "message": message,
        }
        entry.update(kwargs)
        log_entries.append(entry)
        print(f"[{entry['timestamp']}] {level:8} {message}")
        for key, value in kwargs.items():
            if key not in {"timestamp", "level", "logger", "job_id", "message"}:
                print(f"  {key}: {value}")

    log("INFO", f"Starting Ascend job {job_id}")

    # ------------------------------------------------------------------
    # Download package via fsspec (auto-selects backend from URI scheme)
    # ------------------------------------------------------------------
    log("INFO", "Downloading execution package...")
    start_time = time.time()

    storage_opts = _get_storage_options(paths["package"])

    try:
        with fsspec.open(paths["package"], "rb", **storage_opts) as f:
            package = cloudpickle.loads(f.read())
        download_duration = (time.time() - start_time) * 1000
        log("INFO", "Package downloaded", duration_ms=download_duration)
    except Exception as e:
        log("CRITICAL", f"Failed to download package: {e}", exc_info=True)
        sys.exit(1)

    # Install dependencies if specified
    if package.get("requirements"):
        requirements = package["requirements"]
        log("INFO", f"Installing {len(requirements)} dependencies",
            context={"packages": requirements})
        log("INFO", "Note: This may take 10s-5min depending on dependencies...")

        import subprocess
        from packaging.requirements import Requirement, InvalidRequirement

        install_start = time.time()
        failed_packages = []

        for req in requirements:
            try:
                Requirement(req.strip())
            except InvalidRequirement as e:
                log("WARNING",
                    f"Skipping invalid PEP 508 requirement: {req!r} ({e})")
                continue
            try:
                subprocess.check_call([
                    sys.executable, "-m", "pip", "install", "-q",
                    "--trusted-host", "pypi.org",
                    "--trusted-host", "files.pythonhosted.org",
                    req,
                ])
            except subprocess.CalledProcessError as e:
                log("WARNING", f"Failed to install {req}: {e}")
                failed_packages.append(req)

        install_duration = (time.time() - install_start) * 1000
        if failed_packages:
            log("WARNING",
                f"Failed to install {len(failed_packages)} packages",
                context={"failed_packages": failed_packages})
        log("INFO", "Dependencies installed", duration_ms=install_duration)

    # ------------------------------------------------------------------
    # Validate Python version compatibility
    # ------------------------------------------------------------------
    client_python_version = package.get("python_version")
    runner_python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    if client_python_version and client_python_version != runner_python_version:
        log("CRITICAL",
            f"Python version mismatch: package was serialized with Python "
            f"{client_python_version} but runner is Python {runner_python_version}. "
            f"cloudpickle cannot deserialize across different Python versions. "
            f"Ensure the runtime image matches the client Python version.")
        # Upload logs before exiting
        try:
            log_content = "\n".join(json.dumps(entry) for entry in log_entries)
            with fsspec.open(paths["log"], "wb", **storage_opts) as f:
                f.write(log_content.encode("utf-8"))
        except Exception:
            pass
        _close_fsspec_filesystems()
        sys.exit(1)

    # Deserialize function and arguments
    log("INFO", "Executing user function...")
    func = cloudpickle.loads(package["function"])
    args, kwargs = cloudpickle.loads(package["args"])

    function_name = package.get("function_name", func.__name__)
    log("INFO", f"Executing function: {function_name}",
        context={
            "function_name": function_name,
            "args_count": len(args),
            "kwargs_count": len(kwargs),
        })

    # Execute function
    exec_start = time.time()
    exit_code = 0

    try:
        result = func(*args, **kwargs)
        exec_duration = (time.time() - exec_start) * 1000
        log("INFO", "Function completed", duration_ms=exec_duration)
    except Exception as e:
        exec_duration = (time.time() - exec_start) * 1000
        import traceback
        tb_str = traceback.format_exc()
        log("CRITICAL", f"Function execution failed: {e}",
            duration_ms=exec_duration,
            exception={
                "type": type(e).__name__,
                "message": str(e),
                "traceback": tb_str,
            })
        exit_code = 1

        # Serialize exception info for client-side re-raising
        exception_info = {
            "type": type(e).__name__,
            "message": str(e),
            "traceback": tb_str,
        }
        
        try:
            exception_bytes = cloudpickle.dumps(exception_info)
            with fsspec.open(paths["exception"], "wb", **storage_opts) as f:
                f.write(exception_bytes)
            log("INFO", "Exception info uploaded")
        except Exception as upload_err:
            log("WARNING", f"Failed to upload exception info: {upload_err}")

        # Upload logs before exiting
        try:
            log_content = "\n".join(json.dumps(entry) for entry in log_entries)
            with fsspec.open(paths["log"], "wb", **storage_opts) as f:
                f.write(log_content.encode("utf-8"))
        except Exception as log_err:
            print(f"Warning: Failed to upload logs: {log_err}", file=sys.stderr)

        _close_fsspec_filesystems()
        sys.exit(1)

    # Upload result via fsspec
    log("INFO", "Uploading result...")
    upload_start = time.time()

    try:
        result_bytes = cloudpickle.dumps(result)
        with fsspec.open(paths["result"], "wb", **storage_opts) as f:
            f.write(result_bytes)
        upload_duration = (time.time() - upload_start) * 1000
        log("INFO", "Result uploaded", duration_ms=upload_duration)
    except Exception as e:
        log("CRITICAL",
            f"Failed to serialize/upload result: {e}. "
            f"Result type: {type(result).__name__}. "
            f"If the return value contains objects with internal state "
            f"(database connections, file handles, framework-specific "
            f"objects like Optuna Trial), consider returning plain "
            f"Python types (dict, list, float, str) instead.",
            exception={"type": type(e).__name__, "message": str(e)})
        exit_code = 1

    # Upload logs via fsspec
    try:
        log_content = "\n".join(json.dumps(entry) for entry in log_entries)
        with fsspec.open(paths["log"], "wb", **storage_opts) as f:
            f.write(log_content.encode("utf-8"))
        log("INFO", "Logs uploaded")
    except Exception as e:
        log("WARNING", f"Failed to upload logs: {e}")
        print(f"Warning: Failed to upload logs: {e}")

    total_duration = (time.time() - start_time) * 1000
    log("INFO", f"Job {job_id} completed!", duration_ms=total_duration)

    # Close the fsspec AzureBlobFileSystem before exit to prevent a
    # spurious TypeError from adlfs weakref finalizers during shutdown.
    _close_fsspec_filesystems()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
