"""
Job ID generation and parsing utilities.

Job IDs follow a content-addressable format:
{timestamp}-{user}-{project}-{dep_hash}-{run_hash}

Example: 20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


# Maximum lengths for user and project names in job IDs.
# The Kubernetes Job controller auto-adds a "job-name" label using the job name
# as the value. Label values must be <= 63 characters.
# Job name format: "ascend-{YYYYMMDD}-{HHMMSS}-{user}-{project}-{dep_hash}-{run_hash}"
# Fixed parts: "ascend-" (7) + timestamp (15) + separators (5) + hashes (16) = 43
# Remaining for user + project: 63 - 43 = 20
USER_MAX_LENGTH = 10
PROJECT_MAX_LENGTH = 10


@dataclass
class JobIdComponents:
    """Parsed components of a job ID"""
    timestamp: str
    user: str
    project: str
    dep_hash: str
    run_hash: str
    
    @property
    def full_id(self) -> str:
        """Reconstruct full job ID from components"""
        return f"{self.timestamp}-{self.user}-{self.project}-{self.dep_hash}-{self.run_hash}"


def _sanitize_name(name: str, max_length: int = 16) -> str:
    """
    Sanitize a name for use in job IDs.
    
    Args:
        name: Name to sanitize
        max_length: Maximum length (default: 16)
        
    Returns:
        Sanitized name (lowercase, alphanumeric + hyphens, max length)
    """
    # Convert to lowercase
    sanitized = name.lower()
    
    # Replace invalid characters with hyphens
    sanitized = re.sub(r'[^a-z0-9-]', '-', sanitized)
    
    # Remove consecutive hyphens
    sanitized = re.sub(r'-+', '-', sanitized)
    
    # Remove leading/trailing hyphens
    sanitized = sanitized.strip('-')
    
    # Truncate to max length
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip('-')
    
    # Ensure at least one character
    if not sanitized:
        sanitized = "default"
    
    return sanitized


def generate_job_id(
    user: str,
    project: str = "default",
    dep_hash: Optional[str] = None,
    function_name: str = "",
    args_hash: str = "",
    timestamp: Optional[datetime] = None,
) -> str:
    """
    Generate a content-addressable job ID.
    
    Args:
        user: Username
        project: Project name (default: "default")
        dep_hash: Dependency hash (first 8 chars), if None uses "00000000"
        function_name: Function name for run hash calculation
        args_hash: Arguments hash for run hash calculation
        timestamp: Optional timestamp (default: now UTC)
        
    Returns:
        Job ID in format: {timestamp}-{user}-{project}-{dep_hash}-{run_hash}
        
    Example:
        >>> generate_job_id("alice", "frauddetect", "a3f5d2c8", "train_model", "abc123")
        '20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a'
    """
    # Use current time if not provided
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    
    # Format timestamp as YYYYMMDD-HHMMSS
    timestamp_str = timestamp.strftime("%Y%m%d-%H%M%S")
    
    # Sanitize user and project names
    # Max lengths ensure "ascend-" + job_id <= 63 chars (K8s label limit)
    user_sanitized = _sanitize_name(user, max_length=USER_MAX_LENGTH)
    project_sanitized = _sanitize_name(project, max_length=PROJECT_MAX_LENGTH)
    
    # Use dependency hash or default
    if dep_hash is None:
        dep_hash = "00000000"
    else:
        # Ensure it's exactly 8 characters
        dep_hash = dep_hash[:8].ljust(8, '0')
    
    # Generate run hash from function name + args hash + random nonce
    nonce = secrets.token_hex(4)  # 8 hex chars
    run_data = f"{function_name}{args_hash}{nonce}"
    run_hash = hashlib.sha256(run_data.encode()).hexdigest()[:8]
    
    # Construct job ID
    job_id = f"{timestamp_str}-{user_sanitized}-{project_sanitized}-{dep_hash}-{run_hash}"
    
    return job_id


def parse_job_id(job_id: str) -> JobIdComponents:
    """
    Parse a job ID into its components.
    
    Args:
        job_id: Job ID to parse
        
    Returns:
        JobIdComponents with parsed fields
        
    Raises:
        ValueError: If job ID format is invalid
        
    Example:
        >>> components = parse_job_id("20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a")
        >>> components.user
        'alice'
        >>> components.project
        'frauddetect'
    """
    # Expected format: {timestamp}-{user}-{project}-{dep_hash}-{run_hash}
    # timestamp: YYYYMMDD-HHMMSS (15 chars with hyphen)
    # user: up to 16 chars
    # project: up to 16 chars
    # dep_hash: 8 chars
    # run_hash: 8 chars
    
    pattern = r'^(\d{8}-\d{6})-([a-z0-9-]{1,16})-([a-z0-9-]{1,16})-([a-f0-9]{8})-([a-f0-9]{8})$'
    match = re.match(pattern, job_id)
    
    if not match:
        raise ValueError(
            f"Invalid job ID format: {job_id}\n"
            f"Expected format: YYYYMMDD-HHMMSS-user-project-dephash-runhash"
        )
    
    timestamp, user, project, dep_hash, run_hash = match.groups()
    
    return JobIdComponents(
        timestamp=timestamp,
        user=user,
        project=project,
        dep_hash=dep_hash,
        run_hash=run_hash,
    )


def validate_job_id(job_id: str) -> bool:
    """
    Validate a job ID format.
    
    Args:
        job_id: Job ID to validate
        
    Returns:
        True if valid, False otherwise
    """
    try:
        parse_job_id(job_id)
        return True
    except ValueError:
        return False


def extract_metadata_from_job_id(job_id: str) -> dict:
    """
    Extract metadata from a job ID for filtering/querying.
    
    Args:
        job_id: Job ID to extract metadata from
        
    Returns:
        Dictionary with extracted metadata
        
    Example:
        >>> metadata = extract_metadata_from_job_id("20251204-143022-alice-frauddetect-a3f5d2c8-7b9e4f1a")
        >>> metadata['user']
        'alice'
        >>> metadata['date']
        '2025-12-04'
    """
    components = parse_job_id(job_id)
    
    # Parse timestamp to extract date/time components
    timestamp_str = components.timestamp
    year = timestamp_str[0:4]
    month = timestamp_str[4:6]
    day = timestamp_str[6:8]
    hour = timestamp_str[9:11]
    minute = timestamp_str[11:13]
    second = timestamp_str[13:15]
    
    return {
        "job_id": job_id,
        "timestamp": components.timestamp,
        "date": f"{year}-{month}-{day}",
        "time": f"{hour}:{minute}:{second}",
        "year": year,
        "month": month,
        "day": day,
        "user": components.user,
        "project": components.project,
        "dep_hash": components.dep_hash,
        "run_hash": components.run_hash,
    }
