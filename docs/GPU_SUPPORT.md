# GPU and Node Type Support

## Table of Contents
- [Executive Summary](#executive-summary)
- [Overview](#overview)
- [Node Type Architecture](#node-type-architecture)
- [Implementation Details](#implementation-details)
- [GPU Base Image Selection](#gpu-base-image-selection)
- [Usage Examples](#usage-examples)
- [Node Pool Configuration](#node-pool-configuration)
- [Validation and Error Handling](#validation-and-error-handling)
- [Admin CLI](#admin-cli)
- [Best Practices](#best-practices)

## Executive Summary

Ascend now supports multiple node types including GPU-enabled nodes for ML/AI workloads. Users can select the appropriate node type using the `node_type` parameter in the `@ascend` decorator. The system automatically configures Kubernetes resources, node selectors, tolerations, and GPU allocation based on the selected node type.

### Key Features
- **Multiple node types**: Standard compute, memory-optimized, and GPU-enabled nodes
- **Full Azure NC-family support**: V100, A100, and H100 GPUs with explicit instance type selection
- **GPU support**: All Azure NC-family instances with automatic resource allocation
- **Automatic configuration**: Node selectors and tolerations configured automatically
- **GPU base image auto-detection**: Automatically selects the correct PyTorch/CUDA base image from `torch` version in requirements
- **ACR base image caching**: Docker Hub GPU base images are cached in ACR for fast subsequent builds
- **Validation**: Backend validation ensures requested node pools are available
- **Easy to use**: Single parameter (`node_type`) handles all complexity; `base_image` is optional for advanced control

## Overview

Different workloads have different resource requirements:
- **Standard compute**: General-purpose CPU workloads
- **Memory-optimized**: Large in-memory datasets, analytics
- **GPU-enabled**: Deep learning, ML training, scientific computing

Ascend provides a simple interface to select the appropriate node type while handling all the underlying Kubernetes and Azure configuration automatically.

## Node Type Architecture

### Node Type Enum

The `NodeType` enum defines all available node types:

```python
from enum import Enum

class NodeType(str, Enum):
    # Standard compute
    STANDARD_SMALL = "standard_small"      # 2 vCPU, 8 GB RAM
    STANDARD_MEDIUM = "standard_medium"    # 4 vCPU, 16 GB RAM (default)
    STANDARD_LARGE = "standard_large"      # 8 vCPU, 32 GB RAM
    
    # Memory-optimized
    MEMORY_MEDIUM = "memory_medium"        # 4 vCPU, 32 GB RAM
    MEMORY_LARGE = "memory_large"          # 8 vCPU, 64 GB RAM
    
    # GPU-enabled (simple aliases)
    GPU_SMALL = "gpu_small"                # 6 vCPU, 112 GB, 1x V100
    GPU_MEDIUM = "gpu_medium"              # 12 vCPU, 224 GB, 2x V100
    GPU_LARGE = "gpu_large"                # 24 vCPU, 448 GB, 4x V100
    
    # NCv3 Series (V100)
    NC6S_V3 = "nc6s_v3"                    # 6 vCPU, 112 GB RAM, 1x V100
    NC12S_V3 = "nc12s_v3"                  # 12 vCPU, 224 GB RAM, 2x V100
    NC24S_V3 = "nc24s_v3"                  # 24 vCPU, 448 GB RAM, 4x V100
    NC24RS_V3 = "nc24rs_v3"                # 24 vCPU, 448 GB RAM, 4x V100, RDMA
    
    # NC A100 v4 Series
    NC24ADS_A100_V4 = "nc24ads_a100_v4"    # 24 vCPU, 220 GB RAM, 1x A100
    NC48ADS_A100_V4 = "nc48ads_a100_v4"    # 48 vCPU, 440 GB RAM, 2x A100
    NC96ADS_A100_V4 = "nc96ads_a100_v4"    # 96 vCPU, 880 GB RAM, 4x A100
    
    # NCads H100 v5 Series
    NC40ADS_H100_V5 = "nc40ads_h100_v5"    # 40 vCPU, 320 GB RAM, 1x H100
    NC80ADIS_H100_V5 = "nc80adis_h100_v5"  # 80 vCPU, 640 GB RAM, 2x H100
```

### Azure NC-Family Reference

For detailed specifications, see: https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-family

| Series | GPU | Use Case |
|--------|-----|----------|
| NCv3 | NVIDIA V100 (16GB) | Deep learning training, HPC |
| NC A100 v4 | NVIDIA A100 (80GB) | Large-scale LLM training |
| NCads H100 v5 | NVIDIA H100 NVL (94GB) | Latest generation AI/ML |

### Node Type Configuration

Each node type maps to specific Azure VM sizes and Kubernetes configuration:

```python
NODE_TYPE_CONFIG = {
    NodeType.GPU_SMALL: NodeTypeInfo(
        vm_size="Standard_NC6s_v3",
        cpu_cores=6,
        memory_gb=112,
        gpu_count=1,
        gpu_type="nvidia-tesla-v100",
        node_selector={"agentpool": "gpu", "accelerator": "nvidia-tesla-v100"},
        tolerations=[{
            "key": "sku",
            "operator": "Equal",
            "value": "gpu",
            "effect": "NoSchedule",
        }],
    ),
    NodeType.NC24ADS_A100_V4: NodeTypeInfo(
        vm_size="Standard_NC24ads_A100_v4",
        cpu_cores=24,
        memory_gb=220,
        gpu_count=1,
        gpu_type="nvidia-a100-80gb",
        node_selector={"agentpool": "gpu-a100", "accelerator": "nvidia-a100-80gb"},
        tolerations=[...],
    ),
    # ... other node types
}
```

## Implementation Details

### Decorator API

The `@ascend` decorator accepts `node_type` and optional `base_image` parameters:

```python
# Auto-detect base image from torch version in requirements
@ascend(node_type="gpu_small", requirements=["torch==2.5.1"])
def train_model(data):
    import torch
    # Training code — base image pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
    # selected automatically
    return model

# Explicit base image override
@ascend(
    node_type="gpu_small",
    base_image="pytorch/pytorch:2.4.0-cuda12.4-cudnn9-runtime",
    requirements=["torch==2.4.0"]
)
def train_custom(data):
    # Uses the explicitly specified base image
    pass
```

### AscendConfig

`AscendConfig` validates and stores the node type and base image:

```python
class AscendConfig:
    def __init__(
        self,
        cpu: str = "1",
        memory: str = "2Gi",
        timeout: int = 3600,
        stream_logs: bool = True,
        requirements: Optional[list] = None,
        node_type: Optional[str] = None,
        base_image: Optional[str] = None,  # GPU base image override
    ):
        self.cpu = cpu
        self.memory = memory
        self.timeout = timeout
        self.stream_logs = stream_logs
        self.requirements = requirements if requirements is not None else []
        self.node_type = validate_node_type(node_type) if node_type else None
        self.base_image = base_image
```

### Kubernetes Job Configuration

When creating a Kubernetes Job, the system automatically configures:

1. **GPU Resource Requests**: For GPU node types
   ```yaml
   resources:
     requests:
       nvidia.com/gpu: "1"
     limits:
       nvidia.com/gpu: "1"
   ```

2. **Node Selectors**: To target specific node pools
   ```yaml
   nodeSelector:
     agentpool: gpu
     accelerator: nvidia-tesla-v100
   ```

3. **Tolerations**: For tainted GPU nodes
   ```yaml
   tolerations:
   - key: sku
     operator: Equal
     value: gpu
     effect: NoSchedule
   ```

### Code Flow

```
@ascend(node_type="gpu_small", requirements=["torch==2.5.1"])
    ↓
validate_node_type("gpu_small")
    ↓
AscendConfig(node_type=NodeType.GPU_SMALL, base_image=None)
    ↓
create_dependency_set(requirements, base_image=config.base_image)
    ↓
detect_gpu_base_image(requirements)  →  pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
    ↓
_ensure_gpu_base_image(hub_uri)  →  check ACR, import from Docker Hub if missing
    ↓
_generate_dockerfile(dep_set, acr_base_override=...)
    ↓
Build with Kaniko → push to ascend-runtime
    ↓
create_job(config=config)
    ↓
get_node_type_info(config.node_type)
    ↓
Apply node_selector, tolerations, GPU resources
    ↓
Submit Kubernetes Job
```

## GPU Base Image Selection

GPU workloads benefit from using official pre-built Docker images (e.g., `pytorch/pytorch`) that ship with CUDA, cuDNN, and the ML framework pre-installed. This avoids the overhead of installing these large packages from scratch on every image build.

### Three-Tier Selection

Base image selection follows a three-tier priority:

1. **Explicit override** — the user passes `base_image="..."` to `@ascend`
2. **Auto-detection** — the system scans `requirements` for `torch`/`pytorch` and maps the version to the matching Docker Hub image
3. **Fallback** — if no torch dependency is found, the default NVIDIA CUDA base image is used (`nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04`)

```python
# Tier 1: explicit override wins
@ascend(
    node_type="gpu_small",
    base_image="pytorch/pytorch:2.4.0-cuda12.4-cudnn9-runtime",
    requirements=["torch==2.4.0", "transformers"]
)
def train(data): ...

# Tier 2: auto-detected from torch version
@ascend(node_type="gpu_small", requirements=["torch==2.5.1", "lightning"])
def train(data): ...
# → selects pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

# Tier 3: no torch — uses default NVIDIA CUDA image
@ascend(node_type="gpu_small", requirements=["tensorflow"])
def train(data): ...
# → uses nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04
```

### PyTorch ↔ CUDA Compatibility Map

The auto-detection uses the following mapping from PyTorch major.minor to CUDA/cuDNN versions:

| PyTorch | CUDA | cuDNN | Docker Hub Image |
|---------|------|-------|------------------|
| 2.5     | 12.4 | 9     | `pytorch/pytorch:2.5.x-cuda12.4-cudnn9-runtime` |
| 2.4     | 12.4 | 9     | `pytorch/pytorch:2.4.x-cuda12.4-cudnn9-runtime` |
| 2.3     | 12.1 | 8     | `pytorch/pytorch:2.3.x-cuda12.1-cudnn8-runtime` |
| 2.2     | 12.1 | 8     | `pytorch/pytorch:2.2.x-cuda12.1-cudnn8-runtime` |
| 2.1     | 12.1 | 8     | `pytorch/pytorch:2.1.x-cuda12.1-cudnn8-runtime` |

The map lives in `ascend/dependencies/analyzer.py` as `PYTORCH_CUDA_COMPAT`.

### ACR Base Image Caching

To avoid pulling large images from Docker Hub on every build, GPU base images are cached in a dedicated ACR repository (`ascend-gpu-base`):

```
ascend-gpu-base/
├── pytorch-2.5.1-cuda12.4-cudnn9   # cached from Docker Hub
├── pytorch-2.4.0-cuda12.4-cudnn9   # cached from Docker Hub
└── ...
```

**Caching flow:**

1. Convert Docker Hub URI to ACR tag via `docker_hub_uri_to_acr_tag()`
   - `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime` → `("ascend-gpu-base", "pytorch-2.5.1-cuda12.4-cudnn9")`
2. Check if the tag exists in ACR
3. If missing, run an `az acr import` to pull from Docker Hub and cache it
4. Rewrite the Dockerfile `FROM` to reference the ACR-cached copy

This makes subsequent Kaniko builds significantly faster because the base image is already local to the cluster's ACR.

### GPU Dockerfile Generation

The generated Dockerfile differs depending on the base image:

**PyTorch base images** (already include Python + pip):
```dockerfile
FROM {acr_registry}/ascend-gpu-base:pytorch-2.5.1-cuda12.4-cudnn9
RUN pip install --no-cache-dir cloudpickle>=3.0.0 fsspec>=2024.2 ...
COPY runner.py /workspace/runner.py
RUN pip install --no-cache-dir torch==2.5.1 lightning ...
WORKDIR /workspace
ENTRYPOINT ["python", "/workspace/runner.py"]
```

**Generic NVIDIA CUDA images** (need Python installed):
```dockerfile
FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04
RUN apt-get update && apt-get install -y python3 python3-pip ...
RUN pip install --no-cache-dir cloudpickle>=3.0.0 fsspec>=2024.2 ...
COPY runner.py /workspace/runner.py
RUN pip install --no-cache-dir tensorflow ...
WORKDIR /workspace
ENTRYPOINT ["python3", "/workspace/runner.py"]
```

### Impact on Dependency Hashing

The `base_image` field is included in `DependencySet.calculate_hash()`. This means:
- Changing the base image produces a different hash → different cached image
- Two functions with identical requirements but different base images get separate images

## Usage Examples

### Basic GPU Usage

```python
from ascend import ascend

# Pinning torch==2.5.1 auto-selects pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
@ascend(node_type="gpu_small", requirements=["torch==2.5.1"])
def train_neural_network(data, epochs=10):
    import torch
    
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Training on: {device}")
    
    model = MyModel()
    model.to(device)
    # ... training loop ...
    
    return model

result = train_neural_network(my_data, epochs=50)
```

### Explicit Base Image

```python
# Override auto-detection with a specific CUDA image
@ascend(
    node_type="gpu_small",
    base_image="pytorch/pytorch:2.4.0-cuda12.4-cudnn9-runtime",
    requirements=["torch==2.4.0", "transformers"]
)
def fine_tune(data):
    # Uses the explicitly specified base image
    pass
```

### Multi-GPU Usage

```python
@ascend(node_type="gpu_medium", requirements=["torch==2.5.1"])
def distributed_training(config):
    import torch
    import torch.distributed as dist
    
    if torch.cuda.device_count() > 1:
        model = torch.nn.DataParallel(model)
    
    return trained_model
```

### Memory-Intensive Workload

```python
@ascend(node_type="memory_large", requirements=["pandas", "numpy"])
def process_large_dataset(file_path):
    import pandas as pd
    
    # Load large dataset into memory
    df = pd.read_parquet(file_path)
    
    # Process data
    results = df.groupby("category").agg({"value": ["sum", "mean", "std"]})
    
    return results
```

### Standard Compute

```python
@ascend(node_type="standard_medium", cpu="4", memory="8Gi")
def cpu_intensive_task(data):
    # CPU-bound computation
    return process_data(data)
```

## Node Pool Configuration

### Azure AKS Setup

To use GPU nodes, your AKS cluster needs a GPU node pool:

```python
# Example node pool configuration
ManagedClusterAgentPoolProfile(
    name="gpu",
    count=0,  # Autoscale from 0
    vm_size="Standard_NC6s_v3",  # V100 GPU
    mode="User",
    os_type="Linux",
    enable_auto_scaling=True,
    min_count=0,
    max_count=5,
    node_taints=["sku=gpu:NoSchedule"],  # Taint to prevent non-GPU pods
    node_labels={
        "agentpool": "gpu",
        "accelerator": "nvidia-tesla-v100"
    }
)
```

### Required Components

GPU nodes require the NVIDIA device plugin:

```bash
kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.14.0/nvidia-device-plugin.yml
```

### Node Pool Types

Your cluster should have separate node pools for different workload types:

1. **System Pool** (`system`): Kubernetes system components
   - VM: Standard_D2s_v3
   - Always on, minimal size

2. **User Pool** (`user`): Standard CPU workloads
   - VM: Standard_D4s_v3
   - Autoscales 0-10 nodes

3. **Memory Pool** (`memory`): Memory-intensive workloads
   - VM: Standard_E8s_v3
   - Autoscales 0-5 nodes

4. **GPU Pool** (`gpu`): GPU workloads
   - VM: Standard_NC6s_v3/NC12s_v3/NC24s_v3
   - Autoscales 0-3 nodes (expensive!)
   - Tainted to prevent non-GPU scheduling

## Validation and Error Handling

### Node Pool Validation

The system validates that requested node types are available:

```python
from ascend.cloud.node_pool_validator import validate_node_pool_availability

# Validate before job submission
is_valid = validate_node_pool_availability(
    node_type=NodeType.GPU_SMALL,
    resource_group="my-resource-group",
    cluster_name="my-cluster",
    subscription_id="...",
    raise_on_error=True  # Raise exception if not available
)
```

### Validation Approach

1. **Kubernetes API First**: Check if nodes with required labels exist (fast)
2. **Azure API Fallback**: Query AKS for node pools with required VM sizes
3. **Graceful Degradation**: If validation unavailable, allow with warning

### Error Messages

```python
# Invalid node type
ValueError: Invalid node_type 'gpu_xlarge'. Must be one of: 
    standard_small, standard_medium, standard_large,
    memory_medium, memory_large, 
    gpu_small, gpu_medium, gpu_large

# Node pool not available
ValueError: Node type 'gpu_small' requires node pool 'gpu' 
    but no matching nodes found in cluster
```

## Admin CLI

### Pre-caching GPU Base Images

Administrators can pre-cache GPU base images into ACR so that the first user build is fast:

```bash
# Cache a specific Docker Hub image
ascend admin push-gpu-image --image pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

# Or specify by torch version (resolved to the Docker Hub URI automatically)
ascend admin push-gpu-image --torch-version 2.5.1

# Specify resource group and timeout
ascend admin push-gpu-image --torch-version 2.4.0 \
    --resource-group my-rg --timeout 600
```

This runs `az acr import` to pull the image from Docker Hub and store it in the `ascend-gpu-base` repository in the cluster's ACR.

## Best Practices

### Cost Optimization

1. **Start small**: Use `gpu_small` for testing, scale up only if needed
2. **Use autoscaling**: GPU nodes are expensive, scale to zero when idle
3. **Batch jobs**: Combine multiple training runs to amortize startup costs
4. **Monitor usage**: Track GPU utilization to ensure efficient use
5. **Pre-cache base images**: Use `ascend admin push-gpu-image` to avoid Docker Hub pull latency on first build

### Performance

1. **Right-size**: Don't request `gpu_large` if `gpu_small` suffices
2. **CPU/Memory**: Match CPU and memory to your actual workload
3. **Data loading**: Optimize data pipelines to keep GPU busy
4. **Profiling**: Use PyTorch/TensorFlow profilers to identify bottlenecks
5. **Pin torch version**: e.g. `torch==2.5.1` enables auto-detection of the correct CUDA base image

### Development Workflow

1. **Local testing**: Test code locally before using GPU nodes
2. **CPU first**: Verify correctness on CPU nodes (cheaper)
3. **Small scale**: Test with `gpu_small` and small datasets
4. **Scale up**: Move to larger GPU nodes only for production runs

### Code Patterns

```python
# Pattern 1: Conditional GPU usage
@ascend(node_type="gpu_small", requirements=["torch"])
def train(data, use_gpu=True):
    import torch
    device = torch.device("cuda" if use_gpu and torch.cuda.is_available() else "cpu")
    # ... code that works on both CPU and GPU ...

# Pattern 2: Fallback configuration
def get_node_type(dataset_size):
    if dataset_size > 1_000_000:
        return "gpu_medium"
    elif dataset_size > 100_000:
        return "gpu_small"
    else:
        return "standard_medium"

@ascend(node_type=get_node_type(len(data)), requirements=["torch"])
def train(data):
    # Training code
    pass

# Pattern 3: Memory-based selection
@ascend(
    node_type="memory_large" if needs_large_memory else "standard_medium",
    requirements=["pandas"]
)
def process(data):
    # Processing code
    pass
```

### Security Considerations

1. **Node isolation**: GPU nodes are isolated via taints/tolerations
2. **Resource limits**: GPU requests also set limits (no oversubscription)
3. **Namespace isolation**: GPU jobs run in user-specific namespaces
4. **Cost tracking**: GPU usage tagged with user information

## Future Enhancements

1. ~~**Additional GPU types**: Support A100, H100, other accelerators~~ ✅ Implemented
2. **Fractional GPUs**: Support GPU sharing with MPS/MIG
3. **Spot instances**: Use spot VMs for cost-sensitive workloads
4. ~~**Auto-selection**: Automatically pick node type based on code analysis~~ ✅ Base image auto-detection implemented
5. **Cost estimation**: Show estimated costs before job submission
6. **Multi-cloud**: Support GCP TPUs, AWS Trainium/Inferentia
7. **TensorFlow/JAX base images**: Auto-detect and cache TensorFlow or JAX base images (currently PyTorch only)

## References

- [Azure NC-series VMs](https://docs.microsoft.com/en-us/azure/virtual-machines/nc-series)
- [Kubernetes GPU Scheduling](https://kubernetes.io/docs/tasks/manage-gpus/scheduling-gpus/)
- [NVIDIA Device Plugin](https://github.com/NVIDIA/k8s-device-plugin)
- [AKS GPU Node Pools](https://docs.microsoft.com/en-us/azure/aks/gpu-cluster)
