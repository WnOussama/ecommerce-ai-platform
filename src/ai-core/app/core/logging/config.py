"""
Logging Configuration
Structured JSON logging for production
"""

import logging
import sys
import json
from datetime import datetime
from typing import Any, Dict

from app.core.config.settings import settings


class JSONFormatter(logging.Formatter):
    """
    Custom JSON formatter for structured logging.
    Compatible with log aggregation systems (Loki, ELK, etc.)
    """

    def format(self, record: logging.LogRecord) -> str:
        log_data: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add location info
        log_data["location"] = {
            "file": record.filename,
            "line": record.lineno,
            "function": record.funcName
        }

        # Add extra fields
        if hasattr(record, "__dict__"):
            extra_fields = {
                k: v for k, v in record.__dict__.items()
                if k not in (
                    "name", "msg", "args", "created", "filename",
                    "funcName", "levelname", "levelno", "lineno",
                    "module", "msecs", "pathname", "process",
                    "processName", "relativeCreated", "stack_info",
                    "exc_info", "exc_text", "thread", "threadName",
                    "message", "asctime"
                )
            }
            if extra_fields:
                log_data["extra"] = extra_fields

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, default=str)


class ConsoleFormatter(logging.Formatter):
    """
    Human-readable formatter for development console output.
    """

    COLORS = {
        "DEBUG": "\033[36m",    # Cyan
        "INFO": "\033[32m",     # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",    # Red
        "CRITICAL": "\033[35m", # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.RESET)

        # Basic format
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        base = f"{color}[{timestamp}] {record.levelname:8}{self.RESET} {record.name}: {record.getMessage()}"

        # Add extra fields inline
        extra_parts = []
        if hasattr(record, "__dict__"):
            for k, v in record.__dict__.items():
                if k not in (
                    "name", "msg", "args", "created", "filename",
                    "funcName", "levelname", "levelno", "lineno",
                    "module", "msecs", "pathname", "process",
                    "processName", "relativeCreated", "stack_info",
                    "exc_info", "exc_text", "thread", "threadName",
                    "message", "asctime"
                ):
                    extra_parts.append(f"{k}={v}")

        if extra_parts:
            base += f" | {' '.join(extra_parts)}"

        # Add exception if present
        if record.exc_info:
            base += f"\n{self.formatException(record.exc_info)}"

        return base


def setup_logging() -> None:
    """
    Configure application logging.
    Uses JSON format in production, human-readable in development.
    """
    root_logger = logging.getLogger()

    # Clear existing handlers
    root_logger.handlers.clear()

    # Set level
    log_level = getattr(logging, settings.monitoring.log_level.upper(), logging.INFO)
    root_logger.setLevel(log_level)

    # Create handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    # Choose formatter based on environment
    if settings.is_production:
        formatter = JSONFormatter()
    else:
        formatter = ConsoleFormatter()

    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("chromadb").setLevel(logging.WARNING)

    # Log startup
    logger = logging.getLogger(__name__)
    logger.info(
        "Logging configured",
        extra={
            "level": settings.monitoring.log_level,
            "environment": settings.environment,
            "format": "json" if settings.is_production else "console"
        }
    )

