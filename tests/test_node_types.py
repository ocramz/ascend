"""Tests for node type configuration and GPU support"""

import pytest
from ascend import ascend, AscendConfig, NodeType
from ascend.node_types import (
    get_node_type_info,
    validate_node_type,
    NODE_TYPE_CONFIG,
)


def test_node_type_enum_values():
    """Test that NodeType enum has expected values"""
    assert NodeType.STANDARD_SMALL.value == "standard_small"
    assert NodeType.STANDARD_MEDIUM.value == "standard_medium"
    assert NodeType.GPU_SMALL.value == "gpu_small"
    assert NodeType.GPU_MEDIUM.value == "gpu_medium"
    assert NodeType.MEMORY_MEDIUM.value == "memory_medium"


def test_validate_node_type_valid():
    """Test validation of valid node types"""
    node_type = validate_node_type("gpu_small")
    assert node_type == NodeType.GPU_SMALL
    
    node_type = validate_node_type("standard_medium")
    assert node_type == NodeType.STANDARD_MEDIUM


def test_validate_node_type_invalid():
    """Test validation of invalid node types"""
    with pytest.raises(ValueError, match="Invalid node_type"):
        validate_node_type("invalid_type")


def test_get_node_type_info_standard():
    """Test getting info for standard node type"""
    info = get_node_type_info(NodeType.STANDARD_MEDIUM)
    assert info.vm_size == "Standard_D4s_v3"
    assert info.cpu_cores == 4
    assert info.memory_gb == 16
    assert info.gpu_count == 0
    assert info.gpu_type is None
    assert info.node_selector == {"agentpool": "user"}
    assert info.tolerations == []


def test_get_node_type_info_gpu():
    """Test getting info for GPU node type"""
    info = get_node_type_info(NodeType.GPU_SMALL)
    assert info.vm_size == "Standard_NC6s_v3"
    assert info.cpu_cores == 6
    assert info.memory_gb == 112
    assert info.gpu_count == 1
    assert info.gpu_type == "nvidia-tesla-v100"
    assert "agentpool" in info.node_selector
    assert info.node_selector["agentpool"] == "gpu"
    assert len(info.tolerations) > 0
    assert info.tolerations[0]["key"] == "sku"
    assert info.tolerations[0]["value"] == "gpu"


def test_get_node_type_info_memory():
    """Test getting info for memory-optimized node type"""
    info = get_node_type_info(NodeType.MEMORY_LARGE)
    assert info.vm_size == "Standard_E8s_v3"
    assert info.cpu_cores == 8
    assert info.memory_gb == 64
    assert info.gpu_count == 0
    assert info.node_selector == {"agentpool": "memory"}


def test_ascend_config_with_node_type():
    """Test AscendConfig with node_type parameter"""
    config = AscendConfig(
        cpu="2",
        memory="4Gi",
        node_type="gpu_small"
    )
    assert config.node_type == NodeType.GPU_SMALL
    assert config.cpu == "2"
    assert config.memory == "4Gi"


def test_ascend_config_without_node_type():
    """Test AscendConfig without node_type (backward compatibility)"""
    config = AscendConfig(cpu="1", memory="2Gi")
    assert config.node_type is None


def test_ascend_config_invalid_node_type():
    """Test AscendConfig with invalid node_type"""
    with pytest.raises(ValueError, match="Invalid node_type"):
        AscendConfig(node_type="invalid_type")


def test_ascend_decorator_with_node_type():
    """Test @ascend decorator accepts node_type parameter"""
    @ascend(node_type="gpu_small", requirements=["torch"])
    def gpu_function():
        return "result"
    
    # Decorator should not raise an error
    assert callable(gpu_function)


def test_ascend_decorator_with_standard_node():
    """Test @ascend decorator with standard node type"""
    @ascend(node_type="standard_medium", cpu="2", memory="4Gi")
    def standard_function():
        return "result"
    
    assert callable(standard_function)


def test_all_node_types_have_config():
    """Test that all node types have configuration"""
    for node_type in NodeType:
        info = get_node_type_info(node_type)
        assert info is not None
        assert info.vm_size is not None
        assert info.cpu_cores > 0
        assert info.memory_gb > 0


def test_gpu_node_types_have_gpu_count():
    """Test that GPU node types have GPU count > 0"""
    gpu_types = [NodeType.GPU_SMALL, NodeType.GPU_MEDIUM, NodeType.GPU_LARGE]
    for node_type in gpu_types:
        info = get_node_type_info(node_type)
        assert info.gpu_count > 0
        assert info.gpu_type is not None
        assert "nvidia" in info.gpu_type.lower()


def test_standard_node_types_no_gpu():
    """Test that standard node types have no GPU"""
    standard_types = [
        NodeType.STANDARD_SMALL,
        NodeType.STANDARD_MEDIUM,
        NodeType.STANDARD_LARGE,
    ]
    for node_type in standard_types:
        info = get_node_type_info(node_type)
        assert info.gpu_count == 0
        assert info.gpu_type is None


# ============================================================================
# Tests for Azure NC-family explicit instance types
# ============================================================================

def test_ncv3_series_node_types():
    """Test NCv3 series (V100) node types"""
    ncv3_types = [
        (NodeType.NC6S_V3, "Standard_NC6s_v3", 6, 112, 1, "nvidia-tesla-v100"),
        (NodeType.NC12S_V3, "Standard_NC12s_v3", 12, 224, 2, "nvidia-tesla-v100"),
        (NodeType.NC24S_V3, "Standard_NC24s_v3", 24, 448, 4, "nvidia-tesla-v100"),
        (NodeType.NC24RS_V3, "Standard_NC24rs_v3", 24, 448, 4, "nvidia-tesla-v100"),
    ]
    for node_type, vm_size, cpu, memory, gpu_count, gpu_type in ncv3_types:
        info = get_node_type_info(node_type)
        assert info.vm_size == vm_size
        assert info.cpu_cores == cpu
        assert info.memory_gb == memory
        assert info.gpu_count == gpu_count
        assert info.gpu_type == gpu_type


def test_a100_series_node_types():
    """Test NC A100 v4 series node types"""
    a100_types = [
        (NodeType.NC24ADS_A100_V4, "Standard_NC24ads_A100_v4", 24, 220, 1),
        (NodeType.NC48ADS_A100_V4, "Standard_NC48ads_A100_v4", 48, 440, 2),
        (NodeType.NC96ADS_A100_V4, "Standard_NC96ads_A100_v4", 96, 880, 4),
    ]
    for node_type, vm_size, cpu, memory, gpu_count in a100_types:
        info = get_node_type_info(node_type)
        assert info.vm_size == vm_size
        assert info.cpu_cores == cpu
        assert info.memory_gb == memory
        assert info.gpu_count == gpu_count
        assert info.gpu_type == "nvidia-a100-80gb"


def test_h100_series_node_types():
    """Test NCads H100 v5 series node types"""
    h100_types = [
        (NodeType.NC40ADS_H100_V5, "Standard_NC40ads_H100_v5", 40, 320, 1),
        (NodeType.NC80ADIS_H100_V5, "Standard_NC80adis_H100_v5", 80, 640, 2),
    ]
    for node_type, vm_size, cpu, memory, gpu_count in h100_types:
        info = get_node_type_info(node_type)
        assert info.vm_size == vm_size
        assert info.cpu_cores == cpu
        assert info.memory_gb == memory
        assert info.gpu_count == gpu_count
        assert info.gpu_type == "nvidia-h100-nvl"


def test_explicit_instance_type_decorator():
    """Test @ascend decorator with explicit NC instance types"""
    # Test A100 instance
    @ascend(node_type="nc24ads_a100_v4", requirements=["torch"])
    def a100_function():
        return "result"
    
    assert callable(a100_function)
    
    # Test H100 instance
    @ascend(node_type="nc40ads_h100_v5", requirements=["torch"])
    def h100_function():
        return "result"
    
    assert callable(h100_function)


def test_all_nc_family_have_gpu():
    """Test that all NC-family types have GPU configuration"""
    nc_types = [
        NodeType.NC6S_V3, NodeType.NC12S_V3, NodeType.NC24S_V3, NodeType.NC24RS_V3,
        NodeType.NC24ADS_A100_V4, NodeType.NC48ADS_A100_V4, NodeType.NC96ADS_A100_V4,
        NodeType.NC40ADS_H100_V5, NodeType.NC80ADIS_H100_V5,
    ]
    for node_type in nc_types:
        info = get_node_type_info(node_type)
        assert info.gpu_count > 0, f"{node_type} should have GPU"
        assert info.gpu_type is not None, f"{node_type} should have gpu_type"
        assert len(info.tolerations) > 0, f"{node_type} should have tolerations"
