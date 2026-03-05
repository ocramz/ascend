"""
Structured logging infrastructure for Ascend.

Implements JSON Lines (JSONL) format logging with both human-readable
and machine-parsable output.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional, TextIO


class JSONLineFormatter(logging.Formatter):
    """
    Formatter that outputs logs as JSON Lines (one JSON object per line).
    
    Each log entry includes:
    - timestamp: ISO 8601 with microseconds (UTC)
    - level: DEBUG, INFO, WARNING, ERROR, CRITICAL
    - logger: Logger name
    - job_id: Job identifier
    - user: Username
    - project: Project name
    - message: Log message
    - context: Optional structured context
    - extra: Optional additional metadata
    - exception: Optional exception details
    """
    
    def __init__(self, job_id: str, user: str, project: str = "default"):
        super().__init__()
        self.job_id = job_id
        self.user = user
        self.project = project
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON line"""
        # Build base log entry
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "job_id": self.job_id,
            "user": self.user,
            "project": self.project,
            "message": record.getMessage(),
        }
        
        # Add optional context
        if hasattr(record, 'context') and record.context:
            log_entry["context"] = record.context
        
        # Add optional extra fields
        if hasattr(record, 'extra_fields') and record.extra_fields:
            log_entry["extra"] = record.extra_fields
        
        # Add duration if present
        if hasattr(record, 'duration_ms'):
            log_entry["duration_ms"] = record.duration_ms
        
        # Add metrics if present
        if hasattr(record, 'metrics'):
            log_entry["metrics"] = record.metrics
        
        # Add exception details if present
        if record.exc_info:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info),
            }
        
        # Return as single-line JSON
        return json.dumps(log_entry, ensure_ascii=False)


class HumanReadableFormatter(logging.Formatter):
    """
    Formatter that outputs logs in human-readable format.
    
    Format: [YYYY-MM-DD HH:MM:SS] LEVEL    Message [context]
    """
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record for human readability"""
        # Format timestamp
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        # Format level (padded to 8 chars)
        level = record.levelname.ljust(8)
        
        # Get message
        message = record.getMessage()
        
        # Add duration if present
        if hasattr(record, 'duration_ms'):
            duration_sec = record.duration_ms / 1000.0
            if duration_sec < 60:
                message += f" ({duration_sec:.1f}s)"
            else:
                minutes = int(duration_sec / 60)
                seconds = int(duration_sec % 60)
                message += f" ({minutes}m {seconds}s)"
        
        # Add metrics if present
        if hasattr(record, 'metrics'):
            metrics_parts = []
            metrics = record.metrics
            if 'peak_memory_mb' in metrics:
                mem_gb = metrics['peak_memory_mb'] / 1024.0
                metrics_parts.append(f"mem: {mem_gb:.1f}GB")
            if 'gpu_utilization_pct' in metrics:
                metrics_parts.append(f"gpu: {metrics['gpu_utilization_pct']}%")
            if metrics_parts:
                message += f" [{', '.join(metrics_parts)}]"
        
        # Format exception if present
        exception_text = ""
        if record.exc_info:
            exception_text = "\n" + self.formatException(record.exc_info)
        
        return f"[{timestamp}] {level} {message}{exception_text}"


class AscendLogger:
    """
    Logger for Ascend jobs that supports both JSON Lines and human-readable output.
    
    Usage:
        logger = AscendLogger(
            job_id="20251204-143022-alice-default-a3f5d2c8-7b9e4f1a",
            user="alice",
            project="default",
            output_file="/path/to/job.log.jsonl",
        )
        
        logger.info("Job started")
        logger.info("Function completed", duration_ms=276122, metrics={"peak_memory_mb": 4096})
        logger.error("Job failed", exc_info=True)
    """
    
    def __init__(
        self,
        job_id: str,
        user: str,
        project: str = "default",
        output_file: Optional[str] = None,
        console_output: bool = True,
        console_human_readable: bool = True,
        level: int = logging.INFO,
    ):
        """
        Initialize Ascend logger.
        
        Args:
            job_id: Job identifier
            user: Username
            project: Project name
            output_file: Path to JSON Lines output file (optional)
            console_output: Whether to output to console (default: True)
            console_human_readable: Use human-readable format for console (default: True)
            level: Logging level (default: INFO)
        """
        self.job_id = job_id
        self.user = user
        self.project = project
        
        # Create logger
        self.logger = logging.getLogger(f"ascend.job.{job_id}")
        self.logger.setLevel(level)
        self.logger.propagate = False  # Don't propagate to root logger
        
        # Remove any existing handlers
        self.logger.handlers.clear()
        
        # Add file handler with JSON Lines format
        if output_file:
            file_handler = logging.FileHandler(output_file, mode='a', encoding='utf-8')
            file_handler.setFormatter(JSONLineFormatter(job_id, user, project))
            file_handler.setLevel(level)
            self.logger.addHandler(file_handler)
        
        # Add console handler
        if console_output:
            console_handler = logging.StreamHandler(sys.stdout)
            if console_human_readable:
                console_handler.setFormatter(HumanReadableFormatter())
            else:
                console_handler.setFormatter(JSONLineFormatter(job_id, user, project))
            console_handler.setLevel(level)
            self.logger.addHandler(console_handler)
    
    def _log(
        self,
        level: int,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        extra_fields: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[float] = None,
        metrics: Optional[Dict[str, Any]] = None,
        exc_info: bool = False,
    ):
        """Internal logging method"""
        # Create extra dict for custom attributes
        extra = {}
        if context:
            extra['context'] = context
        if extra_fields:
            extra['extra_fields'] = extra_fields
        if duration_ms is not None:
            extra['duration_ms'] = duration_ms
        if metrics:
            extra['metrics'] = metrics
        
        self.logger.log(level, message, exc_info=exc_info, extra=extra)
    
    def debug(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        """Log debug message"""
        self._log(logging.DEBUG, message, context=context, **kwargs)
    
    def info(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        """Log info message"""
        self._log(logging.INFO, message, context=context, **kwargs)
    
    def warning(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        """Log warning message"""
        self._log(logging.WARNING, message, context=context, **kwargs)
    
    def error(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        exc_info: bool = False,
        **kwargs
    ):
        """Log error message"""
        self._log(logging.ERROR, message, context=context, exc_info=exc_info, **kwargs)
    
    def critical(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        exc_info: bool = False,
        **kwargs
    ):
        """Log critical message"""
        self._log(logging.CRITICAL, message, context=context, exc_info=exc_info, **kwargs)


def parse_jsonl_log_file(file_path: str) -> list[dict]:
    """
    Parse a JSON Lines log file.
    
    Args:
        file_path: Path to JSONL log file
        
    Returns:
        List of log entry dictionaries
    """
    log_entries = []
    
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entry = json.loads(line)
                    log_entries.append(entry)
                except json.JSONDecodeError:
                    # Skip invalid lines
                    continue
    
    return log_entries


def format_log_entries_human_readable(log_entries: list[dict]) -> str:
    """
    Format log entries in human-readable format.
    
    Args:
        log_entries: List of log entry dictionaries
        
    Returns:
        Human-readable formatted string
    """
    lines = []
    
    for entry in log_entries:
        # Parse timestamp
        timestamp = entry.get('timestamp', '')
        if timestamp:
            try:
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                timestamp_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, AttributeError):
                timestamp_str = timestamp[:19]  # Fallback
        else:
            timestamp_str = "????-??-?? ??:??:??"
        
        # Format level
        level = entry.get('level', 'INFO').ljust(8)
        
        # Get message
        message = entry.get('message', '')
        
        # Add duration if present
        if 'duration_ms' in entry:
            duration_sec = entry['duration_ms'] / 1000.0
            if duration_sec < 60:
                message += f" ({duration_sec:.1f}s)"
            else:
                minutes = int(duration_sec / 60)
                seconds = int(duration_sec % 60)
                message += f" ({minutes}m {seconds}s)"
        
        # Add metrics if present
        if 'metrics' in entry:
            metrics_parts = []
            metrics = entry['metrics']
            if 'peak_memory_mb' in metrics:
                mem_gb = metrics['peak_memory_mb'] / 1024.0
                metrics_parts.append(f"mem: {mem_gb:.1f}GB")
            if 'gpu_utilization_pct' in metrics:
                metrics_parts.append(f"gpu: {metrics['gpu_utilization_pct']}%")
            if metrics_parts:
                message += f" [{', '.join(metrics_parts)}]"
        
        lines.append(f"[{timestamp_str}] {level} {message}")
        
        # Add exception if present
        if 'exception' in entry:
            exc = entry['exception']
            lines.append(f"    {exc.get('type', 'Exception')}: {exc.get('message', '')}")
            if 'traceback' in exc:
                for line in exc['traceback'].split('\n'):
                    if line.strip():
                        lines.append(f"    {line}")
    
    return '\n'.join(lines)
