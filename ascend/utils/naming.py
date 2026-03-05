"""Utility functions for generating Azure resource names and deriving usernames."""

import hashlib


def generate_resource_names(resource_group: str) -> dict:
    """
    Generate consistent Azure resource names from resource group.

    Uses MD5 hash to create a unique 6-character suffix that ensures
    resource names don't conflict and stay within Azure naming limits.

    Args:
        resource_group: Azure resource group name

    Returns:
        Dictionary with 'storage_account' and 'container_registry' names

    Example:
        >>> names = generate_resource_names("my-resource-group")
        >>> names['storage_account']
        'ascend9a7f6b'
        >>> names['container_registry']
        'ascend9a7f6bacr'
    """
    # Use MD5 hash to create unique 6-char suffix
    rg_hash = hashlib.md5(resource_group.encode()).hexdigest()[:6]

    return {
        "storage_account": f"ascend{rg_hash}",
        "container_registry": f"ascend{rg_hash}acr",
    }


def derive_username_from_credential(credential) -> str:
    """Derive a Kubernetes-safe username from an Azure credential.

    Decodes the JWT access token to extract the user principal name (UPN),
    then normalises it to a K8s-safe label (lowercase, ``@`` domain stripped,
    dots replaced with hyphens).

    Falls back to the local OS login name when the token cannot be decoded
    (e.g. service principal credentials or missing claims).

    Args:
        credential: An Azure credential object that supports ``get_token()``.

    Returns:
        A lowercase string safe for use as a Kubernetes resource name
        component (RFC 1123 label).

    Example:
        >>> derive_username_from_credential(cred)
        'alice-smith'
    """
    try:
        import json
        import base64

        token = credential.get_token("https://management.azure.com/.default")
        payload = token.token.split(".")[1]
        padding = 4 - len(payload) % 4
        payload += "=" * padding
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        upn = (
            decoded.get("upn")
            or decoded.get("unique_name")
            or decoded.get("preferred_username", "")
        )
        if upn:
            return upn.split("@")[0].lower().replace(".", "-")
    except Exception:
        pass

    import getpass
    return getpass.getuser()
