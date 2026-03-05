"""Kubernetes Job creation and management"""

import logging
import sys
import time

from kubernetes import client as k8s_client
from kubernetes.client.rest import ApiException

from ...node_types import get_node_type_info
from ...utils.errors import ExecutionError

logger = logging.getLogger(__name__)


def create_job(
    k8s_client_api,
    namespace: str,
    job_id: str,
    package_url: str,
    config,
    registry: str,
    custom_image_uri: str = None,
    storage_account_name: str | None = None,
    managed_identity_client_id: str | None = None,
) -> str:
    """
    Create a Kubernetes Job for remote execution.
    
    Args:
        k8s_client_api: Kubernetes batch API client
        namespace: Kubernetes namespace
        job_id: Unique job identifier
        package_url: URL to the package in blob storage
        config: Ascend configuration
        registry: Container registry
        custom_image_uri: Optional custom image URI from automatic building
        storage_account_name: Azure storage account name for runner auth
        managed_identity_client_id: Client ID of the managed identity for pod auth
        
    Returns:
        Job name
    """

    job_name = f"ascend-{job_id}"

    # Use custom image if provided, otherwise construct from registry URL
    if custom_image_uri:
        image = custom_image_uri
    else:
        # registry is expected to be a full registry URL (e.g., "myregistry.azurecr.io")
        registry_url = registry.rstrip("/")
        py_tag = f"python{sys.version_info.major}.{sys.version_info.minor}"
        image = f"{registry_url}/ascend-runtime:{py_tag}"

    # Build environment variables
    env_vars = [
        k8s_client.V1EnvVar(name="ASCEND_JOB_ID", value=job_id),
        k8s_client.V1EnvVar(name="ASCEND_PACKAGE_URI", value=package_url),
    ]
    if storage_account_name:
        env_vars.append(
            k8s_client.V1EnvVar(name="AZURE_STORAGE_ACCOUNT_NAME", value=storage_account_name),
        )
    if managed_identity_client_id:
        env_vars.append(
            k8s_client.V1EnvVar(name="AZURE_MANAGED_IDENTITY_CLIENT_ID", value=managed_identity_client_id),
        )

    # Get node type configuration if specified
    node_type_info = None
    node_type = config.get("node_type") if isinstance(config, dict) else getattr(config, "node_type", None)
    # Only get node type info if node_type is not "default" or None
    if node_type and node_type != "default":
        node_type_info = get_node_type_info(node_type)
    
    # Get CPU and memory from config (support both dict and object)
    cpu = config.get("cpu", "1") if isinstance(config, dict) else getattr(config, "cpu", "1")
    memory = config.get("memory", "2Gi") if isinstance(config, dict) else getattr(config, "memory", "2Gi")
    
    # Build resource requirements
    resource_requests = {"cpu": cpu, "memory": memory}
    resource_limits = {"cpu": cpu, "memory": memory}
    
    # Add GPU resources if node type specifies GPUs
    if node_type_info and node_type_info.gpu_count > 0:
        resource_requests["nvidia.com/gpu"] = str(node_type_info.gpu_count)
        resource_limits["nvidia.com/gpu"] = str(node_type_info.gpu_count)

    # Container specification
    container = k8s_client.V1Container(
        name="executor",
        image=image,
        image_pull_policy="Always",  # Ensure we get the latest image
        env=env_vars,
        resources=k8s_client.V1ResourceRequirements(
            requests=resource_requests,
            limits=resource_limits,
        ),
        volume_mounts=[k8s_client.V1VolumeMount(name="workspace", mount_path="/workspace")],
    )

    
    # Build node selector and tolerations from node type
    node_selector = {}
    tolerations = []
    if node_type_info:
        node_selector = node_type_info.node_selector.copy()
        # Convert toleration dicts to V1Toleration objects
        tolerations = [
            k8s_client.V1Toleration(
                key=tol["key"],
                operator=tol["operator"],
                value=tol.get("value"),
                effect=tol["effect"]
            )
            for tol in node_type_info.tolerations
        ]

    # Determine service account name from namespace
    # For user namespaces: ascend-users-{username} -> ascend-user-{username}
    # For project namespaces: ascend-projects-{project} -> use default for now
    service_account_name = None
    if namespace.startswith("ascend-users-"):
        username = namespace.replace("ascend-users-", "")
        service_account_name = f"ascend-user-{username}"
    
    # Pod template
    # Truncate label value to 63 chars (K8s label value limit) as defense-in-depth
    label_value = job_name[:63]
    template = k8s_client.V1PodTemplateSpec(
        metadata=k8s_client.V1ObjectMeta(labels={"job-name": label_value}),
        spec=k8s_client.V1PodSpec(
            restart_policy="Never",
            service_account_name=service_account_name,
            host_network=False,
            dns_policy="ClusterFirst",
            containers=[container],
            volumes=[
                k8s_client.V1Volume(name="workspace", empty_dir=k8s_client.V1EmptyDirVolumeSource())
            ],
            node_selector=node_selector if node_selector else None,
            tolerations=tolerations if tolerations else None,
        ),
    )

    # Job specification
    # Use active_deadline_seconds to enforce the user timeout at the K8s level.
    # This ensures the pod is killed even if the client-side wait is blocked
    # (e.g. during log streaming).
    active_deadline = getattr(config, "timeout", None)
    job = k8s_client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=k8s_client.V1ObjectMeta(name=job_name),
        spec=k8s_client.V1JobSpec(
            template=template,
            backoff_limit=4,  # Allow more retries for transient failures
            ttl_seconds_after_finished=86400,  # 24 hours
            active_deadline_seconds=active_deadline,
        ),
    )

    # Create job
    logger.debug(
        "Creating job %s in namespace %s (image=%s, sa=%s)",
        job_name, namespace, image, service_account_name,
    )
    try:
        k8s_client_api.create_namespaced_job(namespace=namespace, body=job)
        logger.debug("Successfully created job %s", job_name)
    except ApiException as e:
        logger.error(
            "ApiException creating job %s: status=%s, reason=%s",
            job_name, e.status, e.reason, exc_info=True,
        )
        if e.status == 409:  # AlreadyExists
            # Job already exists, which can happen if worker retries
            logger.warning(
                "Job %s already exists in namespace %s, continuing with existing job",
                job_name, namespace,
            )
        elif e.status == 404:
            raise ExecutionError(
                f"Namespace '{namespace}' does not exist on the cluster.\n"
                f"Your .ascend.yaml may reference a namespace that was never created "
                f"or has been deleted.\n"
                f"Re-run 'ascend user init' (with --username if needed) to fix your config, "
                f"or ask a cluster admin to run:\n"
                f"  ascend admin setup --username <username> --cluster <cluster> --resource-group <rg>"
            ) from e
        else:
            raise
    except Exception as e:
        logger.error("Unexpected error creating job %s: %s", job_name, e, exc_info=True)
        raise

    return job_name


def wait_for_completion(
    k8s_client_api,
    namespace: str,
    job_name: str,
    timeout_seconds: int = 3600,
    k8s_core_api=None,
    **kwargs
) -> bool:
    """Wait for job to complete, return True on success
    
    Args:
        k8s_client_api: Kubernetes batch API client
        namespace: Namespace where job runs
        job_name: Name of the Kubernetes job
        timeout_seconds: Maximum time to wait in seconds
        k8s_core_api: Kubernetes core API client (optional, for future pod log retrieval)
        **kwargs: Additional arguments for compatibility
    """

    start_time = time.time()
    wait_time = 2  # Start with 2 seconds
    max_wait = 30  # Cap at 30 seconds

    while time.time() - start_time < timeout_seconds:
        try:
            job = k8s_client_api.read_namespaced_job(name=job_name, namespace=namespace)

            if job.status.succeeded:
                return True

            if job.status.failed:
                return False

            # Exponential backoff with cap
            time.sleep(wait_time)
            wait_time = min(wait_time * 1.5, max_wait)
        except ApiException as e:
            if e.status == 404:
                # Job not found - it may have completed and been deleted
                # Check pods to see if any succeeded
                logger.warning("Job %s not found (404), checking pods for completion status", job_name)
                
                try:
                    # Look for pods with this job name
                    pods = k8s_core_api.list_namespaced_pod(
                        namespace=namespace,
                        label_selector=f"job-name={job_name}"
                    ) if k8s_core_api else None
                    
                    if pods and pods.items:
                        # Check if any pod succeeded
                        for pod in pods.items:
                            if pod.status.phase == "Succeeded":
                                logger.info("Found succeeded pod for job %s", job_name)
                                return True
                        logger.warning("Job %s pods found but none succeeded", job_name)
                        return False
                    else:
                        # No pods found either - job was deleted or expired
                        raise ExecutionError(
                            f"Job '{job_name}' not found — it may have been "
                            f"deleted or expired"
                        )
                except ExecutionError:
                    raise
                except Exception as pod_error:
                    raise ExecutionError(
                        f"Job '{job_name}' not found and pod status check "
                        f"failed: {pod_error}"
                    ) from pod_error
            else:
                raise

    return False  # Timeout
