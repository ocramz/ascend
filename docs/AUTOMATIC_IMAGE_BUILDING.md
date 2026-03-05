# Automatic Image Building Architecture

## Table of Contents
1. [Executive Summary](#executive-summary)
2. [User Story and Motivation](#user-story-and-motivation)
3. [Architecture Overview](#architecture-overview)
4. [Component Design](#component-design)
5. [Dependency Detection and Hashing](#dependency-detection-and-hashing)
6. [Image Building Workflow](#image-building-workflow)
7. [Container Build Technologies](#container-build-technologies)
8. [Image Registry Integration](#image-registry-integration)
9. [Caching Strategy](#caching-strategy)
10. [Error Handling and Fallback](#error-handling-and-fallback)
11. [Performance Considerations](#performance-considerations)
12. [Security Considerations](#security-considerations)
13. [Implementation Roadmap](#implementation-roadmap)
14. [Monitoring and Observability](#monitoring-and-observability)
15. [Future Enhancements](#future-enhancements)

## Executive Summary

Automatic image building is a key feature that enables Ascend users to execute their decorated functions without manual Docker image creation or dependency management. When a user calls an `@ascend` decorated function, the system automatically:

1. **Detects dependencies** from the function's code and requirements
2. **Calculates a hash** of the dependency set to identify unique environments
3. **Checks for existing images** in Azure Container Registry (ACR)
4. **Builds new images** on-demand using Kaniko in the AKS cluster
5. **Caches images** for reuse across multiple executions
6. **Falls back gracefully** if image building fails

This document provides a comprehensive architecture for implementing automatic image building in Ascend Phase 2.

## User Story and Motivation

### User Story
**As a user, I would like my runner images to be built automatically in the cloud as soon as the decorated functions are called.**

### Motivation

**Current State (Phase 1):**
- Users must use pre-built base images with common dependencies
- Custom dependencies require manual image building
- No per-user or per-project dependency isolation
- Limited flexibility in dependency management

**Desired State (Phase 2):**
```python
# User just specifies requirements, everything else is automatic
@ascend(
    cpu="2", 
    memory="4Gi",
    requirements=["pandas==2.0.0", "scikit-learn>=1.3.0", "torch==2.1.0"]
)
def train_model(data):
    import pandas as pd
    from sklearn.ensemble import RandomForestClassifier
    import torch
    
    # Training code with specific dependency versions
    return model

# First call: automatically builds image with exact dependencies
result = train_model(my_data)  # Image built: ~2-3 minutes first time

# Subsequent calls: reuses cached image
result2 = train_model(other_data)  # Uses cached image: <5 seconds to start
```

**Benefits:**
- ✅ **Zero manual image management** - users never interact with Docker
- ✅ **Reproducible environments** - exact dependency versions locked
- ✅ **Fast iteration** - cached images enable quick re-execution
- ✅ **Isolated environments** - each dependency set gets its own image
- ✅ **Version pinning** - supports exact dependency specifications
- ✅ **Transparent operation** - happens automatically in the background

## Architecture Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        User Workstation                              │
│                                                                       │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  @ascend decorator invoked                                      │ │
│  │  ├─> Analyze dependencies (requirements + imports)              │ │
│  │  ├─> Calculate dependency hash                                  │ │
│  │  └─> Check if image exists for this hash                        │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                              │                                        │
└──────────────────────────────┼────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     Azure Container Registry (ACR)                   │
│                                                                       │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  Image Repository: ascend-runtime                               │ │
│  │  ├─> ascend-runtime:base-python311                             │ │
│  │  ├─> ascend-runtime:user-<hash1>                               │ │
│  │  ├─> ascend-runtime:user-<hash2>                               │ │
│  │  └─> ... (one image per unique dependency set)                 │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                              │                                        │
└──────────────────────────────┼────────────────────────────────────────┘
                               │
                 Image exists? │
              ┌─────────────────┴──────────────────┐
              │ YES                                 │ NO
              │                                     │
              ▼                                     ▼
      ┌──────────────┐            ┌────────────────────────────────────┐
      │  Use image   │            │      Trigger Image Build           │
      │  directly    │            │                                    │
      └──────────────┘            │  ┌──────────────────────────────┐ │
                                  │  │  1. Upload Dockerfile to     │ │
                                  │  │     Blob Storage             │ │
                                  │  │                              │ │
                                  │  │  2. Create Kaniko Job in AKS │ │
                                  │  │     - Mount Dockerfile       │ │
                                  │  │     - Build in-cluster       │ │
                                  │  │     - Push to ACR            │ │
                                  │  │                              │ │
                                  │  │  3. Wait for build complete  │ │
                                  │  │     - Stream logs to user    │ │
                                  │  │     - Handle errors          │ │
                                  │  │                              │ │
                                  │  │  4. Verify image in ACR      │ │
                                  │  └──────────────────────────────┘ │
                                  └────────────────────────────────────┘
                                                  │
                                                  ▼
                                  ┌────────────────────────────────────┐
                                  │    Execute User Job with Image     │
                                  │                                    │
                                  │  Pod runs with:                    │
                                  │  image: ascend-runtime:user-<hash> │
                                  └────────────────────────────────────┘
```

### Workflow Sequence

```
User calls @ascend function
    │
    ├─> 1. Dependency Analysis
    │   ├─> Parse requirements parameter
    │   ├─> Analyze function imports
    │   ├─> Detect local module dependencies
    │   └─> Combine into full dependency list
    │
    ├─> 2. Hash Calculation
    │   ├─> Normalize dependency list
    │   ├─> Sort dependencies
    │   ├─> Calculate SHA256 hash
    │   └─> Generate image tag: user-<hash>
    │
    ├─> 3. Image Check
    │   ├─> Query ACR for image with tag
    │   │   └─> GET /v2/ascend-runtime/manifests/user-<hash>
    │   │
    │   └─> Image exists?
    │       ├─> YES: Use existing image (fast path)
    │       └─> NO: Trigger build (build path)
    │
    └─> 4. Image Build (if needed)
        ├─> Generate Dockerfile
        ├─> Upload Dockerfile + requirements.txt to Blob Storage
        ├─> Create Kaniko Job in AKS
        ├─> Wait for build (with timeout)
        ├─> Stream build logs to user
        ├─> Verify image pushed to ACR
        └─> Proceed with user job execution
```

## Component Design

### 1. Dependency Analyzer

**Location:** `ascend/dependencies/analyzer.py` (extend existing)

**Responsibilities:**
- Parse explicit requirements from `@ascend` decorator
- Analyze function code for import statements
- Detect local module dependencies
- Generate normalized requirements list
- Calculate dependency hash

**Interface:**
```python
from dataclasses import dataclass
from typing import List, Set, Optional

@dataclass
class DependencySet:
    """Represents a complete set of dependencies for a function"""
    explicit_requirements: List[str]  # From @ascend decorator
    detected_imports: Set[str]        # From code analysis
    local_modules: Set[str]           # Local packages to include
    python_version: str               # e.g., "3.11"
    system_packages: List[str]        # APT packages if needed
    base_image: Optional[str] = None  # GPU base image (auto-detected or explicit)
    
    def normalize(self) -> List[str]:
        """Normalize and sort dependencies for consistent hashing"""
        pass
    
    def calculate_hash(self) -> str:
        """Calculate SHA256 hash of normalized dependencies (includes base_image)"""
        pass
    
    def get_base_image(self, is_gpu: bool) -> str:
        """Return base image: explicit override → auto-detect from torch → fallback"""
        pass
    
    def to_requirements_txt(self) -> str:
        """Generate requirements.txt content"""
        pass

class DependencyAnalyzer:
    """Analyzes function dependencies"""
    
    def analyze_function(
        self,
        func: Callable,
        explicit_requirements: Optional[List[str]] = None,
        python_version: str = "3.11"
    ) -> DependencySet:
        """
        Analyze a function's dependencies.
        
        Args:
            func: The function to analyze
            explicit_requirements: User-specified requirements
            python_version: Target Python version
            
        Returns:
            DependencySet with all detected dependencies
        """
        pass
    
    def detect_imports(self, func: Callable) -> Set[str]:
        """Extract import statements from function code"""
        pass
    
    def detect_local_modules(self, func: Callable) -> Set[str]:
        """Detect local module dependencies"""
        pass
```

### 2. Image Builder Client

**Location:** `ascend/cloud/azure/image_builder.py` (new)

**Responsibilities:**
- Check if image exists in ACR
- Generate Dockerfile for custom image
- Upload build context to Blob Storage
- Create and manage Kaniko build jobs
- Stream build logs
- Verify image availability

**Interface:**
```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class ImageBuildSpec:
    """Specification for building a container image"""
    base_image: str                    # e.g., "python:3.11-slim"
    requirements: List[str]            # Python packages
    system_packages: List[str]         # APT packages
    image_tag: str                     # e.g., "user-abc123def"
    build_context_url: str             # Blob Storage URL
    
@dataclass
class ImageBuildStatus:
    """Status of an image build operation"""
    job_id: str
    status: str                        # "pending", "building", "completed", "failed"
    progress: Optional[str]            # Human-readable progress
    image_uri: Optional[str]           # Full image URI once built
    error_message: Optional[str]       # Error details if failed
    build_logs: Optional[str]          # Build output logs

class ImageBuilder:
    """Manages automatic image building in AKS"""
    
    def __init__(self, acr_client, blob_client, k8s_client):
        self.acr = acr_client
        self.blob = blob_client
        self.k8s = k8s_client
        
    async def get_or_build_image(
        self,
        dependency_set: DependencySet,
        timeout_seconds: int = 600
    ) -> str:
        """
        Get existing image or build new one if needed.
        
        Args:
            dependency_set: Dependencies to include in image
            timeout_seconds: Max time to wait for build
            
        Returns:
            Full image URI (e.g., "myacr.azurecr.io/ascend-runtime:user-abc123")
            
        Raises:
            ImageBuildError: If build fails
            ImageBuildTimeout: If build exceeds timeout
        """
        image_tag = self._generate_image_tag(dependency_set)
        
        # Fast path: check if image exists
        if await self.image_exists(image_tag):
            return self._image_uri(image_tag)
        
        # Slow path: build image
        return await self.build_image(dependency_set, timeout_seconds)
        
    async def image_exists(self, image_tag: str) -> bool:
        """Check if image exists in ACR"""
        pass
        
    async def build_image(
        self,
        dependency_set: DependencySet,
        timeout_seconds: int
    ) -> str:
        """Build new image using Kaniko"""
        # 1. Generate Dockerfile
        dockerfile = self._generate_dockerfile(dependency_set)
        
        # 2. Upload build context to Blob Storage
        context_url = await self._upload_build_context(
            dockerfile,
            dependency_set.to_requirements_txt()
        )
        
        # 3. Create Kaniko Job
        build_spec = ImageBuildSpec(
            base_image=f"python:{dependency_set.python_version}-slim",
            requirements=dependency_set.explicit_requirements,
            system_packages=dependency_set.system_packages,
            image_tag=self._generate_image_tag(dependency_set),
            build_context_url=context_url
        )
        
        job_id = await self._create_kaniko_job(build_spec)
        
        # 4. Wait for build completion
        status = await self._wait_for_build(job_id, timeout_seconds)
        
        if status.status == "failed":
            raise ImageBuildError(status.error_message, status.build_logs)
        
        return status.image_uri
        
    async def stream_build_logs(
        self,
        job_id: str,
        callback: Callable[[str], None]
    ):
        """Stream build logs in real-time"""
        pass
        
    def _generate_dockerfile(self, dependency_set: DependencySet) -> str:
        """Generate Dockerfile content"""
        pass
        
    def _generate_image_tag(self, dependency_set: DependencySet) -> str:
        """Generate image tag from dependency hash"""
        return f"user-{dependency_set.calculate_hash()}"
        
    def _image_uri(self, image_tag: str) -> str:
        """Construct full image URI"""
        return f"{self.acr.registry_url}/ascend-runtime:{image_tag}"
```

### 3. Kaniko Job Manager

**Location:** `ascend/cloud/kubernetes/kaniko.py` (new)

**Responsibilities:**
- Create Kubernetes Jobs for Kaniko builds
- Configure Kaniko with proper credentials
- Monitor build job status
- Retrieve build logs
- Clean up completed jobs

**Interface:**
```python
class KanikoJobManager:
    """Manages Kaniko build jobs in Kubernetes"""
    
    def __init__(self, k8s_client, namespace: str = "ascend-builds"):
        self.k8s = k8s_client
        self.namespace = namespace
        
    async def create_build_job(
        self,
        build_spec: ImageBuildSpec,
        service_account: str = "kaniko-builder"
    ) -> str:
        """
        Create a Kaniko job to build an image.
        
        Args:
            build_spec: Build specification
            service_account: K8s service account with ACR push permissions
            
        Returns:
            Job ID (job name)
        """
        job_manifest = self._generate_job_manifest(build_spec, service_account)
        job = await self.k8s.create_namespaced_job(
            namespace=self.namespace,
            body=job_manifest
        )
        return job.metadata.name
        
    async def get_job_status(self, job_id: str) -> ImageBuildStatus:
        """Get current status of a build job"""
        pass
        
    async def stream_job_logs(
        self,
        job_id: str,
        callback: Callable[[str], None]
    ):
        """Stream logs from build job"""
        pass
        
    async def delete_job(self, job_id: str):
        """Clean up completed build job"""
        pass
        
    def _generate_job_manifest(
        self,
        build_spec: ImageBuildSpec,
        service_account: str
    ) -> dict:
        """Generate Kubernetes Job manifest for Kaniko"""
        pass
```

## Dependency Detection and Hashing

### Dependency Detection Strategy

**1. Explicit Requirements (Primary)**
```python
@ascend(
    requirements=[
        "pandas==2.0.0",
        "scikit-learn>=1.3.0",
        "torch==2.1.0"
    ]
)
def my_function():
    pass
```
- User explicitly specifies packages
- Most reliable method
- Supports version pinning

**2. Import Analysis (Secondary)**
```python
def my_function():
    import pandas as pd      # Detected: pandas
    from sklearn import svm   # Detected: scikit-learn
    import torch             # Detected: torch
```
- Parse function AST for import statements
- Map import names to package names (using mapping table)
- Use latest compatible version if no version specified

**3. requirements.txt Detection (Tertiary)**
```python
# If .ascend.yaml or cwd has requirements.txt
@ascend()  # No explicit requirements
def my_function():
    pass
```
- Check for requirements.txt in project
- Use as baseline dependencies
- Combine with detected imports

### Hash Calculation Algorithm

**Goal:** Generate deterministic hash that uniquely identifies a dependency set

**Algorithm:**
```python
def calculate_dependency_hash(dependency_set: DependencySet) -> str:
    """
    Calculate deterministic hash of dependencies.
    
    Hash includes:
    - Python version
    - Normalized package list (sorted)
    - System packages (if any)
    - Base image (if set, for GPU workloads)
    
    Returns: SHA256 hash (first 12 chars for brevity)
    """
    # 1. Normalize package names and versions
    packages = []
    for req in dependency_set.explicit_requirements:
        # Parse and normalize: "pandas>=2.0.0" -> "pandas==2.0.3" (resolved)
        normalized = normalize_requirement(req)
        packages.append(normalized)
    
    # 2. Sort for deterministic order
    packages.sort()
    
    # 3. Create hash input
    hash_input = {
        "python_version": dependency_set.python_version,
        "packages": packages,
        "system_packages": sorted(dependency_set.system_packages),
        "base_image": dependency_set.base_image,  # included so GPU base changes invalidate cache
    }
    
    # 4. Calculate SHA256
    import hashlib
    import json
    hash_str = json.dumps(hash_input, sort_keys=True)
    full_hash = hashlib.sha256(hash_str.encode()).hexdigest()
    
    # 5. Return first 12 characters for brevity
    return full_hash[:12]  # e.g., "a3f5d2c8e9b1"
```

**Hash Examples:**
```
Python 3.11 + pandas==2.0.0 + scikit-learn==1.3.0
  → user-a3f5d2c8e9b1

Python 3.11 + pandas==2.0.0 + scikit-learn==1.3.1  (different version)
  → user-b7c4e8a1f6d9

Python 3.12 + pandas==2.0.0 + scikit-learn==1.3.0  (different Python)
  → user-c9a2f5e7b3d8
```

### Version Resolution

**Strategy for unversioned requirements:**

```python
# User specifies: requirements=["pandas", "torch"]
# We resolve to: requirements=["pandas==2.0.3", "torch==2.1.0"]

def resolve_requirement_versions(requirements: List[str]) -> List[str]:
    """
    Resolve unversioned requirements to specific versions.
    
    Uses pip's resolver or pre-cached version mappings.
    """
    resolved = []
    for req in requirements:
        if has_version_specifier(req):
            # Already versioned: use as-is
            resolved.append(req)
        else:
            # Unversioned: resolve to latest compatible
            version = get_latest_version(req)
            resolved.append(f"{req}=={version}")
    return resolved
```

**Caching version resolutions:**
- Maintain mapping of package name → latest version
- Update daily via background job
- Fallback to pip resolver if not cached

## Image Building Workflow

### Detailed Build Process

**Step 1: Generate Dockerfile**

For **CPU workloads**, a standard Python slim base image is used:

```python
def generate_dockerfile(dependency_set: DependencySet) -> str:
    """Generate Dockerfile for user image"""
    
    base_image = f"python:{dependency_set.python_version}-slim"
    
    dockerfile = f"""
FROM {base_image}
{generate_system_deps_section(dependency_set.system_packages)}
WORKDIR /workspace
COPY --from={ACR_REGISTRY}/ascend-runtime:base-python311 /opt/ascend /opt/ascend
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
ENTRYPOINT ["python", "/opt/ascend/runner.py"]
"""
    return dockerfile
```

For **GPU workloads**, the Dockerfile uses a PyTorch or NVIDIA CUDA base image
(see [GPU Base Image Selection](GPU_SUPPORT.md#gpu-base-image-selection)):

```python
# PyTorch base (already has Python + pip)
dockerfile = f"""
FROM {acr_registry}/ascend-gpu-base:pytorch-2.5.1-cuda12.4-cudnn9
RUN pip install --no-cache-dir cloudpickle>=3.0.0 fsspec>=2024.2 ...
COPY runner.py /workspace/runner.py
RUN pip install --no-cache-dir {user_requirements}
WORKDIR /workspace
ENTRYPOINT ["python", "/workspace/runner.py"]
"""

# Generic NVIDIA CUDA base (needs apt-get python3)
dockerfile = f"""
FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04
RUN apt-get update && apt-get install -y python3 python3-pip ...
RUN pip install --no-cache-dir cloudpickle>=3.0.0 fsspec>=2024.2 ...
COPY runner.py /workspace/runner.py
RUN pip install --no-cache-dir {user_requirements}
WORKDIR /workspace
ENTRYPOINT ["python3", "/workspace/runner.py"]
"""
```

GPU Dockerfiles include `runner.py` directly (via `COPY`) and install the
core runner dependencies (`cloudpickle`, `fsspec`, `adlfs`, `azure-identity`,
`packaging`) explicitly, because they don't use the multi-stage
`COPY --from=base` pattern used by CPU images.

**Step 2: Upload Build Context**

```python
async def upload_build_context(
    dockerfile: str,
    requirements_txt: str
) -> str:
    """Upload build context to Blob Storage"""
    
    build_id = generate_uuid()
    
    # Upload Dockerfile
    await blob_client.upload_blob(
        container="builds",
        blob_name=f"{build_id}/Dockerfile",
        data=dockerfile
    )
    
    # Upload requirements.txt
    await blob_client.upload_blob(
        container="builds",
        blob_name=f"{build_id}/requirements.txt",
        data=requirements_txt
    )
    
    # Return SAS URL for Kaniko to access
    return generate_sas_url(f"builds/{build_id}")
```

**Step 3: Create Kaniko Job**

```yaml
# Kaniko Job Manifest Template
apiVersion: batch/v1
kind: Job
metadata:
  name: ascend-build-{build-id}
  namespace: ascend-builds
  labels:
    app: ascend-image-builder
    build-id: {build-id}
    image-tag: {image-tag}
spec:
  backoffLimit: 1
  ttlSecondsAfterFinished: 3600  # Clean up after 1 hour
  template:
    spec:
      restartPolicy: Never
      serviceAccountName: kaniko-builder  # Has ACR push permissions
      containers:
      - name: kaniko
        image: gcr.io/kaniko-project/executor:v1.19.0
        args:
        - "--dockerfile=/workspace/Dockerfile"
        - "--context={blob-storage-sas-url}"
        - "--destination={acr-registry}/ascend-runtime:{image-tag}"
        - "--cache=true"
        - "--cache-repo={acr-registry}/ascend-cache"
        - "--compressed-caching=false"
        - "--snapshot-mode=redo"
        - "--log-format=text"
        - "--verbosity=info"
        volumeMounts:
        - name: kaniko-secret
          mountPath: /kaniko/.docker
          readOnly: true
        resources:
          requests:
            cpu: "1"
            memory: "2Gi"
          limits:
            cpu: "2"
            memory: "4Gi"
      volumes:
      - name: kaniko-secret
        secret:
          secretName: acr-credentials
          items:
          - key: .dockerconfigjson
            path: config.json
```

**Step 4: Wait for Build Completion**

```python
async def wait_for_build(
    job_id: str,
    timeout_seconds: int = 600
) -> ImageBuildStatus:
    """
    Wait for Kaniko job to complete.
    
    Polls job status every 5 seconds until:
    - Job succeeds
    - Job fails
    - Timeout is reached
    """
    start_time = time.time()
    
    while time.time() - start_time < timeout_seconds:
        status = await get_job_status(job_id)
        
        if status.status == "completed":
            return status
        elif status.status == "failed":
            return status
        
        # Stream logs to user in real-time
        await stream_build_logs(job_id, print_log_line)
        
        await asyncio.sleep(5)
    
    # Timeout reached
    raise ImageBuildTimeout(
        f"Image build did not complete within {timeout_seconds} seconds"
    )
```

**Step 5: Verify Image in ACR**

```python
async def verify_image_pushed(image_tag: str) -> bool:
    """
    Verify image was successfully pushed to ACR.
    
    Checks ACR manifest API to confirm image exists.
    """
    try:
        # Query ACR v2 API
        manifest = await acr_client.get_manifest(
            repository="ascend-runtime",
            tag=image_tag
        )
        return manifest is not None
    except ACRImageNotFound:
        return False
```

### Build Timeline Example

```
T+0s:   User calls @ascend function
T+1s:   Dependencies analyzed, hash calculated
T+2s:   ACR checked - image not found
T+3s:   Dockerfile generated
T+4s:   Build context uploaded to Blob Storage
T+5s:   Kaniko Job created in AKS
T+10s:  Kaniko pod scheduled
T+15s:  Base image pulled
T+30s:  System dependencies installed (if any)
T+45s:  Python dependencies installed (pip install)
T+120s: Image built and compressed
T+180s: Image pushed to ACR
T+185s: Build complete, image verified
T+190s: User job starts with new image

Total: ~3 minutes for first build
Subsequent calls: <5 seconds (cached image)
```

## Container Build Technologies

### Option 1: Kaniko (RECOMMENDED)

**Overview:**
- Builds container images from a Dockerfile inside Kubernetes
- Doesn't require Docker daemon (more secure)
- Can run in unprivileged containers
- Built by Google, widely adopted

**Pros:**
- ✅ No Docker daemon required (better security)
- ✅ Runs in standard Kubernetes pods
- ✅ Unprivileged execution (non-root)
- ✅ Built-in layer caching
- ✅ Direct push to registries
- ✅ Well-documented and maintained
- ✅ Works with Blob Storage as build context

**Cons:**
- ❌ Slightly slower than Docker builds
- ❌ Some Dockerfile features not supported (rare edge cases)

**Implementation:**
```yaml
# Kaniko Job Example
containers:
- name: kaniko
  image: gcr.io/kaniko-project/executor:v1.19.0
  args:
  - "--dockerfile=/workspace/Dockerfile"
  - "--context=https://blobstorage.example.com/context.tar.gz"
  - "--destination=myacr.azurecr.io/ascend-runtime:user-abc123"
  - "--cache=true"
```

### Option 2: BuildKit

**Overview:**
- Next-generation Docker build system
- Used by Docker BuildX
- More features than Kaniko but requires more setup

**Pros:**
- ✅ Faster builds with better caching
- ✅ Full Dockerfile compatibility
- ✅ Concurrent build stages
- ✅ Remote cache support

**Cons:**
- ❌ More complex setup in Kubernetes
- ❌ Requires privileged mode or complex rootless setup
- ❌ Heavier resource requirements

**Recommendation:** Use Kaniko for Phase 2, consider BuildKit for future if build performance becomes critical.

### Option 3: Cloud Build (Azure Container Registry Tasks)

**Overview:**
- Managed build service by Azure ACR
- No in-cluster build infrastructure needed

**Pros:**
- ✅ Fully managed (no cluster resources used)
- ✅ Auto-scaling
- ✅ Built-in security

**Cons:**
- ❌ Additional cost per build minute
- ❌ Less control over build environment
- ❌ Network latency for build context upload

**Recommendation:** Evaluate as alternative if Kaniko performance is insufficient.

## Image Registry Integration

### Azure Container Registry (ACR) Setup

**Registry Configuration:**
```yaml
# ACR should be configured with:
- Name: ascend{hash}acr (hash-based naming to avoid conflicts)
- SKU: Standard or Premium (Premium for geo-replication)
- Admin account: Disabled (use Managed Identity)
- Public network access: Enabled (with network rules if needed)
- Anonymous pull: Disabled
- Content trust: Enabled (for production)
```

**Repository Structure:**
```
ascend-runtime/
├── base-python311          # Base image with Ascend runtime
├── base-python312          # Base image for Python 3.12
├── user-a3f5d2c8e9b1      # User image with specific dependencies
├── user-b7c4e8a1f6d9      # Another user image
└── ...

ascend-gpu-base/            # Cached GPU base images (from Docker Hub)
├── pytorch-2.5.1-cuda12.4-cudnn9   # pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
├── pytorch-2.4.0-cuda12.4-cudnn9   # pytorch/pytorch:2.4.0-cuda12.4-cudnn9-runtime
└── ...

ascend-cache/               # Kaniko layer cache
├── <layer-hash-1>
├── <layer-hash-2>
└── ...
```

### Authentication and Permissions

**Kaniko Service Account:**
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: kaniko-builder
  namespace: ascend-builds
  annotations:
    azure.workload.identity/client-id: {managed-identity-client-id}
---
# Azure Managed Identity with these roles:
# - AcrPush on ACR (push images)
# - Storage Blob Data Reader on build context container (read Dockerfile)
```

**User Pods:**
```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ascend-user
  namespace: ascend-users-{username}
  annotations:
    azure.workload.identity/client-id: {user-managed-identity-client-id}
---
# Azure Managed Identity with these roles:
# - AcrPull on ACR (pull images)
# - Storage Blob Data Contributor on user's container
```

### Image Naming Convention

**Format:** `{registry}.azurecr.io/{repository}:{tag}`

**Examples:**
```
ascendprodacr.azurecr.io/ascend-runtime:base-python311
ascendprodacr.azurecr.io/ascend-runtime:user-a3f5d2c8e9b1
ascendprodacr.azurecr.io/ascend-gpu-base:pytorch-2.5.1-cuda12.4-cudnn9
```

**Tag Strategy:**
- `base-python{version}` for base images (repository: `ascend-runtime`)
- `user-{hash}` for user-specific images (repository: `ascend-runtime`)
- `pytorch-{ver}-cuda{ver}-cudnn{n}` for cached GPU base images (repository: `ascend-gpu-base`)
- Hash is first 12 characters of dependency hash (collision probability: negligible)

### Registry Operations

**Check if Image Exists:**
```python
async def check_image_exists(image_tag: str) -> bool:
    """Check if image exists in ACR using registry API"""
    try:
        # Use ACR REST API v2
        url = f"https://{ACR_REGISTRY}/v2/ascend-runtime/manifests/{image_tag}"
        response = await acr_client.get(url)
        return response.status_code == 200
    except Exception:
        return False
```

**Get Image Digest:**
```python
async def get_image_digest(image_tag: str) -> str:
    """Get image digest for reproducible pulls"""
    manifest = await acr_client.get_manifest(
        repository="ascend-runtime",
        tag=image_tag
    )
    return manifest.config.digest
```

**List User Images:**
```python
async def list_user_images(username: str) -> List[str]:
    """List all images built for a user"""
    tags = await acr_client.list_tags(repository="ascend-runtime")
    return [tag for tag in tags if tag.startswith(f"user-")]
```

## Caching Strategy

### Multi-Level Caching

**Level 1: Image Tag Cache (ACR)**
- Check if image with specific tag exists in ACR
- Fastest check: single API call
- Reuses exact image if dependencies match

**Level 2: GPU Base Image Cache (ACR)**
- GPU base images from Docker Hub are cached in `ascend-gpu-base` repository
- `_ensure_gpu_base_image()` checks ACR first, imports via `az acr import` on miss
- Subsequent Kaniko builds reference the ACR-local copy → no Docker Hub pull

**Level 3: Layer Cache (Kaniko)**
- Kaniko reuses base image layers
- Kaniko reuses intermediate build layers
- Stored in ACR cache repository

**Level 4: Dependency Cache (Pip)**
- Pre-populate pip cache in base images
- Common packages already downloaded
- Speeds up dependency installation

### GPU Base Image Caching

GPU base images (e.g. `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime`, ~6 GB) are
expensive to pull from Docker Hub on every build. The system caches them in ACR:

```python
def _ensure_gpu_base_image(self, docker_hub_uri: str) -> Optional[str]:
    """
    Ensure a Docker Hub GPU image is cached in ACR.

    1. Convert URI to ACR repo + tag via docker_hub_uri_to_acr_tag()
    2. Check if the tag already exists in ACR
    3. If missing, run `az acr import --source <hub_uri> ...`
    4. Return the ACR-local image URI, or None on failure
    """
```

The helper `docker_hub_uri_to_acr_tag()` (in `ascend/cloud/azure/registry.py`)
converts Docker Hub URIs:

| Docker Hub URI | ACR Repository | ACR Tag |
|---|---|---|
| `pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime` | `ascend-gpu-base` | `pytorch-2.5.1-cuda12.4-cudnn9` |
| `nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04` | `ascend-gpu-base` | `nvidia-cuda-12.1.0-cudnn8` |

Admins can pre-populate the cache with `ascend admin push-gpu-image`.

### Caching Algorithm

```python
async def get_or_build_image(dependency_set: DependencySet) -> str:
    """
    Get image with multi-level caching.
    
    Level 1: Check ACR for exact image match
    Level 2: Build with Kaniko layer cache
    Level 3: Pip cache in base image
    """
    image_tag = generate_image_tag(dependency_set)
    
    # Level 1: Check ACR
    if await image_exists_in_acr(image_tag):
        logger.info(f"Image cache HIT: {image_tag}")
        return image_uri(image_tag)
    
    logger.info(f"Image cache MISS: {image_tag}")
    logger.info("Building new image with Kaniko (may use layer cache)")
    
    # Level 2+3: Build with Kaniko (uses layer and pip cache)
    return await build_image(dependency_set)
```

### Cache Invalidation

**When to rebuild:**
- Dependency version changes
- Python version changes
- Base image updated (security patches)
- User explicitly requests rebuild

**When to reuse:**
- Exact same dependency hash
- Same Python version
- Base image unchanged

**Cache Warming:**
```python
async def warm_cache_for_common_dependencies():
    """
    Pre-build images for common dependency sets.
    
    Run as background job to populate cache.
    """
    common_sets = [
        ["pandas", "numpy", "scikit-learn"],
        ["torch", "torchvision"],
        ["tensorflow", "keras"],
        ["pandas", "matplotlib", "seaborn"],
    ]
    
    for deps in common_sets:
        dependency_set = create_dependency_set(deps)
        await build_image(dependency_set)
```

### Cache Cleanup

**Strategy:**
- Keep images used in last 30 days
- Delete images not used in 90 days
- Keep base images forever
- Implement LRU eviction if storage limit reached

```python
async def cleanup_unused_images():
    """
    Clean up unused images from ACR.
    
    Run daily as scheduled job.
    """
    all_images = await acr_client.list_tags("ascend-runtime")
    
    for image_tag in all_images:
        if image_tag.startswith("base-"):
            continue  # Keep base images
        
        last_pull = await get_last_pull_time(image_tag)
        
        if days_since(last_pull) > 90:
            logger.info(f"Deleting unused image: {image_tag}")
            await acr_client.delete_tag("ascend-runtime", image_tag)
```

## Error Handling and Fallback

### Error Scenarios

**1. Build Timeout**
```python
class ImageBuildTimeout(Exception):
    """Raised when image build exceeds timeout"""
    pass

# Handle timeout
try:
    image = await build_image(dependency_set, timeout_seconds=600)
except ImageBuildTimeout:
    # Fallback: use base image without custom dependencies
    logger.warning("Build timeout, falling back to base image")
    return use_base_image_with_runtime_install(dependency_set)
```

**2. Build Failure**
```python
class ImageBuildError(Exception):
    """Raised when image build fails"""
    def __init__(self, message: str, logs: str):
        self.message = message
        self.logs = logs

# Handle build failure
try:
    image = await build_image(dependency_set)
except ImageBuildError as e:
    # Show user the build logs
    print(f"Image build failed: {e.message}")
    print("Build logs:")
    print(e.logs)
    
    # Fallback: use base image
    return use_base_image_with_runtime_install(dependency_set)
```

**3. Dependency Resolution Failure**
```python
# Handle incompatible dependencies
try:
    resolved = resolve_dependencies(requirements)
except DependencyConflict as e:
    raise ValueError(
        f"Dependency conflict: {e}\n"
        f"Please specify compatible versions explicitly."
    )
```

**4. ACR Connection Failure**
```python
# Retry with exponential backoff
@retry(max_attempts=3, backoff=2.0)
async def check_image_exists(image_tag: str) -> bool:
    try:
        return await acr_client.check_image(image_tag)
    except ACRConnectionError:
        logger.warning("ACR connection failed, retrying...")
        raise  # Will be retried
```

### Fallback Strategies

**Strategy 1: Base Image + Runtime Install**
```python
def use_base_image_with_runtime_install(dependency_set: DependencySet) -> str:
    """
    Fallback: use base image and install dependencies at runtime.
    
    Slower (installs on every run) but more reliable.
    """
    # Modify job to install packages before running user code
    return {
        "image": "ascend-runtime:base-python311",
        "init_commands": [
            f"pip install {' '.join(dependency_set.explicit_requirements)}"
        ]
    }
```

**Strategy 2: Use Closest Matching Image**
```python
async def find_closest_matching_image(
    dependency_set: DependencySet
) -> Optional[str]:
    """
    Find existing image with similar dependencies.
    
    Useful if build fails but similar image exists.
    """
    target_hash = dependency_set.calculate_hash()
    all_images = await acr_client.list_tags("ascend-runtime")
    
    # Find images with overlapping dependencies
    # (simplified - real implementation would parse manifests)
    similar_images = [
        img for img in all_images
        if hamming_distance(img, target_hash) < 3
    ]
    
    if similar_images:
        logger.info(f"Using similar image: {similar_images[0]}")
        return similar_images[0]
    
    return None
```

**Strategy 3: Notify User and Retry Later**
```python
async def schedule_build_retry(dependency_set: DependencySet):
    """
    Schedule build retry for later.
    
    User continues with fallback, build retries in background.
    """
    await job_queue.enqueue(
        "build_image",
        args=[dependency_set],
        retry_policy="exponential"
    )
    
    print("Image build scheduled for retry.")
    print("Your current job will use the base image.")
    print("Future jobs will use the optimized image once built.")
```

### User Communication

**Build Progress Updates:**
```python
def print_build_progress(status: ImageBuildStatus):
    """Display build progress to user"""
    messages = {
        "pending": "⏳ Image build starting...",
        "building": f"🔨 Building image: {status.progress}",
        "completed": "✅ Image built successfully!",
        "failed": f"❌ Image build failed: {status.error_message}"
    }
    print(messages.get(status.status, "Building..."))
```

**Build Time Estimates:**
```python
def estimate_build_time(dependency_set: DependencySet) -> int:
    """Estimate build time based on dependency count"""
    base_time = 60  # Base build time
    per_package_time = 5  # Seconds per package
    
    num_packages = len(dependency_set.explicit_requirements)
    estimated = base_time + (num_packages * per_package_time)
    
    return min(estimated, 600)  # Cap at 10 minutes
```

## Performance Considerations

### Build Performance Optimization

**1. Parallel Builds**
```python
# Allow multiple builds to run concurrently
MAX_CONCURRENT_BUILDS = 5

async def build_multiple_images(dependency_sets: List[DependencySet]):
    """Build multiple images in parallel"""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_BUILDS)
    
    async def build_with_limit(dep_set):
        async with semaphore:
            return await build_image(dep_set)
    
    return await asyncio.gather(*[
        build_with_limit(dep_set)
        for dep_set in dependency_sets
    ])
```

**2. Build Node Pool**
```yaml
# Dedicated node pool for builds
- name: builds
  count: 2  # Always-on for build capacity
  vm_size: Standard_D4s_v3  # 4 vCPU, 16GB RAM
  mode: User
  labels:
    workload: builds
  taints:
  - key: workload
    value: builds
    effect: NoSchedule
```

**3. Optimize Dockerfile**
```dockerfile
# Bad: Installs packages one by one (slow)
RUN pip install pandas
RUN pip install scikit-learn
RUN pip install torch

# Good: Installs all at once (faster, better caching)
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
```

**4. Pre-populate Base Image**
```dockerfile
# Include common packages in base image
FROM python:3.11-slim

# Pre-install most common data science packages
RUN pip install --no-cache-dir \
    numpy==1.24.0 \
    pandas==2.0.0 \
    scikit-learn==1.3.0 \
    matplotlib==3.7.0

# User images just add additional packages
```

### Runtime Performance

**Fast Path Optimization:**
```python
# Cache ACR lookups in memory
image_cache = TTLCache(maxsize=1000, ttl=300)  # 5 minute TTL

async def get_image(dependency_hash: str) -> str:
    """Get image with in-memory caching"""
    if dependency_hash in image_cache:
        return image_cache[dependency_hash]
    
    image_uri = await acr_client.get_image_uri(dependency_hash)
    image_cache[dependency_hash] = image_uri
    return image_uri
```

**Parallel Image Pull:**
```python
# Kubernetes automatically pulls images in parallel
# Ensure imagePullPolicy is set correctly:
spec:
  containers:
  - name: executor
    image: ascend-runtime:user-abc123
    imagePullPolicy: IfNotPresent  # Use cached image if available
```

### Resource Limits

**Build Job Resources:**
```yaml
resources:
  requests:
    cpu: "1"
    memory: "2Gi"
  limits:
    cpu: "2"      # Allow burst to 2 CPUs
    memory: "4Gi"  # Hard limit to prevent OOM
```

**Timeout Configuration:**
```python
BUILD_TIMEOUTS = {
    "default": 600,      # 10 minutes
    "large_build": 1200,  # 20 minutes (many dependencies)
    "small_build": 300,   # 5 minutes (few dependencies)
}

def get_build_timeout(dependency_set: DependencySet) -> int:
    """Select appropriate timeout based on build size"""
    num_packages = len(dependency_set.explicit_requirements)
    
    if num_packages < 5:
        return BUILD_TIMEOUTS["small_build"]
    elif num_packages > 15:
        return BUILD_TIMEOUTS["large_build"]
    else:
        return BUILD_TIMEOUTS["default"]
```

## Security Considerations

### Build Security

**1. Sandboxed Builds**
- Builds run in isolated Kubernetes pods
- No access to user data or other builds
- Network policies restrict communication

**2. Supply Chain Security**
```python
# Verify base image digests
APPROVED_BASE_IMAGES = {
    "python:3.11-slim": "sha256:abc123...",
    "python:3.12-slim": "sha256:def456...",
}

def validate_base_image(image: str, digest: str):
    """Ensure base image hasn't been tampered with"""
    expected_digest = APPROVED_BASE_IMAGES.get(image)
    if expected_digest != digest:
        raise SecurityError(f"Base image digest mismatch: {image}")
```

**3. Dependency Scanning**
```python
async def scan_dependencies_for_vulnerabilities(
    requirements: List[str]
) -> List[SecurityAlert]:
    """
    Scan dependencies for known vulnerabilities.
    
    Integration with tools like:
    - pip-audit
    - Safety
    - Snyk
    """
    vulnerabilities = []
    
    for package in requirements:
        vulns = await vulnerability_scanner.scan(package)
        if vulns:
            vulnerabilities.extend(vulns)
    
    return vulnerabilities

# Warn user if vulnerabilities found
alerts = await scan_dependencies_for_vulnerabilities(requirements)
if alerts:
    print("⚠️  Security vulnerabilities detected:")
    for alert in alerts:
        print(f"  - {alert.package}: {alert.description}")
    print("\nConsider updating to patched versions.")
```

**4. Secret Management**
```yaml
# ACR credentials stored as Kubernetes Secret
apiVersion: v1
kind: Secret
metadata:
  name: acr-credentials
  namespace: ascend-builds
type: kubernetes.io/dockerconfigjson
data:
  .dockerconfigjson: <base64-encoded-docker-config>
---
# Access via Workload Identity (preferred)
# Secret is only used as fallback
```

**5. Network Isolation**
```yaml
# Network policy for build namespace
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: build-isolation
  namespace: ascend-builds
spec:
  podSelector:
    matchLabels:
      app: ascend-image-builder
  policyTypes:
  - Egress
  egress:
  # Allow ACR access
  - to:
    - namespaceSelector: {}
    ports:
    - protocol: TCP
      port: 443
  # Allow Blob Storage access
  - to:
    - namespaceSelector: {}
    ports:
    - protocol: TCP
      port: 443
  # Allow DNS
  - to:
    - namespaceSelector: {}
    ports:
    - protocol: UDP
      port: 53
```

### Runtime Security

**1. Image Signing**
```python
# Sign images after build (Docker Content Trust / Notary)
async def sign_image(image_uri: str):
    """Sign image with private key"""
    await notary_client.sign(
        image=image_uri,
        key=SIGNING_KEY
    )

# Verify signature before use
async def verify_image_signature(image_uri: str) -> bool:
    """Verify image signature"""
    return await notary_client.verify(
        image=image_uri,
        key=PUBLIC_KEY
    )
```

**2. Image Scanning**
```python
# Scan built images for vulnerabilities
async def scan_image(image_uri: str) -> ScanResult:
    """
    Scan image with Azure Defender or Trivy.
    
    Checks for:
    - Vulnerable packages
    - Malware
    - Misconfigurations
    """
    return await image_scanner.scan(image_uri)
```

**3. RBAC for Builds**
```yaml
# Only build service account can push images
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: image-builder
  namespace: ascend-builds
rules:
- apiGroups: ["batch"]
  resources: ["jobs"]
  verbs: ["create", "get", "list", "delete"]
- apiGroups: [""]
  resources: ["pods", "pods/log"]
  verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: kaniko-builder-binding
  namespace: ascend-builds
subjects:
- kind: ServiceAccount
  name: kaniko-builder
roleRef:
  kind: Role
  name: image-builder
  apiGroup: rbac.authorization.k8s.io
```

## Implementation Roadmap

### Phase 2.1: Core Image Building (Weeks 5-6)

**Week 5:**
- ✅ Extend DependencyAnalyzer for hash calculation
- ✅ Implement ImageBuilder client
- ✅ Create Kaniko job templates
- ✅ Implement ACR image checking

**Week 6:**
- ✅ Implement build triggering and job creation
- ✅ Add build log streaming
- ✅ Implement build status polling
- ✅ Add basic error handling

**Deliverable:** Basic automatic image building working end-to-end

### Phase 2.2: Caching and Performance (Week 7)

**Week 7:**
- ✅ Implement multi-level caching
- ✅ Add Kaniko layer cache configuration
- ✅ Optimize Dockerfile generation
- ✅ Add build timeout handling
- ✅ Implement parallel build support

**Deliverable:** Fast image reuse, optimized build times

### Phase 2.3: Robustness and UX (Week 8)

**Week 8:**
- ✅ Add fallback strategies
- ✅ Improve user progress messages
- ✅ Add build time estimates
- ✅ Implement cache warming
- ✅ Add cache cleanup job

**Deliverable:** Production-ready image building with good UX

### Phase 2.4: Security and Monitoring (Week 9)

**Week 9:**
- ✅ Add dependency vulnerability scanning
- ✅ Implement image signing
- ✅ Add security scanning
- ✅ Add metrics and monitoring
- ✅ Document security best practices

**Deliverable:** Secure, observable image building

## Monitoring and Observability

### Metrics

**Build Metrics:**
```python
# Prometheus metrics
build_duration = Histogram(
    'ascend_image_build_duration_seconds',
    'Time to build container image',
    buckets=[30, 60, 120, 300, 600, 1200]
)

build_total = Counter(
    'ascend_image_builds_total',
    'Total number of image builds',
    ['status']  # success, failed, timeout
)

build_cache_hits = Counter(
    'ascend_image_cache_hits_total',
    'Number of image cache hits'
)

build_queue_depth = Gauge(
    'ascend_build_queue_depth',
    'Number of builds in queue'
)
```

**Usage Metrics:**
```python
image_usage = Counter(
    'ascend_image_usage_total',
    'Number of times each image was used',
    ['image_tag']
)

unique_dependency_sets = Gauge(
    'ascend_unique_dependency_sets',
    'Number of unique dependency sets'
)
```

### Logging

**Structured Build Logs:**
```python
logger.info(
    "image_build_started",
    extra={
        "image_tag": image_tag,
        "dependency_hash": dep_hash,
        "num_packages": len(packages),
        "python_version": python_version,
    }
)

logger.info(
    "image_build_completed",
    extra={
        "image_tag": image_tag,
        "duration_seconds": duration,
        "image_size_mb": size_mb,
        "cache_hit": cache_hit,
    }
)
```

### Alerting

**Build Failure Alert:**
```yaml
alert: HighImageBuildFailureRate
expr: |
  sum(rate(ascend_image_builds_total{status="failed"}[5m]))
  / 
  sum(rate(ascend_image_builds_total[5m]))
  > 0.2
for: 10m
annotations:
  summary: "High image build failure rate"
  description: "{{ $value }}% of image builds are failing"
```

**Build Timeout Alert:**
```yaml
alert: ImageBuildTimeout
expr: ascend_image_build_duration_seconds > 600
for: 5m
annotations:
  summary: "Image build taking too long"
  description: "Build {{ $labels.image_tag }} exceeded 10 minutes"
```

### Dashboards

**Grafana Dashboard Panels:**
```yaml
Build Success Rate:
  - Query: sum(rate(ascend_image_builds_total{status="success"}[5m]))
  - Type: Gauge
  - Target: > 95%

Average Build Time:
  - Query: avg(ascend_image_build_duration_seconds)
  - Type: Graph
  - Goal: < 180 seconds

Cache Hit Rate:
  - Query: rate(ascend_image_cache_hits_total[5m]) / rate(ascend_image_builds_total[5m])
  - Type: Gauge
  - Target: > 80%

Active Builds:
  - Query: ascend_build_queue_depth
  - Type: Gauge
  - Alert: > 10
```

## Future Enhancements

### Phase 3+: Advanced Features

**1. Multi-Architecture Builds**
```python
# Build images for multiple architectures
async def build_multiarch_image(dependency_set: DependencySet):
    """Build for amd64 and arm64"""
    return await asyncio.gather(
        build_image(dependency_set, arch="amd64"),
        build_image(dependency_set, arch="arm64")
    )
```

**2. Incremental Builds**
```python
# Only rebuild layers that changed
async def incremental_build(
    old_dependency_set: DependencySet,
    new_dependency_set: DependencySet
):
    """Build only changed layers"""
    added = new_dependency_set - old_dependency_set
    if len(added) < 3:  # Only a few packages added
        return await build_layer_on_top(old_image, added)
    else:
        return await build_image(new_dependency_set)
```

**3. Pre-built Image Recommendations**
```python
async def suggest_similar_images(dependency_set: DependencySet):
    """Suggest existing images that are similar"""
    all_images = await get_all_user_images()
    similar = find_similar_dependency_sets(dependency_set, all_images)
    
    if similar:
        print("💡 Similar images exist that you could use:")
        for img in similar[:3]:
            print(f"  - {img.tag}: {img.packages}")
```

**4. Build Acceleration with Remote Cache**
```python
# Use remote cache for faster builds
kaniko_args = [
    "--cache=true",
    "--cache-repo={acr}/ascend-cache",
    "--cache-ttl=168h",  # 1 week
]
```

**5. Dependency Conflict Detection**
```python
async def check_dependency_conflicts(requirements: List[str]):
    """Detect conflicting dependencies before building"""
    resolver = DependencyResolver()
    try:
        resolved = await resolver.resolve(requirements)
        return resolved
    except ConflictError as e:
        raise ValueError(
            f"Dependency conflict detected:\n{e}\n"
            f"Suggestions:\n{e.suggestions}"
        )
```

**6. Build Cost Optimization**
```python
# Track and optimize build costs
async def estimate_build_cost(dependency_set: DependencySet) -> float:
    """Estimate Azure cost for build"""
    build_time = estimate_build_time(dependency_set)
    compute_cost = (build_time / 3600) * NODE_HOURLY_COST
    storage_cost = estimate_image_size(dependency_set) * STORAGE_GB_COST
    return compute_cost + storage_cost
```

---

## Conclusion

Automatic image building is a critical feature that dramatically improves the Ascend user experience by eliminating manual Docker operations. The architecture presented here provides:

✅ **Seamless automation** - images build automatically when needed
✅ **Intelligent caching** - fast reuse of existing images  
✅ **Robust error handling** - graceful fallbacks on failure
✅ **Security-first design** - isolated builds, scanned dependencies
✅ **Production-ready** - monitoring, alerting, and observability
✅ **Future-proof** - extensible for advanced features

The implementation roadmap spreads the work across 4 weeks in Phase 2, delivering incremental value:
- Week 5-6: Core functionality
- Week 7: Performance optimization
- Week 8: Robustness and UX
- Week 9: Security and monitoring

This design enables users to focus on their data science work while Ascend handles all infrastructure complexity automatically.
