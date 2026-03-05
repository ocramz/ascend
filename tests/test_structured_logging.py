"""Tests for structured logging"""

import json
import tempfile
import pytest
from pathlib import Path
from ascend.utils.structured_logging import (
    AscendLogger,
    JSONLineFormatter,
    HumanReadableFormatter,
    parse_jsonl_log_file,
    format_log_entries_human_readable,
)


class TestAscendLogger:
    """Tests for AscendLogger class"""
    
    def test_logger_creates_jsonl_file(self):
        """Test that logger creates valid JSON Lines file"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
            log_file = f.name
        
        try:
            logger = AscendLogger(
                job_id="20251204-143022-alice-default-a3f5d2c8-7b9e4f1a",
                user="alice",
                project="default",
                output_file=log_file,
                console_output=False,
            )
            
            logger.info("Test message")
            logger.warning("Warning message")
            logger.error("Error message")
            
            # Read and parse log file
            log_entries = parse_jsonl_log_file(log_file)
            
            assert len(log_entries) == 3
            assert log_entries[0]["message"] == "Test message"
            assert log_entries[0]["level"] == "INFO"
            assert log_entries[0]["job_id"] == "20251204-143022-alice-default-a3f5d2c8-7b9e4f1a"
            assert log_entries[1]["level"] == "WARNING"
            assert log_entries[2]["level"] == "ERROR"
        
        finally:
            Path(log_file).unlink(missing_ok=True)
    
    def test_logger_with_context(self):
        """Test logging with context information"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
            log_file = f.name
        
        try:
            logger = AscendLogger(
                job_id="test-job",
                user="alice",
                output_file=log_file,
                console_output=False,
            )
            
            logger.info(
                "Function started",
                context={"function_name": "train_model", "args_count": 2}
            )
            
            log_entries = parse_jsonl_log_file(log_file)
            
            assert len(log_entries) == 1
            assert "context" in log_entries[0]
            assert log_entries[0]["context"]["function_name"] == "train_model"
            assert log_entries[0]["context"]["args_count"] == 2
        
        finally:
            Path(log_file).unlink(missing_ok=True)
    
    def test_logger_with_duration(self):
        """Test logging with duration metrics"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
            log_file = f.name
        
        try:
            logger = AscendLogger(
                job_id="test-job",
                user="alice",
                output_file=log_file,
                console_output=False,
            )
            
            logger.info("Function completed", duration_ms=5123.5)
            
            log_entries = parse_jsonl_log_file(log_file)
            
            assert len(log_entries) == 1
            assert "duration_ms" in log_entries[0]
            assert log_entries[0]["duration_ms"] == 5123.5
        
        finally:
            Path(log_file).unlink(missing_ok=True)
    
    def test_logger_with_metrics(self):
        """Test logging with performance metrics"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
            log_file = f.name
        
        try:
            logger = AscendLogger(
                job_id="test-job",
                user="alice",
                output_file=log_file,
                console_output=False,
            )
            
            logger.info(
                "Job completed",
                metrics={
                    "peak_memory_mb": 4096,
                    "gpu_utilization_pct": 85,
                }
            )
            
            log_entries = parse_jsonl_log_file(log_file)
            
            assert len(log_entries) == 1
            assert "metrics" in log_entries[0]
            assert log_entries[0]["metrics"]["peak_memory_mb"] == 4096
            assert log_entries[0]["metrics"]["gpu_utilization_pct"] == 85
        
        finally:
            Path(log_file).unlink(missing_ok=True)
    
    def test_logger_with_exception(self):
        """Test logging with exception information"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
            log_file = f.name
        
        try:
            logger = AscendLogger(
                job_id="test-job",
                user="alice",
                output_file=log_file,
                console_output=False,
            )
            
            try:
                raise ValueError("Test error")
            except ValueError:
                logger.error("An error occurred", exc_info=True)
            
            log_entries = parse_jsonl_log_file(log_file)
            
            assert len(log_entries) == 1
            assert "exception" in log_entries[0]
            assert log_entries[0]["exception"]["type"] == "ValueError"
            assert "Test error" in log_entries[0]["exception"]["message"]
            assert "traceback" in log_entries[0]["exception"]
        
        finally:
            Path(log_file).unlink(missing_ok=True)
    
    def test_logger_all_levels(self):
        """Test all logging levels"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
            log_file = f.name
        
        try:
            import logging
            logger = AscendLogger(
                job_id="test-job",
                user="alice",
                output_file=log_file,
                console_output=False,
                level=logging.DEBUG,  # Enable DEBUG level
            )
            
            logger.debug("Debug message")
            logger.info("Info message")
            logger.warning("Warning message")
            logger.error("Error message")
            logger.critical("Critical message")
            
            log_entries = parse_jsonl_log_file(log_file)
            
            assert len(log_entries) == 5
            assert log_entries[0]["level"] == "DEBUG"
            assert log_entries[1]["level"] == "INFO"
            assert log_entries[2]["level"] == "WARNING"
            assert log_entries[3]["level"] == "ERROR"
            assert log_entries[4]["level"] == "CRITICAL"
        
        finally:
            Path(log_file).unlink(missing_ok=True)


class TestParseJsonlLogFile:
    """Tests for parsing JSON Lines log files"""
    
    def test_parse_empty_file(self):
        """Test parsing empty log file"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
            log_file = f.name
        
        try:
            log_entries = parse_jsonl_log_file(log_file)
            assert log_entries == []
        
        finally:
            Path(log_file).unlink(missing_ok=True)
    
    def test_parse_file_with_invalid_lines(self):
        """Test that invalid JSON lines are skipped"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
            f.write('{"message": "valid"}\n')
            f.write('invalid json line\n')
            f.write('{"message": "another valid"}\n')
            log_file = f.name
        
        try:
            log_entries = parse_jsonl_log_file(log_file)
            
            assert len(log_entries) == 2
            assert log_entries[0]["message"] == "valid"
            assert log_entries[1]["message"] == "another valid"
        
        finally:
            Path(log_file).unlink(missing_ok=True)
    
    def test_parse_file_with_blank_lines(self):
        """Test that blank lines are skipped"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
            f.write('{"message": "first"}\n')
            f.write('\n')
            f.write('  \n')
            f.write('{"message": "second"}\n')
            log_file = f.name
        
        try:
            log_entries = parse_jsonl_log_file(log_file)
            
            assert len(log_entries) == 2
            assert log_entries[0]["message"] == "first"
            assert log_entries[1]["message"] == "second"
        
        finally:
            Path(log_file).unlink(missing_ok=True)


class TestFormatLogEntriesHumanReadable:
    """Tests for human-readable log formatting"""
    
    def test_format_basic_entries(self):
        """Test formatting basic log entries"""
        log_entries = [
            {
                "timestamp": "2025-12-04T14:30:22.000000Z",
                "level": "INFO",
                "message": "Job started"
            },
            {
                "timestamp": "2025-12-04T14:35:23.000000Z",
                "level": "INFO",
                "message": "Job completed"
            }
        ]
        
        formatted = format_log_entries_human_readable(log_entries)
        
        assert "[2025-12-04 14:30:22] INFO     Job started" in formatted
        assert "[2025-12-04 14:35:23] INFO     Job completed" in formatted
    
    def test_format_with_duration(self):
        """Test formatting log entries with duration"""
        log_entries = [
            {
                "timestamp": "2025-12-04T14:30:22.000000Z",
                "level": "INFO",
                "message": "Function completed",
                "duration_ms": 5123.5
            }
        ]
        
        formatted = format_log_entries_human_readable(log_entries)
        
        assert "Function completed (5.1s)" in formatted
    
    def test_format_with_duration_minutes(self):
        """Test formatting duration in minutes"""
        log_entries = [
            {
                "timestamp": "2025-12-04T14:30:22.000000Z",
                "level": "INFO",
                "message": "Function completed",
                "duration_ms": 125000  # 125 seconds = 2m 5s
            }
        ]
        
        formatted = format_log_entries_human_readable(log_entries)
        
        assert "Function completed (2m 5s)" in formatted
    
    def test_format_with_metrics(self):
        """Test formatting log entries with metrics"""
        log_entries = [
            {
                "timestamp": "2025-12-04T14:30:22.000000Z",
                "level": "INFO",
                "message": "Job completed",
                "metrics": {
                    "peak_memory_mb": 4096,
                    "gpu_utilization_pct": 85
                }
            }
        ]
        
        formatted = format_log_entries_human_readable(log_entries)
        
        assert "Job completed [mem: 4.0GB, gpu: 85%]" in formatted
    
    def test_format_with_exception(self):
        """Test formatting log entries with exceptions"""
        log_entries = [
            {
                "timestamp": "2025-12-04T14:30:22.000000Z",
                "level": "ERROR",
                "message": "Job failed",
                "exception": {
                    "type": "ValueError",
                    "message": "Invalid input",
                    "traceback": "Traceback (most recent call last):\n  File test.py, line 1"
                }
            }
        ]
        
        formatted = format_log_entries_human_readable(log_entries)
        
        assert "ERROR    Job failed" in formatted
        assert "ValueError: Invalid input" in formatted
        assert "Traceback (most recent call last):" in formatted
