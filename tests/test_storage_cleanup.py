"""Tests for fsspec/adlfs shutdown cleanup.

Verifies that :meth:`AzureCloudStorage.close` pre-empts the adlfs
weakref finalizer and that the ``sys.excepthook`` / ``sys.unraisablehook``
safety nets filter only the expected TypeError.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# AzureCloudStorage.close()
# ---------------------------------------------------------------------------

class TestAzureCloudStorageClose:
    """Unit tests for AzureCloudStorage.close()."""

    def _make_storage(self):
        """Create an AzureCloudStorage with a mocked fsspec filesystem."""
        mock_fs = MagicMock()
        with patch("fsspec.filesystem", return_value=mock_fs):
            from ascend.cloud.azure.storage import AzureCloudStorage
            storage = AzureCloudStorage(
                account_name="testacc",
                credential=MagicMock(),
            )
        return storage, mock_fs

    def test_close_clears_instance_cache(self):
        storage, mock_fs = self._make_storage()
        storage.close()
        mock_fs.clear_instance_cache.assert_called_once()

    def test_close_nullifies_fs(self):
        storage, _ = self._make_storage()
        storage.close()
        assert storage._fs is None

    def test_close_is_idempotent(self):
        """Calling close() twice should not raise."""
        storage, _ = self._make_storage()
        storage.close()
        storage.close()  # _fs is None, should not raise

    def test_close_tolerates_cache_error(self):
        storage, mock_fs = self._make_storage()
        mock_fs.clear_instance_cache.side_effect = RuntimeError("boom")
        storage.close()  # should not raise

    def test_close_replaces_credential_with_safe_dummy(self):
        """close() must swap in _SafeCredential so the finalizer is harmless."""
        from ascend.cloud.azure.storage import _SafeCredential
        storage, mock_fs = self._make_storage()
        storage.close()
        assert isinstance(mock_fs.credential, _SafeCredential)

    def test_safe_credential_close_is_awaitable(self):
        """_SafeCredential.close() must return a real coroutine."""
        import asyncio
        from ascend.cloud.azure.storage import _SafeCredential

        cred = _SafeCredential()
        coro = cred.close()
        # Must be a coroutine, not None
        assert asyncio.iscoroutine(coro)
        # Run it to confirm it completes without error
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# CloudStorage base close() is a no-op
# ---------------------------------------------------------------------------

class TestCloudStorageBaseClose:
    def test_base_close_noop(self):
        """The base class close() should be callable and do nothing."""
        from ascend.cloud.base import CloudStorage

        class DummyStorage(CloudStorage):
            def get_filesystem(self):
                return MagicMock()

            def storage_uri(self, path):
                return f"dummy://{path}"

            def ensure_container(self, name):
                pass

        ds = DummyStorage()
        ds.close()  # should not raise


# ---------------------------------------------------------------------------
# sys.excepthook safety net  (weakref._exitfunc routes errors here)
# ---------------------------------------------------------------------------

class TestExceptHook:
    """Verify the custom excepthook filters adlfs TypeError."""

    def test_swallows_adlfs_typeerror(self):
        from ascend import _quiet_adlfs_excepthook

        exc = TypeError("object NoneType can't be used in 'await' expression")
        # Should return None (swallow) rather than forwarding.
        result = _quiet_adlfs_excepthook(TypeError, exc, None)
        assert result is None

    def test_forwards_other_exceptions(self):
        from ascend import _quiet_adlfs_excepthook

        exc = ValueError("unrelated error")
        with patch("ascend._orig_excepthook") as mock_orig:
            _quiet_adlfs_excepthook(ValueError, exc, None)
            mock_orig.assert_called_once_with(ValueError, exc, None)

    def test_forwards_non_adlfs_typeerror(self):
        from ascend import _quiet_adlfs_excepthook

        exc = TypeError("unsupported operand type(s)")
        with patch("ascend._orig_excepthook") as mock_orig:
            _quiet_adlfs_excepthook(TypeError, exc, None)
            mock_orig.assert_called_once_with(TypeError, exc, None)


# ---------------------------------------------------------------------------
# sys.unraisablehook safety net  (GC-triggered finalizer errors)
# ---------------------------------------------------------------------------

class TestUnraisableHook:
    """Verify the custom unraisablehook filters adlfs TypeError."""

    def test_swallows_adlfs_typeerror(self):
        from ascend import _quiet_adlfs_unraisable

        exc = TypeError("object NoneType can't be used in 'await' expression")
        hook_args = SimpleNamespace(
            exc_type=TypeError,
            exc_value=exc,
            exc_traceback=None,
            err_msg=None,
            object=None,
        )
        result = _quiet_adlfs_unraisable(hook_args)
        assert result is None

    def test_forwards_other_exceptions(self):
        from ascend import _quiet_adlfs_unraisable

        exc = ValueError("unrelated error")
        hook_args = SimpleNamespace(
            exc_type=ValueError,
            exc_value=exc,
            exc_traceback=None,
            err_msg=None,
            object=None,
        )
        with patch("ascend._orig_unraisablehook") as mock_orig:
            _quiet_adlfs_unraisable(hook_args)
            mock_orig.assert_called_once_with(hook_args)

    def test_forwards_non_adlfs_typeerror(self):
        from ascend import _quiet_adlfs_unraisable

        exc = TypeError("unsupported operand type(s)")
        hook_args = SimpleNamespace(
            exc_type=TypeError,
            exc_value=exc,
            exc_traceback=None,
            err_msg=None,
            object=None,
        )
        with patch("ascend._orig_unraisablehook") as mock_orig:
            _quiet_adlfs_unraisable(hook_args)
            mock_orig.assert_called_once_with(hook_args)
