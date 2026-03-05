"""Azure/Kubernetes compute backend.

Implements :class:`ComputeBackend` by delegating to the Kubernetes
job and streaming modules.
"""

from __future__ import annotations

from typing import Any, Optional

from ascend.cloud.base import ComputeBackend


class AzureComputeBackend(ComputeBackend):
    """Compute backend that runs jobs on Kubernetes (AKS)."""

    def __init__(
        self,
        storage_account_name: str | None = None,
        managed_identity_client_id: str | None = None,
    ) -> None:
        self._batch_api = None
        self._core_api = None
        self._storage_account_name = storage_account_name
        self._managed_identity_client_id = managed_identity_client_id

    def _ensure_k8s(self) -> None:
        if self._batch_api is None:
            from kubernetes import client as k8s_client, config as k8s_config

            k8s_config.load_kube_config()
            self._batch_api = k8s_client.BatchV1Api()
            self._core_api = k8s_client.CoreV1Api()

    def create_job(
        self,
        namespace: str,
        job_id: str,
        package_uri: str,
        config: Any,
        registry: str,
        custom_image_uri: Optional[str] = None,
    ) -> str:
        self._ensure_k8s()
        from ascend.cloud.kubernetes.jobs import create_job

        return create_job(
            k8s_client_api=self._batch_api,
            namespace=namespace,
            job_id=job_id,
            package_url=package_uri,
            config=config,
            registry=registry,
            custom_image_uri=custom_image_uri,
            storage_account_name=self._storage_account_name,
            managed_identity_client_id=self._managed_identity_client_id,
        )

    def wait_for_completion(
        self,
        namespace: str,
        job_name: str,
        timeout: int,
    ) -> bool:
        self._ensure_k8s()
        from ascend.cloud.kubernetes.jobs import wait_for_completion

        return wait_for_completion(
            k8s_client_api=self._batch_api,
            namespace=namespace,
            job_name=job_name,
            timeout_seconds=timeout,
            k8s_core_api=self._core_api,
        )

    def stream_logs(
        self,
        namespace: str,
        job_name: str,
    ) -> None:
        self._ensure_k8s()
        from ascend.runtime.streaming import stream_logs

        stream_logs(
            k8s_client=self._core_api,
            namespace=namespace,
            job_name=job_name,
        )

    def delete_job(
        self,
        job_name: str,
        namespace: str,
    ) -> None:
        self._ensure_k8s()
        from kubernetes.client.rest import ApiException
        from kubernetes import client as k8s_client

        try:
            self._batch_api.delete_namespaced_job(
                name=job_name,
                namespace=namespace,
                body=k8s_client.V1DeleteOptions(
                    propagation_policy="Background",
                ),
            )
        except ApiException as e:
            if e.status == 404:
                pass  # Job already gone
            else:
                raise

    def list_jobs(
        self,
        namespace: str,
        label_selector: str | None = None,
    ) -> list[dict]:
        self._ensure_k8s()
        kwargs: dict = {"namespace": namespace}
        if label_selector:
            kwargs["label_selector"] = label_selector

        resp = self._batch_api.list_namespaced_job(**kwargs)
        results: list[dict] = []
        for job in resp.items:
            results.append({
                "name": job.metadata.name,
                "active": job.status.active or 0,
                "succeeded": job.status.succeeded or 0,
                "failed": job.status.failed or 0,
                "start_time": (
                    job.status.start_time.isoformat()
                    if job.status.start_time
                    else None
                ),
                "completion_time": (
                    job.status.completion_time.isoformat()
                    if job.status.completion_time
                    else None
                ),
            })
        return results

    def get_job_status(
        self,
        job_name: str,
        namespace: str,
    ) -> dict | None:
        self._ensure_k8s()
        from kubernetes.client.rest import ApiException

        try:
            job = self._batch_api.read_namespaced_job(
                name=job_name,
                namespace=namespace,
            )
        except ApiException as e:
            if e.status == 404:
                return None
            raise

        return {
            "active": job.status.active or 0,
            "succeeded": job.status.succeeded or 0,
            "failed": job.status.failed or 0,
            "start_time": (
                job.status.start_time.isoformat()
                if job.status.start_time
                else None
            ),
            "completion_time": (
                job.status.completion_time.isoformat()
                if job.status.completion_time
                else None
            ),
        }
