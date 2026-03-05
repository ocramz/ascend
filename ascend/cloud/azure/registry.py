"""Azure Container Registry client.

Implements :class:`ContainerRegistry` for Azure Container Registry (ACR).
Uses the ``azure-containerregistry`` SDK for image existence checks.
"""

from __future__ import annotations

import logging
import re

from ascend.cloud.base import ContainerRegistry

logger = logging.getLogger(__name__)


def docker_hub_uri_to_acr_tag(image_uri: str) -> tuple[str, str]:
    """Convert a Docker Hub image URI to an ACR repository + tag pair.

    Examples:
        >>> docker_hub_uri_to_acr_tag("pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime")
        ('ascend-gpu-base', 'pytorch-2.5.1-cuda12.4-cudnn9')
        >>> docker_hub_uri_to_acr_tag("nvidia/cuda:12.4.0-runtime-ubuntu22.04")
        ('ascend-gpu-base', 'cuda-12.4.0-runtime-ubuntu22.04')
    """
    repository = "ascend-gpu-base"
    # Strip any registry prefix (docker.io/ etc.)
    name = re.sub(r"^[^/]+/", "", image_uri) if "/" in image_uri else image_uri
    # Replace ':' with '-' and remove 'pytorch/' prefix
    tag = name.replace(":", "-").replace("/", "-")
    # Remove '-runtime' suffix for brevity (all our images are runtime)
    tag = re.sub(r"-runtime$", "", tag)
    return repository, tag


class AzureContainerRegistry(ContainerRegistry):
    """ACR implementation of :class:`ContainerRegistry`."""

    _ACR_SUFFIX = ".azurecr.io"

    def __init__(self, login_server: str, credential: object) -> None:
        self._login_server = self._normalize_login_server(login_server)
        self._credential = credential
        self._acr_client = None

    @classmethod
    def _normalize_login_server(cls, login_server: str) -> str:
        """Ensure *login_server* is a fully-qualified ACR hostname.

        If only the registry name is provided (e.g. ``"myacr"``), the
        standard Azure suffix ``.azurecr.io`` is appended so that image
        references resolve correctly.
        """
        server = login_server.strip()
        # Already a FQDN (contains a dot) — return as-is.
        if "." in server:
            return server
        logger.info(
            "Bare ACR name '%s' detected; normalizing to '%s%s'",
            server, server, cls._ACR_SUFFIX,
        )
        return f"{server}{cls._ACR_SUFFIX}"

    @property
    def _client(self):
        """Lazy-initialise the ACR client."""
        if self._acr_client is None:
            from azure.containerregistry import ContainerRegistryClient

            endpoint = self._login_server
            if not endpoint.startswith("https://"):
                endpoint = f"https://{endpoint}"
            self._acr_client = ContainerRegistryClient(
                endpoint=endpoint,
                credential=self._credential,
            )
        return self._acr_client

    def image_exists(self, repository: str, tag: str) -> bool:
        """Check whether *repository:tag* exists in ACR."""
        try:
            manifest = self._client.get_manifest_properties(
                repository=repository, tag_or_digest=tag,
            )
            return manifest is not None
        except Exception as exc:
            # Distinguish "not found" from real errors.  The Azure SDK raises
            # ResourceNotFoundError for genuinely missing images; any other
            # exception (auth, network, …) is logged as a warning so it
            # surfaces in diagnostics rather than being silently swallowed.
            _is_not_found = type(exc).__name__ in (
                "ResourceNotFoundError", "HttpResponseError",
            ) and getattr(exc, "status_code", None) == 404
            if not _is_not_found:
                logger.warning(
                    "Unexpected error checking image %s:%s — treating as missing",
                    repository, tag, exc_info=True,
                )
            return False

    def delete_tag(self, repository: str, tag: str) -> bool:
        """Delete an image tag from ACR."""
        try:
            self._client.delete_tag(repository, tag)
            return True
        except Exception as exc:
            _is_not_found = type(exc).__name__ == "ResourceNotFoundError"
            if not _is_not_found:
                logger.warning(
                    "Failed to delete tag %s:%s",
                    repository, tag, exc_info=True,
                )
            return False

    def registry_url(self) -> str:
        return self._login_server
