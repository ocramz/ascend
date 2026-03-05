# Image Cache Busting for Integration Tests

Plan for ensuring integration tests always run against freshly built Docker images, using a pytest fixture and the Kaniko build pipeline.

**Date:** March 2026

---

## Table of Contents

- [Problem Statement](#problem-statement)
- [Caching Layers](#caching-layers)
- [Design](#design)
  - [1. Add `delete_tag()` to `ContainerRegistry`](#1-add-delete_tag-to-containerregistry)
  - [2. Add `no_cache` support to `KanikoJobManager`](#2-add-no_cache-support-to-kanikojobmanager)
  - [3. Add `force_rebuild` to `AzureImageBuilder`](#3-add-force_rebuild-to-azureimagebuilder)
  - [4. Pytest fixture: `fresh_runtime_image`](#4-pytest-fixture-fresh_runtime_image)
- [Integration Test Usage](#integration-test-usage)
- [Implementation Checklist](#implementation-checklist)

---

## Problem Statement

Integration tests currently reuse cached runtime images from Azure Container Registry (ACR). When the base runtime (`docker/Dockerfile.runtime`), runner script, or core dependencies change, stale images cause silent test failures — the tests pass against old code instead of exercising the new code paths.

We need a mechanism to **force a fresh image build** during integration test runs so that:

1. The ACR image-tag lookup (`AzureImageBuilder.get_or_build_image`) does **not** short-circuit to a cached image.
2. Kaniko layer caching is bypassed, ensuring every Dockerfile instruction is re-executed.
3. The entire flow is triggered by a single pytest fixture that tests can depend on.

---

## Caching Layers

There are two independent caches that must be invalidated:

| Layer | Where | How it works | Bypass mechanism |
|---|---|---|---|
| **ACR tag cache** | `AzureImageBuilder.get_or_build_image()` | Calls `registry.image_exists(repo, tag)` — if the tag exists, returns immediately without building | Delete the tag from ACR before building |
| **Kaniko layer cache** | Kaniko executor args (`--cache=true`, `--cache-repo=…`) | Kaniko checks a cache repo in ACR for previously built layers and skips unchanged steps | Pass `--cache=false` to disable layer reuse |

Both must be busted for a truly clean rebuild.

---

## Design

### 1. Add `delete_tag()` to `ContainerRegistry`

Add an optional `delete_tag` method to the `ContainerRegistry` ABC and implement it in `AzureContainerRegistry`.

**File:** `ascend/cloud/base.py`

```python
class ContainerRegistry(ABC):
    # ... existing methods ...

    def delete_tag(self, repository: str, tag: str) -> bool:
        """Delete an image tag from the registry.

        Default implementation is a no-op (returns False).
        Backends override this to enable cache busting.

        Args:
            repository: Repository name (e.g. ``ascend-runtime``).
            tag: Image tag to delete.

        Returns:
            True if the tag was deleted, False if it didn't exist
            or deletion is not supported.
        """
        return False
```

**File:** `ascend/cloud/azure/registry.py`

```python
class AzureContainerRegistry(ContainerRegistry):
    # ... existing methods ...

    def delete_tag(self, repository: str, tag: str) -> bool:
        """Delete an image tag from ACR."""
        try:
            self._client.delete_tag(repository, tag)
            return True
        except Exception:
            return False
```

The `azure-containerregistry` SDK's `ContainerRegistryClient` already exposes `delete_tag(repository, tag)`, so no new Azure dependencies are needed.

### 2. Add `no_cache` support to `KanikoJobManager`

Add a `no_cache` parameter to `create_build_job()` that suppresses the `--cache=true` and `--cache-repo=…` arguments in the generated Kaniko job manifest.

**File:** `ascend/cloud/kubernetes/kaniko.py`

```python
class KanikoJobManager:
    def create_build_job(
        self,
        build_spec: ImageBuildSpec,
        service_account: str = "kaniko-builder",
        no_cache: bool = False,           # <-- new parameter
    ) -> str:
        job_manifest = self._generate_job_manifest(
            build_spec, service_account, no_cache=no_cache,
        )
        # ... rest unchanged ...

    def _generate_job_manifest(
        self,
        build_spec: ImageBuildSpec,
        service_account: str,
        no_cache: bool = False,           # <-- new parameter
    ) -> dict:
        # ... existing code to build args list ...

        kaniko_args = [
            "--dockerfile=/workspace/Dockerfile",
            "--context=dir:///workspace",
            f"--destination={destination_image}",
            "--snapshot-mode=redo",
            "--log-format=text",
            "--verbosity=info",
        ]

        if no_cache:
            kaniko_args.append("--cache=false")
        else:
            kaniko_args += [
                "--cache=true",
                f"--cache-repo={build_spec.registry_url}/ascend-cache",
                "--compressed-caching=false",
            ]

        # ... rest unchanged ...
```

### 3. Add `force_rebuild` to `AzureImageBuilder`

Add `force_rebuild` to `get_or_build_image()` and `build_image()` that:
1. Deletes the existing tag from ACR (via `ContainerRegistry.delete_tag`).
2. Passes `no_cache=True` through to `KanikoJobManager.create_build_job()`.

**File:** `ascend/cloud/azure/image_builder.py`

```python
class AzureImageBuilder(ImageBuilderABC):

    def get_or_build_image(
        self,
        dependency_set: DependencySet,
        timeout_seconds: int = 600,
        force_rebuild: bool = False,      # <-- new parameter
    ) -> str:
        image_tag = self._generate_image_tag(dependency_set)

        if force_rebuild:
            # Bust ACR tag cache
            deleted = self._registry.delete_tag("ascend-runtime", image_tag)
            if deleted:
                print(f"♻ Deleted cached image tag: {image_tag}")
        elif self._registry.image_exists("ascend-runtime", image_tag):
            # Fast path: reuse cached image (only when not forcing)
            print(f"✓ Using cached image: {image_tag}")
            return self._image_uri(image_tag)

        print(f"⏳ Building new image: {image_tag}")
        return self.build_image(
            dependency_set, timeout_seconds, no_cache=force_rebuild,
        )

    def build_image(
        self,
        dependency_set: DependencySet,
        timeout_seconds: int,
        no_cache: bool = False,           # <-- new parameter
    ) -> str:
        # ... existing Dockerfile / requirements generation ...
        job_id = self.kaniko_manager.create_build_job(
            build_spec, no_cache=no_cache,
        )
        # ... wait and return ...
```

### 4. Pytest fixture: `fresh_runtime_image`

A **session-scoped** fixture in `tests/integration/conftest.py` that forces a clean image build before any integration test using it can run.

```python
@pytest.fixture(scope="session")
def fresh_runtime_image(ensure_infrastructure, real_aks_cluster):
    """Force-rebuild the runtime image, busting both ACR and Kaniko caches.

    Returns the full image URI of the freshly built image.

    This fixture:
    1. Deletes the existing image tag from ACR (if present).
    2. Submits a Kaniko build job with ``--cache=false``.
    3. Waits for the build to complete (up to 10 minutes).
    4. Yields the resulting image URI.
    """
    from azure.identity import DefaultAzureCredential

    from ascend.cloud.azure.image_builder import AzureImageBuilder
    from ascend.cloud.azure.registry import AzureContainerRegistry
    from ascend.dependencies.analyzer import create_dependency_set

    credential = DefaultAzureCredential()
    login_server = ensure_infrastructure.container_registry_login_server

    registry = AzureContainerRegistry(login_server, credential)
    builder = AzureImageBuilder(registry=registry, namespace="ascend-builds")

    # Build a minimal dependency set matching what the integration tests need
    dep_set = create_dependency_set(requirements=[], use_gpu=False)

    image_uri = builder.get_or_build_image(
        dep_set,
        timeout_seconds=600,
        force_rebuild=True,
    )

    yield image_uri
```

#### Controlling when to bust

Not every CI run needs a full cache bust. Add a `--rebuild-images` CLI flag via `pytest_addoption` so the fixture can be conditional:

```python
def pytest_addoption(parser):
    # ... existing options ...
    try:
        parser.addoption(
            "--rebuild-images",
            action="store_true",
            default=False,
            help="Force rebuild of runtime images (bust all caches)",
        )
    except ValueError:
        pass


@pytest.fixture(scope="session")
def fresh_runtime_image(request, ensure_infrastructure, real_aks_cluster):
    """Force-rebuild the runtime image when --rebuild-images is passed."""
    force = request.config.getoption("--rebuild-images", default=False)

    # ... same setup as above ...

    if force:
        image_uri = builder.get_or_build_image(
            dep_set, timeout_seconds=600, force_rebuild=True,
        )
    else:
        image_uri = builder.get_or_build_image(
            dep_set, timeout_seconds=600,
        )

    yield image_uri
```

In CI, the workflow would pass `--rebuild-images` when there are changes to `docker/`, `ascend/cloud/kubernetes/kaniko.py`, or `ascend/cloud/azure/image_builder.py`.

---

## Integration Test Usage

Tests that need a guaranteed-fresh image depend on the fixture:

```python
class TestEndToEnd:
    @pytest.mark.integration
    def test_simple_function_execution(self, real_aks_cluster, fresh_runtime_image):
        """Runs against a freshly built image."""
        from ascend import ascend

        @ascend(cpu="1", memory="2Gi", timeout=300)
        def add_numbers(a, b):
            return a + b

        result = add_numbers(5, 3)
        assert result == 8
```

Since `fresh_runtime_image` is session-scoped, the rebuild happens **once** per test session — not per test.

---

## Implementation Checklist

| # | Task | Files |
|---|---|---|
| 1 | Add `delete_tag()` default to `ContainerRegistry` ABC | `ascend/cloud/base.py` |
| 2 | Implement `delete_tag()` in `AzureContainerRegistry` | `ascend/cloud/azure/registry.py` |
| 3 | Add `no_cache` param to `KanikoJobManager.create_build_job()` and `_generate_job_manifest()` | `ascend/cloud/kubernetes/kaniko.py` |
| 4 | Add `force_rebuild` / `no_cache` params to `AzureImageBuilder` | `ascend/cloud/azure/image_builder.py` |
| 5 | Add unit tests for no-cache manifest generation | `tests/test_kaniko.py` |
| 6 | Add unit test for `delete_tag` | `tests/test_image_builder.py` |
| 7 | Add `--rebuild-images` option and `fresh_runtime_image` fixture | `tests/integration/conftest.py` |
| 8 | Wire `fresh_runtime_image` into integration tests | `tests/integration/test_e2e.py` |
| 9 | Update CI workflow to pass `--rebuild-images` on relevant path changes | `.github/workflows/` |
