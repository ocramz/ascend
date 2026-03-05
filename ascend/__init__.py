"""
Ascend - Serverless cloud execution for Python
"""

import logging as _logging
import sys as _sys
from typing import Any as _Any

# Library best practice: add NullHandler so applications control log output
_logging.getLogger("ascend").addHandler(_logging.NullHandler())

# ---------------------------------------------------------------------------
# Suppress a spurious TypeError raised by adlfs during interpreter shutdown.
#
# AzureBlobFileSystem registers a weakref.finalize callback that calls
#   await credential.close()
# During module teardown credential.close() may return None instead of
# a coroutine, producing:
#   TypeError: object NoneType can't be used in 'await' expression
#
# Primary fix: the atexit handler in ascend.cloud.registry replaces the
# credential with a safely-awaitable dummy (see _SafeCredential).
#
# Safety nets below:
# 1. sys.excepthook – weakref.finalize._exitfunc routes finalizer errors
#    through sys.excepthook, so we filter the adlfs TypeError there.
# 2. sys.unraisablehook – catches the same error if the AzureBlobFileSystem
#    is garbage-collected outside _exitfunc.
# ---------------------------------------------------------------------------

def _is_adlfs_shutdown_error(
    exc_type: type | None, exc_value: BaseException | None,
) -> bool:
    return (
        exc_type is TypeError
        and exc_value is not None
        and "NoneType" in str(exc_value)
        and "await" in str(exc_value)
    )


_orig_excepthook = _sys.excepthook


def _quiet_adlfs_excepthook(
    exc_type: type, exc_value: BaseException, exc_tb: _Any,
) -> None:
    if _is_adlfs_shutdown_error(exc_type, exc_value):
        return
    _orig_excepthook(exc_type, exc_value, exc_tb)


_sys.excepthook = _quiet_adlfs_excepthook

_orig_unraisablehook = _sys.unraisablehook


def _quiet_adlfs_unraisable(unraisable: _Any) -> None:
    """Silence adlfs/fsspec TypeError during interpreter shutdown."""
    if _is_adlfs_shutdown_error(type(unraisable.exc_value), unraisable.exc_value):
        return
    _orig_unraisablehook(unraisable)


_sys.unraisablehook = _quiet_adlfs_unraisable

from .decorator import ascend, AscendConfig
from .node_types import NodeType

# Fail fast if no cloud backend extra is installed
from .cloud.registry import detect_backend_name as _detect_backend

_detect_backend()

__version__ = "0.1.0"
__all__ = ["ascend", "AscendConfig", "NodeType"]
