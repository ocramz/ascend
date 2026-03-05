"""Dependency detection and analysis"""

import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set

# Python versions for which official Docker Hub ``python:{version}-slim``
# images exist and cloudpickle serialization is verified.  Bump this tuple
# when a new CPython release has a published slim image.
SUPPORTED_PYTHON_VERSIONS: tuple[str, ...] = ("3.11", "3.12", "3.13")

# ---------------------------------------------------------------------------
# PyTorch ↔ CUDA version mapping
# ---------------------------------------------------------------------------
# Maps PyTorch minor version → (cuda_version, cudnn_version) for the
# official ``pytorch/pytorch`` Docker Hub images.  Update this when new
# PyTorch releases ship.
PYTORCH_CUDA_COMPAT: dict[str, tuple[str, str]] = {
    "2.6": ("12.4", "9"),
    "2.5": ("12.4", "9"),
    "2.4": ("12.4", "9"),
    "2.3": ("12.1", "8"),
    "2.2": ("12.1", "8"),
    "2.1": ("12.1", "8"),
}

# Fallback CUDA base image when no torch version mapping is available.
DEFAULT_GPU_BASE_IMAGE = "nvidia/cuda:12.4.0-runtime-ubuntu22.04"

# Python version shipped in the official pytorch/pytorch Docker Hub images.
# All tags in the 2.x series currently bundle Python 3.11 (conda-based).
# When this changes upstream, update this constant.
PYTORCH_DOCKER_PYTHON_VERSION = "3.11"


def detect_gpu_base_image(requirements: List[str]) -> Optional[str]:
    """Detect a suitable GPU base image from requirements.

    Scans *requirements* for a ``torch`` or ``pytorch`` package specifier
    and returns the matching official ``pytorch/pytorch`` Docker Hub image.

    If torch is found without a version pin, the latest known version in
    :data:`PYTORCH_CUDA_COMPAT` is used.

    Args:
        requirements: List of pip package specifications.

    Returns:
        Docker Hub image URI or *None* if no torch package was found.

    Examples:
        >>> detect_gpu_base_image(["torch==2.5.1", "numpy"])
        'pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime'
        >>> detect_gpu_base_image(["torch>=2.3"])
        'pytorch/pytorch:2.3.0-cuda12.1-cudnn8-runtime'
        >>> detect_gpu_base_image(["numpy"])  # no torch
    """
    version_re = re.compile(
        r"^(?:torch|pytorch)(?:\[.*\])?\s*(?:([=<>!~]+)\s*(\d+\.\d+(?:\.\d+)?))?",
    )
    for req in requirements:
        m = version_re.match(req.strip())
        if m is None:
            continue
        op, ver = m.group(1), m.group(2)
        if ver:
            minor = ".".join(ver.split(".")[:2])  # e.g. "2.5"
        else:
            # Unversioned torch — use latest known
            minor = sorted(PYTORCH_CUDA_COMPAT, reverse=True)[0]
            ver = f"{minor}.0"

        if minor not in PYTORCH_CUDA_COMPAT:
            return None  # unknown torch version — caller falls back

        cuda, cudnn = PYTORCH_CUDA_COMPAT[minor]
        return f"pytorch/pytorch:{ver}-cuda{cuda}-cudnn{cudnn}-runtime"

    return None  # no torch in requirements


def find_requirements_file() -> Optional[Path]:
    """
    Search for requirements file in current directory and parent directories.

    Looks for common Python dependency files:
    - requirements.txt
    - pyproject.toml
    - setup.py
    - Pipfile

    Returns:
        Path to requirements file if found, None otherwise
    """
    current_dir = Path.cwd()

    # Search patterns in order of preference
    search_patterns = [
        "requirements.txt",
        "pyproject.toml",
        "setup.py",
        "Pipfile",
    ]

    # Search current directory and up to 3 levels of parents
    for _ in range(4):
        for pattern in search_patterns:
            req_file = current_dir / pattern
            if req_file.exists():
                return req_file

        # Move to parent directory
        parent = current_dir.parent
        if parent == current_dir:  # Reached filesystem root
            break
        current_dir = parent

    return None


def load_requirements_from_file(req_file: Path) -> List[str]:
    """
    Parse requirements from file.

    For MVP, only handles requirements.txt format.
    Returns list of package specifications.
    """
    if req_file.name == "requirements.txt":
        with open(req_file, "r") as f:
            requirements = []
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if line and not line.startswith("#"):
                    requirements.append(line)
            return requirements
    else:
        # For other formats, return empty list for now
        # User must provide requirements.txt for MVP
        return []


def get_requirements() -> List[str]:
    """
    Find and load requirements from project files.

    Raises:
        FileNotFoundError: If no requirements file is found
    """
    req_file = find_requirements_file()

    if req_file is None:
        raise FileNotFoundError(
            "No requirements file found. Please create a requirements.txt file "
            "in your project directory with your dependencies. "
            "Searched for: requirements.txt, pyproject.toml, setup.py, Pipfile"
        )

    if req_file.name != "requirements.txt":
        raise ValueError(
            f"Found {req_file.name} but currently only requirements.txt is supported for MVP. "
            f"Please create a requirements.txt file with your dependencies."
        )

    requirements = load_requirements_from_file(req_file)

    if not requirements:
        # Empty requirements file is okay
        return []

    return requirements


@dataclass
class DependencySet:
    """
    Represents a complete set of dependencies for a function.
    
    Used for automatic image building to uniquely identify required packages
    and generate deterministic image tags.
    """
    
    explicit_requirements: List[str] = field(default_factory=list)
    detected_imports: Set[str] = field(default_factory=set)
    local_modules: Set[str] = field(default_factory=set)
    python_version: str = field(default_factory=lambda: f"{sys.version_info.major}.{sys.version_info.minor}")
    system_packages: List[str] = field(default_factory=list)
    use_gpu: bool = False  # Determines base image (CUDA vs slim)
    base_image: Optional[str] = None  # User-specified Docker base image override
    
    def normalize(self) -> List[str]:
        """
        Normalize and sort dependencies for consistent hashing.
        
        Returns:
            Sorted list of normalized package specifications
        """
        # For now, just sort the explicit requirements
        # Future enhancement: resolve unversioned packages to specific versions
        return sorted(self.explicit_requirements)
    
    def calculate_hash(self) -> str:
        """
        Calculate deterministic SHA256 hash of dependencies.
        
        The hash uniquely identifies this dependency set and is used
        to tag container images (format: user-{hash}).
        
        Returns:
            First 12 characters of SHA256 hash (e.g., "a3f5d2c8e9b1")
        """
        # Create hash input with all relevant fields
        hash_input = {
            "python_version": self.python_version,
            "packages": self.normalize(),
            "system_packages": sorted(self.system_packages),
            "use_gpu": self.use_gpu,
            "base_image": self.get_base_image(),
        }
        
        # Calculate SHA256 hash
        hash_str = json.dumps(hash_input, sort_keys=True)
        full_hash = hashlib.sha256(hash_str.encode()).hexdigest()
        
        # Return first 12 characters for brevity
        return full_hash[:12]
    
    def to_requirements_txt(self) -> str:
        """
        Generate requirements.txt file content.
        
        Returns:
            String content for requirements.txt file
        """
        if not self.explicit_requirements:
            return ""
        
        return "\n".join(self.normalize()) + "\n"
    
    def get_base_image(self) -> str:
        """Get appropriate base image based on GPU requirement.

        Selection priority:

        1. **Explicit override** – ``base_image`` field set by the user
           via the ``@ascend(base_image=...)`` decorator parameter.
        2. **Auto-detect** – for GPU workloads, scan ``explicit_requirements``
           for ``torch``/``pytorch`` and pick a matching official PyTorch
           Docker Hub image (see :func:`detect_gpu_base_image`), but only
           when the image’s Python version matches the client’s.  Modern
           PyTorch pip wheels bundle CUDA runtime, so a plain
           ``python:{version}-slim`` base works on GPU nodes.
        3. **Fallback** – ``python:{version}-slim`` for both CPU and
           GPU workloads when no compatible GPU base image is available.

        Returns:
            Base image name (e.g. ``"pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime"``
            or ``"python:3.13-slim"``).
        """
        import logging
        _logger = logging.getLogger(__name__)

        # 1. Explicit user override
        if self.base_image is not None:
            return self.base_image

        # 2. GPU: auto-detect from torch version in requirements
        if self.use_gpu:
            detected = detect_gpu_base_image(self.explicit_requirements)
            if detected is not None:
                # Only use the PyTorch Docker Hub image if its Python
                # version matches the client’s.  The Docker Hub images
                # ship with a fixed Python (currently 3.11).  When there
                # is a mismatch, fall back to python:{version}-slim;
                # PyTorch pip wheels bring their own CUDA runtime.
                if self.python_version == PYTORCH_DOCKER_PYTHON_VERSION:
                    return detected
                _logger.info(
                    "PyTorch Docker Hub image ships Python %s but client "
                    "is Python %s — falling back to python:%s-slim base. "
                    "GPU support provided by pip-installed CUDA libraries.",
                    PYTORCH_DOCKER_PYTHON_VERSION,
                    self.python_version,
                    self.python_version,
                )
            # If torch wasn’t in requirements (no detected image) or
            # Python version doesn’t match, fall through to slim base.
            # The generic NVIDIA CUDA image also has a fixed Python and
            # requires PPA gymnastics for newer versions, so we skip it.

        # CPU workloads — or GPU fallback when no compatible base exists
        return f"python:{self.python_version}-slim"


def create_dependency_set(
    requirements: Optional[List[str]] = None,
    python_version: Optional[str] = None,
    use_gpu: bool = False,
    base_image: Optional[str] = None,
) -> DependencySet:
    """
    Create a DependencySet from requirements.
    
    Args:
        requirements: List of pip package specifications
        python_version: Target Python version (e.g., "3.11"). If None, auto-detects from sys.version_info.
        use_gpu: Whether the workload requires GPU support
        base_image: Explicit Docker base image URI override. When set,
            auto-detection is skipped and this image is used directly.
        
    Returns:
        DependencySet instance

    Raises:
        ValueError: If the resolved Python version is not in
            :data:`SUPPORTED_PYTHON_VERSIONS`.
    """
    if python_version is None:
        python_version = f"{sys.version_info.major}.{sys.version_info.minor}"

    if python_version not in SUPPORTED_PYTHON_VERSIONS:
        raise ValueError(
            f"Python {python_version} is not supported by Ascend. "
            f"Supported versions: {', '.join(SUPPORTED_PYTHON_VERSIONS)}. "
            f"The runtime Docker image (python:{{version}}-slim) must exist "
            f"on Docker Hub and cloudpickle compatibility must be verified."
        )

    return DependencySet(
        explicit_requirements=requirements or [],
        python_version=python_version,
        use_gpu=use_gpu,
        base_image=base_image,
    )
