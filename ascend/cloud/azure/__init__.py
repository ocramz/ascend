"""Azure cloud provider operations."""

from .backend import create_backend
from .infrastructure import (
    InfrastructureResult,
    ensure_acr_role_assignment,
    ensure_all_infrastructure,
    ensure_runtime_image,
)

__all__ = [
    "create_backend",
    "ensure_acr_role_assignment",
    "ensure_all_infrastructure",
    "ensure_runtime_image",
    "InfrastructureResult",
]
