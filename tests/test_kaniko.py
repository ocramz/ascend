"""Tests for Kaniko job manifest generation (pure logic, no K8s client needed)"""

import pytest

from ascend.cloud.kubernetes.kaniko import (
    ImageBuildSpec,
    KanikoJobManager,
    ImageBuildStatus,
)


def _make_build_spec(**overrides) -> ImageBuildSpec:
    """Create a default ImageBuildSpec for testing."""
    defaults = dict(
        base_image="python:3.11-slim",
        requirements=["pandas==2.0.0"],
        system_packages=[],
        image_tag="user-abc123",
        registry_url="test.azurecr.io",
        dockerfile_content="FROM python:3.11-slim\n",
        requirements_txt_content="pandas==2.0.0\n",
    )
    defaults.update(overrides)
    return ImageBuildSpec(**defaults)


def _generate_manifest(
    build_spec: ImageBuildSpec,
    service_account: str = "kaniko-builder",
    no_cache: bool = False,
) -> dict:
    """Generate a Kaniko job manifest using the private method directly."""
    manager = KanikoJobManager.__new__(KanikoJobManager)
    manager.namespace = "test-builds"
    return manager._generate_job_manifest(build_spec, service_account, no_cache=no_cache)


def test_job_manifest_metadata():
    """Test job manifest has correct metadata"""
    build_spec = _make_build_spec()
    manifest = _generate_manifest(build_spec)

    assert manifest["kind"] == "Job"
    assert manifest["metadata"]["name"] == "ascend-build-user-abc123"
    assert manifest["metadata"]["namespace"] == "test-builds"


def test_job_manifest_structure():
    """Test Kaniko job manifest has correct structure"""
    build_spec = _make_build_spec()
    manifest = _generate_manifest(build_spec)

    assert "spec" in manifest
    assert "template" in manifest["spec"]
    assert "spec" in manifest["spec"]["template"]

    pod_spec = manifest["spec"]["template"]["spec"]

    # Check init container (prepares build context)
    assert "initContainers" in pod_spec
    assert len(pod_spec["initContainers"]) == 1
    init_container = pod_spec["initContainers"][0]
    assert init_container["name"] == "prepare-context"
    assert init_container["image"] == "busybox:latest"

    # Check Kaniko container
    assert "containers" in pod_spec
    assert len(pod_spec["containers"]) == 1
    kaniko_container = pod_spec["containers"][0]
    assert kaniko_container["name"] == "kaniko"
    assert "gcr.io/kaniko-project/executor" in kaniko_container["image"]


def test_job_manifest_kaniko_args():
    """Test Kaniko container has correct arguments"""
    build_spec = _make_build_spec()
    manifest = _generate_manifest(build_spec)

    kaniko_container = manifest["spec"]["template"]["spec"]["containers"][0]
    args = kaniko_container["args"]

    assert "--dockerfile=/workspace/Dockerfile" in args
    assert "--context=dir:///workspace" in args
    assert "--destination=test.azurecr.io/ascend-runtime:user-abc123" in args
    assert "--cache=true" in args
    assert any("--cache-repo=" in arg for arg in args)


def test_job_manifest_volumes():
    """Test job manifest has correct volume configuration"""
    build_spec = _make_build_spec()
    manifest = _generate_manifest(build_spec)

    pod_spec = manifest["spec"]["template"]["spec"]
    volumes = pod_spec["volumes"]

    # Check workspace volume
    workspace_vol = next((v for v in volumes if v["name"] == "workspace"), None)
    assert workspace_vol is not None
    assert "emptyDir" in workspace_vol

    # Check kaniko-secret volume
    secret_vol = next((v for v in volumes if v["name"] == "kaniko-secret"), None)
    assert secret_vol is not None
    assert "secret" in secret_vol
    assert secret_vol["secret"]["secretName"] == "registry-credentials"


def test_job_manifest_service_account():
    """Test job manifest uses correct service account"""
    build_spec = _make_build_spec()
    manifest = _generate_manifest(build_spec, service_account="custom-account")

    pod_spec = manifest["spec"]["template"]["spec"]
    assert pod_spec["serviceAccountName"] == "custom-account"


def test_job_manifest_resources():
    """Test Kaniko container has resource limits"""
    build_spec = _make_build_spec()
    manifest = _generate_manifest(build_spec)

    kaniko_container = manifest["spec"]["template"]["spec"]["containers"][0]
    resources = kaniko_container["resources"]

    assert resources["requests"]["cpu"] == "1"
    assert resources["requests"]["memory"] == "2Gi"
    assert resources["limits"]["cpu"] == "2"
    assert resources["limits"]["memory"] == "4Gi"


def test_job_manifest_labels():
    """Test job manifest has correct labels"""
    build_spec = _make_build_spec()
    manifest = _generate_manifest(build_spec)

    labels = manifest["metadata"]["labels"]
    assert labels["app"] == "ascend-image-builder"
    assert labels["image-tag"] == "user-abc123"


def test_job_manifest_no_cache():
    """Test that no_cache=True disables Kaniko layer caching."""
    build_spec = _make_build_spec()
    manifest = _generate_manifest(build_spec, no_cache=True)

    kaniko_container = manifest["spec"]["template"]["spec"]["containers"][0]
    args = kaniko_container["args"]

    assert "--cache=false" in args
    # Must NOT include the caching arguments
    assert "--cache=true" not in args
    assert not any("--cache-repo=" in arg for arg in args)
    assert "--compressed-caching=false" not in args


def test_job_manifest_cache_enabled_by_default():
    """Test that caching is enabled by default (no_cache=False)."""
    build_spec = _make_build_spec()
    manifest = _generate_manifest(build_spec, no_cache=False)

    kaniko_container = manifest["spec"]["template"]["spec"]["containers"][0]
    args = kaniko_container["args"]

    assert "--cache=true" in args
    assert any("--cache-repo=" in arg for arg in args)
    assert "--cache=false" not in args
