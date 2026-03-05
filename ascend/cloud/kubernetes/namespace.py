"""Kubernetes namespace provisioning utilities.

Shared logic for creating user namespaces, service accounts, and RBAC
resources. Used by both ``ascend admin setup`` and ``ascend user init``.
"""

import logging
from dataclasses import dataclass

from kubernetes import client as k8s_client
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NamespaceProvisionResult:
    """Outcome of a namespace provisioning attempt."""

    namespace: str
    service_account: str
    created: bool  # True if newly created, False if already existed


def ensure_namespace(
    username: str,
    *,
    core_v1: k8s_client.CoreV1Api | None = None,
    rbac_v1: k8s_client.RbacAuthorizationV1Api | None = None,
) -> NamespaceProvisionResult:
    """Idempotently create a user namespace with ServiceAccount and RBAC.

    Creates (if not already present):
      - Namespace ``ascend-users-{username}``
      - ServiceAccount ``ascend-user-{username}``
      - Role ``ascend-user-role`` (batch/jobs CRUD, pods/logs read)
      - RoleBinding ``ascend-user-binding``

    Args:
        username: The user to provision.
        core_v1: Optional pre-built CoreV1Api client.
        rbac_v1: Optional pre-built RbacAuthorizationV1Api client.

    Returns:
        NamespaceProvisionResult with details of what was done.

    Raises:
        ApiException: On permission errors or other K8s API failures.
    """
    if core_v1 is None:
        core_v1 = k8s_client.CoreV1Api()
    if rbac_v1 is None:
        rbac_v1 = k8s_client.RbacAuthorizationV1Api()

    namespace = f"ascend-users-{username}"
    sa_name = f"ascend-user-{username}"

    # --- Namespace -------------------------------------------------------
    created = _ensure_resource(
        lambda: core_v1.create_namespace(
            body=k8s_client.V1Namespace(
                metadata=k8s_client.V1ObjectMeta(
                    name=namespace,
                    labels={"ascend.io/user": username, "app": "ascend"},
                )
            )
        ),
        resource_kind="Namespace",
        resource_name=namespace,
    )

    # --- ServiceAccount --------------------------------------------------
    _ensure_resource(
        lambda: core_v1.create_namespaced_service_account(
            namespace=namespace,
            body=k8s_client.V1ServiceAccount(
                metadata=k8s_client.V1ObjectMeta(name=sa_name, namespace=namespace)
            ),
        ),
        resource_kind="ServiceAccount",
        resource_name=sa_name,
    )

    # --- Role ------------------------------------------------------------
    _ensure_resource(
        lambda: rbac_v1.create_namespaced_role(
            namespace=namespace,
            body=k8s_client.V1Role(
                metadata=k8s_client.V1ObjectMeta(
                    name="ascend-user-role", namespace=namespace
                ),
                rules=[
                    k8s_client.V1PolicyRule(
                        api_groups=["batch"],
                        resources=["jobs"],
                        verbs=["create", "get", "list", "delete"],
                    ),
                    k8s_client.V1PolicyRule(
                        api_groups=[""],
                        resources=["pods", "pods/log"],
                        verbs=["get", "list"],
                    ),
                ],
            ),
        ),
        resource_kind="Role",
        resource_name="ascend-user-role",
    )

    # --- RoleBinding -----------------------------------------------------
    _ensure_resource(
        lambda: rbac_v1.create_namespaced_role_binding(
            namespace=namespace,
            body=k8s_client.V1RoleBinding(
                metadata=k8s_client.V1ObjectMeta(
                    name="ascend-user-binding", namespace=namespace
                ),
                subjects=[
                    k8s_client.RbacV1Subject(
                        kind="ServiceAccount",
                        name=sa_name,
                        namespace=namespace,
                    )
                ],
                role_ref=k8s_client.V1RoleRef(
                    api_group="rbac.authorization.k8s.io",
                    kind="Role",
                    name="ascend-user-role",
                ),
            ),
        ),
        resource_kind="RoleBinding",
        resource_name="ascend-user-binding",
    )

    return NamespaceProvisionResult(
        namespace=namespace,
        service_account=sa_name,
        created=created,
    )


def namespace_exists(namespace: str, core_v1: k8s_client.CoreV1Api | None = None) -> bool:
    """Check whether a Kubernetes namespace exists.

    Args:
        namespace: Namespace name to check.
        core_v1: Optional pre-built CoreV1Api client.

    Returns:
        True if the namespace exists, False otherwise.
    """
    if core_v1 is None:
        core_v1 = k8s_client.CoreV1Api()
    try:
        core_v1.read_namespace(name=namespace)
        return True
    except ApiException as exc:
        if exc.status == 404:
            return False
        raise


def list_user_namespaces(
    core_v1: k8s_client.CoreV1Api | None = None,
) -> list[str]:
    """List all Ascend user namespaces on the cluster.

    Returns namespace names matching the ``ascend-users-*`` prefix.

    Args:
        core_v1: Optional pre-built CoreV1Api client.

    Returns:
        Sorted list of namespace names.
    """
    if core_v1 is None:
        core_v1 = k8s_client.CoreV1Api()
    try:
        ns_list = core_v1.list_namespace()
        return sorted(
            ns.metadata.name
            for ns in ns_list.items
            if ns.metadata.name.startswith("ascend-users-")
        )
    except ApiException:
        logger.debug("Failed to list namespaces", exc_info=True)
        return []


def _ensure_resource(create_fn, *, resource_kind: str, resource_name: str) -> bool:
    """Call *create_fn*; swallow 409 (AlreadyExists).

    Returns True if the resource was newly created, False if it already existed.
    """
    try:
        create_fn()
        logger.debug("%s %s created", resource_kind, resource_name)
        return True
    except ApiException as exc:
        if exc.status == 409:
            logger.debug("%s %s already exists", resource_kind, resource_name)
            return False
        raise
