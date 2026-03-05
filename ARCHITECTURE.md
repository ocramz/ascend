# Ascend Technical Architecture

## Table of Contents
1. [Executive Summary](#executive-summary)
2. [System Overview](#system-overview)
3. [Architecture Options: Control Plane vs Serverless](#architecture-options-control-plane-vs-serverless)
4. [Component Architecture](#component-architecture)
5. [Data Flow and Lifecycle](#data-flow-and-lifecycle)
6. [Security and Authentication](#security-and-authentication)
7. [Implementation Details](#implementation-details)
8. [Deployment and Operations](#deployment-and-operations)
9. [Future Considerations](#future-considerations)

## Executive Summary

Ascend is a Python-based framework that enables data scientists to seamlessly execute compute-intensive workloads (e.g., model training) on cloud-based infrastructure without manual cloud operations overhead. The system consists of:

1. **Python Decorator Library**: A decorator (`@ascend`) that transparently remotes decorated functions to cloud execution environments
2. **AKS Backend**: Azure Kubernetes Service-based execution environment for running user workloads

This document evaluates two architectural approaches and provides detailed technical specifications for the recommended implementation.

## System Overview

### High-Level Architecture

```
┌───────────────────────────────────────────────┐
│          User Workstation                     │
│                                               │
│  ┌────────────────────┐                       │
│  │  User Script       │                       │
│  │  (@ascend          │                       │
│  │   decorator)       │                       │
│  └────────┬───────────┘                       │
│           │                                   │
└───────────┼─────────────────────────────────┼─┘
            │                                 │
            │ Code + Dependencies             │ Admin Operations
            │ (via Python API)                │ (via Python API)
            │                                 │
            ▼                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Azure Cloud                               │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │              Azure Kubernetes Service (AKS)                ││
│  │                                                            ││
│  │  ┌──────────────────┐    ┌─────────────────────────────┐   ││
│  │  │ User Workload    │    │  Infrastructure Nodes       │   ││
│  │  │ Nodes            │    │  (Admin Operations)         │   ││
│  │  │                  │    │                             │   ││
│  │  │ ┌──────────┐     │    │  ┌──────────────────┐       │   ││
│  │  │ │Kubernetes│     │    │  │ Image Builder    │       │   ││
│  │  │ │Jobs/     │     │    │  │ (Kaniko in k8s)  │       │   ││
│  │  │ │Services  │     │    │  └──────────────────┘       │   ││
│  │  │ └──────────┘     │    │                             │   ││
│  │  │                  │    │  ┌──────────────────┐       │   ││
│  │  │ (User            │    │  │                  │       │   ││
│  │  │  Containers)     │    │  │                  │       │   ││
│  │  │                  │    │  └──────────────────┘       │   ││
│  │  └──────────────────┘    └─────────────────────────────┘   ││
│  │                                                               ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │             Azure Container Registry (ACR)                   ││
│  │          (User Runtime Container Images)                     ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │             Azure Blob Storage                               ││
│  │       (Code, Dependencies, Artifacts, Logs)                  ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
```

### Design Goals

1. **Minimal User Friction**: Data scientists should write normal Python code with minimal changes
2. **Fast Iteration**: Quick feedback loops for development and debugging
3. **Cloud Agnostic Core**: Clear separation between Kubernetes operations and cloud-specific functionality
4. **Infrastructure as Code**: All cloud operations via official Python SDKs (Azure SDK for Python)
5. **Security First**: Proper isolation, authentication, and authorization throughout



### Serverless Architecture

The client library directly interacts with Azure APIs and Kubernetes without an intermediary control plane server.

**Architecture:**
```
User Workstation
    ├─> Python Decorator Library
    │   ├─> Azure SDK (azure-mgmt-*)
    │   ├─> Kubernetes Python Client
    │   └─> Azure Blob Storage SDK
    │
    └─> Directly communicates with:
        ├─> AKS API Server (via kubectl/k8s client)
        ├─> Azure Container Registry
        └─> Azure Blob Storage
```

**Pros:**
- ✅ **Simpler architecture**: No control plane server to deploy, maintain, or scale
- ✅ **Lower operational overhead**: Fewer moving parts, less infrastructure to monitor
- ✅ **Better security model**: Direct authentication using Azure credentials (Azure AD/Service Principal)
- ✅ **No single point of failure**: No control plane that could become a bottleneck
- ✅ **Lower latency**: Direct communication without proxy layer
- ✅ **Easier development**: Simpler to test and debug locally
- ✅ **Cost effective**: No additional compute resources for control plane
- ✅ **Natural rate limiting**: Azure API rate limits apply per-user credential

**Cons:**
- ❌ **Client complexity**: More logic in client library (but manageable with good abstraction)
- ❌ **Distributed state**: No centralized view of all operations (can use Azure Monitor/Log Analytics)
- ❌ **Client version management**: Users need to keep library updated (standard for Python libraries)
- ❌ **Limited central policy enforcement**: Harder to enforce organization-wide policies (can use Azure Policy instead)

**Best suited for:**
- Individual data scientists or small teams
- Environments where users already have Azure credentials
- Use cases requiring maximum simplicity and reliability

## Component Architecture

### 1. Python Decorator Library (`ascend`)

The core user-facing component that provides the `@ascend` decorator and supporting functionality.

#### Package Structure
```
ascend/
├── __init__.py              # Public API
├── decorator.py             # @ascend decorator implementation
├── config.py                # Configuration management (.ascend.yaml)
├── git_utils.py             # Git repository utilities
├── node_types.py            # Node type definitions and configuration
├── serialization.py         # Code and dependency serialization
├── cli/
│   ├── __init__.py
│   ├── main.py              # Click-based CLI entry point
│   ├── admin.py             # Admin provisioning commands
│   └── user.py              # User self-setup commands
├── runtime/
│   ├── __init__.py
│   ├── executor.py          # Execution orchestration
│   └── streaming.py         # Log/artifact streaming
├── cloud/
│   ├── __init__.py
│   ├── base.py              # Abstract cloud provider interfaces (ABCs)
│   ├── registry.py          # Backend auto-detection and registration
│   ├── azure/
│   │   ├── __init__.py
│   │   ├── auth.py          # Azure credential management
│   │   ├── backend.py       # Azure backend factory
│   │   ├── cli.py           # Azure-specific CLI operations
│   │   ├── infrastructure.py # Idempotent infrastructure provisioning
│   │   ├── compute.py       # Azure compute backend (AKS)
│   │   ├── image_builder.py # Automatic image building (ACR integration)
│   │   ├── node_pool_validator.py # AKS node pool validation
│   │   ├── registry.py      # Azure Container Registry implementation
│   │   └── storage.py       # Azure Blob Storage via fsspec/adlfs
│   └── kubernetes/
│       ├── __init__.py
│       ├── jobs.py          # Job creation/management
│       └── kaniko.py        # Kaniko build job management
├── dependencies/
│   ├── __init__.py
│   └── analyzer.py          # Dependency detection and DependencySet
├── storage/
│   ├── __init__.py
│   ├── metadata.py          # Job metadata management
│   └── paths.py             # Blob storage path utilities
└── utils/
    ├── __init__.py
    ├── errors.py            # Custom exception hierarchy
    ├── job_ids.py           # Content-addressable job ID generation
    ├── naming.py            # Resource naming utilities
    └── structured_logging.py # Structured logging utilities
```


#### Key Features

1. **Automatic Dependency Detection**
   - Analyze `import` statements in function code
   - Detect local module dependencies
   - Generate `requirements.txt` from detected packages

2. **Code Serialization**
   - Use `cloudpickle` for function and closure serialization
   - Support for complex Python objects
   - Handle local imports and module references

3. **Result Handling**
   - Serialize return values and exceptions
   - Upload results to Blob Storage
   - client polls for job completion and returns results or raises exceptions

4. **Error Handling**
   - Capture and re-raise remote exceptions locally
   - Include remote stack traces
   - Timeout handling with cleanup

5. **Git Validation**
   - Validates clean Git working tree before job submission (by default)
   - Attaches commit metadata (hash, branch, author) to job packages
   - Configurable via `git_check` parameter on `@ascend` decorator or in `.ascend.yaml`
   - Projects (`project=True`) always require clean Git state regardless of `git_check`


### 2. AKS Backend Architecture

#### Cluster Design

**Node Pools:**
1. **System Pool** (1-3 nodes, always on)
   - Critical system components
   - Kubernetes control plane workloads
   - Ingress controllers, monitoring

2. **User Workload Pool** (0-N nodes, autoscaling)
   - User job/service execution
   - Scale to zero when idle
   - Different SKUs (CPU, GPU, memory-optimized)

3. **Admin Pool** (1-2 nodes, always on)
   - Image building infrastructure
   - Node pool warming
   - Administrative tasks

**Namespaces:**
- `kube-system`: Kubernetes system components
- `ascend-admin`: Administrative workloads
- `ascend-users-<username>`: Per-user namespace isolation

#### Runtime Container Images

Each user has a container image built with their Python dependencies.

**Base Image Layers:**
```dockerfile
# Base image for CPU python jobs (shared across all users)
FROM python:3.11-slim
# ... 
```

**Image Management:**
- Store in Azure Container Registry (ACR)
- Tag format: `<registry>.azurecr.io/ascend-runtime:<username>-<hash>`
- Rebuild triggered by dependency changes
- Use multi-stage builds to minimize image size

#### Job Execution Model

**Kubernetes Job Template:**
```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: ascend-job-{job-id}
  namespace: ascend-users-{username}
  labels:
    app: ascend
    user: {username}
    job-id: {job-id}
spec:
  backoffLimit: 2
  ttlSecondsAfterFinished: 86400  # 24 hours
  template:
    spec:
      restartPolicy: Never
      serviceAccountName: ascend-user-{username}
      containers:
      - name: executor
        image: {acr}.azurecr.io/ascend-runtime:{username}-{hash}
        env:
        - name: ASCEND_JOB_ID
          value: "{job-id}"
        - name: ASCEND_PACKAGE_URL
          value: "{blob-storage-url}"
        - name: AZURE_STORAGE_ACCOUNT
          value: "{storage-account}"
        resources:
          requests:
            memory: "4Gi"
            cpu: "2"
          limits:
            memory: "8Gi"
            cpu: "4"
        volumeMounts:
        - name: workspace
          mountPath: /workspace
      volumes:
      - name: workspace
        emptyDir: {}
```



## Data Flow and Lifecycle

### Execution Lifecycle

```
1. User Code Execution
   ├─> @ascend decorator invoked
   └─> Function and arguments serialized

2. Package Preparation
   ├─> Analyze dependencies (imports, local modules)
   ├─> Create execution package (code + metadata)
   └─> Generate unique job ID

3. Upload Phase
   ├─> Upload package to Azure Blob Storage
   │   └─> Path: /<user>/<job-id>/package.pkl
   └─> Upload metadata
       └─> Path: /<user>/<job-id>/metadata.json

4. Container Image Check
   ├─> Calculate dependency hash
   ├─> Check if matching image exists in ACR
   └─> If not, trigger image build
       ├─> Create Kubernetes Job for image build
       ├─> Build using Kaniko
       └─> Push to ACR

5. Job Submission
   ├─> Create Kubernetes Job manifest
   ├─> Apply to cluster via k8s API
   └─> Return job ID to client

6. Execution Phase
   ├─> Pod scheduled on appropriate node pool
   ├─> Container pulls image from ACR
   ├─> Container downloads package from Blob Storage
   ├─> Execute user code in container
   |-> Store outputs and logs in blob storage
   └─> Stream logs to stdout/stderr

7. Log Streaming (Parallel)
   ├─> Client opens connection to k8s API
   ├─> Stream pod logs to local terminal
   └─> Display in real-time

8. Result Collection
   ├─> User code serializes return value
   ├─> Upload result to Blob Storage
   │   └─> Path: /<user>/<job-id>/result.pkl
   ├─> Upload artifacts (if any)
   │   └─> Path: /<user>/<job-id>/artifacts/*
   └─> Pod marks job as complete

9. Result Retrieval
   ├─> Client polls job status
   ├─> On completion, download result from Blob Storage
   ├─> Deserialize result
   └─> Return result to user code, or raise exception if job failed

10. Cleanup
    ├─> Job TTL expires (default: 24 hours)
    ├─> Kubernetes deletes Job and Pods
    └─> Optional: Archive artifacts, delete temp files
```

### Data Storage Strategy

**Azure Blob Storage Structure:**
```
<container-name>/
├── users/
│   └── <username>/
│       ├── jobs/
│       │   └── <job-id>/
│       │       ├── package.pkl       # Serialized function + args
│       │       ├── metadata.json     # Job metadata
│       │       ├── result.pkl        # Execution result
│       │       ├── logs/
│       │       │   └── execution.log # Captured logs
│       │       └── artifacts/        # User-generated files
│       │           ├── model.pkl
│       │           ├── plot.png
│       │           └── data.csv
│       └── images/
│           └── <hash>/
│               └── requirements.txt  # Dependency manifest
└── shared/
    └── base-images/
        └── python-3.11/              # Shared base images
```

## Security and Authentication

### Authentication Model

**User Authentication (Serverless):**
```
User Workstation
    └─> Azure Identity (DefaultAzureCredential)
        ├─> Environment variables (for CI/CD)
        ├─> Managed Identity (if on Azure VM)
        ├─> Azure CLI credentials (for local dev)
        └─> Interactive browser (fallback)
```

**Service Authentication:**
- **AKS to ACR**: Managed Identity with AcrPull role
- **AKS to Blob Storage**: Managed Identity with Storage Blob Data Contributor
- **User Pods**: Workload Identity (per-namespace service accounts)

### Authorization Model

**Azure RBAC Roles:**
- **Ascend User**: Can create jobs/services in their namespace
  - `Microsoft.ContainerService/managedClusters/read`
  - `Microsoft.ContainerService/managedClusters/listClusterUserCredential/action`
  - Scoped to specific AKS cluster

- **Ascend Admin**: Can provision infrastructure
  - Full AKS management permissions
  - ACR management
  - Storage account management

**Kubernetes RBAC:**
```yaml
# Per-user role
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: ascend-user-role
  namespace: ascend-users-{username}
rules:
- apiGroups: ["batch"]
  resources: ["jobs"]
  verbs: ["create", "get", "list", "delete"]
- apiGroups: ["apps"]
  resources: ["deployments"]
  verbs: ["create", "get", "list", "delete"]
- apiGroups: [""]
  resources: ["pods", "pods/log"]
  verbs: ["get", "list"]
- apiGroups: [""]
  resources: ["services"]
  verbs: ["create", "get", "list", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ascend-user-binding
  namespace: ascend-users-{username}
subjects:
- kind: ServiceAccount
  name: ascend-user-{username}
  namespace: ascend-users-{username}
roleRef:
  kind: Role
  name: ascend-user-role
  apiGroup: rbac.authorization.k8s.io
```




### User provisioning and Secret Management

#### Admin Responsibilities (admin script):
0. Create AKS cluster and resource group (manual or via IaC)
1. Run `ascend admin bootstrap --resource-group <RG>` to idempotently create storage account, blob container (`ascend-data`), and container registry
2. Run `ascend admin setup --cluster <AKS> --resource-group <RG>` to create k8s user namespace and service account. The username is derived from the current Azure identity automatically (same logic as `ascend user init`). Use `--username <USER>` to override.
3. Ensure the AKS cluster has Storage Blob Data Contributor role or managed identity with appropriate permissions for blob storage access
4. Ensure the AKS cluster has ACRPull and ACRPush permissions or managed identity with appropriate permissions (Kaniko needs push permissions to upload built images)


#### User Responsibilities:
1. User authenticates using Azure credentials (Azure CLI, environment variables, or managed identity)
2. User runs `ascend user init --cluster <AKS> --resource-group <RG>` which verifies access, derives username from their Azure identity, checks that a matching namespace exists, and writes `.ascend.yaml`. Use `--username <NAME>` to override the auto-derived name (e.g. when the admin provisioned a specific username).
3. User can then use `@ascend` decorator to run jobs without further authentication steps


## Deployment and Operations

### Initial Cluster Setup

**1. Prerequisites:**
```bash
# Install Azure CLI
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

# Login to Azure
az login

# Install kubectl
az aks install-cli

# Install ascend CLI
pip install ascend -e ".[dev]"
```

**2. Bootstrap Azure Infrastructure:**

The `ascend admin bootstrap` command idempotently creates the required Azure
resources (storage account, blob container, container registry).  It is safe
to run repeatedly — existing resources are left untouched.

```bash
# Create infrastructure with auto-generated names (deterministic from RG):
uv run ascend admin bootstrap --resource-group my-rg

# With explicit overrides and location:
uv run ascend admin bootstrap --resource-group my-rg \
  --location eastus \
  --storage-account mystorageacct \
  --container-registry myacrregistry
```

The underlying provisioning functions live in
`ascend/cloud/azure/infrastructure.py` and are reused by the integration-test
fixtures to ensure infrastructure exists before running the test suite.

Requirements:
- The resource group **must already exist**.
- The caller needs **Contributor** (or Owner) Azure RBAC on the resource group.

**3. Verify Resources:**


### User Onboarding

**Automated User Setup:**
```bash
# Creates:
# 1. User namespace in AKS
# 2. Service account with RBAC
# 3. Storage container for user data
# 4. Initial runtime image build
```

**Behind the Scenes:**
```python
def initialize_user(username: str, cluster_name: str):
    """Initialize user environment"""
    # 1. Create namespace
    k8s_client.create_namespace(f"ascend-users-{username}")
    
    # 2. Create service account
    create_service_account(username)
    
    # 3. Apply RBAC roles
    apply_rbac_policies(username)
    
    # 4. Create storage container
    blob_client.create_container(f"users/{username}")
    
    # 5. Build initial runtime image
    trigger_image_build(username, python_version="3.11")
    
    # 6. Save configuration
    save_user_config(username, cluster_name)
```

### Monitoring and Observability

**Metrics to Track:**
```yaml
# Prometheus metrics
- ascend_job_total{status="success|failed|timeout"}
- ascend_job_duration_seconds{user, instance_type}
- ascend_job_queue_depth
- ascend_image_build_duration_seconds
- ascend_node_pool_size{pool}
- ascend_cost_estimate_dollars{user, instance_type}
```

**Logging Strategy:**
- **Application Logs**: Captured by Kubernetes (stdout/stderr)
- **Audit Logs**: AKS audit logs to Azure Monitor
- **User Job Logs**: Streamed to user + archived to Blob Storage
- **Centralized Logging**: Azure Log Analytics + Azure Monitor


### Cost Optimization

**Strategies:**
1. **Aggressive Autoscaling**
   - Scale user node pools to zero when idle
   - Use spot instances for fault-tolerant workloads
   
3. **Image Caching**
   - Cache common dependencies in base images
   - Share base layers across users
   
4. **Scheduled Scaling**
   - Scale down during off-hours
   - Keep system pool minimal
   



### Advanced Features

**1. GPU and Accelerator Support** 
- Multiple node types (standard, memory-optimized, GPU-enabled)
- NVIDIA Tesla V100 GPU support
- Automatic Kubernetes configuration for GPU workloads
- See [GPU_SUPPORT.md](docs/GPU_SUPPORT.md) for details

**2. Automatic Image Building**
- Automatic container image building using Kaniko in-cluster
- GPU-aware base image selection (CUDA for GPU workloads, slim Python for CPU)
- Dependency hashing for deterministic image tagging (`user-{hash}`)
- Multi-level caching (ACR image cache + Kaniko layer cache)
- See [AUTOMATIC_IMAGE_BUILDING.md](docs/AUTOMATIC_IMAGE_BUILDING.md) for details

**Implementation Highlights:**
- `DependencySet` class for dependency analysis and SHA256 hashing
- `KanikoJobManager` for Kubernetes Job creation and lifecycle management
- `ImageBuilder` for ACR integration and build orchestration
- Graceful fallback to base images on build failures

## Conclusion

The architecture is designed with:
- **Clear separation** between Kubernetes and cloud-specific code
- **Abstraction layers** that enable future multi-cloud support
- **Extensibility points** for advanced features
- **Migration path** to control plane if needed
