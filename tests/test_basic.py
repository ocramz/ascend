"""Basic tests for ascend package"""
import pytest
from unittest.mock import patch


def test_imports():
    """Test that core modules can be imported when a backend is available"""
    # Mock the backend detection to avoid requiring adlfs
    with patch("ascend.cloud.registry.detect_backend_name", return_value="azure"):
        import importlib
        import ascend
        importlib.reload(ascend)
        from ascend import ascend as ascend_decorator, AscendConfig
        assert ascend_decorator is not None
        assert AscendConfig is not None


def test_ascend_config():
    """Test AscendConfig initialization"""
    from ascend.decorator import AscendConfig

    config = AscendConfig(
        cpu="2",
        memory="4Gi",
        timeout=1800,
        stream_logs=True,
        requirements=["numpy"]
    )

    assert config.cpu == "2"
    assert config.memory == "4Gi"
    assert config.timeout == 1800
    assert config.stream_logs is True
    assert config.requirements == ["numpy"]


def test_ascend_decorator_exists():
    """Test that decorator can be applied"""
    from ascend.decorator import ascend

    @ascend(cpu="1", memory="2Gi")
    def simple_function(x):
        return x * 2

    # Just verify decorator doesn't crash
    assert simple_function is not None


def test_no_backend_error():
    """Test that NoBackendError is raised when no backend probe module exists"""
    # Load a fresh copy of the registry module to get the real detect function
    import importlib.util, sys
    origin = sys.modules["ascend.cloud.registry"].__spec__.origin
    spec = importlib.util.spec_from_file_location("_registry_fresh", origin)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)

    # Point _BACKENDS at a non-existent probe module so detection fails
    fresh._BACKENDS = {"fake": ("nonexistent_module_xyz", "some.module")}
    with pytest.raises(fresh.NoBackendError, match="No cloud backend installed"):
        fresh.detect_backend_name()


def test_cloud_backend_detection():
    """Test backend detection with mock"""
    with patch("ascend.cloud.registry.importlib") as mock_importlib:
        # Simulate adlfs being available
        mock_importlib.import_module.return_value = True
        from ascend.cloud.registry import detect_backend_name
        result = detect_backend_name()
        assert result == "azure"
