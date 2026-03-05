"""Configuration management for Ascend

Loads user configuration from .ascend.yaml, searching upward from the current
directory to the filesystem root.
"""

import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .utils.errors import ConfigError


# Required fields in .ascend.yaml
_REQUIRED_FIELDS = [
    "username",
    "cluster_name",
    "resource_group",
    "namespace",
    "storage_account",
    "container_registry",
]

# Optional top-level fields
_OPTIONAL_FIELDS = [
    "cloud_provider",  # e.g. "azure", "gcp", "aws" — auto-detected if omitted
    "git_check",       # bool — validate Git clean tree before job submission (default: true)
    "auto_build_images",          # bool — enable automatic Kaniko image builds (default: false)
    "managed_identity_client_id", # str — explicit managed identity client ID for pod auth
]


def _find_config_file(start: Optional[Path] = None) -> Path:
    """
    Search for .ascend.yaml from *start* upward to the filesystem root.

    Args:
        start: Directory to begin search (defaults to cwd).

    Returns:
        Path to the config file.

    Raises:
        ConfigError: If no config file is found.
    """
    current = (start or Path.cwd()).resolve()
    while True:
        candidate = current / ".ascend.yaml"
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    raise ConfigError(
        "No .ascend.yaml configuration file found.\n"
        "Run 'ascend user init' to create one, or create it manually."
    )


def _validate_config(config: Dict[str, Any], path: Path) -> None:
    """
    Validate that all required fields are present.

    Raises:
        ConfigError: On missing required fields.
    """
    missing = [f for f in _REQUIRED_FIELDS if f not in config]
    if missing:
        raise ConfigError(
            f"Invalid configuration in {path}: missing required fields: {', '.join(missing)}"
        )


def _reject_placeholder_values(config: Dict[str, Any], path: Path) -> None:
    """
    Reject configuration with PLACEHOLDER values.

    These values are written by the CLI when resource discovery fails
    and must be manually corrected before the config is usable.

    Raises:
        ConfigError: If any critical field contains "PLACEHOLDER".
    """
    # Critical fields that must not be PLACEHOLDER
    critical_fields = ["storage_account", "container_registry", "username", "namespace"]
    
    placeholder_fields = [
        f for f in critical_fields
        if config.get(f) == "PLACEHOLDER"
    ]
    
    if placeholder_fields:
        raise ConfigError(
            f"Invalid configuration in {path}: the following fields contain "
            f"placeholder values and must be manually set: {', '.join(placeholder_fields)}\n\n"
            f"These values could not be auto-discovered during 'ascend user init'. "
            f"Please edit {path} and replace PLACEHOLDER with actual values."
        )


# --- Format / value validation patterns ---
# Kubernetes names: lowercase alphanumeric or '-', must start and end with
# an alphanumeric character, max 63 chars (RFC 1123 label).
_K8S_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

# Azure storage account: 3-24 lowercase letters and digits only.
_STORAGE_ACCOUNT_RE = re.compile(r"^[a-z0-9]{3,24}$")

# Kubernetes resource quantities: integer, decimal, or with suffix
# (e.g. "1", "500m", "2.5", "2Gi", "512Mi").
_K8S_QUANTITY_RE = re.compile(
    r"^[0-9]+(\.[0-9]+)?(m|k|M|G|T|P|E|Ki|Mi|Gi|Ti|Pi|Ei)?$"
)


def _validate_config_values(config: Dict[str, Any], path: Path) -> None:
    """Validate the *format* of configuration values.

    Called after ``_validate_config`` (required-key presence) and
    ``_reject_placeholder_values``, so every validated field is guaranteed
    to exist and not be ``"PLACEHOLDER"``.

    Raises:
        ConfigError: On any value that doesn't match its expected format.
    """
    errors: list[str] = []

    # -- username (must be a valid K8s label component) --
    username = config.get("username", "")
    if not _K8S_NAME_RE.match(username):
        errors.append(
            f"Invalid username '{username}': must be 1-63 lowercase alphanumeric "
            f"characters or '-', and must start and end with an alphanumeric character."
        )

    # -- namespace (K8s namespace naming rules) --
    namespace = config.get("namespace", "")
    if not _K8S_NAME_RE.match(namespace):
        errors.append(
            f"Invalid namespace '{namespace}': must be 1-63 lowercase alphanumeric "
            f"characters or '-', and must start and end with an alphanumeric character."
        )

    # -- storage_account (Azure naming rules) --
    storage_account = config.get("storage_account", "")
    if not _STORAGE_ACCOUNT_RE.match(storage_account):
        errors.append(
            f"Invalid storage_account '{storage_account}': must be 3-24 lowercase "
            f"alphanumeric characters (Azure naming requirement)."
        )

    # -- container_registry (normalise bare ACR names) --
    cr = config.get("container_registry", "")
    if cr and "." not in cr:
        config["container_registry"] = f"{cr}.azurecr.io"

    # -- cpu (optional, validated only when present) --
    cpu = config.get("cpu")
    if cpu is not None and not _K8S_QUANTITY_RE.match(str(cpu)):
        errors.append(
            f"Invalid cpu '{cpu}': must be a valid Kubernetes resource quantity "
            f"(e.g. '1', '500m', '2.5')."
        )

    # -- memory (optional, validated only when present) --
    memory = config.get("memory")
    if memory is not None and not _K8S_QUANTITY_RE.match(str(memory)):
        errors.append(
            f"Invalid memory '{memory}': must be a valid Kubernetes resource quantity "
            f"(e.g. '2Gi', '512Mi', '1024')."
        )

    if errors:
        joined = "\n  - ".join(errors)
        raise ConfigError(
            f"Invalid configuration values in {path}:\n  - {joined}"
        )


def load_config(project: bool = False, start_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Load Ascend configuration from .ascend.yaml.

    Searches upward from *start_dir* (or the current directory).
    When *project* is ``True`` the ``namespace`` value is overridden
    to ``ascend-projects-{repo_name}``.

    Args:
        project: If True, override namespace to project namespace.
        start_dir: Directory to start searching from (defaults to cwd).

    Returns:
        Configuration dictionary with at least the required keys:
        ``username``, ``cluster_name``, ``resource_group``, ``namespace``,
        ``storage_account``, ``container_registry``.

    Raises:
        ConfigError: On missing file, parse error, or missing required fields.
    """
    path = _find_config_file(start=start_dir)

    try:
        with open(path) as fh:
            config = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse {path}: {exc}")

    if not isinstance(config, dict):
        raise ConfigError(f"Invalid configuration in {path}: expected a YAML mapping")

    _validate_config(config, path)
    _reject_placeholder_values(config, path)
    _validate_config_values(config, path)

    # Project namespace override
    if project:
        from .git_utils import get_git_repo_name

        repo_name = get_git_repo_name()
        config["namespace"] = f"ascend-projects-{repo_name}"

    return config


def save_config(path: Path, config_dict: Dict[str, Any]) -> None:
    """
    Write a configuration dictionary to *path* as YAML.

    Args:
        path: Destination file path.
        config_dict: Configuration to persist.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        yaml.dump(config_dict, fh, default_flow_style=False, sort_keys=False)
