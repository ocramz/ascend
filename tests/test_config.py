"""Tests for ascend.config module"""

from pathlib import Path

import pytest
import yaml

from ascend.config import load_config, save_config, _find_config_file
from ascend.utils.errors import ConfigError


@pytest.fixture
def valid_config_dir(tmp_path):
    """Create a temporary directory with a valid .ascend.yaml."""
    config = {
        "username": "testuser",
        "cluster_name": "ascend-test",
        "resource_group": "test-rg",
        "namespace": "ascend-users-test",
        "storage_account": "teststorage123",
        "container_registry": "testacr.azurecr.io",
    }
    config_path = tmp_path / ".ascend.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f)
    return tmp_path


@pytest.fixture
def invalid_config_dir(tmp_path):
    """Create a temporary directory with an invalid .ascend.yaml (missing fields)."""
    config = {
        "username": "testuser",
        "storage_account": "minio",
    }
    config_path = tmp_path / ".ascend.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f)
    return tmp_path


class TestFindConfigFile:
    def test_finds_config_in_current_dir(self, valid_config_dir):
        path = _find_config_file(valid_config_dir)
        assert path == valid_config_dir / ".ascend.yaml"

    def test_finds_config_in_parent_dir(self, valid_config_dir):
        child = valid_config_dir / "sub" / "deep"
        child.mkdir(parents=True)
        path = _find_config_file(child)
        assert path == valid_config_dir / ".ascend.yaml"

    def test_raises_on_missing_config(self, tmp_path):
        with pytest.raises(ConfigError, match="No .ascend.yaml"):
            _find_config_file(tmp_path)


class TestLoadConfig:
    def test_loads_valid_config(self, valid_config_dir):
        config = load_config(start_dir=valid_config_dir)
        assert config["username"] == "testuser"
        assert config["cluster_name"] == "ascend-test"
        assert config["resource_group"] == "test-rg"
        assert config["namespace"] == "ascend-users-test"
        assert config["storage_account"] == "teststorage123"
        assert config["container_registry"] == "testacr.azurecr.io"

    def test_raises_on_missing_file(self, tmp_path):
        with pytest.raises(ConfigError, match="No .ascend.yaml"):
            load_config(start_dir=tmp_path)

    def test_raises_on_invalid_config(self, invalid_config_dir):
        with pytest.raises(ConfigError, match="missing required fields"):
            load_config(start_dir=invalid_config_dir)

    def test_rejects_placeholder_storage_account(self, tmp_path):
        """Config with PLACEHOLDER storage_account should be rejected."""
        config = {
            "username": "testuser",
            "cluster_name": "ascend-test",
            "resource_group": "test-rg",
            "namespace": "ascend-users-test",
            "storage_account": "PLACEHOLDER",
            "container_registry": "testacr.azurecr.io",
        }
        config_path = tmp_path / ".ascend.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)
        
        with pytest.raises(ConfigError, match="placeholder values"):
            load_config(start_dir=tmp_path)

    def test_rejects_placeholder_container_registry(self, tmp_path):
        """Config with PLACEHOLDER container_registry should be rejected."""
        config = {
            "username": "testuser",
            "cluster_name": "ascend-test",
            "resource_group": "test-rg",
            "namespace": "ascend-users-test",
            "storage_account": "teststorage123",
            "container_registry": "PLACEHOLDER",
        }
        config_path = tmp_path / ".ascend.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)
        
        with pytest.raises(ConfigError, match="placeholder values"):
            load_config(start_dir=tmp_path)

    def test_rejects_multiple_placeholders(self, tmp_path):
        """Config with multiple PLACEHOLDER fields should be rejected."""
        config = {
            "username": "PLACEHOLDER",
            "cluster_name": "ascend-test",
            "resource_group": "test-rg",
            "namespace": "PLACEHOLDER",
            "storage_account": "PLACEHOLDER",
            "container_registry": "PLACEHOLDER",
        }
        config_path = tmp_path / ".ascend.yaml"
        with open(config_path, "w") as f:
            yaml.dump(config, f)
        
        with pytest.raises(ConfigError, match="placeholder values"):
            load_config(start_dir=tmp_path)


class TestSaveConfig:
    def test_saves_and_reads_back(self, tmp_path):
        config = {
            "username": "alice",
            "cluster_name": "prod-cluster",
            "resource_group": "prod-rg",
            "namespace": "ascend-users-alice",
            "storage_account": "prodstorage",
            "container_registry": "prodacr.azurecr.io",
        }
        path = tmp_path / ".ascend.yaml"
        save_config(path, config)

        with open(path) as f:
            loaded = yaml.safe_load(f)
        assert loaded == config

    def test_creates_parent_directories(self, tmp_path):
        path = tmp_path / "deep" / "nested" / ".ascend.yaml"
        save_config(path, {"username": "test"})
        assert path.exists()
