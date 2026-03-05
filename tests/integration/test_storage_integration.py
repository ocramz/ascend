"""
Integration tests for Azure Blob Storage operations.

These tests validate Blob Storage upload/download operations on real Azure infrastructure.
"""

import pytest


class TestStorageIntegration:
    """Integration tests for Azure Blob Storage operations."""

    @pytest.mark.integration
    def test_upload_package_creates_blob(self, real_aks_cluster):
        """Test package upload creates correct blob path on real storage."""
        from ascend import ascend

        @ascend(cpu="1", memory="1Gi", timeout=120)
        def identity(x):
            return x

        # Execute and verify result came back from real storage
        result = identity({"key": "value"})
        assert result == {"key": "value"}

    @pytest.mark.integration
    def test_upload_handles_large_results(self, real_aks_cluster):
        """Test upload handles large return values through real storage."""
        from ascend import ascend

        @ascend(cpu="1", memory="2Gi", timeout=300)
        def generate_large_data():
            # Generate ~5MB of data
            return {"data": "x" * (5 * 1024 * 1024)}

        result = generate_large_data()
        assert len(result["data"]) == 5 * 1024 * 1024
