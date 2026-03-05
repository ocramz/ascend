"""Kaniko build job management for Kubernetes"""

import logging
import time
from dataclasses import dataclass
from typing import Optional, Callable, List
from kubernetes import client as k8s_client

from ...utils.errors import ImageBuildError, ImageBuildTimeout

logger = logging.getLogger(__name__)


@dataclass
class ImageBuildSpec:
    """Specification for building a container image"""

    base_image: str  # e.g., "python:3.11-slim" or "nvidia/cuda:12.2.0-runtime-ubuntu22.04"
    requirements: List[str]  # Python packages
    system_packages: List[str]  # APT packages
    image_tag: str  # e.g., "user-abc123def"
    registry_url: str  # e.g., "ascendprodacr.azurecr.io"
    dockerfile_content: str  # Generated Dockerfile content
    requirements_txt_content: str  # Generated requirements.txt content
    runner_script: Optional[str] = None  # runner.py content for GPU builds
    destination_repository: str = "ascend-runtime"  # ACR repository name


@dataclass
class ImageBuildStatus:
    """Status of an image build operation"""

    job_id: str
    status: str  # "pending", "building", "completed", "failed"
    progress: Optional[str] = None  # Human-readable progress
    image_uri: Optional[str] = None  # Full image URI once built
    error_message: Optional[str] = None  # Error details if failed
    build_logs: Optional[str] = None  # Build output logs


class KanikoJobManager:
    """Manages Kaniko build jobs in Kubernetes"""

    def __init__(self, k8s_client, namespace: str = "ascend-builds"):
        """
        Initialize Kaniko job manager.

        Args:
            k8s_client: Kubernetes batch API client
            namespace: Kubernetes namespace for build jobs
        """
        self.k8s = k8s_client
        self.namespace = namespace

    def create_build_job(
        self,
        build_spec: ImageBuildSpec,
        service_account: str = "kaniko-builder",
        no_cache: bool = False,
    ) -> str:
        """
        Create a Kaniko job to build an image.

        Args:
            build_spec: Build specification
            service_account: K8s service account with ACR push permissions
            no_cache: If True, disable Kaniko layer caching.

        Returns:
            Job ID (job name)
        """
        # Generate unique job name
        job_name = f"ascend-build-{build_spec.image_tag}"

        # Generate job manifest
        job_manifest = self._generate_job_manifest(
            build_spec, service_account, no_cache=no_cache,
        )

        # Create the job, cleaning up any stale job with the same name first
        try:
            job = self.k8s.create_namespaced_job(namespace=self.namespace, body=job_manifest)
        except k8s_client.exceptions.ApiException as exc:
            if exc.status == 409:
                # Job already exists — delete it and wait for removal
                self._delete_job_and_wait(job_name)
                job = self.k8s.create_namespaced_job(
                    namespace=self.namespace, body=job_manifest,
                )
            else:
                raise

        return job.metadata.name

    def get_job_status(self, job_id: str) -> ImageBuildStatus:
        """
        Get current status of a build job.

        Args:
            job_id: Job name/ID

        Returns:
            ImageBuildStatus with current status
        """
        try:
            job = self.k8s.read_namespaced_job(name=job_id, namespace=self.namespace)

            # Check job conditions
            if job.status.succeeded:
                return ImageBuildStatus(
                    job_id=job_id, status="completed", progress="Build completed successfully"
                )
            elif job.status.failed:
                return ImageBuildStatus(
                    job_id=job_id,
                    status="failed",
                    error_message="Build job failed",
                )
            elif job.status.active:
                return ImageBuildStatus(
                    job_id=job_id,
                    status="building",
                    progress="Build in progress",
                )
            else:
                return ImageBuildStatus(
                    job_id=job_id,
                    status="pending",
                    progress="Build job pending",
                )
        except k8s_client.exceptions.ApiException as e:
            logger.warning(
                "Failed to get status for build job %s: %s",
                job_id, e.reason,
            )
            return ImageBuildStatus(
                job_id=job_id,
                status="failed",
                error_message=f"Failed to get job status: {e.reason}",
            )
        except Exception as e:
            logger.error(
                "Unexpected error getting status for build job %s",
                job_id, exc_info=True,
            )
            raise

    def delete_job(self, job_id: str):
        """
        Clean up completed build job.

        Args:
            job_id: Job name/ID to delete
        """
        try:
            self.k8s.delete_namespaced_job(
                name=job_id,
                namespace=self.namespace,
                body=k8s_client.V1DeleteOptions(propagation_policy="Foreground"),
            )
        except k8s_client.exceptions.ApiException as exc:
            if exc.status != 404:
                logger.warning(
                    "Failed to delete build job %s: %s",
                    job_id, exc.reason,
                )
        except Exception:
            logger.warning(
                "Unexpected error deleting build job %s",
                job_id, exc_info=True,
            )

    def _delete_job_and_wait(
        self, job_id: str, timeout_seconds: int = 60,
    ) -> None:
        """Delete a job and block until it disappears from the API server.

        Args:
            job_id: Job name/ID to delete.
            timeout_seconds: Maximum time to wait for the deletion.
        """
        self.delete_job(job_id)

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                self.k8s.read_namespaced_job(
                    name=job_id, namespace=self.namespace,
                )
            except k8s_client.exceptions.ApiException as exc:
                if exc.status == 404:
                    return  # Job has been fully removed
                raise
            time.sleep(2)

    def _generate_job_manifest(
        self,
        build_spec: ImageBuildSpec,
        service_account: str,
        no_cache: bool = False,
    ) -> dict:
        """
        Generate Kubernetes Job manifest for Kaniko.

        Args:
            build_spec: Build specification
            service_account: Service account name
            no_cache: If True, disable Kaniko layer caching.

        Returns:
            Job manifest as dictionary
        """
        job_name = f"ascend-build-{build_spec.image_tag}"
        destination_image = (
            f"{build_spec.registry_url}/{build_spec.destination_repository}"
            f":{build_spec.image_tag}"
        )

        # Build the init-container script that writes build context files
        init_script = (
            "mkdir -p /workspace && "
            f"cat > /workspace/Dockerfile <<'EOF'\n{build_spec.dockerfile_content}\nEOF\n"
            f"cat > /workspace/requirements.txt <<'EOF'\n{build_spec.requirements_txt_content}\nEOF\n"
        )
        # GPU builds need runner.py in the build context for the COPY instruction
        if build_spec.runner_script:
            init_script += (
                f"cat > /workspace/runner.py <<'ASCEND_RUNNER_EOF'\n{build_spec.runner_script}\nASCEND_RUNNER_EOF\n"
            )
        init_script += "ls -la /workspace/"

        job_manifest = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": job_name,
                "namespace": self.namespace,
                "labels": {
                    "app": "ascend-image-builder",
                    "image-tag": build_spec.image_tag,
                },
            },
            "spec": {
                "backoffLimit": 1,
                "ttlSecondsAfterFinished": 3600,  # Clean up after 1 hour
                "template": {
                    "spec": {
                        "restartPolicy": "Never",
                        "serviceAccountName": service_account,
                        "initContainers": [
                            {
                                "name": "prepare-context",
                                "image": "busybox:latest",
                                "command": ["sh", "-c"],
                                "args": [init_script],
                                "volumeMounts": [
                                    {"name": "workspace", "mountPath": "/workspace"}
                                ],
                            }
                        ],
                        "containers": [
                            {
                                "name": "kaniko",
                                "image": "gcr.io/kaniko-project/executor:v1.19.0",
                                "args": self._kaniko_args(
                                    destination_image, build_spec, no_cache,
                                ),
                                "volumeMounts": [
                                    {"name": "workspace", "mountPath": "/workspace"},
                                    {
                                        "name": "kaniko-secret",
                                        "mountPath": "/kaniko/.docker",
                                        "readOnly": True,
                                    },
                                ],
                                "resources": {
                                    "requests": {"cpu": "1", "memory": "2Gi"},
                                    "limits": {"cpu": "2", "memory": "4Gi"},
                                },
                            }
                        ],
                        "volumes": [
                            {"name": "workspace", "emptyDir": {}},
                            {
                                "name": "kaniko-secret",
                                "secret": {
                                    "secretName": "registry-credentials",
                                    "items": [{"key": ".dockerconfigjson", "path": "config.json"}],
                                },
                            },
                        ],
                    }
                },
            },
        }

        return job_manifest

    @staticmethod
    def _kaniko_args(
        destination_image: str,
        build_spec: ImageBuildSpec,
        no_cache: bool,
        build_args: dict[str, str] | None = None,
    ) -> list[str]:
        """Build the Kaniko executor argument list.

        Args:
            destination_image: Full destination image URI.
            build_spec: Build specification.
            no_cache: If True, disable layer caching.
            build_args: Optional Docker build arguments (e.g. PYTHON_VERSION).

        Returns:
            List of CLI arguments for the Kaniko executor.
        """
        args = [
            "--dockerfile=/workspace/Dockerfile",
            "--context=dir:///workspace",
            f"--destination={destination_image}",
            "--snapshot-mode=redo",
            "--log-format=text",
            "--verbosity=info",
        ]

        if build_args:
            for key, value in sorted(build_args.items()):
                args.append(f"--build-arg={key}={value}")

        if no_cache:
            args.append("--cache=false")
        else:
            args += [
                "--cache=true",
                f"--cache-repo={build_spec.registry_url}/ascend-cache",
                "--compressed-caching=false",
            ]

        return args
