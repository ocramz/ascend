"""Tests for dependency analyzer and DependencySet"""

import hashlib
import json
import sys

import pytest

from ascend.dependencies.analyzer import (
    DEFAULT_GPU_BASE_IMAGE,
    DependencySet,
    PYTORCH_CUDA_COMPAT,
    PYTORCH_DOCKER_PYTHON_VERSION,
    SUPPORTED_PYTHON_VERSIONS,
    create_dependency_set,
    detect_gpu_base_image,
)


def test_dependency_set_creation():
    """Test creating a DependencySet"""
    dep_set = DependencySet(
        explicit_requirements=["pandas==2.0.0", "numpy>=1.24.0"],
        python_version="3.11",
        use_gpu=False,
    )

    assert dep_set.explicit_requirements == ["pandas==2.0.0", "numpy>=1.24.0"]
    assert dep_set.python_version == "3.11"
    assert dep_set.use_gpu is False


def test_dependency_set_normalize():
    """Test dependency normalization (sorting)"""
    dep_set = DependencySet(
        explicit_requirements=["torch==2.1.0", "pandas==2.0.0", "numpy>=1.24.0"],
    )

    normalized = dep_set.normalize()
    assert normalized == ["numpy>=1.24.0", "pandas==2.0.0", "torch==2.1.0"]


def test_dependency_set_hash_calculation():
    """Test hash calculation is deterministic"""
    dep_set1 = DependencySet(
        explicit_requirements=["pandas==2.0.0", "numpy>=1.24.0"],
        python_version="3.11",
        use_gpu=False,
    )

    dep_set2 = DependencySet(
        explicit_requirements=["pandas==2.0.0", "numpy>=1.24.0"],
        python_version="3.11",
        use_gpu=False,
    )

    # Same dependencies should produce same hash
    hash1 = dep_set1.calculate_hash()
    hash2 = dep_set2.calculate_hash()
    assert hash1 == hash2

    # Hash should be 12 characters
    assert len(hash1) == 12

    # Hash should be hex string
    assert all(c in "0123456789abcdef" for c in hash1)


def test_dependency_set_hash_differs_with_different_deps():
    """Test hash changes when dependencies differ"""
    dep_set1 = DependencySet(
        explicit_requirements=["pandas==2.0.0"],
        python_version="3.11",
    )

    dep_set2 = DependencySet(
        explicit_requirements=["pandas==2.1.0"],  # Different version
        python_version="3.11",
    )

    hash1 = dep_set1.calculate_hash()
    hash2 = dep_set2.calculate_hash()
    assert hash1 != hash2


def test_dependency_set_hash_differs_with_different_python_version():
    """Test hash changes when Python version differs"""
    dep_set1 = DependencySet(
        explicit_requirements=["pandas==2.0.0"],
        python_version="3.11",
    )

    dep_set2 = DependencySet(
        explicit_requirements=["pandas==2.0.0"],
        python_version="3.12",  # Different Python version
    )

    hash1 = dep_set1.calculate_hash()
    hash2 = dep_set2.calculate_hash()
    assert hash1 != hash2


def test_dependency_set_hash_differs_with_gpu_flag():
    """Test hash changes when GPU flag differs"""
    dep_set1 = DependencySet(
        explicit_requirements=["torch==2.1.0"],
        python_version="3.11",
        use_gpu=False,
    )

    dep_set2 = DependencySet(
        explicit_requirements=["torch==2.1.0"],
        python_version="3.11",
        use_gpu=True,  # GPU enabled
    )

    hash1 = dep_set1.calculate_hash()
    hash2 = dep_set2.calculate_hash()
    assert hash1 != hash2


def test_dependency_set_hash_order_independent():
    """Test hash is same regardless of requirement order"""
    dep_set1 = DependencySet(
        explicit_requirements=["torch==2.1.0", "pandas==2.0.0", "numpy>=1.24.0"],
    )

    dep_set2 = DependencySet(
        explicit_requirements=["numpy>=1.24.0", "pandas==2.0.0", "torch==2.1.0"],
    )

    hash1 = dep_set1.calculate_hash()
    hash2 = dep_set2.calculate_hash()
    assert hash1 == hash2


def test_dependency_set_to_requirements_txt():
    """Test generating requirements.txt content"""
    dep_set = DependencySet(
        explicit_requirements=["torch==2.1.0", "pandas==2.0.0"],
    )

    requirements_txt = dep_set.to_requirements_txt()
    assert "pandas==2.0.0" in requirements_txt
    assert "torch==2.1.0" in requirements_txt
    # Should be sorted
    lines = requirements_txt.strip().split("\n")
    assert lines == ["pandas==2.0.0", "torch==2.1.0"]


def test_dependency_set_to_requirements_txt_empty():
    """Test generating requirements.txt with no dependencies"""
    dep_set = DependencySet(explicit_requirements=[])

    requirements_txt = dep_set.to_requirements_txt()
    assert requirements_txt == ""


def test_dependency_set_get_base_image_cpu():
    """Test base image selection for CPU workloads"""
    dep_set = DependencySet(
        explicit_requirements=["pandas==2.0.0"],
        python_version="3.11",
        use_gpu=False,
    )

    base_image = dep_set.get_base_image()
    assert base_image == "python:3.11-slim"


def test_dependency_set_get_base_image_gpu():
    """Test base image selection for GPU workloads"""
    dep_set = DependencySet(
        explicit_requirements=["torch==2.1.0"],
        python_version="3.11",
        use_gpu=True,
    )

    base_image = dep_set.get_base_image()
    # torch==2.1.0 should auto-detect to pytorch/pytorch image
    assert "pytorch/pytorch" in base_image
    assert "cuda" in base_image


def test_dependency_set_get_base_image_gpu_no_torch():
    """Test GPU base image falls back to python:slim when no torch present."""
    dep_set = DependencySet(
        explicit_requirements=["numpy==1.24.0"],
        python_version="3.11",
        use_gpu=True,
    )

    base_image = dep_set.get_base_image()
    # No torch in requirements → no PyTorch Docker Hub image → slim fallback
    assert base_image == "python:3.11-slim"


def test_dependency_set_get_base_image_explicit_override():
    """Test that explicit base_image overrides auto-detection."""
    custom = "my-registry/my-image:latest"
    dep_set = DependencySet(
        explicit_requirements=["torch==2.5.1"],
        python_version="3.11",
        use_gpu=True,
        base_image=custom,
    )

    assert dep_set.get_base_image() == custom


def test_dependency_set_get_base_image_gpu_python_version_mismatch():
    """GPU with torch falls back to python:slim when Python version doesn't match Docker Hub image."""
    dep_set = DependencySet(
        explicit_requirements=["torch==2.5.1"],
        python_version="3.13",
        use_gpu=True,
    )

    base_image = dep_set.get_base_image()
    # PyTorch Docker Hub image has Python 3.11, client is 3.13
    # → falls back to python:3.13-slim (pip torch brings its own CUDA)
    assert base_image == "python:3.13-slim"


def test_dependency_set_get_base_image_gpu_python_version_matches():
    """GPU with torch returns PyTorch Docker Hub image when Python versions match."""
    dep_set = DependencySet(
        explicit_requirements=["torch==2.5.1"],
        python_version=PYTORCH_DOCKER_PYTHON_VERSION,
        use_gpu=True,
    )

    base_image = dep_set.get_base_image()
    assert "pytorch/pytorch" in base_image
    assert "cuda" in base_image


def test_create_dependency_set_helper():
    """Test create_dependency_set helper function"""
    dep_set = create_dependency_set(
        requirements=["pandas==2.0.0", "numpy>=1.24.0"],
        python_version="3.11",
        use_gpu=False,
    )

    assert isinstance(dep_set, DependencySet)
    assert dep_set.explicit_requirements == ["pandas==2.0.0", "numpy>=1.24.0"]
    assert dep_set.python_version == "3.11"
    assert dep_set.use_gpu is False


def test_create_dependency_set_defaults():
    """Test create_dependency_set with defaults"""
    current_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    if current_version not in SUPPORTED_PYTHON_VERSIONS:
        # On an unsupported interpreter, verify the guard fires
        with pytest.raises(ValueError, match="not supported by Ascend"):
            create_dependency_set()
    else:
        dep_set = create_dependency_set()
        assert dep_set.explicit_requirements == []
        assert dep_set.python_version == current_version
        assert dep_set.use_gpu is False


def test_dependency_set_hash_matches_manual_calculation():
    """Test hash calculation matches expected algorithm"""
    dep_set = DependencySet(
        explicit_requirements=["pandas==2.0.0"],
        python_version="3.11",
        use_gpu=False,
    )

    # Manually calculate expected hash — now includes base_image
    hash_input = {
        "python_version": "3.11",
        "packages": ["pandas==2.0.0"],
        "system_packages": [],
        "use_gpu": False,
        "base_image": "python:3.11-slim",
    }
    hash_str = json.dumps(hash_input, sort_keys=True)
    expected_hash = hashlib.sha256(hash_str.encode()).hexdigest()[:12]

    actual_hash = dep_set.calculate_hash()
    assert actual_hash == expected_hash


def test_create_dependency_set_rejects_unsupported_version():
    """Test that create_dependency_set raises for unsupported Python versions"""
    with pytest.raises(ValueError, match="not supported by Ascend"):
        create_dependency_set(python_version="3.9")


def test_create_dependency_set_accepts_supported_versions():
    """Test that all SUPPORTED_PYTHON_VERSIONS are accepted"""
    for version in SUPPORTED_PYTHON_VERSIONS:
        dep_set = create_dependency_set(
            requirements=["pandas==2.0.0"],
            python_version=version,
        )
        assert dep_set.python_version == version


# ---------------------------------------------------------------------------
# detect_gpu_base_image tests
# ---------------------------------------------------------------------------


def test_detect_gpu_base_image_pinned_torch():
    """Auto-detect picks correct PyTorch Docker Hub image for pinned version."""
    result = detect_gpu_base_image(["torch==2.5.1", "numpy"])
    assert result == "pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime"


def test_detect_gpu_base_image_minimum_torch():
    """Auto-detect works with >= operator (uses specified version)."""
    result = detect_gpu_base_image(["torch>=2.3.0"])
    assert result == "pytorch/pytorch:2.3.0-cuda12.1-cudnn8-runtime"


def test_detect_gpu_base_image_unversioned_torch():
    """Auto-detect picks latest known version for unversioned torch."""
    result = detect_gpu_base_image(["torch"])
    assert result is not None
    assert "pytorch/pytorch" in result


def test_detect_gpu_base_image_no_torch():
    """Returns None when no torch/pytorch package is found."""
    result = detect_gpu_base_image(["numpy", "pandas", "scikit-learn"])
    assert result is None


def test_detect_gpu_base_image_unknown_version():
    """Returns None for unknown torch versions (caller falls back)."""
    result = detect_gpu_base_image(["torch==99.0.0"])
    assert result is None


def test_detect_gpu_base_image_pytorch_alias():
    """Auto-detect also works with the 'pytorch' package name."""
    result = detect_gpu_base_image(["pytorch==2.4.0"])
    assert result == "pytorch/pytorch:2.4.0-cuda12.4-cudnn9-runtime"


# ---------------------------------------------------------------------------
# base_image hash isolation
# ---------------------------------------------------------------------------


def test_dependency_set_hash_differs_with_base_image():
    """Hash changes when explicit base_image differs."""
    dep_set1 = DependencySet(
        explicit_requirements=["torch==2.5.1"],
        python_version="3.12",
        use_gpu=True,
    )
    dep_set2 = DependencySet(
        explicit_requirements=["torch==2.5.1"],
        python_version="3.12",
        use_gpu=True,
        base_image="custom-registry/my-gpu-image:latest",
    )
    assert dep_set1.calculate_hash() != dep_set2.calculate_hash()


def test_create_dependency_set_with_base_image():
    """create_dependency_set passes base_image through."""
    dep_set = create_dependency_set(
        requirements=["torch==2.5.1"],
        python_version="3.12",
        use_gpu=True,
        base_image="pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime",
    )
    assert dep_set.base_image == "pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime"
    assert dep_set.get_base_image() == "pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime"
