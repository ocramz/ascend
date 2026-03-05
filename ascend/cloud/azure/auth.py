"""Azure authentication utilities

Wraps ``DefaultAzureCredential`` with caching and clear error messages.
"""

from typing import Optional

from ...utils.errors import AuthenticationError

# Module-level credential cache
_cached_credential = None


def get_azure_credential():
    """
    Return a cached ``DefaultAzureCredential`` instance.

    On first call the credential is created and verified with a test
    ``get_token`` call.  Subsequent calls return the cached object.

    Returns:
        An ``azure.identity.DefaultAzureCredential`` instance.

    Raises:
        AuthenticationError: If Azure credentials cannot be obtained.
    """
    global _cached_credential

    if _cached_credential is not None:
        return _cached_credential

    try:
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()

        # Quick sanity check – attempt to fetch a management-plane token.
        # This surfaces auth problems early with a friendly message.
        credential.get_token("https://management.azure.com/.default")

        _cached_credential = credential
        return _cached_credential

    except ImportError:
        raise AuthenticationError(
            "azure-identity package is not installed. "
            "Install it with: pip install azure-identity"
        )
    except Exception as exc:
        raise AuthenticationError(
            f"Failed to obtain Azure credentials: {exc}\n"
            "Make sure you are logged in via 'az login' or have "
            "appropriate environment variables set."
        )


def clear_credential_cache() -> None:
    """Clear the cached credential (useful for testing)."""
    global _cached_credential
    _cached_credential = None
