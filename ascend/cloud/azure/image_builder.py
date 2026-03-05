"""Azure Container Registry image builder.

Implements :class:`ImageBuilder` ABC, delegating image existence checks
to an injected :class:`ContainerRegistry` and builds to Kaniko.
"""

import logging
import time
from typing import Optional

from ...dependencies.analyzer import DependencySet
from ..base import ImageBuilder as ImageBuilderABC, ContainerRegistry
from ..kubernetes.kaniko import (
    ImageBuildSpec,
    ImageBuildStatus,
    KanikoJobManager,
    ImageBuildError,
    ImageBuildTimeout,
)

logger = logging.getLogger(__name__)

# Runner dependencies that must be present in every image (CPU and GPU).
_RUNNER_DEPS = [
    "cloudpickle>=3.0.0",
    "fsspec>=2024.2",
    "packaging>=23.0",
    "adlfs>=2024.4",
    "azure-identity>=1.15.0",
]


class AzureImageBuilder(ImageBuilderABC):
    """Manages automatic image building in AKS using Kaniko."""

    def __init__(
        self,
        registry: ContainerRegistry,
        namespace: str = "ascend-builds",
        k8s_client=None,
        credential=None,
        login_server: str | None = None,
    ):
        """
        Initialize image builder.

        Args:
            registry: :class:`ContainerRegistry` used for image existence checks.
            namespace: Kubernetes namespace for build jobs.
            k8s_client: Kubernetes batch API client (lazy-loaded if *None*).
            credential: Azure credential for refreshing registry tokens.
            login_server: ACR login server (e.g. ``myacr.azurecr.io``).
        """
        self._registry = registry
        self.namespace = namespace
        self._k8s_client = k8s_client
        self._kaniko_manager: Optional[KanikoJobManager] = None
        self._credential = credential
        self._login_server = login_server

    @property
    def kaniko_manager(self) -> KanikoJobManager:
        if self._kaniko_manager is None:
            if self._k8s_client is None:
                from kubernetes import client as k8s_client, config as k8s_config
                k8s_config.load_kube_config()
                self._k8s_client = k8s_client.BatchV1Api()
            self._kaniko_manager = KanikoJobManager(self._k8s_client, self.namespace)
        return self._kaniko_manager

    def get_or_build_image(
        self,
        dependency_set: DependencySet,
        timeout_seconds: int = 600,
        force_rebuild: bool = False,
    ) -> str:
        """
        Get existing image or build new one if needed.

        Args:
            dependency_set: Dependencies to include in image
            timeout_seconds: Max time to wait for build
            force_rebuild: If True, delete the cached tag and rebuild
                with Kaniko layer caching disabled.

        Returns:
            Full image URI (e.g., "myacr.azurecr.io/ascend-runtime:user-abc123")

        Raises:
            ImageBuildError: If build fails
            ImageBuildTimeout: If build exceeds timeout
        """
        image_tag = self._generate_image_tag(dependency_set)

        if force_rebuild:
            # Bust ACR tag cache
            deleted = self._registry.delete_tag("ascend-runtime", image_tag)
            if deleted:
                logger.info("Deleted cached image tag: %s", image_tag)
        elif self._registry.image_exists("ascend-runtime", image_tag):
            # Fast path: reuse cached image (only when not forcing)
            logger.info("Using cached image: %s", image_tag)
            return self._image_uri(image_tag)

        # Slow path: build image
        logger.info("Building new image: %s", image_tag)
        logger.info("Estimated time: 2-3 minutes for first build")
        return self.build_image(
            dependency_set, timeout_seconds, no_cache=force_rebuild,
        )

    def build_image(
        self,
        dependency_set: DependencySet,
        timeout_seconds: int,
        no_cache: bool = False,
    ) -> str:
        """
        Build new image using Kaniko.

        Args:
            dependency_set: Dependencies to include
            timeout_seconds: Max time to wait for build
            no_cache: If True, disable Kaniko layer caching.

        Returns:
            Full image URI

        Raises:
            ImageBuildError: If build fails
            ImageBuildTimeout: If build exceeds timeout
        """
        # Refresh ACR registry credentials before building
        self._refresh_registry_credentials()

        base_image = dependency_set.get_base_image()
        _is_gpu_base = dependency_set.use_gpu and not base_image.startswith("python:")

        # For GPU workloads with a real GPU base image (PyTorch / NVIDIA),
        # ensure it is cached in ACR.  When the base is ``python:*-slim``
        # (Python-version fallback), skip — the CPU-style Dockerfile path
        # is used instead.
        acr_base_image: Optional[str] = None
        if _is_gpu_base:
            acr_base_image = self._ensure_gpu_base_image(
                base_image, timeout_seconds,
            )

        # Generate Dockerfile
        dockerfile = self._generate_dockerfile(
            dependency_set, acr_base_override=acr_base_image,
        )

        # Generate requirements.txt
        requirements_txt = dependency_set.to_requirements_txt()

        # GPU builds with a real GPU base need runner.py in the build
        # context (CPU / fallback images inherit it from ascend-runtime).
        runner_script: Optional[str] = None
        if _is_gpu_base:
            runner_script = self._get_runner_script()

        # Create build specification
        image_tag = self._generate_image_tag(dependency_set)
        build_spec = ImageBuildSpec(
            base_image=dependency_set.get_base_image(),
            requirements=dependency_set.explicit_requirements,
            system_packages=dependency_set.system_packages,
            image_tag=image_tag,
            registry_url=self._registry.registry_url(),
            dockerfile_content=dockerfile,
            requirements_txt_content=requirements_txt,
            runner_script=runner_script,
        )

        # Create Kaniko job
        job_id = self.kaniko_manager.create_build_job(
            build_spec, no_cache=no_cache,
        )
        logger.debug("Build job created: %s", job_id)

        # Wait for build completion
        status = self._wait_for_build(job_id, timeout_seconds)

        if status.status == "failed":
            raise ImageBuildError(status.error_message or "Build failed", status.build_logs)

        image_uri = self._image_uri(image_tag)
        logger.info("Build complete: %s", image_uri)
        return image_uri

    def _wait_for_build(self, job_id: str, timeout_seconds: int) -> ImageBuildStatus:
        """
        Wait for Kaniko job to complete.

        Args:
            job_id: Job name/ID
            timeout_seconds: Max time to wait

        Returns:
            Final ImageBuildStatus

        Raises:
            ImageBuildTimeout: If timeout is exceeded
        """
        start_time = time.time()
        poll_interval = 5  # Poll every 5 seconds

        while time.time() - start_time < timeout_seconds:
            status = self.kaniko_manager.get_job_status(job_id)

            if status.status == "completed":
                return status
            elif status.status == "failed":
                return status

            # Print progress update
            if status.progress:
                logger.debug("%s", status.progress)

            time.sleep(poll_interval)

        # Timeout reached
        raise ImageBuildTimeout(
            f"Image build did not complete within {timeout_seconds} seconds"
        )

    def _generate_dockerfile(
        self,
        dependency_set: DependencySet,
        acr_base_override: Optional[str] = None,
    ) -> str:
        """
        Generate Dockerfile for user image.

        For CPU workloads the generated image is based on the pre-built
        Ascend runtime image (``ascend-runtime:python{version}``), which
        already contains ``runner.py``, ``cloudpickle``, ``fsspec``,
        ``adlfs``, ``azure-identity`` and the correct ``ENTRYPOINT``.
        User requirements are layered on top.

        For GPU workloads the base image is either an official PyTorch/CUDA
        image (auto-detected or user-specified), or a generic NVIDIA CUDA
        image.  The GPU Dockerfile installs Python (if needed), the Ascend
        runner and its core dependencies, then user requirements.

        Args:
            dependency_set: Dependencies to include
            acr_base_override: ACR-cached base image URI for GPU builds.
                When provided this replaces the Docker Hub image in the
                ``FROM`` line so pulls stay within the cluster.

        Returns:
            Dockerfile content as string
        """
        dep_hash = dependency_set.calculate_hash()
        runner_deps_line = " ".join(f'"{d}"' for d in _RUNNER_DEPS)

        base_image_raw = dependency_set.get_base_image()
        # A *real* GPU base image is one that is NOT a standard Python
        # image (i.e. PyTorch, NVIDIA CUDA, or a user override).  When
        # ``get_base_image()`` falls back to ``python:{ver}-slim`` due to
        # a Python-version mismatch, we treat the workload like a CPU
        # build for Dockerfile purposes: the ``ascend-runtime`` image
        # already contains runner.py, core deps and the correct Python.
        _is_gpu_base = dependency_set.use_gpu and not base_image_raw.startswith("python:")

        # Different Dockerfiles for GPU vs CPU
        if _is_gpu_base:
            original_base = base_image_raw
            base_image = acr_base_override or original_base
            is_pytorch_image = "pytorch/pytorch" in original_base

            dockerfile = f"""# Generated Dockerfile for Ascend runtime (GPU)
# Hash: {dep_hash}
# Python: {dependency_set.python_version}
# Base: {base_image}

FROM {base_image}

"""
            if is_pytorch_image:
                # Official pytorch images ship with Python + pip already
                # installed.  We only need to ensure our runner deps.
                dockerfile += f"""# Suppress pip root-user warning inside container
ENV PIP_ROOT_USER_ACTION=ignore

# Install Ascend runner dependencies
RUN pip install --no-cache-dir {runner_deps_line}

"""
            else:
                # Generic CUDA image — needs Python from apt
                dockerfile += f"""# Install Python and pip (CUDA images don't include Python by default)
RUN apt-get update && apt-get install -y \\
    python{dependency_set.python_version} \\
    python3-pip \\
    && rm -rf /var/lib/apt/lists/*

# Set Python alias
RUN ln -sf /usr/bin/python{dependency_set.python_version} /usr/bin/python

# Suppress pip root-user warning inside container
ENV PIP_ROOT_USER_ACTION=ignore

# Install Ascend runner dependencies
RUN pip install --no-cache-dir {runner_deps_line}

"""
            # Copy runner.py (written by the Kaniko init container)
            dockerfile += """# Install Ascend runner
COPY runner.py /opt/ascend/runner.py

# Set working directory
WORKDIR /workspace

# Install user Python dependencies
"""
        else:
            # CPU (or GPU fallback when Python versions don't match):
            # Use the pre-built runtime image as base.  This inherits
            # runner.py, core dependencies (cloudpickle, fsspec, adlfs,
            # azure-identity) and the ENTRYPOINT.  For GPU fallback,
            # PyTorch pip wheels bundle their own CUDA runtime, so the
            # standard Python image works on GPU nodes.
            runtime_base = (
                f"{self._registry.registry_url()}/ascend-runtime"
                f":python{dependency_set.python_version}"
            )
            dockerfile = f"""# Generated Dockerfile for Ascend runtime (CPU)
# Hash: {dep_hash}
# Python: {dependency_set.python_version}
# Base: {runtime_base}

FROM {runtime_base}

# Install user Python dependencies
"""

        # CPU/fallback images inherit PIP_ROOT_USER_ACTION from
        # ascend-runtime; GPU images set it above.  For safety, ensure
        # it is always present before any user pip install.
        if not _is_gpu_base:
            dockerfile += """# Suppress pip root-user warning inside container
ENV PIP_ROOT_USER_ACTION=ignore

"""

        # Add requirements installation if any
        if dependency_set.explicit_requirements:
            dockerfile += """COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \\
    rm /tmp/requirements.txt
"""

        # GPU images with a real GPU base need an explicit ENTRYPOINT
        # (CPU / fallback images inherit it from ascend-runtime)
        if _is_gpu_base:
            dockerfile += """
# Default entrypoint
ENTRYPOINT ["python", "/opt/ascend/runner.py"]
"""

        return dockerfile

    def _generate_image_tag(self, dependency_set: DependencySet) -> str:
        """
        Generate image tag from dependency hash.

        Args:
            dependency_set: Dependencies

        Returns:
            Image tag (e.g., "user-abc123def456")
        """
        return f"user-{dependency_set.calculate_hash()}"

    def _image_uri(self, image_tag: str) -> str:
        """
        Construct full image URI.

        Args:
            image_tag: Image tag

        Returns:
            Full image URI
        """
        return f"{self._registry.registry_url()}/ascend-runtime:{image_tag}"

    @staticmethod
    def _get_runner_script() -> str:
        """Return the content of ``docker/runner.py``.

        Resolves the path relative to this package so it works from any cwd.
        """
        import pathlib

        docker_dir = pathlib.Path(__file__).resolve().parents[3] / "docker"
        return (docker_dir / "runner.py").read_text()

    def _refresh_registry_credentials(self) -> None:
        """Refresh the ACR ``registry-credentials`` K8s secret.

        This ensures the token used by Kaniko to push images is up-to-date.
        Skipped silently when credentials are unavailable (e.g. in tests).
        """
        if self._credential is None or self._login_server is None:
            logger.debug(
                "Skipping registry credential refresh (no credential/login_server)"
            )
            return

        try:
            from ..azure.infrastructure import ensure_registry_credentials_secret

            ensure_registry_credentials_secret(
                self._login_server,
                self._credential,
                namespace=self.namespace,
                quiet=True,
            )
            logger.debug("Registry credentials refreshed")
        except Exception:
            logger.warning(
                "Failed to refresh registry credentials — build may fail "
                "if existing token has expired",
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # GPU base-image caching
    # ------------------------------------------------------------------

    def _ensure_gpu_base_image(
        self,
        docker_hub_uri: str,
        timeout_seconds: int = 600,
    ) -> str:
        """Ensure a GPU base image from Docker Hub is cached in ACR.

        If the image is already present in ACR, its URI is returned
        immediately.  Otherwise a trivial ``FROM <docker_hub_uri>``
        Kaniko build is submitted to pull and push it into ACR.

        Args:
            docker_hub_uri: Full Docker Hub image reference
                (e.g. ``pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime``).
            timeout_seconds: Maximum time to wait for the import build.

        Returns:
            ACR-local image URI (e.g.
            ``myacr.azurecr.io/ascend-gpu-base:pytorch-2.5.1-cuda12.4-cudnn9``).
        """
        from .registry import docker_hub_uri_to_acr_tag

        repository, tag = docker_hub_uri_to_acr_tag(docker_hub_uri)

        acr_uri = f"{self._registry.registry_url()}/{repository}:{tag}"

        # Fast path — already cached
        if self._registry.image_exists(repository, tag):
            logger.info("GPU base image already cached in ACR: %s", acr_uri)
            return acr_uri

        logger.info(
            "Importing GPU base image %s → %s (this may take a few minutes on first use)",
            docker_hub_uri, acr_uri,
        )

        # Build a trivial image to import into ACR
        dockerfile = f"FROM {docker_hub_uri}\n"

        build_spec = ImageBuildSpec(
            base_image=docker_hub_uri,
            requirements=[],
            system_packages=[],
            image_tag=tag,
            registry_url=self._registry.registry_url(),
            dockerfile_content=dockerfile,
            requirements_txt_content="",
            destination_repository=repository,
        )

        # Use a different destination repository for base images
        job_id = self.kaniko_manager.create_build_job(
            build_spec,
            no_cache=True,
        )
        logger.debug("GPU base image import job created: %s", job_id)

        status = self._wait_for_build(job_id, timeout_seconds)
        if status.status == "failed":
            raise ImageBuildError(
                f"Failed to import GPU base image {docker_hub_uri}: "
                f"{status.error_message}",
                status.build_logs,
            )

        logger.info("GPU base image cached: %s", acr_uri)
        return acr_uri
