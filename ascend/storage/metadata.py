"""
Job metadata schema and generation.

Metadata is stored as metadata.json alongside each job in blob storage.
"""

import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any


@dataclass
class DependencyMetadata:
    """Dependency information for a job"""
    hash: str
    python_version: str
    packages: List[str] = field(default_factory=list)
    system_packages: List[str] = field(default_factory=list)
    use_gpu: bool = False


@dataclass
class ExecutionMetadata:
    """Execution details for a job"""
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_seconds: Optional[float] = None
    pod_name: Optional[str] = None
    namespace: Optional[str] = None
    node_name: Optional[str] = None
    exit_code: Optional[int] = None


@dataclass
class ResourceMetadata:
    """Resource usage for a job"""
    cpu_requested: str = "1"
    memory_requested: str = "2Gi"
    gpu_requested: str = "0"
    peak_memory_mb: Optional[float] = None
    avg_cpu_percent: Optional[float] = None
    avg_gpu_utilization_percent: Optional[float] = None


@dataclass
class ArtifactInfo:
    """Information about a single artifact"""
    name: str
    type: str
    size_bytes: int
    created_at: str


@dataclass
class JobMetadata:
    """Complete metadata for a job"""
    job_id: str
    created_at: str
    updated_at: str
    status: str  # queued, running, completed, failed, cancelled
    user: str
    project: str
    function_name: str
    config: Dict[str, Any]
    dependencies: DependencyMetadata
    execution: ExecutionMetadata = field(default_factory=ExecutionMetadata)
    resources: ResourceMetadata = field(default_factory=ResourceMetadata)
    artifacts: List[ArtifactInfo] = field(default_factory=list)
    tags: Dict[str, str] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        result = {
            "job_id": self.job_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "user": self.user,
            "project": self.project,
            "function_name": self.function_name,
            "config": self.config,
            "dependencies": asdict(self.dependencies),
            "execution": asdict(self.execution),
            "resources": asdict(self.resources),
            "artifacts": [asdict(a) for a in self.artifacts],
            "tags": self.tags,
        }
        return result
    
    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
    
    @classmethod
    def from_dict(cls, data: dict) -> "JobMetadata":
        """Create from dictionary"""
        # Parse dependencies
        dep_data = data.get("dependencies", {})
        dependencies = DependencyMetadata(
            hash=dep_data.get("hash", ""),
            python_version=dep_data.get("python_version", f"{sys.version_info.major}.{sys.version_info.minor}"),
            packages=dep_data.get("packages", []),
            system_packages=dep_data.get("system_packages", []),
            use_gpu=dep_data.get("use_gpu", False),
        )
        
        # Parse execution
        exec_data = data.get("execution", {})
        execution = ExecutionMetadata(**exec_data)
        
        # Parse resources
        res_data = data.get("resources", {})
        resources = ResourceMetadata(**res_data) if res_data else ResourceMetadata()
        
        # Parse artifacts
        artifacts = [
            ArtifactInfo(**a) for a in data.get("artifacts", [])
        ]
        
        return cls(
            job_id=data["job_id"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            status=data["status"],
            user=data["user"],
            project=data["project"],
            function_name=data["function_name"],
            config=data.get("config", {}),
            dependencies=dependencies,
            execution=execution,
            resources=resources,
            artifacts=artifacts,
            tags=data.get("tags", {}),
        )
    
    @classmethod
    def from_json(cls, json_str: str) -> "JobMetadata":
        """Create from JSON string"""
        data = json.loads(json_str)
        return cls.from_dict(data)


def create_job_metadata(
    job_id: str,
    user: str,
    project: str,
    function_name: str,
    config: Dict[str, Any],
    dep_hash: str = "00000000",
    python_version: Optional[str] = None,
    packages: Optional[List[str]] = None,
    use_gpu: bool = False,
) -> JobMetadata:
    """
    Create initial job metadata.
    
    Args:
        job_id: Job identifier
        user: Username
        project: Project name
        function_name: Function being executed
        config: Job configuration (cpu, memory, timeout, etc.)
        dep_hash: Dependency hash
        python_version: Python version (auto-detects if None)
        packages: List of package specifications
        use_gpu: Whether GPU is used
        
    Returns:
        JobMetadata instance
    """
    if python_version is None:
        python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    
    now = datetime.now(timezone.utc).isoformat()
    
    dependencies = DependencyMetadata(
        hash=dep_hash,
        python_version=python_version,
        packages=packages or [],
        system_packages=[],
        use_gpu=use_gpu,
    )
    
    resources = ResourceMetadata(
        cpu_requested=config.get("cpu", "1"),
        memory_requested=config.get("memory", "2Gi"),
        gpu_requested="1" if use_gpu else "0",
    )
    
    return JobMetadata(
        job_id=job_id,
        created_at=now,
        updated_at=now,
        status="queued",
        user=user,
        project=project,
        function_name=function_name,
        config=config,
        dependencies=dependencies,
        resources=resources,
    )


def update_metadata_status(
    metadata: JobMetadata,
    status: str,
    execution_data: Optional[Dict[str, Any]] = None,
) -> JobMetadata:
    """
    Update metadata with new status and execution data.
    
    Args:
        metadata: Current metadata
        status: New status
        execution_data: Optional execution details to update
        
    Returns:
        Updated metadata
    """
    metadata.status = status
    metadata.updated_at = datetime.now(timezone.utc).isoformat()
    
    if execution_data:
        for key, value in execution_data.items():
            if hasattr(metadata.execution, key):
                setattr(metadata.execution, key, value)
    
    return metadata
