"""Azure Blob Storage operations via fsspec/adlfs.

Implements :class:`CloudStorage` using the ``adlfs`` fsspec backend
so that all I/O flows through cloud-agnostic primitives.
"""

from __future__ import annotations

import logging

import fsspec

from ascend.cloud.base import CloudStorage

logger = logging.getLogger(__name__)


class _SafeCredential:
    """Dummy credential whose ``close()`` returns a real coroutine.

    Replaces the real credential on the ``AzureBlobFileSystem`` before
    shutdown so that the weakref finalizer's
    ``await file_obj.credential.close()`` doesn't hit
    ``TypeError: object NoneType can't be used in 'await' expression``.
    """

    async def close(self) -> None:  # noqa: D102
        pass


class AzureCloudStorage(CloudStorage):
    """Azure Blob Storage backend for :class:`CloudStorage`."""

    def __init__(self, account_name: str, credential: object) -> None:
        self._account_name = account_name
        self._fs = fsspec.filesystem(
            "az",
            account_name=account_name,
            credential=credential,
        )

    def get_filesystem(self) -> fsspec.AbstractFileSystem:
        return self._fs

    def storage_uri(self, path: str) -> str:
        """Return ``az://ascend-data/<path>``."""
        return f"az://ascend-data/{path}"

    def close(self) -> None:
        """Pre-empt adlfs weakref finalizer to avoid shutdown TypeError.

        ``AzureBlobFileSystem`` registers a ``weakref.finalize`` callback
        that calls ``await credential.close()``.  During interpreter
        shutdown the async machinery is already torn down and the await
        raises ``TypeError: object NoneType can't be used in 'await'
        expression``.

        The finalizer stores a strong reference to the filesystem in its
        args, so the instance survives until ``weakref.finalize._exitfunc``
        runs.  Clearing the instance cache or dropping ``self._fs``
        therefore does **not** prevent the finalizer from firing.

        Instead we *replace* the credential with a dummy whose ``close()``
        returns a proper coroutine.  When ``_exitfunc`` later invokes the
        finalizer, ``await _SafeCredential().close()`` succeeds silently.
        """
        fs = self._fs
        if fs is None:
            return
        # Swap in a safely-awaitable credential before shutdown.
        try:
            fs.credential = _SafeCredential()
        except Exception:
            pass
        try:
            fs.clear_instance_cache()
        except Exception:
            pass
        self._fs = None  # type: ignore[assignment]

    def ensure_container(self, name: str) -> None:
        try:
            self._fs.mkdir(name)
        except FileExistsError:
            pass
        except Exception:
            # Some fsspec backends raise other errors when the container
            # already exists.  Log at DEBUG so the info is available in
            # verbose diagnostics without being noisy by default.
            logger.debug(
                "Container creation for '%s' raised (may already exist)",
                name, exc_info=True,
            )
