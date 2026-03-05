"""Runtime execution orchestration."""

import logging
import os
from typing import Any, Optional

from ..cloud.base import CloudBackend
from ..utils.errors import ExecutionError

logger = logging.getLogger(__name__)


class RemoteExecutor:
    """Orchestrates remote execution via an injected cloud backend."""

    def __init__(self, ascend_config, backend: CloudBackend):
        self.config = ascend_config
        self.backend = backend

        # Load configuration from .ascend.yaml in project directory
        from ..config import load_config

        self.user_config = load_config(project=ascend_config.project)

    def _should_use_automatic_image_building(self) -> bool:
        """Check if automatic image building should be used.

        Returns *True* when any of the following hold:
        - ``ASCEND_AUTO_BUILD_IMAGES=true`` environment variable is set.
        - ``auto_build_images: true`` in ``.ascend.yaml``.
        - A GPU node type is requested — the vanilla Python base image
          has no CUDA stack, so a custom image with the correct GPU base
          is always required.
        """
        if os.environ.get("ASCEND_AUTO_BUILD_IMAGES", "").lower() == "true":
            return True
        if self.user_config.get("auto_build_images", False):
            return True
        # GPU workloads *require* a custom image with CUDA / PyTorch;
        # the generic runtime image has no GPU support.
        if self.config.node_type:
            from ..node_types import get_node_type_info
            if get_node_type_info(self.config.node_type).gpu_count > 0:
                return True
        return False

    def _validate_namespace(self, namespace: str) -> None:
        """Validate that the target namespace exists on the cluster.

        Called before ``create_job`` so the user gets a fast, actionable error
        instead of a cryptic 404 from the Kubernetes API.

        Raises:
            ExecutionError: When the namespace does not exist.
        """
        try:
            from ..cloud.kubernetes.namespace import namespace_exists

            if not namespace_exists(namespace):
                raise ExecutionError(
                    f"Namespace '{namespace}' does not exist on the cluster.\n"
                    f"Your .ascend.yaml may be pointing to a namespace that was "
                    f"never provisioned or has been deleted.\n"
                    f"Re-run 'ascend user init' (with --username if needed) to "
                    f"fix your config, or ask a cluster admin to provision it."
                )
        except ExecutionError:
            raise
        except Exception:
            # If we can't reach the K8s API at all, let the job creation
            # call surface the real connectivity error.
            logger.debug("Namespace pre-flight check skipped", exc_info=True)

    def _validate_node_pool(self) -> None:
        """Validate that the cluster has a matching node pool for the requested node type.

        Called before ``create_job`` so the user gets a fast, actionable error
        rather than a generic pod-scheduling timeout.

        Raises:
            ExecutionError: When validation finds no matching node pool.
        """
        from ..cloud.azure.node_pool_validator import NodePoolValidator

        node_type = self.config.node_type
        resource_group = self.user_config.get("resource_group")
        cluster_name = self.user_config.get("cluster_name")

        # Try to obtain subscription_id for the Azure API fallback path.
        subscription_id: str | None = None
        try:
            from ..cloud.azure.auth import get_azure_credential
            from ..cloud.azure.cli import get_subscription_id

            credential = get_azure_credential()
            subscription_id = get_subscription_id(credential)
        except Exception:
            pass  # K8s-only validation will still work

        validator = NodePoolValidator(subscription_id=subscription_id)
        is_valid, message = validator.validate_node_type_available(
            node_type=node_type,
            resource_group=resource_group,
            cluster_name=cluster_name,
        )

        if not is_valid:
            raise ExecutionError(
                f"Node type '{node_type.value}' is not available in cluster "
                f"'{cluster_name}': {message}\n"
                f"Ask your admin to run 'ascend admin setup --gpu' to "
                f"provision the required node pool."
            )

    def _get_or_build_image(self, requirements: list) -> Optional[str]:
        """Get or build container image for the given requirements."""
        if not self.backend.image_builder:
            return None

        try:
            from ..dependencies.analyzer import create_dependency_set
            from ..node_types import get_node_type_info

            use_gpu = False
            if self.config.node_type:
                node_info = get_node_type_info(self.config.node_type)
                use_gpu = node_info.gpu_count > 0

            base_image = getattr(self.config, "base_image", None)

            dep_set = create_dependency_set(
                requirements=requirements,
                use_gpu=use_gpu,
                base_image=base_image,
            )

            image_uri = self.backend.image_builder.get_or_build_image(
                dep_set,
                timeout_seconds=600,
            )
            return image_uri

        except Exception as e:
            logger.warning(
                "Image building failed, falling back to base runtime image",
                exc_info=True,
            )
            return None

    def execute(self, package: dict) -> Any:
        """Execute package remotely and return result."""
        job_id = package["job_id"]
        username = self.user_config["username"]
        namespace = self.user_config["namespace"]
        project = package.get("project", "default")
        function_name = package.get("function_name", "unknown")

        logger.info("Submitting job %s...", job_id)

        # Generate and upload job metadata
        from ..storage.metadata import create_job_metadata
        from ..storage.paths import get_metadata_path
        from ..dependencies.analyzer import create_dependency_set
        from ..node_types import get_node_type_info
        import json

        dep_hash = package.get("dep_hash", "00000000")
        use_gpu = False
        if self.config.node_type:
            node_info = get_node_type_info(self.config.node_type)
            use_gpu = node_info.gpu_count > 0

        # Recreate dep_set to get python_version
        requirements = package.get("requirements", [])
        base_image = getattr(self.config, "base_image", None)
        dep_set = create_dependency_set(
            requirements=requirements,
            use_gpu=use_gpu,
            base_image=base_image,
        )

        metadata = create_job_metadata(
            job_id=job_id,
            user=username,
            project=project,
            function_name=function_name,
            config={
                "cpu": self.config.cpu,
                "memory": self.config.memory,
                "timeout": self.config.timeout,
                "node_type": self.config.node_type,
            },
            dep_hash=dep_hash,
            python_version=dep_set.python_version,
            packages=package.get("requirements", []),
            use_gpu=use_gpu,
        )

        # Upload metadata via backend storage
        metadata_path = get_metadata_path(project, username, job_id)
        self.backend.storage.ensure_container("ascend-data")
        self.backend.storage.write(metadata_path, metadata.to_json().encode("utf-8"))
        logger.debug("Metadata uploaded to %s", metadata_path)

        # 1. Build or get custom image if automatic building is enabled
        custom_image_uri = None
        if (
            self._should_use_automatic_image_building()
            and self.backend.image_builder
            and package.get("requirements")
        ):
            logger.debug("Checking for custom image...")
            custom_image_uri = self._get_or_build_image(package["requirements"])

        # 2. Upload package to storage
        package_uri = self.backend.storage.upload_package(
            username=username,
            job_id=job_id,
            package=package,
            project=project,
        )
        logger.debug("Package uploaded to storage")

        # 2b. Validate namespace exists (fail fast before creating job)
        self._validate_namespace(namespace)

        # 2c. Validate node pool availability (fail fast before creating job)
        if self.config.node_type:
            self._validate_node_pool()

        # 3. Create compute job
        registry_url = self.backend.registry.registry_url()
        job_name = self.backend.compute.create_job(
            namespace=namespace,
            job_id=job_id,
            package_uri=package_uri,
            config=self.config,
            registry=registry_url,
            custom_image_uri=custom_image_uri,
        )
        logger.info("Job %s created in namespace %s", job_name, namespace)

        # Update metadata to "running" now that the K8s job exists
        from ..storage.metadata import update_metadata_status as _update_status
        from datetime import datetime, timezone

        metadata = _update_status(
            metadata,
            status="running",
            execution_data={
                "start_time": datetime.now(timezone.utc).isoformat(),
            },
        )
        self.backend.storage.write(
            metadata_path, metadata.to_json().encode("utf-8")
        )

        # Wrap streaming + wait + download in try/except KeyboardInterrupt
        # so that Ctrl+C deletes the K8s job and marks it cancelled.
        try:
            # 4. Stream logs if enabled
            if self.config.stream_logs:
                logger.info("Streaming logs...")
                self.backend.compute.stream_logs(
                    namespace=namespace,
                    job_name=job_name,
                )

            # 5. Wait for completion
            logger.info("Waiting for job completion...")
            success = self.backend.compute.wait_for_completion(
                namespace=namespace,
                job_name=job_name,
                timeout=self.config.timeout,
            )
        except KeyboardInterrupt:
            logger.info("Cancelling job %s...", job_name)
            try:
                self.backend.compute.delete_job(
                    job_name=job_name,
                    namespace=namespace,
                )
            except Exception as del_err:
                logger.warning("Failed to delete K8s job %s: %s", job_name, del_err)

            # Best-effort metadata update
            try:
                metadata = _update_status(
                    metadata,
                    status="cancelled",
                    execution_data={
                        "end_time": datetime.now(timezone.utc).isoformat(),
                    },
                )
                self.backend.storage.write(
                    metadata_path, metadata.to_json().encode("utf-8")
                )
            except Exception as meta_err:
                logger.warning("Failed to update metadata for %s: %s", job_id, meta_err)

            raise

        if not success:
            # Update metadata with failure status
            from ..storage.metadata import update_metadata_status
            from datetime import datetime, timezone

            metadata = update_metadata_status(
                metadata,
                status="failed",
                execution_data={
                    "end_time": datetime.now(timezone.utc).isoformat(),
                    "exit_code": 1,
                },
            )
            self.backend.storage.write(
                metadata_path, metadata.to_json().encode("utf-8")
            )

            # Try to download remote exception info
            from ..utils.errors import RemoteExecutionError
            
            exception_info = self.backend.storage.download_exception(
                username=username,
                job_id=job_id,
                project=project,
            )
            
            if exception_info:
                # Re-raise with full remote exception details
                raise RemoteExecutionError(
                    remote_type=exception_info.get("type", "UnknownError"),
                    remote_message=exception_info.get("message", ""),
                    remote_traceback=exception_info.get("traceback", ""),
                    job_id=job_id,
                )
            else:
                # Fallback to generic error if no exception info available
                raise ExecutionError(f"Job {job_id} failed or timed out")

        # 6. Download result
        logger.info("Downloading result...")
        result = self.backend.storage.download_result(
            username=username,
            job_id=job_id,
            project=project,
        )

        # 7. Update metadata with success status
        from ..storage.metadata import update_metadata_status
        from datetime import datetime, timezone

        metadata = update_metadata_status(
            metadata,
            status="completed",
            execution_data={
                "end_time": datetime.now(timezone.utc).isoformat(),
                "exit_code": 0,
            },
        )
        self.backend.storage.write(
            metadata_path, metadata.to_json().encode("utf-8")
        )

        logger.info("Job %s completed successfully!", job_id)
        return result
