"""Load Azure test credentials from a ``.env`` file with graceful fallback.

This module is the single source of truth for test-credential bootstrapping.
It is called once per session by an ``autouse`` fixture in ``tests/conftest.py``.

Behaviour
---------
1. Try to locate a ``.env`` file (searching upwards from the repo root).
2. If found, load its contents into ``os.environ`` (existing env vars are
   **not** overwritten so that CI-injected secrets always take precedence).
3. Check which of the 8 canonical Azure variables are present after loading.
4. Emit a human-readable warning when the file is missing or incomplete.

The function never raises – missing credentials simply mean integration tests
will be skipped by their own guard fixtures.
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field

from dotenv import load_dotenv, find_dotenv

# Canonical set of env vars used by integration tests.
AZURE_TEST_VARS: tuple[str, ...] = (
    "AZURE_SUBSCRIPTION_ID",
    "AZURE_RESOURCE_GROUP",
    "AZURE_AKS_CLUSTER_NAME",
    "AZURE_STORAGE_ACCOUNT",
    "AZURE_CONTAINER_REGISTRY",
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
)


@dataclass(frozen=True)
class CredentialStatus:
    """Result of credential loading."""

    dotenv_loaded: bool
    available_vars: frozenset[str] = field(default_factory=frozenset)
    missing_vars: frozenset[str] = field(default_factory=frozenset)


def load_test_credentials() -> CredentialStatus:
    """Load test credentials from ``.env`` and report status.

    Returns
    -------
    CredentialStatus
        Which variables were found and which are missing.
    """
    dotenv_path = find_dotenv(usecwd=True)
    dotenv_loaded = bool(dotenv_path)

    if dotenv_loaded:
        # override=False so CI-injected env vars always win.
        load_dotenv(dotenv_path, override=False)

    available = frozenset(v for v in AZURE_TEST_VARS if os.getenv(v))
    missing = frozenset(AZURE_TEST_VARS) - available

    if not dotenv_loaded:
        warnings.warn(
            "No .env file found; falling back to ambient credentials "
            "(az login / env vars).  Copy .env.example → .env for local development.",
            stacklevel=2,
        )
    elif missing:
        warnings.warn(
            f".env loaded from {dotenv_path} but the following variables are "
            f"missing or empty: {sorted(missing)}.  "
            "DefaultAzureCredential will attempt fallback authentication.",
            stacklevel=2,
        )
    else:
        # All vars present – no warning needed, but a debug-level note is useful.
        pass

    return CredentialStatus(
        dotenv_loaded=dotenv_loaded,
        available_vars=available,
        missing_vars=missing,
    )
