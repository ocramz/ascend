"""Tests for config format validation (_validate_config_values)."""

from pathlib import Path

import pytest
import yaml

from ascend.config import load_config, _validate_config_values
from ascend.utils.errors import ConfigError


def _write_config(tmp_path: Path, overrides: dict) -> Path:
    """Write a valid config with *overrides* applied and return tmp_path."""
    base = {
        "username": "testuser",
        "cluster_name": "ascend-test",
        "resource_group": "test-rg",
        "namespace": "ascend-users-test",
        "storage_account": "teststorage123",
        "container_registry": "testacr.azurecr.io",
    }
    base.update(overrides)
    config_path = tmp_path / ".ascend.yaml"
    with open(config_path, "w") as fh:
        yaml.dump(base, fh)
    return tmp_path


# --------------------------------------------------------------------------- #
#  Username validation
# --------------------------------------------------------------------------- #

class TestUsernameValidation:
    def test_valid_lowercase(self, tmp_path):
        """Lowercase alphanumeric username should pass."""
        cfg = load_config(start_dir=_write_config(tmp_path, {"username": "alice"}))
        assert cfg["username"] == "alice"

    def test_valid_with_hyphens(self, tmp_path):
        cfg = load_config(start_dir=_write_config(tmp_path, {"username": "my-user-1"}))
        assert cfg["username"] == "my-user-1"

    def test_valid_single_char(self, tmp_path):
        cfg = load_config(start_dir=_write_config(tmp_path, {"username": "a"}))
        assert cfg["username"] == "a"

    def test_rejects_uppercase(self, tmp_path):
        with pytest.raises(ConfigError, match="Invalid username"):
            load_config(start_dir=_write_config(tmp_path, {"username": "Alice"}))

    def test_rejects_underscore(self, tmp_path):
        with pytest.raises(ConfigError, match="Invalid username"):
            load_config(start_dir=_write_config(tmp_path, {"username": "my_user"}))

    def test_rejects_starting_with_hyphen(self, tmp_path):
        with pytest.raises(ConfigError, match="Invalid username"):
            load_config(start_dir=_write_config(tmp_path, {"username": "-user"}))

    def test_rejects_ending_with_hyphen(self, tmp_path):
        with pytest.raises(ConfigError, match="Invalid username"):
            load_config(start_dir=_write_config(tmp_path, {"username": "user-"}))

    def test_rejects_empty(self, tmp_path):
        with pytest.raises(ConfigError, match="Invalid username"):
            load_config(start_dir=_write_config(tmp_path, {"username": ""}))

    def test_rejects_too_long(self, tmp_path):
        with pytest.raises(ConfigError, match="Invalid username"):
            load_config(start_dir=_write_config(tmp_path, {"username": "a" * 64}))


# --------------------------------------------------------------------------- #
#  Namespace validation
# --------------------------------------------------------------------------- #

class TestNamespaceValidation:
    def test_valid_namespace(self, tmp_path):
        cfg = load_config(start_dir=_write_config(tmp_path, {"namespace": "ascend-users-bob"}))
        assert cfg["namespace"] == "ascend-users-bob"

    def test_rejects_uppercase_namespace(self, tmp_path):
        with pytest.raises(ConfigError, match="Invalid namespace"):
            load_config(start_dir=_write_config(tmp_path, {"namespace": "Ascend-Users"}))

    def test_rejects_dots_in_namespace(self, tmp_path):
        with pytest.raises(ConfigError, match="Invalid namespace"):
            load_config(start_dir=_write_config(tmp_path, {"namespace": "my.namespace"}))


# --------------------------------------------------------------------------- #
#  Storage account validation
# --------------------------------------------------------------------------- #

class TestStorageAccountValidation:
    def test_valid_storage_account(self, tmp_path):
        cfg = load_config(start_dir=_write_config(tmp_path, {"storage_account": "mystore123"}))
        assert cfg["storage_account"] == "mystore123"

    def test_valid_min_length(self, tmp_path):
        cfg = load_config(start_dir=_write_config(tmp_path, {"storage_account": "abc"}))
        assert cfg["storage_account"] == "abc"

    def test_valid_max_length(self, tmp_path):
        cfg = load_config(start_dir=_write_config(tmp_path, {"storage_account": "a" * 24}))
        assert cfg["storage_account"] == "a" * 24

    def test_rejects_too_short(self, tmp_path):
        with pytest.raises(ConfigError, match="Invalid storage_account"):
            load_config(start_dir=_write_config(tmp_path, {"storage_account": "ab"}))

    def test_rejects_too_long(self, tmp_path):
        with pytest.raises(ConfigError, match="Invalid storage_account"):
            load_config(start_dir=_write_config(tmp_path, {"storage_account": "a" * 25}))

    def test_rejects_uppercase(self, tmp_path):
        with pytest.raises(ConfigError, match="Invalid storage_account"):
            load_config(start_dir=_write_config(tmp_path, {"storage_account": "MyStore"}))

    def test_rejects_hyphens(self, tmp_path):
        with pytest.raises(ConfigError, match="Invalid storage_account"):
            load_config(start_dir=_write_config(tmp_path, {"storage_account": "my-store"}))

    def test_rejects_underscores(self, tmp_path):
        with pytest.raises(ConfigError, match="Invalid storage_account"):
            load_config(start_dir=_write_config(tmp_path, {"storage_account": "my_store"}))


# --------------------------------------------------------------------------- #
#  Container registry normalization
# --------------------------------------------------------------------------- #

class TestContainerRegistryNormalization:
    def test_fqdn_unchanged(self, tmp_path):
        """A fully-qualified registry name should be left as-is."""
        cfg = load_config(start_dir=_write_config(tmp_path, {"container_registry": "myacr.azurecr.io"}))
        assert cfg["container_registry"] == "myacr.azurecr.io"

    def test_bare_name_gets_suffix(self, tmp_path):
        """A bare ACR name (no dot) should be normalised with .azurecr.io."""
        cfg = load_config(start_dir=_write_config(tmp_path, {"container_registry": "myacr"}))
        assert cfg["container_registry"] == "myacr.azurecr.io"

    def test_custom_domain_unchanged(self, tmp_path):
        """A custom domain with dots should not be modified."""
        cfg = load_config(start_dir=_write_config(tmp_path, {"container_registry": "registry.example.com"}))
        assert cfg["container_registry"] == "registry.example.com"


# --------------------------------------------------------------------------- #
#  CPU validation (optional field)
# --------------------------------------------------------------------------- #

class TestCpuValidation:
    def test_omitted_cpu_is_fine(self, tmp_path):
        """CPU is optional — omitting it should not raise."""
        cfg = load_config(start_dir=_write_config(tmp_path, {}))
        assert "cpu" not in cfg  # not provided in config

    def test_valid_integer_cpu(self, tmp_path):
        cfg = load_config(start_dir=_write_config(tmp_path, {"cpu": "4"}))
        assert cfg["cpu"] == "4"

    def test_valid_millicore_cpu(self, tmp_path):
        cfg = load_config(start_dir=_write_config(tmp_path, {"cpu": "500m"}))
        assert cfg["cpu"] == "500m"

    def test_valid_decimal_cpu(self, tmp_path):
        cfg = load_config(start_dir=_write_config(tmp_path, {"cpu": "2.5"}))
        assert cfg["cpu"] == "2.5"

    def test_rejects_invalid_cpu(self, tmp_path):
        with pytest.raises(ConfigError, match="Invalid cpu"):
            load_config(start_dir=_write_config(tmp_path, {"cpu": "lots"}))

    def test_rejects_negative_cpu(self, tmp_path):
        with pytest.raises(ConfigError, match="Invalid cpu"):
            load_config(start_dir=_write_config(tmp_path, {"cpu": "-1"}))


# --------------------------------------------------------------------------- #
#  Memory validation (optional field)
# --------------------------------------------------------------------------- #

class TestMemoryValidation:
    def test_omitted_memory_is_fine(self, tmp_path):
        cfg = load_config(start_dir=_write_config(tmp_path, {}))
        assert "memory" not in cfg

    def test_valid_gi_memory(self, tmp_path):
        cfg = load_config(start_dir=_write_config(tmp_path, {"memory": "2Gi"}))
        assert cfg["memory"] == "2Gi"

    def test_valid_mi_memory(self, tmp_path):
        cfg = load_config(start_dir=_write_config(tmp_path, {"memory": "512Mi"}))
        assert cfg["memory"] == "512Mi"

    def test_valid_plain_integer_memory(self, tmp_path):
        cfg = load_config(start_dir=_write_config(tmp_path, {"memory": "1024"}))
        assert cfg["memory"] == "1024"

    def test_rejects_invalid_memory(self, tmp_path):
        with pytest.raises(ConfigError, match="Invalid memory"):
            load_config(start_dir=_write_config(tmp_path, {"memory": "big"}))


# --------------------------------------------------------------------------- #
#  Multiple errors reported together
# --------------------------------------------------------------------------- #

class TestMultipleErrors:
    def test_reports_all_errors_at_once(self, tmp_path):
        """All format problems should be reported in a single ConfigError."""
        with pytest.raises(ConfigError, match="Invalid username") as exc_info:
            load_config(
                start_dir=_write_config(
                    tmp_path,
                    {
                        "username": "BAD_USER",
                        "namespace": "BAD_NS",
                        "storage_account": "X",
                    },
                )
            )
        msg = str(exc_info.value)
        assert "Invalid namespace" in msg
        assert "Invalid storage_account" in msg
