"""Backward-compatibility re-export.

The node pool validator has moved to ``ascend.cloud.azure.node_pool_validator``.
"""

from ascend.cloud.azure.node_pool_validator import (  # noqa: F401
    NodePoolValidator,
    validate_node_pool_availability,
)

__all__ = ["NodePoolValidator", "validate_node_pool_availability"]
