"""Abstract cloud provider interfaces.

These ABCs define the contract that each cloud backend must implement,
enabling multi-cloud support.  Concrete backends live under
``ascend/cloud/<provider>/`` (e.g. ``ascend/cloud/azure/``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

import fsspec


class CloudStorage(ABC):
    """Interface for cloud object storage backed by fsspec."""

    @abstractmethod
    def get_filesystem(self) -> fsspec.AbstractFileSystem:
        """Return a configured fsspec filesystem instance."""

    @abstractmethod
    def storage_uri(self, path: str) -> str:
        """Convert a relative storage path to a full URI (e.g. az://container/path)."""

    def write(self, path: str, data: bytes, overwrite: bool = True) -> str:
        """Write bytes to storage. Returns the canonical URI."""
        uri = self.storage_uri(path)
        with self.get_filesystem().open(uri, "wb") as f:
            f.write(data)
        return uri

    def read(self, path: str) -> bytes:
        """Read bytes from storage."""
        uri = self.storage_uri(path)
        with self.get_filesystem().open(uri, "rb") as f:
            return f.read()

    def exists(self, path: str) -> bool:
        """Check if a path exists in storage."""
        return self.get_filesystem().exists(self.storage_uri(path))

    def list(self, prefix: str) -> list[str]:
        """List direct children under *prefix* in storage.

        Args:
            prefix: Path prefix to list (e.g. ``projects/p/users/u/jobs/``).

        Returns:
            List of child path strings (relative to the container).
        """
        uri = self.storage_uri(prefix)
        fs = self.get_filesystem()
        try:
            entries = fs.ls(uri, detail=False)
        except FileNotFoundError:
            return []
        # Strip the leading container name so callers get relative paths
        return [e.split("/", 1)[1] if "/" in e else e for e in entries]

    def close(self) -> None:
        """Release resources held by the storage backend.

        Called during interpreter shutdown (via ``atexit``) to prevent
        spurious exceptions from fsspec/adlfs weakref finalizers.
        The default implementation is a no-op; backends that hold
        long-lived filesystem handles should override this.
        """

    @abstractmethod
    def ensure_container(self, name: str) -> None:
        """Ensure the storage container/bucket exists."""

    # --- convenience methods (non-abstract) ---

    def upload_package(
        self,
        username: str,
        job_id: str,
        package: dict,
        project: Optional[str] = None,
    ) -> str:
        """Upload an execution package and return its URI.

        Args:
            username: User identifier.
            job_id: Unique job identifier.
            package: Serialized execution package.
            project: Optional project name.

        Returns:
            URI to the uploaded package.
        """
        from ascend.storage.paths import get_package_path, get_legacy_package_path

        if project:
            path = get_package_path(project, username, job_id)
        else:
            path = get_legacy_package_path(username, job_id)

        from ascend.serialization import serialize

        self.ensure_container("ascend-data")
        data = serialize(package)
        return self.write(path, data)

    def download_result(
        self,
        username: str,
        job_id: str,
        project: Optional[str] = None,
    ) -> Any:
        """Download and deserialise an execution result.

        Args:
            username: User identifier.
            job_id: Unique job identifier.
            project: Optional project name.

        Returns:
            Deserialized result object.
        """
        import time
        from ascend.storage.paths import get_result_path, get_legacy_result_path

        if project:
            path = get_result_path(project, username, job_id)
        else:
            path = get_legacy_result_path(username, job_id)

        # Wait for result to be available
        max_wait = 60
        start = time.time()
        while time.time() - start < max_wait:
            if self.exists(path):
                break
            time.sleep(2)

        if not self.exists(path):
            raise RuntimeError("Result not found in storage")

        from ascend.serialization import deserialize
        from ascend.utils.errors import SerializationError

        try:
            return deserialize(self.read(path))
        except SerializationError:
            raise
        except Exception as exc:
            raise SerializationError(
                f"Failed to deserialize result for job {job_id}: {exc}"
            ) from exc

    def download_exception(
        self,
        username: str,
        job_id: str,
        project: Optional[str] = None,
    ) -> Optional[dict]:
        """Download and deserialize exception info from a failed job.

        Args:
            username: User identifier.
            job_id: Unique job identifier.
            project: Optional project name.

        Returns:
            Dictionary with "type", "message", and "traceback" keys if exception
            info exists, None otherwise.
        """
        from ascend.storage.paths import get_exception_path

        if project:
            path = get_exception_path(project, username, job_id)
        else:
            # Legacy path for backwards compatibility
            path = f"users/{username}/jobs/{job_id}/exception.pkl"

        if not self.exists(path):
            return None

        from ascend.serialization import deserialize

        return deserialize(self.read(path))


class ContainerRegistry(ABC):
    """Interface for container image registry queries."""

    @abstractmethod
    def image_exists(self, repository: str, tag: str) -> bool:
        """Check whether an image tag exists in the registry.

        Args:
            repository: Repository name (e.g. ``ascend-runtime``).
            tag: Image tag.

        Returns:
            True if the image exists.
        """

    def delete_tag(self, repository: str, tag: str) -> bool:
        """Delete an image tag from the registry.

        Default implementation is a no-op (returns False).
        Backends override this to enable cache busting.

        Args:
            repository: Repository name (e.g. ``ascend-runtime``).
            tag: Image tag to delete.

        Returns:
            True if the tag was deleted, False if it didn't exist
            or deletion is not supported.
        """
        return False

    @abstractmethod
    def registry_url(self) -> str:
        """Return the registry base URL (e.g. myacr.azurecr.io)."""


class ImageBuilder(ABC):
    """Interface for building container images."""

    @abstractmethod
    def build_image(self, dependency_set: Any, timeout_seconds: int) -> str:
        """Build a container image and return its full URI.

        Args:
            dependency_set: ``DependencySet`` describing the required packages.
            timeout_seconds: Maximum time to wait for the build.

        Returns:
            Full image URI.
        """

    @abstractmethod
    def get_or_build_image(self, dependency_set: Any, timeout_seconds: int) -> str:
        """Return existing image URI or build a new one.

        Args:
            dependency_set: ``DependencySet`` describing the required packages.
            timeout_seconds: Maximum time to wait for the build.

        Returns:
            Full image URI.
        """


class ComputeBackend(ABC):
    """Interface for job submission and lifecycle management."""

    @abstractmethod
    def create_job(
        self,
        namespace: str,
        job_id: str,
        package_uri: str,
        config: Any,
        registry: str,
        custom_image_uri: Optional[str] = None,
    ) -> str:
        """Create a compute job and return its name.

        Args:
            namespace: Target namespace.
            job_id: Unique job identifier.
            package_uri: URI of the uploaded execution package.
            config: Ascend execution configuration.
            registry: Container registry reference.
            custom_image_uri: Pre-built image to use instead of the default.

        Returns:
            Job name.
        """

    @abstractmethod
    def wait_for_completion(
        self,
        namespace: str,
        job_name: str,
        timeout: int,
    ) -> bool:
        """Block until the job completes or times out.

        Args:
            namespace: Target namespace.
            job_name: Name of the job to wait on.
            timeout: Maximum seconds to wait.

        Returns:
            True if the job completed successfully.
        """

    @abstractmethod
    def stream_logs(
        self,
        namespace: str,
        job_name: str,
    ) -> None:
        """Stream job logs to stdout.

        Args:
            namespace: Target namespace.
            job_name: Job whose logs to stream.
        """

    @abstractmethod
    def delete_job(
        self,
        job_name: str,
        namespace: str,
    ) -> None:
        """Delete a Kubernetes job.

        If the job does not exist the implementation should silently succeed.

        Args:
            job_name: Name of the job to delete.
            namespace: Namespace the job lives in.
        """

    @abstractmethod
    def list_jobs(
        self,
        namespace: str,
        label_selector: str | None = None,
    ) -> list[dict]:
        """List jobs in a namespace.

        Args:
            namespace: Target namespace.
            label_selector: Optional Kubernetes label selector.

        Returns:
            List of dicts with job metadata (name, status, creation_timestamp).
        """

    @abstractmethod
    def get_job_status(
        self,
        job_name: str,
        namespace: str,
    ) -> dict | None:
        """Return status information for a single job.

        Args:
            job_name: Job name.
            namespace: Namespace.

        Returns:
            Dict with keys ``active``, ``succeeded``, ``failed``,
            ``start_time``, ``completion_time`` — or *None* if the
            job does not exist.
        """


@dataclass
class CloudBackend:
    """Facade bundling all cloud service interfaces for a provider."""

    name: str
    storage: CloudStorage
    registry: ContainerRegistry
    image_builder: Optional[ImageBuilder]
    compute: ComputeBackend
