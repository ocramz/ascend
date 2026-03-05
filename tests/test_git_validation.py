"""Tests for the git_check configuration option.

Verifies that:
- ``git_check=False`` on the decorator suppresses the UserWarning
- ``git_check=True`` (the default) still warns when the tree is dirty
- YAML ``git_check: false`` is respected when the decorator does not override
- An explicit decorator value overrides the YAML setting
"""

import warnings
from unittest.mock import patch, MagicMock

import pytest

from ascend.decorator import AscendConfig
from ascend.git_utils import GitError


# ---------------------------------------------------------------------------
# AscendConfig unit tests
# ---------------------------------------------------------------------------


class TestAscendConfigGitCheck:
    """AscendConfig stores git_check properly."""

    def test_default_is_none(self):
        config = AscendConfig()
        assert config.git_check is None

    def test_explicit_true(self):
        config = AscendConfig(git_check=True)
        assert config.git_check is True

    def test_explicit_false(self):
        config = AscendConfig(git_check=False)
        assert config.git_check is False


# ---------------------------------------------------------------------------
# Decorator wrapper integration tests (heavily mocked)
# ---------------------------------------------------------------------------

# All tests below patch the full remote-execution path so that
# only the git-validation / warning path is exercised.

_VALID_USER_CONFIG = {
    "username": "testuser",
    "cluster_name": "ascend-test",
    "resource_group": "test-rg",
    "namespace": "ascend-users-test",
    "storage_account": "teststorage123",
    "container_registry": "testacr.azurecr.io",
}

_GIT_METADATA = {
    "commit_hash": "abc1234567890",
    "short_hash": "abc1234",
    "branch": "main",
    "author": "test <test@test.com>",
    "timestamp": "2025-01-01T00:00:00+00:00",
    "message": "init",
}


def _make_decorated_fn(**decorator_kwargs):
    """Return a decorated no-op function using the given decorator kwargs."""
    from ascend.decorator import ascend

    # Always provide requirements to avoid auto-detection hitting pyproject.toml
    decorator_kwargs.setdefault("requirements", [])

    @ascend(**decorator_kwargs)
    def _fn():
        return 42

    return _fn


def _executor_patches():
    """Context-manager stack that patches everything past git validation."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("ascend.decorator.serialize", side_effect=lambda x, **kw: b"bytes"))
    mock_backend = MagicMock()
    stack.enter_context(patch("ascend.cloud.registry.get_backend", return_value=mock_backend))
    mock_exec = MagicMock()
    mock_exec.execute.return_value = 42
    stack.enter_context(patch("ascend.runtime.executor.RemoteExecutor", return_value=mock_exec))
    return stack


class TestGitCheckDecorator:
    """End-to-end decorator behaviour governed by git_check."""

    # -- git_check=False on decorator ----------------------------------------

    def test_no_warning_when_git_check_false(self):
        """git_check=False skips validate_git_repository; no warning even if
        the tree is dirty, and metadata is still collected best-effort."""
        with _executor_patches(), \
             patch("ascend.git_utils.validate_git_repository") as mock_validate, \
             patch("ascend.git_utils.get_git_commit_metadata", return_value=_GIT_METADATA), \
             patch("ascend.git_utils.get_git_repo_name", return_value="my-repo"), \
             patch("ascend.config.load_config", return_value={**_VALID_USER_CONFIG}):

            fn = _make_decorated_fn(git_check=False)

            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                fn()

            # validate_git_repository should NOT have been called
            mock_validate.assert_not_called()

            # No UserWarning emitted
            git_warnings = [x for x in w if issubclass(x.category, UserWarning)
                            and "Git" in str(x.message)]
            assert git_warnings == []

    def test_no_warning_when_metadata_fails_and_git_check_false(self):
        """Even if metadata collection fails, git_check=False still suppresses
        the warning and the job proceeds with git_metadata=None."""
        with _executor_patches(), \
             patch("ascend.git_utils.validate_git_repository"), \
             patch("ascend.git_utils.get_git_commit_metadata", side_effect=GitError("no repo")), \
             patch("ascend.git_utils.get_git_repo_name", side_effect=GitError("no repo")), \
             patch("ascend.config.load_config", return_value={**_VALID_USER_CONFIG}):

            fn = _make_decorated_fn(git_check=False)

            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                fn()

            git_warnings = [x for x in w if issubclass(x.category, UserWarning)
                            and "Git" in str(x.message)]
            assert git_warnings == []

    # -- git_check=True (default) with dirty tree ----------------------------

    def test_warning_when_git_check_true_and_dirty(self):
        """Default behaviour: dirty tree produces a UserWarning."""
        with _executor_patches(), \
             patch("ascend.git_utils.validate_git_repository",
                   side_effect=GitError("Git working tree is not clean")), \
             patch("ascend.git_utils.get_git_commit_metadata"), \
             patch("ascend.git_utils.get_git_repo_name"), \
             patch("ascend.config.load_config", return_value={**_VALID_USER_CONFIG}):

            fn = _make_decorated_fn()  # git_check defaults to None -> True

            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                fn()

            git_warnings = [x for x in w if issubclass(x.category, UserWarning)
                            and "Git" in str(x.message)]
            assert len(git_warnings) == 1
            assert "Git working tree is not clean" in str(git_warnings[0].message)

    # -- YAML git_check: false with decorator None ---------------------------

    def test_yaml_git_check_false_suppresses_warning(self):
        """YAML config ``git_check: false`` is honoured when decorator
        git_check is None (the default)."""
        with _executor_patches(), \
             patch("ascend.git_utils.validate_git_repository",
                   side_effect=GitError("dirty")), \
             patch("ascend.git_utils.get_git_commit_metadata",
                   side_effect=GitError("no repo")), \
             patch("ascend.git_utils.get_git_repo_name",
                   side_effect=GitError("no repo")), \
             patch("ascend.config.load_config",
                   return_value={**_VALID_USER_CONFIG, "git_check": False}):

            fn = _make_decorated_fn()  # decorator git_check=None

            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                fn()

            git_warnings = [x for x in w if issubclass(x.category, UserWarning)
                            and "Git" in str(x.message)]
            assert git_warnings == []

    # -- Decorator overrides YAML ------------------------------------------

    def test_decorator_true_overrides_yaml_false(self):
        """Decorator git_check=True wins over YAML git_check: false."""
        with _executor_patches(), \
             patch("ascend.git_utils.validate_git_repository",
                   side_effect=GitError("dirty tree")), \
             patch("ascend.git_utils.get_git_commit_metadata"), \
             patch("ascend.git_utils.get_git_repo_name"), \
             patch("ascend.config.load_config",
                   return_value={**_VALID_USER_CONFIG, "git_check": False}):

            fn = _make_decorated_fn(git_check=True)

            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                fn()

            git_warnings = [x for x in w if issubclass(x.category, UserWarning)
                            and "Git" in str(x.message)]
            assert len(git_warnings) == 1

    def test_decorator_false_overrides_yaml_true(self):
        """Decorator git_check=False wins over YAML git_check: true."""
        with _executor_patches(), \
             patch("ascend.git_utils.validate_git_repository") as mock_validate, \
             patch("ascend.git_utils.get_git_commit_metadata",
                   side_effect=GitError("no repo")), \
             patch("ascend.git_utils.get_git_repo_name",
                   side_effect=GitError("no repo")), \
             patch("ascend.config.load_config",
                   return_value={**_VALID_USER_CONFIG, "git_check": True}):

            fn = _make_decorated_fn(git_check=False)

            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                fn()

            # validate_git_repository should not have been called
            mock_validate.assert_not_called()

            git_warnings = [x for x in w if issubclass(x.category, UserWarning)
                            and "Git" in str(x.message)]
            assert git_warnings == []
