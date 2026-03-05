"""Cloud backend auto-detection and registry."""

from __future__ import annotations

import atexit
import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ascend.cloud.base import CloudBackend

# Mapping: extra name -> (probe module, backend factory module)
_BACKENDS: dict[str, tuple[str, str]] = {
    "azure": ("adlfs", "ascend.cloud.azure.backend"),
}

_detected: CloudBackend | None = None


class NoBackendError(ImportError):
    """Raised when no cloud backend extra is installed."""

    def __init__(self) -> None:
        extras = ", ".join(f"ascend[{k}]" for k in _BACKENDS)
        super().__init__(
            f"No cloud backend installed. "
            f"Install one with: uv pip install {extras}\n"
            f"  Or run: make install (includes Azure backend by default)\n"
            f"  Or for full dev setup: make setup"
        )


def detect_backend_name() -> str:
    """Return the name of the installed backend extra, or raise.

    Raises:
        NoBackendError: If no backend extra is installed.
        ImportError: If multiple backends are detected and no
            ``cloud_provider`` is set in the config.
    """
    found: list[str] = []
    for name, (probe, _) in _BACKENDS.items():
        try:
            importlib.import_module(probe)
            found.append(name)
        except ImportError:
            continue
    if len(found) == 0:
        raise NoBackendError()
    if len(found) > 1:
        raise ImportError(
            f"Multiple cloud backends detected: {found}. "
            f"Set 'cloud_provider' in .ascend.yaml to disambiguate."
        )
    return found[0]


def get_backend() -> CloudBackend:
    """Return the singleton CloudBackend for the detected provider."""
    global _detected
    if _detected is not None:
        return _detected
    name = detect_backend_name()
    _, factory_module = _BACKENDS[name]
    mod = importlib.import_module(factory_module)
    _detected = mod.create_backend()  # each backend module exposes this
    # Pre-empt adlfs weakref finalizer: atexit handlers run *before*
    # weakref finalizers, so closing the storage here avoids the
    # spurious TypeError at interpreter shutdown.
    atexit.register(_detected.storage.close)
    return _detected


def reset_backend() -> None:
    """Clear the cached backend (useful for testing)."""
    global _detected
    _detected = None
