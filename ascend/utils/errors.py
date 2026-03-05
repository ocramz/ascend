"""Custom exception hierarchy for Ascend"""


class AscendError(Exception):
    """Base exception for all Ascend errors."""


class ConfigError(AscendError):
    """Raised for configuration-related errors (missing file, invalid fields)."""


class AuthenticationError(AscendError):
    """Raised when Azure or Kubernetes authentication fails."""


class SerializationError(AscendError):
    """Raised when function or argument serialization/deserialization fails."""


class ExecutionError(AscendError, RuntimeError):
    """Raised when remote job execution fails."""


class RemoteExecutionError(ExecutionError):
    """
    Raised when a function executed remotely raises an exception.
    
    Preserves the original exception type, message, and traceback
    from the remote execution environment.
    """
    
    def __init__(
        self,
        remote_type: str,
        remote_message: str,
        remote_traceback: str,
        job_id: str = "",
    ):
        """
        Initialize with remote exception details.
        
        Args:
            remote_type: Original exception type name (e.g., "ValueError")
            remote_message: Original exception message
            remote_traceback: Formatted traceback from remote execution
            job_id: Job identifier for context
        """
        self.remote_type = remote_type
        self.remote_message = remote_message
        self.remote_traceback = remote_traceback
        self.job_id = job_id
        
        message = f"Remote function raised {remote_type}: {remote_message}"
        if job_id:
            message = f"Job {job_id} failed.\n{message}"
        
        super().__init__(message)
    
    def __str__(self) -> str:
        """Format with remote traceback for debugging."""
        lines = [
            super().__str__(),
            "",
            "Remote traceback (most recent call last):",
        ]
        # Add indented traceback lines
        for line in self.remote_traceback.splitlines():
            lines.append(f"  {line}")
        
        return "\n".join(lines)


class ImageBuildError(AscendError):
    """Raised when container image building fails."""

    def __init__(self, message: str = "", logs: str | None = None):
        self.logs = logs
        super().__init__(message)


class ImageBuildTimeout(AscendError, TimeoutError):
    """Raised when an image build exceeds its timeout."""


class JobTimeoutError(AscendError, RuntimeError):
    """Raised when a job exceeds its configured timeout."""
