"""Node type definitions for AKS execution"""

from enum import Enum
from typing import Optional


class NodeType(str, Enum):
    """
    AKS node types with associated VM sizes.
    
    Each node type maps to specific Azure VM sizes optimized for different workloads.
    For GPU types, users can also specify Azure VM sizes directly using the
    `vm_size` parameter for explicit control.
    
    Azure NC-family reference:
    https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-family
    """
    
    # Standard compute-optimized nodes
    STANDARD_SMALL = "standard_small"      # Standard_D2s_v3: 2 vCPU, 8 GB RAM
    STANDARD_MEDIUM = "standard_medium"    # Standard_D4s_v3: 4 vCPU, 16 GB RAM
    STANDARD_LARGE = "standard_large"      # Standard_D8s_v3: 8 vCPU, 32 GB RAM
    
    # Memory-optimized nodes
    MEMORY_MEDIUM = "memory_medium"        # Standard_E4s_v3: 4 vCPU, 32 GB RAM
    MEMORY_LARGE = "memory_large"          # Standard_E8s_v3: 8 vCPU, 64 GB RAM
    
    # GPU-enabled nodes - Abstract types (aliases for common use cases)
    GPU_SMALL = "gpu_small"                # Standard_NC6s_v3: 6 vCPU, 112 GB RAM, 1x V100 (16GB)
    GPU_MEDIUM = "gpu_medium"              # Standard_NC12s_v3: 12 vCPU, 224 GB RAM, 2x V100 (32GB)
    GPU_LARGE = "gpu_large"                # Standard_NC24s_v3: 24 vCPU, 448 GB RAM, 4x V100 (64GB)
    
    # ============================================================================
    # Azure NC-family GPU instances (explicit VM sizes)
    # Users can request specific instance types for precise control
    # ============================================================================
    
    # NCv3 Series - NVIDIA Tesla V100 (16GB HBM2)
    # Best for: Deep learning training, inference, HPC
    NC6S_V3 = "nc6s_v3"                    # 6 vCPU, 112 GB RAM, 1x V100
    NC12S_V3 = "nc12s_v3"                  # 12 vCPU, 224 GB RAM, 2x V100
    NC24S_V3 = "nc24s_v3"                  # 24 vCPU, 448 GB RAM, 4x V100
    NC24RS_V3 = "nc24rs_v3"                # 24 vCPU, 448 GB RAM, 4x V100, RDMA
    
    # NC A100 v4 Series - NVIDIA A100 (80GB PCIe)
    # Best for: Large-scale deep learning, LLM training, HPC
    NC24ADS_A100_V4 = "nc24ads_a100_v4"    # 24 vCPU, 220 GB RAM, 1x A100
    NC48ADS_A100_V4 = "nc48ads_a100_v4"    # 48 vCPU, 440 GB RAM, 2x A100
    NC96ADS_A100_V4 = "nc96ads_a100_v4"    # 96 vCPU, 880 GB RAM, 4x A100
    
    # NCas T4 v3 Series - NVIDIA Tesla T4 (16GB GDDR6)
    # Best for: Cost-effective inference, light training, mixed-precision workloads
    NC8AS_T4_V3 = "nc8as_t4_v3"            # 8 vCPU, 56 GB RAM, 1x T4
    NC16AS_T4_V3 = "nc16as_t4_v3"          # 16 vCPU, 110 GB RAM, 1x T4
    NC64AS_T4_V3 = "nc64as_t4_v3"          # 64 vCPU, 440 GB RAM, 4x T4
    
    # NCads H100 v5 Series - NVIDIA H100 NVL (94GB HBM3)
    # Best for: Latest generation AI/ML, LLM training, generative AI
    NC40ADS_H100_V5 = "nc40ads_h100_v5"    # 40 vCPU, 320 GB RAM, 1x H100
    NC80ADIS_H100_V5 = "nc80adis_h100_v5"  # 80 vCPU, 640 GB RAM, 2x H100


class NodeTypeInfo:
    """Information about a specific node type"""
    
    def __init__(
        self,
        vm_size: str,
        cpu_cores: int,
        memory_gb: int,
        gpu_count: int = 0,
        gpu_type: Optional[str] = None,
        node_selector: Optional[dict] = None,
        tolerations: Optional[list] = None,
    ):
        self.vm_size = vm_size
        self.cpu_cores = cpu_cores
        self.memory_gb = memory_gb
        self.gpu_count = gpu_count
        self.gpu_type = gpu_type
        self.node_selector = node_selector or {}
        self.tolerations = tolerations or []


# Common GPU toleration configuration
_GPU_TOLERATIONS = [
    {
        "key": "sku",
        "operator": "Equal",
        "value": "gpu",
        "effect": "NoSchedule",
    }
]


# Node type configuration mapping
NODE_TYPE_CONFIG = {
    # Standard compute nodes
    NodeType.STANDARD_SMALL: NodeTypeInfo(
        vm_size="Standard_D2s_v3",
        cpu_cores=2,
        memory_gb=8,
        node_selector={"agentpool": "user"},
    ),
    NodeType.STANDARD_MEDIUM: NodeTypeInfo(
        vm_size="Standard_D4s_v3",
        cpu_cores=4,
        memory_gb=16,
        node_selector={"agentpool": "user"},
    ),
    NodeType.STANDARD_LARGE: NodeTypeInfo(
        vm_size="Standard_D8s_v3",
        cpu_cores=8,
        memory_gb=32,
        node_selector={"agentpool": "user"},
    ),
    
    # Memory-optimized nodes
    NodeType.MEMORY_MEDIUM: NodeTypeInfo(
        vm_size="Standard_E4s_v3",
        cpu_cores=4,
        memory_gb=32,
        node_selector={"agentpool": "memory"},
    ),
    NodeType.MEMORY_LARGE: NodeTypeInfo(
        vm_size="Standard_E8s_v3",
        cpu_cores=8,
        memory_gb=64,
        node_selector={"agentpool": "memory"},
    ),
    
    # Abstract GPU types (aliases for common use cases - maps to NCv3)
    NodeType.GPU_SMALL: NodeTypeInfo(
        vm_size="Standard_NC6s_v3",
        cpu_cores=6,
        memory_gb=112,
        gpu_count=1,
        gpu_type="nvidia-tesla-v100",
        node_selector={"agentpool": "gpu", "accelerator": "nvidia-tesla-v100"},
        tolerations=_GPU_TOLERATIONS,
    ),
    NodeType.GPU_MEDIUM: NodeTypeInfo(
        vm_size="Standard_NC12s_v3",
        cpu_cores=12,
        memory_gb=224,
        gpu_count=2,
        gpu_type="nvidia-tesla-v100",
        node_selector={"agentpool": "gpu", "accelerator": "nvidia-tesla-v100"},
        tolerations=_GPU_TOLERATIONS,
    ),
    NodeType.GPU_LARGE: NodeTypeInfo(
        vm_size="Standard_NC24s_v3",
        cpu_cores=24,
        memory_gb=448,
        gpu_count=4,
        gpu_type="nvidia-tesla-v100",
        node_selector={"agentpool": "gpu", "accelerator": "nvidia-tesla-v100"},
        tolerations=_GPU_TOLERATIONS,
    ),
    
    # ========================================================================
    # NCv3 Series - NVIDIA Tesla V100 (16GB HBM2)
    # ========================================================================
    NodeType.NC6S_V3: NodeTypeInfo(
        vm_size="Standard_NC6s_v3",
        cpu_cores=6,
        memory_gb=112,
        gpu_count=1,
        gpu_type="nvidia-tesla-v100",
        node_selector={"agentpool": "gpu-ncv3", "accelerator": "nvidia-tesla-v100"},
        tolerations=_GPU_TOLERATIONS,
    ),
    NodeType.NC12S_V3: NodeTypeInfo(
        vm_size="Standard_NC12s_v3",
        cpu_cores=12,
        memory_gb=224,
        gpu_count=2,
        gpu_type="nvidia-tesla-v100",
        node_selector={"agentpool": "gpu-ncv3", "accelerator": "nvidia-tesla-v100"},
        tolerations=_GPU_TOLERATIONS,
    ),
    NodeType.NC24S_V3: NodeTypeInfo(
        vm_size="Standard_NC24s_v3",
        cpu_cores=24,
        memory_gb=448,
        gpu_count=4,
        gpu_type="nvidia-tesla-v100",
        node_selector={"agentpool": "gpu-ncv3", "accelerator": "nvidia-tesla-v100"},
        tolerations=_GPU_TOLERATIONS,
    ),
    NodeType.NC24RS_V3: NodeTypeInfo(
        vm_size="Standard_NC24rs_v3",
        cpu_cores=24,
        memory_gb=448,
        gpu_count=4,
        gpu_type="nvidia-tesla-v100",
        node_selector={"agentpool": "gpu-ncv3-rdma", "accelerator": "nvidia-tesla-v100"},
        tolerations=_GPU_TOLERATIONS,
    ),
    
    # ========================================================================
    # NC A100 v4 Series - NVIDIA A100 (80GB PCIe)
    # ========================================================================
    NodeType.NC24ADS_A100_V4: NodeTypeInfo(
        vm_size="Standard_NC24ads_A100_v4",
        cpu_cores=24,
        memory_gb=220,
        gpu_count=1,
        gpu_type="nvidia-a100-80gb",
        node_selector={"agentpool": "gpu-a100", "accelerator": "nvidia-a100-80gb"},
        tolerations=_GPU_TOLERATIONS,
    ),
    NodeType.NC48ADS_A100_V4: NodeTypeInfo(
        vm_size="Standard_NC48ads_A100_v4",
        cpu_cores=48,
        memory_gb=440,
        gpu_count=2,
        gpu_type="nvidia-a100-80gb",
        node_selector={"agentpool": "gpu-a100", "accelerator": "nvidia-a100-80gb"},
        tolerations=_GPU_TOLERATIONS,
    ),
    NodeType.NC96ADS_A100_V4: NodeTypeInfo(
        vm_size="Standard_NC96ads_A100_v4",
        cpu_cores=96,
        memory_gb=880,
        gpu_count=4,
        gpu_type="nvidia-a100-80gb",
        node_selector={"agentpool": "gpu-a100", "accelerator": "nvidia-a100-80gb"},
        tolerations=_GPU_TOLERATIONS,
    ),
    
    # ========================================================================
    # NCas T4 v3 Series - NVIDIA Tesla T4 (16GB GDDR6)
    # ========================================================================
    NodeType.NC8AS_T4_V3: NodeTypeInfo(
        vm_size="Standard_NC8as_T4_v3",
        cpu_cores=8,
        memory_gb=56,
        gpu_count=1,
        gpu_type="nvidia-tesla-t4",
        node_selector={"agentpool": "ncast4v4"},
        tolerations=_GPU_TOLERATIONS,
    ),
    NodeType.NC16AS_T4_V3: NodeTypeInfo(
        vm_size="Standard_NC16as_T4_v3",
        cpu_cores=16,
        memory_gb=110,
        gpu_count=1,
        gpu_type="nvidia-tesla-t4",
        node_selector={"agentpool": "gpu-t4"},
        tolerations=_GPU_TOLERATIONS,
    ),
    NodeType.NC64AS_T4_V3: NodeTypeInfo(
        vm_size="Standard_NC64as_T4_v3",
        cpu_cores=64,
        memory_gb=440,
        gpu_count=4,
        gpu_type="nvidia-tesla-t4",
        node_selector={"agentpool": "gpu-t4"},
        tolerations=_GPU_TOLERATIONS,
    ),
    
    # ========================================================================
    # NCads H100 v5 Series - NVIDIA H100 NVL (94GB HBM3)
    # ========================================================================
    NodeType.NC40ADS_H100_V5: NodeTypeInfo(
        vm_size="Standard_NC40ads_H100_v5",
        cpu_cores=40,
        memory_gb=320,
        gpu_count=1,
        gpu_type="nvidia-h100-nvl",
        node_selector={"agentpool": "gpu-h100", "accelerator": "nvidia-h100-nvl"},
        tolerations=_GPU_TOLERATIONS,
    ),
    NodeType.NC80ADIS_H100_V5: NodeTypeInfo(
        vm_size="Standard_NC80adis_H100_v5",
        cpu_cores=80,
        memory_gb=640,
        gpu_count=2,
        gpu_type="nvidia-h100-nvl",
        node_selector={"agentpool": "gpu-h100", "accelerator": "nvidia-h100-nvl"},
        tolerations=_GPU_TOLERATIONS,
    ),
}


def get_node_type_info(node_type: NodeType) -> NodeTypeInfo:
    """Get configuration information for a node type"""
    return NODE_TYPE_CONFIG[node_type]


def validate_node_type(node_type: str) -> NodeType:
    """
    Validate and convert string to NodeType enum.
    
    Args:
        node_type: String representation of node type
        
    Returns:
        NodeType enum value
        
    Raises:
        ValueError: If node_type is not valid
    """
    try:
        return NodeType(node_type)
    except ValueError:
        valid_types = ", ".join([t.value for t in NodeType])
        raise ValueError(
            f"Invalid node_type '{node_type}'. Must be one of: {valid_types}"
        )
