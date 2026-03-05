# Multi-Cloud Backend Refactoring

> Make Ascend cloud-agnostic by moving Azure-specific code behind optional extras.

## Table of Contents

- [Executive Summary](#executive-summary)
- [Motivation](#motivation)
- [Current State Assessment](#current-state-assessment)
  - [Azure Coupling Map](#azure-coupling-map)
  - [Already Cloud-Agnostic](#already-cloud-agnostic)
- [Target Architecture](#target-architecture)
  - [Dependency Groups](#1-dependency-groups)
  - [Backend Auto-Detection](#2-backend-auto-detection)
  - [Revised ABCs](#3-revised-abcs)
  - [Azure Backend Implementation](#4-azure-backend-implementation)
  - [Runtime Executor Refactor](#5-runtime-executor-refactor)
  - [Kubernetes Layer Refactor](#6-kubernetes-layer-refactor)
  - [In-Pod Runner Refactor](#7-in-pod-runner-refactor)
  - [CLI Refactor](#8-cli-refactor)
  - [Config Changes](#9-config-changes)
- [Storage Abstraction: fsspec](#storage-abstraction-fsspec)
- [Registry Abstraction: docker library](#registry-abstraction-docker-library)
- [Adding a New Cloud Backend](#adding-a-new-cloud-backend)
- [Migration Checklist](#migration-checklist)
- [Verification](#verification)

---

## Executive Summary

Ascend currently hardcodes Azure SDK calls across the runtime executor, Kubernetes job layer, CLI, in-pod runner, and Dockerfile. The abstract base classes in `ascend/cloud/base.py` (`CloudStorage`, `ContainerRegistry`, `ComputeBackend`) exist but are **completely unused** — no concrete class implements them and no code calls them.

This refactoring:

1. **Makes Azure an optional extra** — `pip install ascend[azure]`. Installing without a backend (`pip install ascend`) raises an `ImportError` on import.
2. **Replaces direct Azure Blob SDK calls with `fsspec`** for cloud storage, enabling any fsspec-compatible backend (Azure via `adlfs`, AWS via `s3fs`, GCP via `gcsfs`).
3. **Introduces a thin `ContainerRegistry` interface** backed by the `docker` Python library for image existence checks.
4. **Wires up and revises the existing ABCs** so that concrete backends actually implement them and all cloud I/O flows through the abstraction.
5. **Keeps Kubernetes as a core dependency** — all supported backends use K8s.
6. **Uses a single universal runner image** with fsspec, making the in-pod runner inherently multi-cloud (the cloud is encoded in the URI scheme: `az://`, `s3://`, `gs://`).

---

## Motivation

- **Portability**: Users should be able to run Ascend on AWS EKS or GCP GKE without forking the library.
- **Lighter installs**: Users who only need Azure should not be forced to install GCP SDKs and vice versa.
- **Clean architecture**: The existing ABCs in `base.py` are dead code. Wiring them in enforces separation of concerns and makes the codebase easier to test (mock the interface, not 8 Azure SDK modules).
- **Fail-fast feedback**: If no backend is installed, the user should see a clear error immediately on import, not a cryptic `ModuleNotFoundError` deep in a stack trace.

---

## Current State Assessment

### Azure Coupling Map

| Module | Azure Coupling | Severity |
|--------|---------------|----------|
| `runtime/executor.py` | Top-level imports of `BlobServiceClient`, `get_azure_credential`, `upload_package`, `download_result`. Constructs `https://{account}.blob.core.windows.net` URLs. Calls `metadata_blob.upload_blob()` directly. | **Critical** |
| `docker/runner.py` | Top-level `BlobServiceClient` import. Constructs Azure Blob URLs. Uses `DefaultAzureCredential`. | **Critical** |
| `docker/Dockerfile.runtime` | Hardcodes `pip install azure-storage-blob azure-identity`. | **High** |
| `cloud/kubernetes/jobs.py` | Hardcodes `.azurecr.io` domain detection. Injects `AZURE_STORAGE_ACCOUNT` env var into pods. | **High** |
| `cloud/kubernetes/kaniko.py` | `acr_registry` field name. References `acr-credentials` K8s secret. | **Medium** |
| `cloud/node_pool_validator.py` | Top-level imports of `DefaultAzureCredential`, `ContainerServiceClient`. Lives outside `cloud/azure/` despite being 100% Azure AKS code. | **Medium** |
| `cloud/azure/image_builder.py` | `ContainerRegistryClient` top-level import. Does not implement `ContainerRegistry` ABC. | **Medium** |
| `cli/admin.py` | `StorageManagementClient`, `ContainerRegistryManagementClient` imports. | **Medium** |
| `cli/user.py` | 4 Azure mgmt imports. Derives username from Azure JWT. | **Medium** |
| `config.py` | Azure-oriented field names (`storage_account`, `container_registry`, `resource_group`). No `cloud_provider` field. | **Low** |

### Already Cloud-Agnostic

These modules require **no changes**:

- `ascend/decorator.py` (except it transitively depends on the Azure-coupled executor)
- `ascend/storage/paths.py` — pure string path construction
- `ascend/storage/metadata.py` — dataclasses with JSON serialization
- `ascend/runtime/streaming.py` — pure K8s watch API
- `ascend/dependencies/analyzer.py`
- `ascend/node_types.py`
- `ascend/serialization.py`
- `ascend/utils/*`
- `ascend/__init__.py`

---

## Target Architecture

### 1. Dependency Groups

Restructure `pyproject.toml` so Azure SDK packages are optional:

```toml
[project]
dependencies = [
    "cloudpickle>=3.0",
    "click>=8.1",
    "pyyaml>=6.0",
    "kubernetes>=29.0",
    "rich>=13.7",
    "requests>=2.31",
    "fsspec>=2024.2",       # Cloud storage abstraction
    "docker>=7.0",          # Container registry abstraction
]

[project.optional-dependencies]
azure = [
    "azure-identity>=1.15",
    "azure-storage-blob>=12.19",
    "azure-mgmt-containerservice>=34.0",
    "azure-mgmt-storage>=21.1",
    "azure-mgmt-containerregistry>=10.3",
    "azure-mgmt-resource>=23.1",
    "azure-containerregistry>=1.2",
    "azure-mgmt-msi>=7.0",
    "azure-mgmt-authorization>=4.0",
    "adlfs>=2024.4",       # fsspec Azure backend
]
# Future:
# gcp = ["gcsfs>=2024.2", "google-cloud-container>=2.40", ...]
# aws = ["s3fs>=2024.2", "boto3>=1.34", ...]
```

Install the current implementation:

```bash
pip install -e ".[azure]"
```

### 2. Backend Auto-Detection

Create `ascend/cloud/registry.py`:

```python
"""Cloud backend auto-detection and registry."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ascend.cloud.base import CloudBackend

# Mapping: extra name -> (probe module, backend factory module)
_BACKENDS: dict[str, tuple[str, str]] = {
    "azure": ("adlfs", "ascend.cloud.azure.backend"),
}

_detected: CloudBackend | None = None


class NoBackendError(ImportError):
    """Raised when no cloud backend extra is installed."""

    def __init__(self) -> None:
        extras = ", ".join(f"ascend[{k}]" for k in _BACKENDS)
        super().__init__(
            f"No cloud backend installed. "
            f"Install one with: pip install {extras}"
        )


def detect_backend_name() -> str:
    """Return the name of the installed backend extra, or raise."""
    found: list[str] = []
    for name, (probe, _) in _BACKENDS.items():
        try:
            importlib.import_module(probe)
            found.append(name)
        except ImportError:
            continue
    if len(found) == 0:
        raise NoBackendError()
    if len(found) > 1:
        raise ImportError(
            f"Multiple cloud backends detected: {found}. "
            f"Set 'cloud_provider' in .ascend.yaml to disambiguate."
        )
    return found[0]


def get_backend() -> CloudBackend:
    """Return the singleton CloudBackend for the detected provider."""
    global _detected
    if _detected is not None:
        return _detected
    name = detect_backend_name()
    _, factory_module = _BACKENDS[name]
    mod = importlib.import_module(factory_module)
    _detected = mod.create_backend()  # each backend module exposes this
    return _detected
```

Wire the import-time guard into `ascend/__init__.py`:

```python
from ascend.cloud.registry import detect_backend_name as _detect

# Fail fast if no backend is installed
_detect()
```

### 3. Revised ABCs

Rewrite `ascend/cloud/base.py`. The key changes:

- **`CloudStorage`** gets low-level `fsspec`-aligned primitives (`write`, `read`, `exists`, `ensure_container`) plus a `get_filesystem()` method. High-level `upload_package` / `download_result` become non-abstract convenience methods that handle `cloudpickle` serialization on top of the primitives.
- **`ContainerRegistry`** keeps only `image_exists`. The `build_image` method moves to a separate `ImageBuilder` class (it's orchestration logic, not a registry operation).
- **`ComputeBackend`** is unchanged.
- A new **`CloudBackend`** dataclass bundles all three plus a credential accessor.

```python
"""Abstract cloud provider interfaces."""

from __future__ import annotations

import cloudpickle
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

import fsspec


class CloudStorage(ABC):
    """Interface for cloud object storage backed by fsspec."""

    @abstractmethod
    def get_filesystem(self) -> fsspec.AbstractFileSystem:
        """Return a configured fsspec filesystem instance."""

    @abstractmethod
    def storage_uri(self, path: str) -> str:
        """Convert a relative storage path to a full URI (e.g. az://container/path)."""

    def write(self, path: str, data: bytes, overwrite: bool = True) -> str:
        """Write bytes to storage. Returns the canonical URI."""
        uri = self.storage_uri(path)
        mode = "wb"
        with self.get_filesystem().open(uri, mode) as f:
            f.write(data)
        return uri

    def read(self, path: str) -> bytes:
        """Read bytes from storage."""
        uri = self.storage_uri(path)
        with self.get_filesystem().open(uri, "rb") as f:
            return f.read()

    def exists(self, path: str) -> bool:
        """Check if a path exists in storage."""
        return self.get_filesystem().exists(self.storage_uri(path))

    @abstractmethod
    def ensure_container(self, name: str) -> None:
        """Ensure the storage container/bucket exists."""

    # --- convenience methods (non-abstract) ---

    def upload_package(
        self, username: str, job_id: str, package: dict,
        project: Optional[str] = None,
    ) -> str:
        from ascend.storage.paths import package_blob_path
        path = package_blob_path(username, job_id, project)
        data = cloudpickle.dumps(package)
        return self.write(path, data)

    def download_result(
        self, username: str, job_id: str,
        project: Optional[str] = None,
    ) -> Any:
        from ascend.storage.paths import result_blob_path
        path = result_blob_path(username, job_id, project)
        return cloudpickle.loads(self.read(path))


class ContainerRegistry(ABC):
    """Interface for container image registry queries."""

    @abstractmethod
    def image_exists(self, repository: str, tag: str) -> bool:
        """Check whether an image tag exists in the registry."""

    @abstractmethod
    def registry_url(self) -> str:
        """Return the registry base URL (e.g. myacr.azurecr.io)."""


class ImageBuilder(ABC):
    """Interface for building container images."""

    @abstractmethod
    def build_image(self, dependency_set: Any, timeout_seconds: int) -> str:
        """Build a container image and return its full URI."""

    @abstractmethod
    def get_or_build_image(self, dependency_set: Any, timeout_seconds: int) -> str:
        """Return existing image URI or build a new one."""


class ComputeBackend(ABC):
    """Interface for job submission and lifecycle management."""

    @abstractmethod
    def create_job(
        self, namespace: str, job_id: str, package_uri: str,
        config: Any, registry: str,
        custom_image_uri: Optional[str] = None,
    ) -> str:
        """Create a compute job and return its name."""

    @abstractmethod
    def wait_for_completion(
        self, namespace: str, job_name: str, timeout: int,
    ) -> bool:
        """Block until the job completes or times out."""

    @abstractmethod
    def stream_logs(self, namespace: str, job_name: str) -> None:
        """Stream job logs to stdout."""


@dataclass
class CloudBackend:
    """Facade bundling all cloud service interfaces for a provider."""

    name: str
    storage: CloudStorage
    registry: ContainerRegistry
    image_builder: ImageBuilder
    compute: ComputeBackend
```

### 4. Azure Backend Implementation

All Azure-specific code stays under `ascend/cloud/azure/`. New and modified modules:

#### `ascend/cloud/azure/backend.py` (new)

Factory that assembles the Azure backend:

```python
def create_backend() -> CloudBackend:
    """Construct an AzureBackend from the current Ascend config."""
    from ascend.config import AscendConfig
    from .auth import get_azure_credential
    from .storage import AzureCloudStorage
    from .registry import AzureContainerRegistry
    from .image_builder import AzureImageBuilder
    # ... K8s compute backend ...

    cfg = AscendConfig.load()
    credential = get_azure_credential()

    storage = AzureCloudStorage(
        account_name=cfg.storage_account,
        credential=credential,
    )
    registry = AzureContainerRegistry(
        login_server=cfg.container_registry,
    )
    # ...
    return CloudBackend(
        name="azure",
        storage=storage,
        registry=registry,
        image_builder=image_builder,
        compute=compute,
    )
```

#### `ascend/cloud/azure/storage.py` (rewritten)

Implements `CloudStorage` using `fsspec` with the `adlfs` backend:

```python
import fsspec
from ascend.cloud.base import CloudStorage

class AzureCloudStorage(CloudStorage):
    def __init__(self, account_name: str, credential):
        self._account_name = account_name
        self._fs = fsspec.filesystem(
            "az", account_name=account_name, credential=credential,
        )

    def get_filesystem(self) -> fsspec.AbstractFileSystem:
        return self._fs

    def storage_uri(self, path: str) -> str:
        return f"az://ascend-data/{path}"

    def ensure_container(self, name: str) -> None:
        try:
            self._fs.mkdir(f"az://{name}")
        except FileExistsError:
            pass
```

#### `ascend/cloud/azure/registry.py` (new)

Implements `ContainerRegistry` using the `docker` library:

```python
import docker
from ascend.cloud.base import ContainerRegistry

class AzureContainerRegistry(ContainerRegistry):
    def __init__(self, login_server: str):
        self._login_server = login_server
        self._client = docker.from_env()

    def image_exists(self, repository: str, tag: str) -> bool:
        image_ref = f"{self._login_server}/{repository}:{tag}"
        try:
            self._client.images.get_registry_data(image_ref)
            return True
        except docker.errors.NotFound:
            return False

    def registry_url(self) -> str:
        return self._login_server
```

#### `ascend/cloud/node_pool_validator.py` → `ascend/cloud/azure/node_pool_validator.py`

Relocate — this module is 100% Azure AKS code (`DefaultAzureCredential`, `ContainerServiceClient`).

### 5. Runtime Executor Refactor

`ascend/runtime/executor.py` is the **critical coupling point**. Changes:

| Before | After |
|--------|-------|
| `from azure.storage.blob import BlobServiceClient` | Removed |
| `from ..cloud.azure.auth import get_azure_credential` | Removed |
| `from ..cloud.azure.storage import upload_package, download_result` | Removed |
| `BlobServiceClient(account_url=..., credential=...)` | `self.backend.storage.get_filesystem()` |
| `https://{acct}.blob.core.windows.net` URL construction | `self.backend.storage.storage_uri(path)` |
| `metadata_blob.upload_blob(json, overwrite=True)` | `self.backend.storage.write(path, json_bytes)` |
| Direct `upload_package()` call | `self.backend.storage.upload_package(...)` |
| Direct `download_result()` call | `self.backend.storage.download_result(...)` |

`RemoteExecutor.__init__` gains a `backend: CloudBackend` parameter:

```python
class RemoteExecutor:
    def __init__(self, config: AscendConfig, backend: CloudBackend):
        self.config = config
        self.backend = backend
```

`ascend/decorator.py` obtains the backend via `get_backend()` and passes it in:

```python
from ascend.cloud.registry import get_backend

backend = get_backend()
executor = RemoteExecutor(config, backend)
```

### 6. Kubernetes Layer Refactor

#### `ascend/cloud/kubernetes/jobs.py`

| Before | After |
|--------|-------|
| Hardcoded `.azurecr.io` domain detection in image URI construction | Accept `registry_url` parameter from the backend (`backend.registry.registry_url()`) |
| `AZURE_STORAGE_ACCOUNT` env var injected into pods | Generic `ASCEND_STORAGE_URI` env var containing an fsspec-compatible URI (e.g. `az://ascend-data/projects/.../package.pkl`) |

#### `ascend/cloud/kubernetes/kaniko.py`

| Before | After |
|--------|-------|
| `acr_registry` field in `ImageBuildSpec` | Renamed to `registry_url` |
| `acr-credentials` K8s secret reference | Renamed to `registry-credentials` (or made configurable) |
| `acr` cache repo reference | Generic `cache` repo reference |

### 7. In-Pod Runner Refactor

`docker/runner.py` currently imports `azure.storage.blob.BlobServiceClient` at the top level and constructs Azure-specific blob URLs.

**After refactoring**, the runner uses `fsspec` exclusively:

```python
import fsspec

# The pod receives the full fsspec URI via environment variable
package_uri = os.environ["ASCEND_PACKAGE_URI"]   # e.g. az://ascend-data/projects/.../package.pkl
result_uri  = os.environ["ASCEND_RESULT_URI"]     # e.g. az://ascend-data/projects/.../result.pkl

# Read package — fsspec auto-selects backend from URI scheme
with fsspec.open(package_uri, "rb") as f:
    package = cloudpickle.loads(f.read())

# Write result
with fsspec.open(result_uri, "wb") as f:
    f.write(cloudpickle.dumps(result))
```

The cloud choice is encoded in the URI scheme (`az://`, `s3://`, `gs://`), so the runner code is inherently multi-cloud. The correct fsspec backend (`adlfs`, `s3fs`, `gcsfs`) must be installed in the runner image.

#### `docker/Dockerfile.runtime`

Replace hardcoded Azure pip packages:

```dockerfile
# Before
RUN pip install azure-storage-blob azure-identity

# After
RUN pip install fsspec adlfs azure-identity
# Future clouds: add gcsfs, s3fs etc.
```

Since we use a single universal runner image, all fsspec backends can be installed. Alternatively, accept a `CLOUD_BACKEND` build arg:

```dockerfile
ARG CLOUD_BACKEND=azure
RUN pip install fsspec && \
    if [ "$CLOUD_BACKEND" = "azure" ]; then pip install adlfs azure-identity; fi && \
    if [ "$CLOUD_BACKEND" = "gcp" ]; then pip install gcsfs; fi && \
    if [ "$CLOUD_BACKEND" = "aws" ]; then pip install s3fs; fi
```

### 8. CLI Refactor

`ascend/cli/admin.py` and `ascend/cli/user.py` contain Azure management-plane operations (listing registries, creating storage accounts, etc.). These are inherently cloud-specific — there is no cross-cloud abstraction for "create an ACR" vs "create an ECR".

**Approach**: Gate cloud-specific CLI commands behind backend detection:

```python
from ascend.cloud.registry import detect_backend_name

@cli.command()
def setup():
    backend = detect_backend_name()
    if backend == "azure":
        from ascend.cloud.azure.cli import run_azure_setup
        run_azure_setup()
    else:
        raise click.UsageError(f"Setup not implemented for backend: {backend}")
```

Move the Azure-specific CLI logic into `ascend/cloud/azure/cli.py` (new module) to keep the top-level CLI routing cloud-agnostic.

### 9. Config Changes

`ascend/config.py` currently has Azure-oriented field names (`storage_account`, `container_registry`, `resource_group`). Changes:

- Add an **optional** `cloud_provider` field. If present, it overrides auto-detection (for users who install multiple backends).
- Each backend defines its **required config fields**. The `AzureBackend` requires `storage_account`, `container_registry`, `resource_group`. A future GCP backend would require `project_id`, `region`, etc.
- Add a `validate_for_backend(backend_name)` method that checks the right fields are present.

```yaml
# .ascend.yaml — Azure example
cloud_provider: azure   # optional, auto-detected if omitted
username: alice
cluster_name: my-aks
resource_group: my-rg
storage_account: mydata
container_registry: myacr.azurecr.io
namespace: ascend-users-alice
```

---

## Storage Abstraction: fsspec

[fsspec](https://filesystem-spec.readthedocs.io/) provides a uniform Python API for local and cloud filesystems. Each cloud has a dedicated fsspec implementation:

| Cloud | fsspec backend | PyPI package | URI scheme |
|-------|---------------|-------------|------------|
| Azure Blob | `adlfs` | `adlfs` | `az://` or `abfs://` |
| AWS S3 | `s3fs` | `s3fs` | `s3://` |
| GCP GCS | `gcsfs` | `gcsfs` | `gs://` |

### Operation Mapping

Every storage operation currently performed by Ascend maps cleanly to an fsspec primitive:

| Current Azure SDK call | fsspec equivalent |
|----------------------|-------------------|
| `container_client.create_container()` | `fs.mkdir("az://ascend-data")` |
| `blob.upload_blob(data, overwrite=True)` | `fs.open(uri, "wb").write(data)` |
| `blob.download_blob().readall()` | `fs.open(uri, "rb").read()` |
| `blob.exists()` | `fs.exists(uri)` |
| `blob.url` | Replaced by passing raw fsspec URIs to pods |
| `BlobServiceClient(url, credential)` | `fsspec.filesystem("az", account_name=..., credential=...)` |

### Note on `blob.url`

The current codebase passes `blob.url` (a full `https://...blob.core.windows.net/...` URL) as the `ASCEND_PACKAGE_URL` env var to the K8s pod. With fsspec, this is replaced by an fsspec URI (`az://ascend-data/projects/.../package.pkl`). The runner opens it directly with `fsspec.open()`, which auto-selects the backend from the scheme. This is a **design improvement**: the runner no longer needs to know which cloud it's on — the URI carries that information.

---

## Registry Abstraction: docker library

The `docker` Python library replaces only the `image_exists` check (the only data-plane registry operation performed by Python code). Image pushes are handled by Kaniko inside the cluster.

| Registry operation | Current implementation | New implementation |
|-------------------|----------------------|-------------------|
| Check image exists | `ContainerRegistryClient.get_manifest_properties()` (Azure SDK) | `docker.images.get_registry_data(image_ref)` (docker library) |
| Push image | Kaniko K8s job (unchanged) | Kaniko K8s job (unchanged) |
| List registries (CLI) | `ContainerRegistryManagementClient.registries.list_by_resource_group()` | Stays Azure SDK — management-plane, cloud-specific |

The `docker` library requires credentials configured via `docker login` (or a Docker config file). ACR, ECR, and GCR all support `docker login`.

---

## Adding a New Cloud Backend

To add support for a new cloud (e.g., GCP), implement these steps:

### 1. Create the backend module

```
ascend/cloud/gcp/
├── __init__.py
├── backend.py       # create_backend() factory
├── storage.py       # GcpCloudStorage(CloudStorage) using gcsfs
├── registry.py      # GcpContainerRegistry(ContainerRegistry)
├── auth.py          # GCP credential management
└── cli.py           # GCP-specific CLI commands (optional)
```

### 2. Implement the ABCs

```python
# ascend/cloud/gcp/storage.py
import fsspec
from ascend.cloud.base import CloudStorage

class GcpCloudStorage(CloudStorage):
    def __init__(self, project: str, bucket: str):
        self._bucket = bucket
        self._fs = fsspec.filesystem("gs", project=project)

    def get_filesystem(self):
        return self._fs

    def storage_uri(self, path: str) -> str:
        return f"gs://{self._bucket}/{path}"

    def ensure_container(self, name: str) -> None:
        self._fs.mkdir(f"gs://{name}")
```

### 3. Register the backend

In `ascend/cloud/registry.py`, add an entry:

```python
_BACKENDS = {
    "azure": ("adlfs", "ascend.cloud.azure.backend"),
    "gcp":   ("gcsfs", "ascend.cloud.gcp.backend"),
}
```

### 4. Add the optional dependency group

In `pyproject.toml`:

```toml
[project.optional-dependencies]
gcp = [
    "gcsfs>=2024.2",
    "google-cloud-container>=2.40",
    # ...
]
```

### 5. Update the runner Dockerfile

Add the `gcsfs` pip install to the runner image (or use the build arg pattern).

### 6. Add tests and documentation

- Add unit tests for the GCP backend implementations.
- Update this document with GCP-specific notes.

---

## Migration Checklist

The refactoring should be executed in this order, with each step resulting in a working, testable state:

### Phase 1: Foundation

- [x] Restructure `pyproject.toml` — move Azure packages to `[project.optional-dependencies] azure`, add `fsspec` and `docker` to core deps.
- [x] Rewrite `ascend/cloud/base.py` with revised ABCs (`CloudStorage`, `ContainerRegistry`, `ImageBuilder`, `ComputeBackend`, `CloudBackend`).
- [x] Create `ascend/cloud/registry.py` with auto-detection logic and `NoBackendError`.
- [x] Wire import-time guard into `ascend/__init__.py`.

### Phase 2: Azure Backend

- [x] Create `ascend/cloud/azure/backend.py` — factory assembling the Azure backend.
- [x] Rewrite `ascend/cloud/azure/storage.py` — implement `AzureCloudStorage(CloudStorage)` using fsspec/adlfs.
- [x] Create `ascend/cloud/azure/registry.py` — implement `AzureContainerRegistry(ContainerRegistry)` using the docker library.
- [x] Refactor `ascend/cloud/azure/image_builder.py` to implement `ImageBuilder` ABC, accept injected `ContainerRegistry`.
- [x] Move `ascend/cloud/node_pool_validator.py` into `ascend/cloud/azure/`.

### Phase 3: Core Refactor

- [x] Refactor `ascend/runtime/executor.py` — remove all Azure imports, accept `CloudBackend` parameter, use `backend.storage.*` for all I/O.
- [x] Refactor `ascend/decorator.py` — obtain backend via `get_backend()`, pass to `RemoteExecutor`.
- [x] Refactor `ascend/cloud/kubernetes/jobs.py` — remove `.azurecr.io` hardcoding, accept `registry_url` param, use `ASCEND_PACKAGE_URI` env var.
- [x] Refactor `ascend/cloud/kubernetes/kaniko.py` — rename `acr_registry` → `registry_url`, `acr-credentials` → `registry-credentials`.

### Phase 4: Runner & CLI

- [x] Refactor `docker/runner.py` — replace Azure Blob SDK with fsspec, read URI from `ASCEND_PACKAGE_URI` env var.
- [x] Update `docker/Dockerfile.runtime` — replace Azure pip packages with fsspec + adlfs.
- [x] Refactor `ascend/cli/admin.py` and `ascend/cli/user.py` — gate Azure-specific commands behind backend detection, move Azure CLI logic into `ascend/cloud/azure/cli.py`.
- [x] Update `ascend/config.py` — add optional `cloud_provider` field, per-backend config validation.

### Phase 5: Tests & Docs

- [x] Add `test_no_backend_error` — verify `import ascend` fails cleanly without a backend.
- [x] Update existing tests to work with the new backend abstraction.
- [x] Guard Azure-specific tests so they only run when the Azure extra is installed.
- [x] Update `ARCHITECTURE.md`, `README.md`, and `docs/README.md`.

---

## Verification

After completing the refactoring, confirm:

1. **`pip install -e ".[azure]"`** — imports succeed, all existing tests pass.
2. **`pip install -e .`** (no extra) — `import ascend` raises `ImportError` with the message: `"No cloud backend installed. Install one with: pip install ascend[azure]"`.
3. **`make test-unit`** — all unit tests pass (Azure extra installed).
4. **`docker/runner.py`** can read/write via fsspec with an `az://` URI (integration test).
5. **`image_exists`** works via the `docker` library against ACR.
6. **`RemoteExecutor`** works end-to-end with the injected `AzureBackend`.
7. **No top-level Azure SDK imports** remain outside of `ascend/cloud/azure/`.
