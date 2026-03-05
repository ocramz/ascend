"""Log streaming from Kubernetes pods"""

import logging
import time

from kubernetes import watch

logger = logging.getLogger(__name__)


def stream_logs(k8s_client, namespace: str, job_name: str):
    """Stream logs from job pods to stdout"""

    # Wait for pod to be created
    max_wait = 60  # seconds
    start_time = time.time()
    pod_name = None

    while time.time() - start_time < max_wait:
        pods = k8s_client.list_namespaced_pod(
            namespace=namespace, label_selector=f"job-name={job_name}"
        )

        if pods.items:
            pod_name = pods.items[0].metadata.name
            break

        time.sleep(2)

    if not pod_name:
        logger.warning("Could not find pod for log streaming")
        return

    # Wait for pod to start running (with timeout)
    pod_start_timeout = 300  # 5 minutes
    pod_start_time = time.time()
    while time.time() - pod_start_time < pod_start_timeout:
        pod = k8s_client.read_namespaced_pod(name=pod_name, namespace=namespace)
        if pod.status.phase in ["Running", "Succeeded", "Failed"]:
            break
        time.sleep(1)

    if pod.status.phase not in ["Running", "Succeeded", "Failed"]:
        logger.warning("Pod took too long to start")

    # Stream logs
    try:
        w = watch.Watch()
        for line in w.stream(
            k8s_client.read_namespaced_pod_log, name=pod_name, namespace=namespace, follow=True
        ):
            print(line)
    except Exception as e:
        logger.warning("Log streaming interrupted: %s", e)
