"""Core @ascend decorator implementation"""

from typing import Callable, Any, Optional
from functools import wraps
import hashlib

from .serialization import serialize, validate_serialization

from .node_types import NodeType, validate_node_type
from .utils.job_ids import generate_job_id
from .utils.errors import ExecutionError, SerializationError


class AscendConfig:
    """Configuration for remote execution"""

    def __init__(
        self,
        cpu: str = "1",
        memory: str = "2Gi",
        timeout: int = 3600,
        stream_logs: bool = True,
        requirements: Optional[list] = None,
        node_type: Optional[str] = None,
        project: bool = False,
        git_check: Optional[bool] = None,
        base_image: Optional[str] = None,
    ):
        self.cpu = cpu
        self.memory = memory
        self.timeout = timeout
        self.stream_logs = stream_logs
        # Store None to distinguish "not provided" from "empty list"
        self._requirements_provided = requirements is not None
        self.requirements = requirements if requirements is not None else []
        # Validate and store node_type
        self.node_type = validate_node_type(node_type) if node_type else None
        # Store project flag for multi-project support
        self.project = project
        # Tri-state: None = defer to YAML config, True/False = override
        self.git_check = git_check
        # Explicit Docker base image override (e.g. pytorch/pytorch:...)
        self.base_image = base_image


def ascend(
    cpu: str = "1",
    memory: str = "2Gi",
    timeout: int = 3600,
    stream_logs: bool = True,
    requirements: Optional[list] = None,
    node_type: Optional[str] = None,
    project: bool = False,
    git_check: Optional[bool] = None,
    base_image: Optional[str] = None,
) -> Callable:
    """
    Decorator to execute a function on AKS.

    Args:
        cpu: CPU request (e.g., "1", "2", "4")
        memory: Memory request (e.g., "2Gi", "4Gi", "8Gi")
        timeout: Maximum execution time in seconds
        stream_logs: Whether to stream logs to terminal
        requirements: List of pip packages (e.g., ["numpy==1.24.0"])
        node_type: Node type for execution (e.g., "standard_medium", "gpu_small", "memory_large")
        project: If True, execute in shared project namespace (identified by Git repository name)
        git_check: Whether to validate Git repository state (clean working tree) before
            job submission. Defaults to None which defers to the ``git_check`` value in
            ``.ascend.yaml`` (itself defaulting to True). Set to False to suppress
            Git dirty-tree warnings.
        base_image: Docker base image override for GPU workloads. When set, the
            image builder uses this as the ``FROM`` image instead of auto-detecting
            from requirements. Useful for pinning a specific PyTorch + CUDA
            combination (e.g. ``"pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime"``).
            The image is cached in ACR on first use.

    Example:
        @ascend(cpu="2", memory="4Gi", requirements=["pandas", "scikit-learn"])
        def train_model(data, epochs=10):
            import pandas as pd
            from sklearn.ensemble import RandomForestClassifier
            # Training code...
            return model

        # GPU example with auto-detected PyTorch base image:
        @ascend(node_type="gpu_small", requirements=["torch==2.5.1", "transformers"])
        def train_gpu_model(data):
            import torch
            # GPU training code...
            return model

        # GPU example with explicit base image:
        @ascend(
            node_type="gpu_small",
            base_image="pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime",
            requirements=["transformers"],
        )
        def train_custom_base(data):
            import torch
            return model
        
        # Project example (uses Git repository name):
        @ascend(cpu="4", memory="8Gi", project=True)
        def analyze_team_data(data):
            # Run in shared project namespace for the Git repository
            # Requires clean Git tree and attaches commit metadata
            return results

        result = train_model(my_data, epochs=20)
    """
    config = AscendConfig(
        cpu=cpu,
        memory=memory,
        timeout=timeout,
        stream_logs=stream_logs,
        requirements=requirements,
        node_type=node_type,
        project=project,
        git_check=git_check,
        base_image=base_image,
    )

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # Get username from config (loaded first so git_check can be resolved)
            from .config import load_config
            user_config = load_config(project=config.project)

            # Resolve git_check: decorator param > YAML config > default True
            effective_git_check = config.git_check
            if effective_git_check is None:
                effective_git_check = user_config.get("git_check", True)

            # Validate Git repository and get metadata
            from .git_utils import validate_git_repository, get_git_commit_metadata, get_git_repo_name
            
            try:
                if effective_git_check:
                    validate_git_repository()
                git_metadata = get_git_commit_metadata()
                repo_name = get_git_repo_name()
            except Exception as e:
                # If we have a project specified, fail hard - projects MUST be Git repos
                if config.project:
                    raise ExecutionError(
                        f"Project execution requires a clean Git repository: {e}"
                    )
                # When git_check is disabled, silently proceed without metadata
                if effective_git_check:
                    import warnings
                    warnings.warn(
                        f"Git repository validation failed: {e}. "
                        "Job will proceed without Git metadata. "
                        "For better traceability, initialize a Git repository.",
                        UserWarning
                    )
                git_metadata = None
                repo_name = None
            username = user_config["username"]
            
            # Determine project name
            project_name = "default"
            if config.project and repo_name:
                project_name = repo_name
            
            # 1. Validate and serialize function and arguments
            # Validate each argument individually to catch objects with
            # non-serializable internal state (e.g. Optuna Trial, DB
            # connections) before they cause silent failures on the pod.
            for i, arg in enumerate(args):
                validate_serialization(
                    arg,
                    name=f"argument {i} of {func.__name__}()",
                )
            for key, val in kwargs.items():
                validate_serialization(
                    val,
                    name=f"keyword argument '{key}' of {func.__name__}()",
                )

            serialized_func = serialize(func, by_value=True)
            serialized_args = serialize((args, kwargs))

            # 2. Load dependencies from requirements file (if not provided)
            # Create a local copy to avoid modifying the shared config
            requirements = config.requirements.copy()

            # Only try to auto-detect requirements if not explicitly provided
            if not config._requirements_provided:
                from .dependencies.analyzer import get_requirements

                try:
                    requirements = get_requirements()
                except FileNotFoundError as e:
                    raise FileNotFoundError(
                        f"Unable to determine dependencies: {e}\n"
                        "Please either:\n"
                        "1. Create a requirements.txt file in your project directory, or\n"
                        "2. Specify requirements explicitly: @ascend(requirements=['package1', 'package2'])"
                    )
            
            # Calculate dependency hash
            dep_hash = "00000000"  # Default
            if requirements:
                from .dependencies.analyzer import create_dependency_set
                from .node_types import get_node_type_info
                
                # Determine if GPU is used
                use_gpu = False
                if config.node_type:
                    node_info = get_node_type_info(config.node_type)
                    use_gpu = node_info.gpu_count > 0
                
                dep_set = create_dependency_set(
                    requirements=requirements,
                    use_gpu=use_gpu,
                    base_image=config.base_image,
                )
                dep_hash = dep_set.calculate_hash()[:8]  # First 8 chars
            
            # Calculate args hash for run hash
            args_hash = hashlib.sha256(serialized_args).hexdigest()[:8]
            
            # Generate content-addressable job ID
            job_id = generate_job_id(
                user=username,
                project=project_name,
                dep_hash=dep_hash,
                function_name=func.__name__,
                args_hash=args_hash,
            )

            # 3. Create execution package with Git metadata
            import sys as _sys
            package = {
                "function": serialized_func,
                "args": serialized_args,
                "requirements": requirements,
                "job_id": job_id,
                "function_name": func.__name__,
                "project": project_name,
                "dep_hash": dep_hash,
                "python_version": f"{_sys.version_info.major}.{_sys.version_info.minor}",
            }
            
            # Add Git metadata if available
            if git_metadata:
                package["git_metadata"] = git_metadata
                package["repo_name"] = repo_name

            # 4. Execute remotely via cloud backend
            from .cloud.registry import get_backend
            from .runtime.executor import RemoteExecutor

            backend = get_backend()
            executor = RemoteExecutor(config, backend)
            result = executor.execute(package)

            return result

        return wrapper

    return decorator
