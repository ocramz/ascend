"""
Blob storage path utilities for project-scoped storage.

Implements the storage hierarchy:
projects/{project}/users/{user}/jobs/{job-id}/...
"""

from typing import Optional


def get_job_base_path(project: str, user: str, job_id: str) -> str:
    """
    Get base path for a job in blob storage.
    
    Args:
        project: Project name
        user: Username
        job_id: Job identifier
        
    Returns:
        Base path: projects/{project}/users/{user}/jobs/{job-id}
        
    Example:
        >>> get_job_base_path("frauddetect", "alice", "20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a")
        'projects/frauddetect/users/alice/jobs/20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a'
    """
    return f"projects/{project}/users/{user}/jobs/{job_id}"


def get_package_path(project: str, user: str, job_id: str) -> str:
    """Get path for job package (serialized function + args)"""
    return f"{get_job_base_path(project, user, job_id)}/package.pkl"


def get_metadata_path(project: str, user: str, job_id: str) -> str:
    """Get path for job metadata JSON"""
    return f"{get_job_base_path(project, user, job_id)}/metadata.json"


def get_log_path(project: str, user: str, job_id: str) -> str:
    """Get path for job log file (JSON Lines)"""
    return f"{get_job_base_path(project, user, job_id)}/job.log.jsonl"


def get_result_path(project: str, user: str, job_id: str) -> str:
    """Get path for job result (serialized output)"""
    return f"{get_job_base_path(project, user, job_id)}/result.pkl"


def get_exception_path(project: str, user: str, job_id: str) -> str:
    """Get path for job exception (serialized exception info from failed jobs)"""
    return f"{get_job_base_path(project, user, job_id)}/exception.pkl"


def get_artifacts_base_path(project: str, user: str, job_id: str) -> str:
    """Get base path for user-generated artifacts"""
    return f"{get_job_base_path(project, user, job_id)}/artifacts"


def get_artifact_path(project: str, user: str, job_id: str, artifact_name: str) -> str:
    """Get path for a specific artifact"""
    return f"{get_artifacts_base_path(project, user, job_id)}/{artifact_name}"


def get_user_jobs_prefix(project: str, user: str) -> str:
    """
    Get prefix for listing all jobs by a user in a project.
    
    Args:
        project: Project name
        user: Username
        
    Returns:
        Prefix: projects/{project}/users/{user}/jobs/
    """
    return f"projects/{project}/users/{user}/jobs/"


def get_project_logs_by_date_path(project: str, year: str, month: str, day: str) -> str:
    """
    Get path for logs organized by date (secondary index).
    
    Args:
        project: Project name
        year: Year (YYYY)
        month: Month (MM)
        day: Day (DD)
        
    Returns:
        Path: projects/{project}/logs/by-date/{YYYY}/{MM}/{DD}/
    """
    return f"projects/{project}/logs/by-date/{year}/{month}/{day}/"


def get_image_dependency_path(project: str, user: str, dep_hash: str) -> str:
    """
    Get path for image dependency files.
    
    Args:
        project: Project name
        user: Username
        dep_hash: Dependency hash
        
    Returns:
        Path: projects/{project}/users/{user}/images/{dep-hash}/
    """
    return f"projects/{project}/users/{user}/images/{dep_hash}/"


# Legacy path support (backward compatibility)
def get_legacy_package_path(user: str, job_id: str) -> str:
    """
    Get legacy package path (without project scoping).
    
    For backward compatibility with existing storage.
    
    Returns:
        Path: users/{user}/jobs/{job-id}/package.pkl
    """
    return f"users/{user}/jobs/{job_id}/package.pkl"


def get_legacy_result_path(user: str, job_id: str) -> str:
    """
    Get legacy result path (without project scoping).
    
    For backward compatibility with existing storage.
    
    Returns:
        Path: users/{user}/jobs/{job-id}/result.pkl
    """
    return f"users/{user}/jobs/{job_id}/result.pkl"
