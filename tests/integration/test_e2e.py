"""
End-to-end integration tests for complete job lifecycle on real AKS.

These tests validate the entire flow on real Azure infrastructure.
"""

import pytest


class TestEndToEnd:
    """End-to-end integration tests for complete job lifecycle on real AKS."""

    @pytest.mark.integration
    def test_simple_function_execution(self, real_aks_cluster, fresh_runtime_image):
        """Test complete execution of simple function on AKS."""
        from ascend import ascend

        @ascend(cpu="1", memory="2Gi", timeout=300)
        def add_numbers(a, b):
            return a + b

        result = add_numbers(5, 3)
        assert result == 8

    @pytest.mark.integration
    def test_function_with_dependencies(self, real_aks_cluster):
        """Test function that requires pip package installation."""
        from ascend import ascend

        @ascend(cpu="1", memory="2Gi", requirements=["numpy>=1.26.0"])
        def numpy_operation():
            import numpy as np

            return int(np.array([1, 2, 3]).sum())

        result = numpy_operation()
        assert result == 6

    @pytest.mark.integration
    def test_function_with_complex_result(self, real_aks_cluster):
        """Test serialization of complex return values."""
        from ascend import ascend

        @ascend(cpu="1", memory="2Gi")
        def complex_result():
            return {
                "list": [1, 2, 3],
                "nested": {"a": {"b": "c"}},
                "tuple": (1, 2, 3),
            }

        result = complex_result()
        assert result["list"] == [1, 2, 3]
        assert result["nested"]["a"]["b"] == "c"
        assert result["tuple"] == (1, 2, 3)

    @pytest.mark.integration
    def test_function_raises_exception(self, real_aks_cluster):
        """Test that remote exceptions are propagated with full details."""
        from ascend import ascend
        from ascend.utils.errors import RemoteExecutionError

        @ascend(cpu="1", memory="2Gi", timeout=120)
        def failing_function():
            raise ValueError("Intentional test failure")

        with pytest.raises(RemoteExecutionError) as exc_info:
            failing_function()

        exc = exc_info.value
        # Original exception type is preserved
        assert exc.remote_type == "ValueError"
        # Original message is preserved
        assert "Intentional test failure" in exc.remote_message
        # Remote traceback is available and contains useful context
        assert exc.remote_traceback
        assert "ValueError" in exc.remote_traceback
        assert "failing_function" in exc.remote_traceback
        # Still a RuntimeError subclass for backward compatibility
        assert isinstance(exc, RuntimeError)

    @pytest.mark.integration
    def test_exception_preserves_different_types(self, real_aks_cluster):
        """Test that different exception types are faithfully propagated."""
        from ascend import ascend
        from ascend.utils.errors import RemoteExecutionError

        @ascend(cpu="1", memory="2Gi", timeout=120)
        def type_error_function():
            raise TypeError("wrong argument type")

        with pytest.raises(RemoteExecutionError) as exc_info:
            type_error_function()

        exc = exc_info.value
        assert exc.remote_type == "TypeError"
        assert "wrong argument type" in exc.remote_message

    @pytest.mark.integration
    def test_log_streaming(self, real_aks_cluster, capsys):
        """Test that logs are streamed to terminal."""
        from ascend import ascend

        @ascend(cpu="1", memory="2Gi", stream_logs=True)
        def logging_function():
            print("Test log message from remote execution")
            return "done"

        result = logging_function()
        captured = capsys.readouterr()

        assert result == "done"
        # Verify log streaming occurred
        assert "Streaming logs" in captured.out or "Test log message" in captured.out

    @pytest.mark.integration
    def test_timeout_raises_generic_error(self, real_aks_cluster):
        """Test that timeouts raise generic ExecutionError, not RemoteExecutionError.

        When a job is killed by timeout, no exception.pkl is written,
        so the client falls back to the generic error path.
        """
        from ascend import ascend
        from ascend.utils.errors import RemoteExecutionError

        @ascend(cpu="1", memory="2Gi", timeout=10)
        def slow_function():
            import time

            time.sleep(60)  # Sleep longer than timeout
            return "done"

        with pytest.raises(RuntimeError, match="timed out") as exc_info:
            slow_function()

        # Timeout should NOT produce a RemoteExecutionError
        assert not isinstance(exc_info.value, RemoteExecutionError)
