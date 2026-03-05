"""Backend validation for node pool availability in AKS"""

from typing import Optional
from kubernetes import client as k8s_client, config as k8s_config
from azure.identity import DefaultAzureCredential
from azure.mgmt.containerservice import ContainerServiceClient

from ...node_types import NodeType, get_node_type_info


class NodePoolValidator:
    """Validates that required node pools exist in the cluster"""
    
    def __init__(self, subscription_id: Optional[str] = None):
        """
        Initialize validator.
        
        Args:
            subscription_id: Azure subscription ID (optional, uses default if not provided)
        """
        self.subscription_id = subscription_id
        self._credential = None
        self._aks_client = None
    
    @property
    def credential(self):
        """Lazy-load Azure credential"""
        if self._credential is None:
            self._credential = DefaultAzureCredential()
        return self._credential
    
    @property
    def aks_client(self):
        """Lazy-load AKS client"""
        if self._aks_client is None:
            if not self.subscription_id:
                raise ValueError("subscription_id is required for AKS client")
            self._aks_client = ContainerServiceClient(self.credential, self.subscription_id)
        return self._aks_client
    
    def get_cluster_node_pools(
        self, resource_group: str, cluster_name: str
    ) -> list[dict]:
        """
        Get list of node pools in the AKS cluster.
        
        Args:
            resource_group: Azure resource group name
            cluster_name: AKS cluster name
            
        Returns:
            List of node pool information dictionaries
        """
        cluster = self.aks_client.managed_clusters.get(
            resource_group_name=resource_group,
            resource_name=cluster_name
        )
        
        node_pools = []
        for pool in cluster.agent_pool_profiles:
            node_pools.append({
                "name": pool.name,
                "vm_size": pool.vm_size,
                "count": pool.count,
                "min_count": pool.min_count,
                "max_count": pool.max_count,
                "mode": pool.mode,
            })
        
        return node_pools
    
    def get_kubernetes_nodes(self) -> list[dict]:
        """
        Get list of nodes from Kubernetes API.
        
        Returns:
            List of node information dictionaries
        """
        try:
            k8s_config.load_kube_config()
            v1 = k8s_client.CoreV1Api()
            nodes = v1.list_node()
            
            node_info = []
            for node in nodes.items:
                labels = node.metadata.labels
                node_info.append({
                    "name": node.metadata.name,
                    "agentpool": labels.get("agentpool", "unknown"),
                    "accelerator": labels.get("accelerator"),
                    "instance_type": labels.get("node.kubernetes.io/instance-type"),
                    "taints": [
                        {
                            "key": taint.key,
                            "value": taint.value,
                            "effect": taint.effect
                        }
                        for taint in (node.spec.taints or [])
                    ],
                })
            
            return node_info
        except Exception as e:
            # If we can't access Kubernetes, return empty list
            return []
    
    def validate_node_type_available(
        self,
        node_type: NodeType,
        resource_group: Optional[str] = None,
        cluster_name: Optional[str] = None,
    ) -> tuple[bool, str]:
        """
        Validate that the requested node type is available in the cluster.
        
        Args:
            node_type: The node type to validate
            resource_group: Azure resource group (for Azure validation)
            cluster_name: AKS cluster name (for Azure validation)
            
        Returns:
            Tuple of (is_valid, message)
        """
        node_info = get_node_type_info(node_type)
        
        # Try Kubernetes validation first (faster and doesn't require Azure creds)
        k8s_nodes = self.get_kubernetes_nodes()
        if k8s_nodes:
            # Check if nodes with required labels exist
            required_pool = node_info.node_selector.get("agentpool")
            
            matching_nodes = [
                n for n in k8s_nodes
                if n["agentpool"] == required_pool
            ]
            
            if matching_nodes:
                return True, f"Node type '{node_type.value}' is available"
            # No running nodes found — fall through to Azure API validation.
            # The node pool may still exist with autoscaling enabled and
            # count == 0; the Azure API checks pool configuration, not
            # running nodes.
        
        # Fall back to Azure API validation (checks node pool definition,
        # which works even when the pool is scaled to zero).
        if resource_group and cluster_name and self.subscription_id:
            try:
                node_pools = self.get_cluster_node_pools(resource_group, cluster_name)
                required_pool = node_info.node_selector.get("agentpool")
                
                # Check by pool name first (AKS auto-labels nodes with
                # agentpool=<pool-name>), then fall back to VM size match.
                matching_pools = [
                    p for p in node_pools
                    if p["name"] == required_pool or p["vm_size"] == node_info.vm_size
                ]
                
                if matching_pools:
                    return True, f"Node type '{node_type.value}' is available"
                else:
                    return (
                        False,
                        f"Node type '{node_type.value}' requires VM size '{node_info.vm_size}' "
                        f"but no matching node pool found in cluster"
                    )
            except Exception as e:
                # If Azure API fails, we can't validate
                return (
                    False,
                    f"Unable to validate node type: {str(e)}"
                )
        
        # If we can't validate, return a warning but don't fail
        return (
            True,
            f"Unable to validate node type '{node_type.value}' (validation skipped)"
        )


def validate_node_pool_availability(
    node_type: NodeType,
    resource_group: Optional[str] = None,
    cluster_name: Optional[str] = None,
    subscription_id: Optional[str] = None,
    raise_on_error: bool = False,
) -> bool:
    """
    Convenience function to validate node pool availability.
    
    Args:
        node_type: The node type to validate
        resource_group: Azure resource group
        cluster_name: AKS cluster name
        subscription_id: Azure subscription ID
        raise_on_error: Whether to raise an exception on validation failure
        
    Returns:
        True if valid, False otherwise
        
    Raises:
        ValueError: If raise_on_error is True and validation fails
    """
    validator = NodePoolValidator(subscription_id)
    is_valid, message = validator.validate_node_type_available(
        node_type, resource_group, cluster_name
    )
    
    if not is_valid and raise_on_error:
        raise ValueError(message)
    
    return is_valid
