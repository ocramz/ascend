"""Azure cloud backend factory.

Assembles the :class:`CloudBackend` for Azure from the current Ascend config.
"""

from __future__ import annotations

import logging

from ascend.cloud.base import CloudBackend

logger = logging.getLogger(__name__)


def _resolve_managed_identity(cfg: dict, credential) -> str | None:
    """Return the managed-identity client ID, auto-discovering if needed.

    Resolution order:
    1. Explicit ``managed_identity_client_id`` in ``.ascend.yaml``.
    2. Live lookup of the kubelet identity from AKS (requires
       ``resource_group`` and ``cluster_name`` in config).

    Returns ``None`` when the identity cannot be determined.
    """
    client_id = cfg.get("managed_identity_client_id")
    if client_id:
        return client_id

    resource_group = cfg.get("resource_group")
    cluster_name = cfg.get("cluster_name")
    if not resource_group or not cluster_name:
        logger.debug(
            "Cannot auto-discover managed identity: "
            "resource_group and cluster_name are required in .ascend.yaml"
        )
        return None

    try:
        from .cli import discover_kubelet_identity

        client_id = discover_kubelet_identity(
            credential, resource_group, cluster_name,
        )
        if client_id:
            logger.info(
                "Auto-discovered kubelet managed identity: %s", client_id,
            )
            return client_id
    except Exception:
        logger.debug(
            "Failed to auto-discover managed identity", exc_info=True,
        )

    logger.warning(
        "managed_identity_client_id is not set in .ascend.yaml and could "
        "not be auto-discovered from the AKS cluster. Pods may fail to "
        "authenticate to Azure storage. Run 'ascend init' or add "
        "'managed_identity_client_id' to your .ascend.yaml."
    )
    return None


def create_backend() -> CloudBackend:
    """Construct an Azure :class:`CloudBackend` from the current Ascend config.

    This is the entry-point called by :func:`ascend.cloud.registry.get_backend`
    when the Azure extra is detected.
    """
    from ascend.config import load_config
    from .auth import get_azure_credential
    from .storage import AzureCloudStorage
    from .registry import AzureContainerRegistry
    from .image_builder import AzureImageBuilder
    from .compute import AzureComputeBackend

    cfg = load_config()
    credential = get_azure_credential()

    managed_identity_client_id = _resolve_managed_identity(cfg, credential)

    storage = AzureCloudStorage(
        account_name=cfg["storage_account"],
        credential=credential,
    )

    registry = AzureContainerRegistry(
        login_server=cfg["container_registry"],
        credential=credential,
    )

    image_builder = AzureImageBuilder(
        registry=registry,
        namespace="ascend-builds",
        credential=credential,
        login_server=cfg["container_registry"],
    )

    compute = AzureComputeBackend(
        storage_account_name=cfg["storage_account"],
        managed_identity_client_id=managed_identity_client_id,
    )

    return CloudBackend(
        name="azure",
        storage=storage,
        registry=registry,
        image_builder=image_builder,
        compute=compute,
    )
