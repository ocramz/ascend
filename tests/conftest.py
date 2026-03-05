"""Shared test fixtures.

The ``ascend`` package calls ``detect_backend_name()`` at import time which
requires *adlfs* to be installed.  We patch the probe at conftest-load time
so unit tests can run without cloud extras.

Credential bootstrapping
------------------------
A session-scoped ``autouse`` fixture loads Azure test credentials from a
``.env`` file (if present) via ``python-dotenv``.  When the file is absent or
incomplete a warning is emitted and ``DefaultAzureCredential`` handles
fallback automatically.  See ``tests/credential_loader.py`` for details.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from tests.credential_loader import load_test_credentials

# We need to patch detect_backend_name *before* ascend/__init__.py runs.
# Importing ascend.cloud.registry normally triggers ascend/__init__.py,
# so we load the registry module from its file path directly, bypassing
# the normal import chain.

_ascend_root = Path(__file__).resolve().parent.parent / "ascend"

# Pre-populate package stubs so that the registry module's relative imports
# resolve without triggering real __init__.py files.
_cloud_path = _ascend_root / "cloud"
for _name, _path in [
    ("ascend", _ascend_root),
    ("ascend.cloud", _cloud_path),
]:
    if _name not in sys.modules:
        _stub = types.ModuleType(_name)
        _stub.__path__ = [str(_path)]
        _stub.__package__ = _name
        sys.modules[_name] = _stub

# Load base.py first (needed by registry.py's TYPE_CHECKING imports)
_base_spec = importlib.util.spec_from_file_location(
    "ascend.cloud.base", _cloud_path / "base.py"
)
_base_mod = importlib.util.module_from_spec(_base_spec)
sys.modules["ascend.cloud.base"] = _base_mod
_base_spec.loader.exec_module(_base_mod)

# Load registry.py from file
_reg_spec = importlib.util.spec_from_file_location(
    "ascend.cloud.registry", _cloud_path / "registry.py"
)
_reg_mod = importlib.util.module_from_spec(_reg_spec)
sys.modules["ascend.cloud.registry"] = _reg_mod
_reg_spec.loader.exec_module(_reg_mod)

# Patch detect_backend_name so it returns "azure" without probing.
_reg_mod.detect_backend_name = lambda: "azure"

# Remove the stub package entries so the real package __init__.py
# will execute when tests actually import ``ascend``.
for _name in ("ascend", "ascend.cloud"):
    _m = sys.modules.get(_name)
    if _m is not None and not getattr(_m, "__file__", None):
        del sys.modules[_name]


# ---------------------------------------------------------------------------
# Session-scoped fixture: load credentials from .env before any tests run
# ---------------------------------------------------------------------------


def pytest_addoption(parser):
    """Register custom command-line options used across the test suite.

    ValueError is caught because pytest raises it when the same option is
    registered more than once (e.g. if a plugin or conftest loaded in a
    different context already added it).
    """
    try:
        parser.addoption(
            "--integration-debug",
            action="store_true",
            default=False,
            help="Enable debug mode for integration tests",
        )
    except ValueError:
        pass
    try:
        parser.addoption(
            "--rebuild-images",
            action="store_true",
            default=False,
            help="Force rebuild of runtime images (bust all caches)",
        )
    except ValueError:
        pass


@pytest.fixture(scope="session", autouse=True)
def _load_dotenv_credentials():
    """Load Azure credentials from ``.env`` (if present) at session start.

    This is a no-op when ``.env`` is absent – ambient credentials (CI env
    vars, ``az login``) are used instead.  A warning is emitted so
    developers know which path was taken.
    """
    return load_test_credentials()
